# mtDNA Location Classifier MVP (Google Colab)
# Accepts accession number → Fetches PubMed ID + isolate name → Gets abstract → Predicts location
import os
#import streamlit as st
import subprocess
import re
from Bio import Entrez
import fitz
import spacy
from spacy.cli import download
from NER.PDF import pdf
from NER.WordDoc import wordDoc
from NER.html import extractHTML
from NER.word2Vec import word2vec
from transformers import pipeline
import urllib.parse, requests
from pathlib import Path
from upgradeClassify import filter_context_for_sample, infer_location_for_sample
import model
# Set your email (required by NCBI Entrez)
#Entrez.email = "your-email@example.com"
import nltk

nltk.download("stopwords")
nltk.download("punkt")
nltk.download('punkt_tab')
# Step 1: Get PubMed ID from Accession using EDirect
from Bio import Entrez, Medline
import re

Entrez.email = "your_email@example.com"

# --- Helper Functions (Re-organized and Upgraded) ---

def fetch_ncbi_metadata(accession_number):
    """
    Fetches metadata directly from NCBI GenBank using Entrez.
    Includes robust error handling and improved field extraction.
    Prioritizes location extraction from geo_loc_name, then notes, then other qualifiers.
    Also attempts to extract ethnicity and sample_type (ancient/modern).

    Args:
        accession_number (str): The NCBI accession number (e.g., "ON792208").

    Returns:
        dict: A dictionary containing 'country', 'specific_location', 'ethnicity',
              'sample_type', 'collection_date', 'isolate', 'title', 'doi', 'pubmed_id'.
    """
    Entrez.email = "your.email@example.com" # Required by NCBI, REPLACE WITH YOUR EMAIL

    country = "unknown"
    specific_location = "unknown"
    ethnicity = "unknown"
    sample_type = "unknown"
    collection_date = "unknown"
    isolate = "unknown"
    title = "unknown"
    doi = "unknown"
    pubmed_id = None
    all_feature = "unknown"

    KNOWN_COUNTRIES = [
        "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda", "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan",
        "Bahamas", "Bahrain", "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bhutan", "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei", "Bulgaria", "Burkina Faso", "Burundi",
        "Cabo Verde", "Cambodia", "Cameroon", "Canada", "Central African Republic", "Chad", "Chile", "China", "Colombia", "Comoros", "Congo (Brazzaville)", "Congo (Kinshasa)", "Costa Rica", "Croatia", "Cuba", "Cyprus", "Czechia",
        "Denmark", "Djibouti", "Dominica", "Dominican Republic", "Ecuador", "Egypt", "El Salvador", "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia",
        "Fiji", "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Greece", "Grenada", "Guatemala", "Guinea", "Guinea-Bissau", "Guyana",
        "Haiti", "Honduras", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy", "Ivory Coast", "Jamaica", "Japan", "Jordan",
        "Kazakhstan", "Kenya", "Kiribati", "Kosovo", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania", "Luxembourg",
        "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta", "Marshall Islands", "Mauritania", "Mauritius", "Mexico", "Micronesia", "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco", "Mozambique", "Myanmar",
        "Namibia", "Nauru", "Nepal", "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea", "North Macedonia", "Norway", "Oman",
        "Pakistan", "Palau", "Palestine", "Panama", "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Romania", "Russia", "Rwanda",
        "Saint Kitts and Nevis", "Saint Lucia", "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe", "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore", "Slovakia", "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden", "Switzerland", "Syria",
        "Taiwan", "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Tuvalu",
        "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Vanuatu", "Vatican City", "Venezuela", "Vietnam",
        "Yemen", "Zambia", "Zimbabwe"
    ]
    COUNTRY_PATTERN = re.compile(r'\b(' + '|'.join(re.escape(c) for c in KNOWN_COUNTRIES) + r')\b', re.IGNORECASE)

    try:
        handle = Entrez.efetch(db="nucleotide", id=str(accession_number), rettype="gb", retmode="xml")
        record = Entrez.read(handle)
        handle.close()

        gb_seq = None
        # Validate record structure: It should be a list with at least one element (a dict)
        if isinstance(record, list) and len(record) > 0:
            if isinstance(record[0], dict):
                gb_seq = record[0]
            else:
                print(f"Warning: record[0] is not a dictionary for {accession_number}. Type: {type(record[0])}")
        else:
            print(f"Warning: No valid record or empty record list from NCBI for {accession_number}.")

        # If gb_seq is still None, return defaults
        if gb_seq is None:
            return {"country": "unknown",
                "specific_location": "unknown",
                "ethnicity": "unknown",
                "sample_type": "unknown",
                "collection_date": "unknown",
                "isolate": "unknown",
                "title": "unknown",
                "doi": "unknown",
                "pubmed_id": None,
                "all_features": "unknown"}


        # If gb_seq is valid, proceed with extraction
        collection_date = gb_seq.get("GBSeq_create-date","unknown")

        references = gb_seq.get("GBSeq_references", [])
        for ref in references:
            if not pubmed_id:
                pubmed_id = ref.get("GBReference_pubmed",None)
            if title == "unknown":
                title = ref.get("GBReference_title","unknown")
            for xref in ref.get("GBReference_xref", []):
                if xref.get("GBXref_dbname") == "doi":
                    doi = xref.get("GBXref_id")
                    break

        features = gb_seq.get("GBSeq_feature-table", [])

        context_for_flagging = "" # Accumulate text for ancient/modern detection
        features_context = ""
        for feature in features:
            if feature.get("GBFeature_key") == "source":
                feature_context = ""
                qualifiers = feature.get("GBFeature_quals", [])
                found_country = "unknown"
                found_specific_location = "unknown"
                found_ethnicity = "unknown"

                temp_geo_loc_name = "unknown"
                temp_note_origin_locality = "unknown"
                temp_country_qual = "unknown"
                temp_locality_qual = "unknown"
                temp_collection_location_qual = "unknown"
                temp_isolation_source_qual = "unknown"
                temp_env_sample_qual = "unknown"
                temp_pop_qual = "unknown"
                temp_organism_qual = "unknown"
                temp_specimen_qual = "unknown"
                temp_strain_qual = "unknown"

                for qual in qualifiers:
                    qual_name = qual.get("GBQualifier_name")
                    qual_value = qual.get("GBQualifier_value")
                    feature_context += qual_name + ": " + qual_value +"\n"
                    if qual_name == "collection_date":
                        collection_date = qual_value
                    elif qual_name == "isolate":
                        isolate = qual_value
                    elif qual_name == "population":
                        temp_pop_qual = qual_value
                    elif qual_name == "organism":
                        temp_organism_qual = qual_value
                    elif qual_name == "specimen_voucher" or qual_name == "specimen":
                        temp_specimen_qual = qual_value
                    elif qual_name == "strain":
                        temp_strain_qual = qual_value
                    elif qual_name == "isolation_source":
                        temp_isolation_source_qual = qual_value
                    elif qual_name == "environmental_sample":
                        temp_env_sample_qual = qual_value

                    if qual_name == "geo_loc_name": temp_geo_loc_name = qual_value
                    elif qual_name == "note":
                        if qual_value.startswith("origin_locality:"):
                            temp_note_origin_locality = qual_value
                        context_for_flagging += qual_value + " " # Capture all notes for flagging  
                    elif qual_name == "country": temp_country_qual = qual_value
                    elif qual_name == "locality": temp_locality_qual = qual_value
                    elif qual_name == "collection_location": temp_collection_location_qual = qual_value


                # --- Aggregate all relevant info into context_for_flagging ---
                context_for_flagging += f" {isolate} {temp_isolation_source_qual} {temp_specimen_qual} {temp_strain_qual} {temp_organism_qual} {temp_geo_loc_name} {temp_collection_location_qual} {temp_env_sample_qual}"
                context_for_flagging = context_for_flagging.strip()
                
                # --- Determine final country and specific_location based on priority ---
                if temp_geo_loc_name != "unknown":
                    parts = [p.strip() for p in temp_geo_loc_name.split(':')]
                    if len(parts) > 1: 
                      found_specific_location = parts[-1]; found_country = parts[0]
                    else: found_country = temp_geo_loc_name; found_specific_location = "unknown"
                elif temp_note_origin_locality != "unknown":
                    match = re.search(r"origin_locality:\s*(.*)", temp_note_origin_locality, re.IGNORECASE)
                    if match:
                        location_string = match.group(1).strip()
                        parts = [p.strip() for p in location_string.split(':')]
                        if len(parts) > 1: 
                            #found_country = parts[-1]; found_specific_location = parts[0]
                            found_country = model.get_country_from_text(temp_note_origin_locality.lower())
                            if found_country == "unknown":
                                found_country = parts[0]; 
                                found_specific_location = parts[-1]
                        else: found_country = location_string; found_specific_location = "unknown"
                elif temp_locality_qual != "unknown":
                    found_country_match = COUNTRY_PATTERN.search(temp_locality_qual)
                    if found_country_match: found_country = found_country_match.group(1); temp_loc = re.sub(re.escape(found_country), '', temp_locality_qual, flags=re.IGNORECASE).strip().replace(',', '').replace(':', '').replace(';', '').strip(); found_specific_location = temp_loc if temp_loc else "unknown"
                    else: found_specific_location = temp_locality_qual; found_country = "unknown"
                elif temp_collection_location_qual != "unknown":
                    found_country_match = COUNTRY_PATTERN.search(temp_collection_location_qual)
                    if found_country_match: found_country = found_country_match.group(1); temp_loc = re.sub(re.escape(found_country), '', temp_collection_location_qual, flags=re.IGNORECASE).strip().replace(',', '').replace(':', '').replace(';', '').strip(); found_specific_location = temp_loc if temp_loc else "unknown"
                    else: found_specific_location = temp_collection_location_qual; found_country = "unknown"
                elif temp_isolation_source_qual != "unknown":
                    found_country_match = COUNTRY_PATTERN.search(temp_isolation_source_qual)
                    if found_country_match: found_country = found_country_match.group(1); temp_loc = re.sub(re.escape(found_country), '', temp_isolation_source_qual, flags=re.IGNORECASE).strip().replace(',', '').replace(':', '').replace(';', '').strip(); found_specific_location = temp_loc if temp_loc else "unknown"
                    else: found_specific_location = temp_isolation_source_qual; found_country = "unknown"
                elif temp_env_sample_qual != "unknown":
                    found_country_match = COUNTRY_PATTERN.search(temp_env_sample_qual)
                    if found_country_match: found_country = found_country_match.group(1); temp_loc = re.sub(re.escape(found_country), '', temp_env_sample_qual, flags=re.IGNORECASE).strip().replace(',', '').replace(':', '').replace(';', '').strip(); found_specific_location = temp_loc if temp_loc else "unknown"
                    else: found_specific_location = temp_env_sample_qual; found_country = "unknown"
                if found_country == "unknown" and temp_country_qual != "unknown":
                     found_country_match = COUNTRY_PATTERN.search(temp_country_qual)
                     if found_country_match: found_country = found_country_match.group(1)

                country = found_country
                specific_location = found_specific_location
                # --- Determine final ethnicity ---
                if temp_pop_qual != "unknown":
                    found_ethnicity = temp_pop_qual
                elif isolate != "unknown" and re.fullmatch(r'[A-Za-z\s\-]+', isolate) and get_country_from_text(isolate) == "unknown":
                     found_ethnicity = isolate
                elif context_for_flagging != "unknown": # Use the broader context for ethnicity patterns
                    eth_match = re.search(r'(?:population|ethnicity|isolate source):\s*([A-Za-z\s\-]+)', context_for_flagging, re.IGNORECASE)
                    if eth_match:
                        found_ethnicity = eth_match.group(1).strip()

                ethnicity = found_ethnicity

                # --- Determine sample_type (ancient/modern) ---
                if context_for_flagging:
                    sample_type, explain = detect_ancient_flag(context_for_flagging)
                features_context += feature_context + "\n"
                break

        if specific_location != "unknown" and specific_location.lower() == country.lower():
            specific_location = "unknown"
        if not features_context:  features_context = "unknown"    
        return {"country": country.lower(),
                "specific_location": specific_location.lower(),
                "ethnicity": ethnicity.lower(),
                "sample_type": sample_type.lower(),
                "collection_date": collection_date,
                "isolate": isolate,
                "title": title,
                "doi": doi,
                "pubmed_id": pubmed_id,
                "all_features": features_context}

    except:
        print(f"Error fetching NCBI data for {accession_number}")
        return {"country": "unknown",
                "specific_location": "unknown",
                "ethnicity": "unknown",
                "sample_type": "unknown",
                "collection_date": "unknown",
                "isolate": "unknown",
                "title": "unknown",
                "doi": "unknown",
                "pubmed_id": None,
                "all_features": "unknown"}

# --- Helper function for country matching (re-defined from main code to be self-contained) ---
_country_keywords = {
    "thailand": "Thailand", "laos": "Laos", "cambodia": "Cambodia", "myanmar": "Myanmar",
    "philippines": "Philippines", "indonesia": "Indonesia", "malaysia": "Malaysia",
    "china": "China", "chinese": "China", "india": "India", "taiwan": "Taiwan",
    "vietnam": "Vietnam", "russia": "Russia", "siberia": "Russia", "nepal": "Nepal",
    "japan": "Japan", "sumatra": "Indonesia", "borneu": "Indonesia",
    "yunnan": "China", "tibet": "China", "northern mindanao": "Philippines",
    "west malaysia": "Malaysia", "north thailand": "Thailand", "central thailand": "Thailand",
    "northeast thailand": "Thailand", "east myanmar": "Myanmar", "west thailand": "Thailand",
    "central india": "India", "east india": "India", "northeast india": "India",
    "south sibera": "Russia", "mongolia": "China", "beijing": "China", "south korea": "South Korea",
    "north asia": "unknown", "southeast asia": "unknown", "east asia": "unknown"
}

def get_country_from_text(text):
    text_lower = text.lower()
    for keyword, country in _country_keywords.items():
        if keyword in text_lower:
            return country
    return "unknown"
# The result will be seen as manualLink for the function get_paper_text
# def search_google_custom(query, max_results=3):
#   # query should be the title from ncbi or paper/source title
#     GOOGLE_CSE_API_KEY = os.environ["GOOGLE_CSE_API_KEY"]
#     GOOGLE_CSE_CX = os.environ["GOOGLE_CSE_CX"]
#     endpoint = os.environ["SEARCH_ENDPOINT"]
#     params = {
#         "key": GOOGLE_CSE_API_KEY,
#         "cx": GOOGLE_CSE_CX,
#         "q": query,
#         "num": max_results
#     }
#     try:
#         response = requests.get(endpoint, params=params)
#         if response.status_code == 429:
#             print("Rate limit hit. Try again later.")
#             return []
#         response.raise_for_status()
#         data = response.json().get("items", [])
#         return [item.get("link") for item in data if item.get("link")]
#     except Exception as e:
#         print("Google CSE error:", e)
#         return []

def search_serper(query, max_results=3):
    """Search via Serper API (Google results). Requires SERPER_API_KEY env var.
    Sign up free at serper.dev — 2500 queries/month free tier."""
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        return []
    try:
        response = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
            timeout=10
        )
        if response.status_code == 429:
            print("Serper rate limit hit.")
            return []
        response.raise_for_status()
        items = response.json().get("organic", [])
        return [item["link"] for item in items if item.get("link")]
    except Exception as e:
        print(f"Serper search error: {e}")
        return []


def search_pubmed_free(query, max_results=3):
    """Search PubMed E-utilities — always free, no API key needed."""
    try:
        search_resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": query, "retmode": "json", "retmax": max_results},
            timeout=10
        )
        search_resp.raise_for_status()
        ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
        return [f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" for pmid in ids]
    except Exception as e:
        print(f"PubMed free search error: {e}")
        return []


def search_europepmc_free(query, max_results=3):
    """Search Europe PMC — always free, no API key needed."""
    try:
        resp = requests.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": query, "format": "json", "pageSize": max_results, "resultType": "lite"},
            timeout=10
        )
        resp.raise_for_status()
        results = resp.json().get("resultList", {}).get("result", [])
        links = []
        for r in results:
            pmid = r.get("pmid")
            doi = r.get("doi")
            if pmid:
                links.append(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
            elif doi:
                links.append(f"https://doi.org/{doi}")
        return links
    except Exception as e:
        print(f"EuropePMC free search error: {e}")
        return []


def search_ncbi_direct_urls(query):
    """Build direct NCBI database URLs from accession patterns — no API needed."""
    links = []
    # BioSample: SAMN*, SAME*, SAMD*
    for m in re.finditer(r'\b(SAM[NED]\d+)\b', query, re.IGNORECASE):
        acc = m.group(1).upper()
        links.append(f"https://www.ncbi.nlm.nih.gov/biosample/{acc}/")
    # SRA runs: SRR*, ERR*, DRR*
    for m in re.finditer(r'\b([SED]RR\d+)\b', query, re.IGNORECASE):
        acc = m.group(1).upper()
        links.append(f"https://www.ncbi.nlm.nih.gov/sra/{acc}/")
    # BioProject: PRJNA*, PRJEB*, PRJDB*
    for m in re.finditer(r'\b(PRJ[NED][A-Z]\d+)\b', query, re.IGNORECASE):
        acc = m.group(1).upper()
        links.append(f"https://www.ncbi.nlm.nih.gov/bioproject/{acc}/")
    # GenBank nucleotide accessions (e.g. MT123456)
    for m in re.finditer(r'\b([A-Z]{1,2}\d{5,8})\b', query):
        acc = m.group(1)
        links.append(f"https://www.ncbi.nlm.nih.gov/nuccore/{acc}/")
    return links


def search_google_custom(query, max_results=3):
    """Layered search: Serper (Google) → PubMed → EuropePMC → direct NCBI URLs."""
    links = []

    # Layer 1: Serper API (Google results) — best coverage if key is set
    serper_links = search_serper(query, max_results)
    if serper_links:
        print(f"  [Serper] {len(serper_links)} results")
        for l in serper_links:
            if l not in links:
                links.append(l)
        return links

    # Layer 2: PubMed free search
    pubmed_links = search_pubmed_free(query, max_results)
    if pubmed_links:
        print(f"  [PubMed] {len(pubmed_links)} results")
        for l in pubmed_links:
            if l not in links:
                links.append(l)

    # Layer 3: EuropePMC free search
    epmc_links = search_europepmc_free(query, max_results)
    if epmc_links:
        print(f"  [EuropePMC] {len(epmc_links)} results")
        for l in epmc_links:
            if l not in links:
                links.append(l)

    # Layer 4: Direct NCBI URLs from accession patterns in the query
    direct_links = search_ncbi_direct_urls(query)
    for l in direct_links:
        if l not in links:
            links.append(l)

    if not links:
        print(f"  [search] No results found for: {query}")
    return links


def search_google_custom_backup(query, max_results=3):
    """Kept for compatibility — now delegates to the layered search."""
    return search_google_custom(query, max_results)
# Step 3: Extract Text: Get the paper (html text), sup. materials (pdf, doc, excel) and do text-preprocessing
# Step 3.1: Extract Text
# sub: download excel file
def download_excel_file(url, save_path="temp.xlsx"):
    if "view.officeapps.live.com" in url:
        parsed_url = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        real_url = urllib.parse.unquote(parsed_url["src"][0])
        response = requests.get(real_url)
        with open(save_path, "wb") as f:
            f.write(response.content)
        return save_path
    elif url.startswith("http") and (url.endswith(".xls") or url.endswith(".xlsx")):
        response = requests.get(url)
        response.raise_for_status()  # Raises error if download fails
        with open(save_path, "wb") as f:
            f.write(response.content)
        return save_path
    else:
        print("URL must point directly to an .xls or .xlsx file\n or it already downloaded.")
        return url
def get_paper_text(doi,id,manualLinks=None):
  # create the temporary folder to contain the texts
  folder_path = Path("data/"+str(id))
  if not folder_path.exists():
      cmd = f'mkdir data/{id}'
      result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
      print("data/"+str(id) +" created.")
  else:
      print("data/"+str(id) +" already exists.")
  saveLinkFolder = "data/"+id

  link = 'https://doi.org/' + doi
  '''textsToExtract = { "doiLink":"paperText"
                        "file1.pdf":"text1",
                        "file2.doc":"text2",
                        "file3.xlsx":excelText3'''
  textsToExtract = {}
  # get the file to create listOfFile for each id
  html = extractHTML.HTML("",link)
  jsonSM = html.getSupMaterial()
  text = ""
  links  = [link] + sum((jsonSM[key] for key in jsonSM),[])
  if manualLinks != None:
    links += manualLinks
  for l in links:
    # get the main paper
    name = l.split("/")[-1]
    file_path = folder_path / name
    if l == link:
      text = html.getListSection()
      textsToExtract[link] = text
    elif l.endswith(".pdf"):
      if file_path.is_file():
          l = saveLinkFolder + "/" + name
          print("File exists.")
      p = pdf.PDF(l,saveLinkFolder,doi)
      f = p.openPDFFile()
      pdf_path = saveLinkFolder + "/" + l.split("/")[-1]
      doc = fitz.open(pdf_path)
      text = "\n".join([page.get_text() for page in doc])
      textsToExtract[l] = text
    elif l.endswith(".doc") or l.endswith(".docx"):
      d = wordDoc.wordDoc(l,saveLinkFolder)
      text = d.extractTextByPage()
      textsToExtract[l] = text
    elif l.split(".")[-1].lower() in "xlsx":
      wc = word2vec.word2Vec()
      # download excel file if it not downloaded yet
      savePath = saveLinkFolder +"/"+ l.split("/")[-1]
      excelPath = download_excel_file(l, savePath)
      corpus = wc.tableTransformToCorpusText([],excelPath)
      text = ''
      for c in corpus:
        para = corpus[c]
        for words in para:
          text += " ".join(words)
      textsToExtract[l] = text
  # delete folder after finishing getting text
  #cmd = f'rm -r data/{id}'
  #result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
  return textsToExtract
# Step 3.2: Extract context
def extract_context(text, keyword, window=500):
    # firstly try accession number
    idx = text.find(keyword)
    if idx == -1:
        return "Sample ID not found."
    return text[max(0, idx-window): idx+window]
def extract_relevant_paragraphs(text, accession, keep_if=None, isolate=None):
    if keep_if is None:
        keep_if = ["sample", "method", "mtdna", "sequence", "collected", "dataset", "supplementary", "table"]

    outputs = ""
    text = text.lower()

    # If isolate is provided, prioritize paragraphs that mention it
    # If isolate is provided, prioritize paragraphs that mention it
    if accession and accession.lower() in text:
        if extract_context(text, accession.lower(), window=700) != "Sample ID not found.":
            outputs += extract_context(text, accession.lower(), window=700)       
    if isolate and isolate.lower() in text:
        if extract_context(text, isolate.lower(), window=700) != "Sample ID not found.":
            outputs += extract_context(text, isolate.lower(), window=700)
    for keyword in keep_if:
        para = extract_context(text, keyword)
        if para and para not in outputs:
            outputs += para + "\n"
    return outputs
# Step 4: Classification for now (demo purposes)
# 4.1: Using a HuggingFace model (question-answering)
def infer_fromQAModel(context, question="Where is the mtDNA sample from?"):
    try:
        qa = pipeline("question-answering", model="distilbert-base-uncased-distilled-squad")
        result = qa({"context": context, "question": question})
        return result.get("answer", "Unknown")
    except Exception as e:
        return f"Error: {str(e)}"

# 4.2: Infer from haplogroup
# Load pre-trained spaCy model for NER
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    download("en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")

# Define the haplogroup-to-region mapping (simple rule-based)
import csv

def load_haplogroup_mapping(csv_path):
    mapping = {}
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            mapping[row["haplogroup"]] = [row["region"],row["source"]]
    return mapping

# Function to extract haplogroup from the text
def extract_haplogroup(text):
    match = re.search(r'\bhaplogroup\s+([A-Z][0-9a-z]*)\b', text)
    if match:
        submatch = re.match(r'^[A-Z][0-9]*', match.group(1))
        if submatch:
            return submatch.group(0)
        else:
            return match.group(1)  # fallback
    fallback = re.search(r'\b([A-Z][0-9a-z]{1,5})\b', text)
    if fallback:
        return fallback.group(1)
    return None


# Function to extract location based on NER
def extract_location(text):
    doc = nlp(text)
    locations = []
    for ent in doc.ents:
        if ent.label_ == "GPE":  # GPE = Geopolitical Entity (location)
            locations.append(ent.text)
    return locations

# Function to infer location from haplogroup
def infer_location_from_haplogroup(haplogroup):
  haplo_map = load_haplogroup_mapping("data/haplogroup_regions_extended.csv")
  return haplo_map.get(haplogroup, ["Unknown","Unknown"])

# Function to classify the mtDNA sample
def classify_mtDNA_sample_from_haplo(text):
    # Extract haplogroup
    haplogroup = extract_haplogroup(text)
    # Extract location based on NER
    locations = extract_location(text)
    # Infer location based on haplogroup
    inferred_location, sourceHaplo = infer_location_from_haplogroup(haplogroup)[0],infer_location_from_haplogroup(haplogroup)[1]
    return {
        "source":sourceHaplo,
        "locations_found_in_context": locations,
        "haplogroup": haplogroup,
        "inferred_location": inferred_location

    }
# 4.3 Get from available NCBI
def infer_location_fromNCBI(accession):
    try:
        handle = Entrez.efetch(db="nuccore", id=accession, rettype="medline", retmode="text")
        text = handle.read()
        handle.close()
        match = re.search(r'/(geo_loc_name|country|location)\s*=\s*"([^"]+)"', text)
        if match:
            return match.group(2), match.group(0)  # This is the value like "Brunei"
        return "Not found", "Not found"

    except Exception as e:
        print("❌ Entrez error:", e)
        return "Not found", "Not found"

### ANCIENT/MODERN FLAG
from Bio import Entrez
import re

def flag_ancient_modern(accession, textsToExtract, isolate=None):
    """
    Try to classify a sample as Ancient or Modern using:
    1. NCBI accession (if available)
    2. Supplementary text or context fallback
    """
    context = ""
    label, explain = "", ""

    try:
        # Check if we can fetch metadata from NCBI using the accession
        handle = Entrez.efetch(db="nuccore", id=accession, rettype="medline", retmode="text")
        text = handle.read()
        handle.close()

        isolate_source = re.search(r'/(isolation_source)\s*=\s*"([^"]+)"', text)
        if isolate_source:
            context += isolate_source.group(0) + " "

        specimen = re.search(r'/(specimen|specimen_voucher)\s*=\s*"([^"]+)"', text)
        if specimen:
            context += specimen.group(0) + " "

        if context.strip():
            label, explain = detect_ancient_flag(context)
            if label!="Unknown":
              return label, explain + " from NCBI\n(" + context + ")"

        # If no useful NCBI metadata, check supplementary texts
        if textsToExtract:
            labels = {"modern": [0, ""], "ancient": [0, ""], "unknown": 0}

            for source in textsToExtract:
                text_block = textsToExtract[source]
                context = extract_relevant_paragraphs(text_block, accession, isolate=isolate)  # Reduce to informative paragraph(s)
                label, explain = detect_ancient_flag(context)

                if label == "Ancient":
                    labels["ancient"][0] += 1
                    labels["ancient"][1] += f"{source}:\n{explain}\n\n"
                elif label == "Modern":
                    labels["modern"][0] += 1
                    labels["modern"][1] += f"{source}:\n{explain}\n\n"
                else:
                    labels["unknown"] += 1

            if max(labels["modern"][0],labels["ancient"][0]) > 0:
                if labels["modern"][0] > labels["ancient"][0]:
                    return "Modern", labels["modern"][1]
                else:
                    return "Ancient", labels["ancient"][1]
            else:
              return "Unknown", "No strong keywords detected"
        else:
            print("No DOI or PubMed ID available for inference.")
            return "", ""

    except Exception as e:
        print("Error:", e)
        return "", ""


def detect_ancient_flag(context_snippet):
    context = context_snippet.lower()

    ancient_keywords = [
        "ancient", "archaeological", "prehistoric", "neolithic", "mesolithic", "paleolithic",
        "bronze age", "iron age", "burial", "tomb", "skeleton", "14c", "radiocarbon", "carbon dating",
        "postmortem damage", "udg treatment", "adna", "degradation", "site", "excavation",
        "archaeological context", "temporal transect", "population replacement", "cal bp", "calbp", "carbon dated"
    ]

    modern_keywords = [
        "modern", "hospital", "clinical", "consent","blood","buccal","unrelated", "blood sample","buccal sample","informed consent", "donor", "healthy", "patient",
        "genotyping", "screening", "medical", "cohort", "sequencing facility", "ethics approval",
        "we analysed", "we analyzed", "dataset includes", "new sequences", "published data",
        "control cohort", "sink population", "genbank accession", "sequenced", "pipeline", 
        "bioinformatic analysis", "samples from", "population genetics", "genome-wide data", "imr collection"
    ]

    ancient_hits = [k for k in ancient_keywords if k in context]
    modern_hits = [k for k in modern_keywords if k in context]

    if ancient_hits and not modern_hits:
        return "Ancient", f"Flagged as ancient due to keywords: {', '.join(ancient_hits)}"
    elif modern_hits and not ancient_hits:
        return "Modern", f"Flagged as modern due to keywords: {', '.join(modern_hits)}"
    elif ancient_hits and modern_hits:
        if len(ancient_hits) >= len(modern_hits):
            return "Ancient", f"Mixed context, leaning ancient due to: {', '.join(ancient_hits)}"
        else:
            return "Modern", f"Mixed context, leaning modern due to: {', '.join(modern_hits)}"
    
    # Fallback to QA
    answer = infer_fromQAModel(context, question="Are the mtDNA samples ancient or modern? Explain why.")
    if answer.startswith("Error"):
        return "Unknown", answer
    if "ancient" in answer.lower():
        return "Ancient", f"Leaning ancient based on QA: {answer}"
    elif "modern" in answer.lower():
        return "Modern", f"Leaning modern based on QA: {answer}"
    else:
        return "Unknown", f"No strong keywords or QA clues. QA said: {answer}"

# STEP 5: Main pipeline: accession -> 1. get pubmed id and isolate -> 2. get doi -> 3. get text -> 4. prediction -> 5. output: inferred location + explanation + confidence score
def classify_sample_location(accession):
  outputs = {}
  keyword, context, location, qa_result, haplo_result = "", "", "", "", ""
  # Step 1: get pubmed id and isolate
  pubmedID, isolate = get_info_from_accession(accession)
  '''if not pubmedID:
    return {"error": f"Could not retrieve PubMed ID for accession {accession}"}'''
  if not isolate:
    isolate = "UNKNOWN_ISOLATE"
  # Step 2: get doi
  doi = get_doi_from_pubmed_id(pubmedID)
  '''if not doi:
    return {"error": "DOI not found for this accession. Cannot fetch paper or context."}'''
  # Step 3: get text
  '''textsToExtract = { "doiLink":"paperText"
                        "file1.pdf":"text1",
                        "file2.doc":"text2",
                        "file3.xlsx":excelText3'''
  if doi and pubmedID:                      
    textsToExtract = get_paper_text(doi,pubmedID)
  else: textsToExtract = {}  
  '''if not textsToExtract:
    return {"error": f"No texts extracted for DOI {doi}"}'''
  if isolate not in [None, "UNKNOWN_ISOLATE"]:
    label, explain = flag_ancient_modern(accession,textsToExtract,isolate)
  else: 
    label, explain = flag_ancient_modern(accession,textsToExtract)  
  # Step 4: prediction
  outputs[accession] = {}
  outputs[isolate] = {}
  # 4.0 Infer from NCBI
  location, outputNCBI = infer_location_fromNCBI(accession)
  NCBI_result = {
      "source": "NCBI",
      "sample_id": accession,
      "predicted_location": location,
      "context_snippet": outputNCBI}
  outputs[accession]["NCBI"]= {"NCBI": NCBI_result}
  if textsToExtract:
    long_text = ""
    for key in textsToExtract:
      text = textsToExtract[key]
      # try accession number first
      outputs[accession][key] = {}
      keyword = accession
      context = extract_context(text, keyword, window=500)
      # 4.1: Using a HuggingFace model (question-answering)
      location = infer_fromQAModel(context, question=f"Where is the mtDNA sample {keyword} from?")
      qa_result = {
          "source": key,
          "sample_id": keyword,
          "predicted_location": location,
          "context_snippet": context
      }
      outputs[keyword][key]["QAModel"] = qa_result
      # 4.2: Infer from haplogroup
      haplo_result = classify_mtDNA_sample_from_haplo(context)
      outputs[keyword][key]["haplogroup"] = haplo_result
      # try isolate
      keyword = isolate
      outputs[isolate][key] = {}
      context = extract_context(text, keyword, window=500)
      # 4.1.1: Using a HuggingFace model (question-answering)
      location = infer_fromQAModel(context, question=f"Where is the mtDNA sample {keyword} from?")
      qa_result = {
          "source": key,
          "sample_id": keyword,
          "predicted_location": location,
          "context_snippet": context
      }
      outputs[keyword][key]["QAModel"] = qa_result
      # 4.2.1: Infer from haplogroup
      haplo_result = classify_mtDNA_sample_from_haplo(context)
      outputs[keyword][key]["haplogroup"] = haplo_result
      # add long text
      long_text += text + ". \n"
    # 4.3: UpgradeClassify
    # try sample_id as accession number
    sample_id = accession
    if sample_id:
      filtered_context = filter_context_for_sample(sample_id.upper(), long_text, window_size=1)
      locations = infer_location_for_sample(sample_id.upper(), filtered_context)
      if locations!="No clear location found in top matches":
        outputs[sample_id]["upgradeClassifier"] = {}
        outputs[sample_id]["upgradeClassifier"]["upgradeClassifier"] = {
          "source": "From these sources combined: "+ ", ".join(list(textsToExtract.keys())),
          "sample_id": sample_id,
          "predicted_location": ", ".join(locations),
          "context_snippep": "First 1000 words: \n"+ filtered_context[:1000]
        }
    # try sample_id as isolate name
    sample_id = isolate
    if sample_id:
      filtered_context = filter_context_for_sample(sample_id.upper(), long_text, window_size=1)
      locations = infer_location_for_sample(sample_id.upper(), filtered_context)
      if locations!="No clear location found in top matches":
        outputs[sample_id]["upgradeClassifier"] = {}
        outputs[sample_id]["upgradeClassifier"]["upgradeClassifier"] = {
          "source": "From these sources combined: "+ ", ".join(list(textsToExtract.keys())),
          "sample_id": sample_id,
          "predicted_location": ", ".join(locations),
          "context_snippep": "First 1000 words: \n"+ filtered_context[:1000]
        }
  return outputs, label, explain