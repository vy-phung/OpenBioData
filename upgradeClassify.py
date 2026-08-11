import re
import spacy
from nltk.tokenize import sent_tokenize, word_tokenize
import nltk
#import coreferee
import copy
from sentence_transformers import SentenceTransformer, util
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_distances
from collections import defaultdict
import numpy as np
#from mtdna_classifier import infer_fromQAModel
# 1. SENTENCE-BERT MODEL
# Step 1: Preprocess the text
def normalize_text(text):
    # Normalize various separators to "-"
    text = re.sub(r'\s*(–+|—+|--+>|–>|->|-->|to|→|➝|➔|➡)\s*', '-', text, flags=re.IGNORECASE)
    # Fix GEN10GEN30 → GEN10-GEN30
    text = re.sub(r'\b([a-zA-Z]+)(\d+)(\1)(\d+)\b', r'\1\2-\1\4', text)
    # Fix GEN10-30 → GEN10-GEN30
    text = re.sub(r'\b([a-zA-Z]+)(\d+)-(\d+)\b', r'\1\2-\1\3', text)
    return text

def preprocess_text(text):
    normalized = normalize_text(text)
    sentences = sent_tokenize(normalized)
    return [re.sub(r"[^a-zA-Z0-9\s\-]", "", s).strip() for s in sentences]

# Before step 2, check NLP cache to avoid calling it muliple times:
# Global model cache
_spacy_models = {}

def get_spacy_model(model_name, add_coreferee=False):
    global _spacy_models
    if model_name not in _spacy_models:
        nlp = spacy.load(model_name)
        if add_coreferee and "coreferee" not in nlp.pipe_names:
            nlp.add_pipe("coreferee")
        _spacy_models[model_name] = nlp
    return _spacy_models[model_name]

# Step 2: NER to Extract Locations and Sample Names
def extract_entities(text, sample_id=None):
    nlp = get_spacy_model("en_core_web_sm")
    doc = nlp(text)
    
    # Filter entities by GPE, but exclude things that match sample ID format
    gpe_candidates = [ent.text for ent in doc.ents if ent.label_ == "GPE"]
    
    # Remove entries that match SAMPLE ID patterns like XXX123 or similar
    gpe_filtered = [gpe for gpe in gpe_candidates if not re.fullmatch(r'[A-Z]{2,5}\d{2,4}', gpe.strip())]
    
    # Optional: further filter known invalid patterns (e.g., things shorter than 3 chars, numeric only)
    gpe_filtered = [gpe for gpe in gpe_filtered if len(gpe) > 2 and not gpe.strip().isdigit()]
    
    if sample_id is None:
        return list(set(gpe_filtered)), []
    else:
        sample_prefix = re.match(r'[A-Z]+', sample_id).group()
        samples = re.findall(rf'{sample_prefix}\d+', text)
        return list(set(gpe_filtered)), list(set(samples))

# Step 3: Build a Soft Matching Layer
# Handle patterns like "BRU1–BRU20" and identify BRU18 as part of it.
def is_sample_in_range(sample_id, sentence):
    # Match prefix up to digits
    sample_prefix_match = re.match(r'^([A-Z0-9]+?)(?=\d+$)', sample_id)
    sample_number_match = re.search(r'(\d+)$', sample_id)

    if not sample_prefix_match or not sample_number_match:
        return False

    sample_prefix = sample_prefix_match.group(1)
    sample_number = int(sample_number_match.group(1))
    sentence = normalize_text(sentence)
    # Case 1: Full prefix on both sides
    pattern1 = rf'{sample_prefix}(\d+)\s*-\s*{sample_prefix}(\d+)'
    for match in re.findall(pattern1, sentence):
        start, end = int(match[0]), int(match[1])
        if start <= sample_number <= end:
            return True

    # Case 2: Prefix only on first number
    pattern2 = rf'{sample_prefix}(\d+)\s*-\s*(\d+)'
    for match in re.findall(pattern2, sentence):
        start, end = int(match[0]), int(match[1])
        if start <= sample_number <= end:
            return True

    return False

# Step 4: Use coreferree to merge the sentences have same coreference # still cannot cause packages conflict
# ========== HEURISTIC GROUP → LOCATION MAPPERS ==========
# === Generalized version to replace your old extract_sample_to_group_general ===
# === Generalized version to replace your old extract_group_to_location_general ===
def extract_population_locations(text):
    text = normalize_text(text)
    pattern = r'([A-Za-z ,\-]+)\n([A-Z]+\d*)\n([A-Za-z ,\-]+)\n([A-Za-z ,\-]+)'
    pop_to_location = {}

    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        _, pop_code, region, country = match.groups()
        pop_to_location[pop_code.upper()] = f"{region.strip()}\n{country.strip()}"

    return pop_to_location
 
def extract_sample_ranges(text):
    text = normalize_text(text)
    # Updated pattern to handle punctuation and line breaks
    pattern = r'\b([A-Z0-9]+\d+)[–\-]([A-Z0-9]+\d+)[,:\.\s]*([A-Z0-9]+\d+)\b'
    sample_to_pop = {}
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        start_id, end_id, pop_code = match.groups()
        start_prefix = re.match(r'^([A-Z0-9]+?)(?=\d+$)', start_id, re.IGNORECASE).group(1).upper()
        end_prefix = re.match(r'^([A-Z0-9]+?)(?=\d+$)', end_id, re.IGNORECASE).group(1).upper()
        if start_prefix != end_prefix:
            continue
        start_num = int(re.search(r'(\d+)$', start_id).group())
        end_num = int(re.search(r'(\d+)$', end_id).group())
        for i in range(start_num, end_num + 1):
            sample_id = f"{start_prefix}{i:03d}"
            sample_to_pop[sample_id] = pop_code.upper()

    return sample_to_pop

def filter_context_for_sample(sample_id, full_text, window_size=2):

    # Normalize and tokenize
    full_text = normalize_text(full_text)
    sentences = sent_tokenize(full_text)

    # Step 1: Find indices with direct mention or range match
    match_indices = [
        i for i, s in enumerate(sentences)
        if sample_id in s or is_sample_in_range(sample_id, s)
    ]

    # Step 2: Get sample → group mapping from full text
    sample_to_group = extract_sample_ranges(full_text)
    group_id = sample_to_group.get(sample_id)

    # Step 3: Find group-related sentences
    group_indices = []
    if group_id:
        for i, s in enumerate(sentences):
            if group_id in s:
                group_indices.append(i)

    # Step 4: Collect sentences within window
    selected_indices = set()
    if len(match_indices + group_indices) > 0:
      for i in match_indices + group_indices:
          start = max(0, i - window_size)
          end = min(len(sentences), i + window_size + 1)
          selected_indices.update(range(start, end))

      filtered_sentences = [sentences[i] for i in sorted(selected_indices)]
      return " ".join(filtered_sentences) 
    return full_text  
# Load the SpaCy transformer model with coreferee
def mergeCorefSen(text):
  sen = preprocess_text(text)
  return sen

# Before step 5 and below, let check transformer cache to avoid calling again
# Global SBERT model cache
_sbert_models = {}

def get_sbert_model(model_name="all-MiniLM-L6-v2"):
    global _sbert_models
    if model_name not in _sbert_models:
        _sbert_models[model_name] = SentenceTransformer(model_name)
    return _sbert_models[model_name]

# Step 5: Sentence-BERT retriever → Find top paragraphs related to keyword.
'''Use sentence transformers to embed the sentence that mentions the sample and
compare it to sentences that mention locations.'''

def find_top_para(sample_id, text,top_k=5):
    sentences = mergeCorefSen(text)
    model = get_sbert_model("all-mpnet-base-v2")
    embeddings = model.encode(sentences, convert_to_tensor=True)

    # Find the sentence that best matches the sample_id
    sample_matches = [s for s in sentences if sample_id in s or is_sample_in_range(sample_id, s)]
    if not sample_matches:
        return [],"No context found for sample"

    sample_embedding = model.encode(sample_matches[0], convert_to_tensor=True)
    cos_scores = util.pytorch_cos_sim(sample_embedding, embeddings)[0]

    # Get top-k most similar sentence indices
    top_indices = cos_scores.argsort(descending=True)[:top_k]
    return top_indices, sentences

# Step 6: DBSCAN to cluster the group of similar paragraphs.
def clusterPara(tokens):
  # Load Sentence-BERT model
  sbert_model = get_sbert_model("all-mpnet-base-v2")
  sentence_embeddings = sbert_model.encode(tokens)

  # Compute cosine distance matrix
  distance_matrix = cosine_distances(sentence_embeddings)

  # DBSCAN clustering
  clustering_model = DBSCAN(eps=0.3, min_samples=1, metric="precomputed")
  cluster_labels = clustering_model.fit_predict(distance_matrix)

  # Group sentences by cluster
  clusters = defaultdict(list)
  cluster_embeddings = defaultdict(list)
  sentence_to_cluster = {}
  for i, label in enumerate(cluster_labels):
    clusters[label].append(tokens[i])
    cluster_embeddings[label].append(sentence_embeddings[i])
    sentence_to_cluster[tokens[i]] = label
  # Compute cluster centroids
  centroids = {
      label: np.mean(embs, axis=0)
      for label, embs in cluster_embeddings.items()
  }
  return clusters, sentence_to_cluster, centroids

def rankSenFromCluster(clusters, sentence_to_cluster, centroids, target_sentence):
  target_cluster = sentence_to_cluster[target_sentence]
  target_centroid = centroids[target_cluster]
  sen_rank = []
  sen_order = list(sentence_to_cluster.keys())
  # Compute distances to other cluster centroids
  dists = []
  for label, centroid in centroids.items():
    dist = cosine_distances([target_centroid], [centroid])[0][0]
    dists.append((label, dist))
  dists.sort(key=lambda x: x[1])  # sort by proximity
  for d in dists:
    cluster = clusters[d[0]]
    for sen in cluster:
      if sen != target_sentence:
        sen_rank.append(sen_order.index(sen))
  return sen_rank
# Step 7: Final Inference Wrapper
def infer_location_for_sample(sample_id, context_text):
    # Go through each of the top sentences in order
    top_indices, sentences = find_top_para(sample_id, context_text,top_k=5)
    if top_indices==[] or sentences == "No context found for sample":
      return "No clear location found in top matches"
    clusters, sentence_to_cluster, centroids = clusterPara(sentences)
    topRankSen_DBSCAN = []
    mostTopSen = ""
    locations = ""
    i = 0
    while len(locations) == 0 or i < len(top_indices):
      # Firstly, start with the top-ranked Sentence-BERT result
      idx = top_indices[i]
      best_sentence = sentences[idx]
      if i == 0:
        mostTopSen = best_sentence
      locations, _ = extract_entities(best_sentence, sample_id)
      if locations:
        return locations
      # If no location, then look for sample overlap in the same DBSCAN cluster
      # Compute distances to other cluster centroids
      if len(topRankSen_DBSCAN)==0 and mostTopSen:
        topRankSen_DBSCAN = rankSenFromCluster(clusters, sentence_to_cluster, centroids, mostTopSen)
      if i >= len(topRankSen_DBSCAN): break
      idx_DBSCAN = topRankSen_DBSCAN[i]
      best_sentence_DBSCAN = sentences[idx_DBSCAN]
      locations, _ = extract_entities(best_sentence, sample_id)
      if locations:
        return locations
      # If no, then backtrack to next best Sentence-BERT sentence (such as 2nd rank sentence), and repeat step 1 and 2 until run out
      i += 1
    # Last resort: LLM (e.g. chatGPT, deepseek, etc.)
    #if len(locations) == 0:
    return "No clear location found in top matches"
