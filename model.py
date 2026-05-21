import re
import json
import os
import asyncio
from collections import defaultdict
import ast
import math
try:
    import pycountry
except ImportError:
    pycountry = None
try:
    from docx import Document
except ImportError:
    Document = None
try:
    import numpy as np
    import faiss
except ImportError:
    np = faiss = None
try:
    import data_preprocess
except ImportError:
    data_preprocess = None
try:
    import mtdna_classifier
except ImportError:
    mtdna_classifier = None
try:
    import smart_fallback
except ImportError:
    smart_fallback = None
try:
    import pipeline
except ImportError:
    pipeline = None
# --- IMPORTANT: UNCOMMENT AND CONFIGURE YOUR REAL API KEY ---
import google.generativeai as genai

#genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
#genai.configure(api_key=os.getenv("GOOGLE_API_KEY_BACKUP"))
genai.configure(api_key=os.getenv("NEW_GOOGLE_API_KEY"))

import nltk
from nltk.corpus import stopwords
try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords')
nltk.download('punkt_tab')    
# # --- Define Pricing Constants (for Gemini 1.5 Flash & text-embedding-004) ---
# # Prices are per 1,000 tokens
# PRICE_PER_1K_INPUT_LLM = 0.000075  # $0.075 per 1M tokens
# PRICE_PER_1K_OUTPUT_LLM = 0.0003   # $0.30 per 1M tokens
# PRICE_PER_1K_EMBEDDING_INPUT = 0.000025 # $0.025 per 1M tokens

# Gemini 2.5 Flash-Lite pricing per 1,000 tokens
PRICE_PER_1K_INPUT_LLM = 0.00010      # $0.10 per 1M input tokens
PRICE_PER_1K_OUTPUT_LLM = 0.00040     # $0.40 per 1M output tokens

# Embedding-001 pricing per 1,000 input tokens
PRICE_PER_1K_EMBEDDING_INPUT = 0.00015  # $0.15 per 1M input tokens
# --- API Functions (REAL API FUNCTIONS) ---

# def get_embedding(text, task_type="RETRIEVAL_DOCUMENT"):
#     """Generates an embedding for the given text using a Google embedding model."""
#     try:
#         result = genai.embed_content(
#             model="models/text-embedding-004", # Specify the embedding model
#             content=text,
#             task_type=task_type
#         )
#         return np.array(result['embedding']).astype('float32')
#     except Exception as e:
#         print(f"Error getting embedding: {e}")
#         return np.zeros(768, dtype='float32')
def get_embedding(text, task_type="RETRIEVAL_DOCUMENT"):
    """Safe Gemini 1.5 embedding call with fallback."""
    import numpy as np
    try:
        if not text or len(text.strip()) == 0:
            raise ValueError("Empty text cannot be embedded.")
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=text,
            task_type=task_type
        )
        return np.array(result['embedding'], dtype='float32')
    except Exception as e:
        print(f"❌ Embedding error: {e}")
        return np.zeros(768, dtype='float32')


def call_llm_api(prompt, model_name=None):
    """Call LLM — tries Anthropic Claude first, falls back to Gemini."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=anthropic_key)
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text, None
        except Exception as e:
            print(f"Anthropic API error: {e} — falling back to Gemini.")

    # Gemini fallback
    gemini_model = model_name or "gemini-2.5-flash-lite"
    try:
        m = genai.GenerativeModel(gemini_model)
        response = m.generate_content(prompt)
        return response.text, m
    except Exception as e:
        print(f"Gemini API error: {e}")
        return "Error: Could not get response from LLM API.", None


# --- Core Document Processing Functions (All previously provided and fixed) ---

def read_docx_text(path):
    """
    Reads text and extracts potential table-like strings from a .docx document.
    Separates plain text from structured [ [ ] ] list-like tables.
    Also attempts to extract a document title.
    """
    doc = Document(path)
    plain_text_paragraphs = []
    table_strings = []
    document_title = "Unknown Document Title" # Default

    # Attempt to extract the document title from the first few paragraphs
    title_paragraphs = [p.text.strip() for p in doc.paragraphs[:5] if p.text.strip()]
    if title_paragraphs:
        # A heuristic to find a title: often the first or second non-empty paragraph
        # or a very long first paragraph if it's the title
        if len(title_paragraphs[0]) > 50 and "Human Genetics" not in title_paragraphs[0]:
            document_title = title_paragraphs[0]
        elif len(title_paragraphs) > 1 and len(title_paragraphs[1]) > 50 and "Human Genetics" not in title_paragraphs[1]:
            document_title = title_paragraphs[1]
        elif any("Complete mitochondrial genomes" in p for p in title_paragraphs):
            # Fallback to a known title phrase if present
            document_title = "Complete mitochondrial genomes of Thai and Lao populations indicate an ancient origin of Austroasiatic groups and demic diffusion in the spread of Tai–Kadai languages"

    current_table_lines = []
    in_table_parsing_mode = False

    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue

        # Condition to start or continue table parsing
        if text.startswith("## Table "): # Start of a new table section
            if in_table_parsing_mode and current_table_lines:
                table_strings.append("\n".join(current_table_lines))
            current_table_lines = [text] # Include the "## Table X" line
            in_table_parsing_mode = True
        elif in_table_parsing_mode and (text.startswith("[") or text.startswith('"')):
            # Continue collecting lines if we're in table mode and it looks like table data
            # Table data often starts with '[' for lists, or '"' for quoted strings within lists.
            current_table_lines.append(text)
        else:
            # If not in table mode, or if a line doesn't look like table data,
            # then close the current table (if any) and add the line to plain text.
            if in_table_parsing_mode and current_table_lines:
                table_strings.append("\n".join(current_table_lines))
                current_table_lines = []
            in_table_parsing_mode = False
            plain_text_paragraphs.append(text)

    # After the loop, add any remaining table lines
    if current_table_lines:
        table_strings.append("\n".join(current_table_lines))

    return "\n".join(plain_text_paragraphs), table_strings, document_title

# --- Structured Data Extraction and RAG Functions ---

def parse_literal_python_list(table_str):
    list_match = re.search(r'(\[\s*\[\s*(?:.|\n)*?\s*\]\s*\])', table_str)
    #print("Debug: list_match object (before if check):", list_match)
    if not list_match:
        if "table" in table_str.lower(): # then the table doest have the "]]" at the end
            table_str += "]]"
            list_match = re.search(r'(\[\s*\[\s*(?:.|\n)*?\s*\]\s*\])', table_str)
    if list_match:
        try:
            matched_string = list_match.group(1)
            #print("Debug: Matched string for literal_eval:", matched_string)
            return ast.literal_eval(matched_string)
        except (ValueError, SyntaxError) as e:
            print(f"Error evaluating literal: {e}")
            return []
    return []


_individual_code_parser = re.compile(r'([A-Z0-9]+?)(\d+)$', re.IGNORECASE)
def _parse_individual_code_parts(code_str):
    match = _individual_code_parser.search(code_str)
    if match:
        return match.group(1), match.group(2)
    return None, None


def parse_sample_id_to_population_code(plain_text_content):
    sample_id_map = {}
    contiguous_ranges_data = defaultdict(list)

    #section_start_marker = "The sample identification of each population is as follows:"
    section_start_marker = ["The sample identification of each population is as follows:","## table"]
    
    for s in section_start_marker:
      relevant_text_search = re.search(
          re.escape(s.lower()) + r"\s*(.*?)(?=\n##|\Z)",
          plain_text_content.lower(),
          re.DOTALL
      )
      if relevant_text_search: 
        break
      
    if not relevant_text_search:
        print("Warning: 'Sample ID Population Code' section start marker not found or block empty.")
        return sample_id_map, contiguous_ranges_data

    relevant_text_block = relevant_text_search.group(1).strip()

    # print(f"\nDEBUG_PARSING: --- Start of relevant_text_block (first 500 chars) ---")
    # print(relevant_text_block[:500])
    # print(f"DEBUG_PARSING: --- End of relevant_text_block (last 500 chars) ---")
    # print(relevant_text_block[-500:])
    # print(f"DEBUG_PARSING: Relevant text block length: {len(relevant_text_block)}")

    mapping_pattern = re.compile(
    r'\b([A-Z0-9]+\d+)(?:-([A-Z0-9]+\d+))?\s+([A-Z0-9]+)\b', # Changed the last group
    re.IGNORECASE)

    range_expansion_count = 0
    direct_id_count = 0
    total_matches_found = 0
    for match in mapping_pattern.finditer(relevant_text_block):
        total_matches_found += 1
        id1_full_str, id2_full_str_opt, pop_code = match.groups()

        #print(f"  DEBUG_PARSING: Matched: '{match.group(0)}'")

        pop_code_upper = pop_code.upper()

        id1_prefix, id1_num_str = _parse_individual_code_parts(id1_full_str)
        if id1_prefix is None:
            #print(f"    DEBUG_PARSING: Failed to parse ID1: {id1_full_str}. Skipping this mapping.")
            continue

        if id2_full_str_opt:
            id2_prefix_opt, id2_num_str_opt = _parse_individual_code_parts(id2_full_str_opt)
            if id2_prefix_opt is None:
                #print(f"    DEBUG_PARSING: Failed to parse ID2: {id2_full_str_opt}. Treating {id1_full_str} as single ID1.")
                sample_id_map[f"{id1_prefix.upper()}{id1_num_str}"] = pop_code_upper
                direct_id_count += 1
                continue

            #print(f"    DEBUG_PARSING: Comparing prefixes: '{id1_prefix.lower()}' vs '{id2_prefix_opt.lower()}'")
            if id1_prefix.lower() == id2_prefix_opt.lower():
                #print(f"    DEBUG_PARSING: ---> Prefixes MATCH for range expansion! Range: {id1_prefix}{id1_num_str}-{id2_prefix_opt}{id2_num_str_opt}")
                try:
                    start_num = int(id1_num_str)
                    end_num = int(id2_num_str_opt)
                    for num in range(start_num, end_num + 1):
                        sample_id = f"{id1_prefix.upper()}{num}"
                        sample_id_map[sample_id] = pop_code_upper
                        range_expansion_count += 1
                    contiguous_ranges_data[id1_prefix.upper()].append(
                        (start_num, end_num, pop_code_upper)
                    )
                except ValueError:
                    print(f"        DEBUG_PARSING: ValueError in range conversion for {id1_num_str}-{id2_num_str_opt}. Adding endpoints only.")
                    sample_id_map[f"{id1_prefix.upper()}{id1_num_str}"] = pop_code_upper
                    sample_id_map[f"{id2_prefix_opt.upper()}{id2_num_str_opt}"] = pop_code_upper
                    direct_id_count += 2
            else:
                #print(f"    DEBUG_PARSING: Prefixes MISMATCH for range: '{id1_prefix}' vs '{id2_prefix_opt}'. Adding endpoints only.")
                sample_id_map[f"{id1_prefix.upper()}{id1_num_str}"] = pop_code_upper
                sample_id_map[f"{id2_prefix_opt.upper()}{id2_num_str_opt}"] = pop_code_upper
                direct_id_count += 2
        else:
            sample_id_map[f"{id1_prefix.upper()}{id1_num_str}"] = pop_code_upper
            direct_id_count += 1

    # print(f"DEBUG_PARSING: Total matches found by regex: {total_matches_found}.")
    # print(f"DEBUG_PARSING: Parsed sample IDs: {len(sample_id_map)} total entries.")
    # print(f"DEBUG_PARSING:   (including {range_expansion_count} from range expansion and {direct_id_count} direct ID/endpoint entries).")
    return sample_id_map, contiguous_ranges_data

country_keywords_regional_overrides = {
    "north thailand": "Thailand", "central thailand": "Thailand",
    "northeast thailand": "Thailand", "east myanmar": "Myanmar", "west thailand": "Thailand",
    "central india": "India", "east india": "India", "northeast india": "India",
    "south sibera": "Russia", "siberia": "Russia", "yunnan": "China", #"tibet": "China",
    "sumatra": "Indonesia", "borneo": "Indonesia",
    "northern mindanao": "Philippines", "west malaysia": "Malaysia",
    "mongolia": "China",
    "beijing": "China",
    "north laos": "Laos", "central laos": "Laos",
    "east myanmar": "Myanmar", "west myanmar": "Myanmar"}

# Updated get_country_from_text function
def get_country_from_text(text):
    text_lower = text.lower()

    # 1. Use pycountry for official country names and common aliases
    for country in pycountry.countries:
        # Check full name match first
        if text_lower == country.name.lower():
            return country.name
        
        # Safely check for common_name
        if hasattr(country, 'common_name') and text_lower == country.common_name.lower():
            return country.common_name
            
        # Safely check for official_name
        if hasattr(country, 'official_name') and text_lower == country.official_name.lower():
            return country.official_name

        # Check if country name is part of the text (e.g., 'Thailand' in 'Thailand border')
        if country.name.lower() in text_lower:
            return country.name
            
        # Safely check if common_name is part of the text
        if hasattr(country, 'common_name') and country.common_name.lower() in text_lower:
            return country.common_name
    # 2. Prioritize specific regional overrides
    for keyword, country in country_keywords_regional_overrides.items():
        if keyword in text_lower:
            return country
    # 3. Check for broader regions that you want to map to "unknown" or a specific country
    if "north asia" in text_lower or "southeast asia" in text_lower or "east asia" in text_lower:
        return "unknown"

    return "unknown"

# Get the list of English stop words from NLTK
non_meaningful_pop_names = set(stopwords.words('english'))

def parse_population_code_to_country(plain_text_content, table_strings):
    pop_code_country_map = {}
    pop_code_ethnicity_map = {} # NEW: To store ethnicity for structured lookup
    pop_code_specific_loc_map = {} # NEW: To store specific location for structured lookup

    # Regex for parsing population info in structured lists and general text
    # This pattern captures: (Pop Name/Ethnicity) (Pop Code) (Region/Specific Location) (Country) (Linguistic Family)
    # The 'Pop Name/Ethnicity' (Group 1) is often the ethnicity
    pop_info_pattern = re.compile(
          r'([A-Za-z\s]+?)\s+([A-Z]+\d*)\s+'      # Pop Name (Group 1), Pop Code (Group 2) - Changed \d+ to \d* for codes like 'SH'
          r'([A-Za-z\s\(\)\-,\/]+?)\s+'          # Region/Specific Location (Group 3)
          r'(North+|South+|West+|East+|Thailand|Laos|Cambodia|Myanmar|Philippines|Indonesia|Malaysia|China|India|Taiwan|Vietnam|Russia|Nepal|Japan|South Korea)\b' # Country (Group 4)
          r'(?:.*?([A-Za-z\s\-]+))?\s*'          # Optional Linguistic Family (Group 5), made optional with ?, followed by optional space
          r'(\d+(?:\s+\d+\.?\d*)*)?', # Match all the numbers (Group 6) - made optional
          re.IGNORECASE
      )
    for table_str in table_strings:
        table_data = parse_literal_python_list(table_str)
        if table_data:
            is_list_of_lists = bool(table_data) and isinstance(table_data[0], list)
            if is_list_of_lists:
                for row_idx, row in enumerate(table_data):
                    row_text = " ".join(map(str, row))
                    match = pop_info_pattern.search(row_text)
                    if match:
                        pop_name = match.group(1).strip()
                        pop_code = match.group(2).upper()
                        specific_loc_text = match.group(3).strip()
                        country_text = match.group(4).strip()
                        linguistic_family = match.group(5).strip() if match.group(5) else 'unknown'

                        final_country = get_country_from_text(country_text)
                        if final_country == 'unknown': # Try specific loc text for country if direct country is not found
                            final_country = get_country_from_text(specific_loc_text)

                        if pop_code:
                            pop_code_country_map[pop_code] = final_country

                            # Populate ethnicity map (often Pop Name is ethnicity)
                            pop_code_ethnicity_map[pop_code] = pop_name

                            # Populate specific location map
                            pop_code_specific_loc_map[pop_code] = specific_loc_text # Store as is from text
            else:
                row_text = " ".join(map(str, table_data))   
                match = pop_info_pattern.search(row_text)
                if match:
                    pop_name = match.group(1).strip()
                    pop_code = match.group(2).upper()
                    specific_loc_text = match.group(3).strip()
                    country_text = match.group(4).strip()
                    linguistic_family = match.group(5).strip() if match.group(5) else 'unknown'

                    final_country = get_country_from_text(country_text)
                    if final_country == 'unknown': # Try specific loc text for country if direct country is not found
                        final_country = get_country_from_text(specific_loc_text)

                    if pop_code:
                        pop_code_country_map[pop_code] = final_country

                        # Populate ethnicity map (often Pop Name is ethnicity)
                        pop_code_ethnicity_map[pop_code] = pop_name

                        # Populate specific location map
                        pop_code_specific_loc_map[pop_code] = specific_loc_text # Store as is from text

                        # # Special case refinements for ethnicity/location if more specific rules are known from document:
                        # if pop_name.lower() == "khon mueang": # and specific conditions if needed
                        #     pop_code_ethnicity_map[pop_code] = "Khon Mueang"
                        #     # If Khon Mueang has a specific city/district, add here
                        #     # e.g., if 'Chiang Mai' is directly linked to KM1 in a specific table
                        #     # pop_code_specific_loc_map[pop_code] = "Chiang Mai"
                        # elif pop_name.lower() == "lawa":
                        #      pop_code_ethnicity_map[pop_code] = "Lawa"
                        # # Add similar specific rules for other populations (e.g., Mon for MO1, MO2, MO3)
                        # elif pop_name.lower() == "mon":
                        #     pop_code_ethnicity_map[pop_code] = "Mon"
                        #     # For MO2: "West Thailand (Thailand Myanmar border)" -> no city
                        #     # For MO3: "East Myanmar (Thailand Myanmar border)" -> no city
                        #     # If the doc gives "Bangkok" for MO4, add it here for MO4's actual specific_location.
                        # # etc.

    # Fallback to parsing general plain text content (sentences)
    sentences = data_preprocess.extract_sentences(plain_text_content)
    for s in sentences: # Still focusing on just this one sentence
      # Use re.finditer to get all matches
      matches = pop_info_pattern.finditer(s)
      pop_name, pop_code, specific_loc_text, country_text = "unknown", "unknown", "unknown", "unknown"
      for match in matches:
          if match.group(1):
            pop_name = match.group(1).strip()
          if match.group(2):  
            pop_code = match.group(2).upper()
          if match.group(3):  
            specific_loc_text = match.group(3).strip()
          if match.group(4):  
            country_text = match.group(4).strip()
          # linguistic_family = match.group(5).strip() if match.group(5) else 'unknown' # Already captured by pop_info_pattern

          final_country = get_country_from_text(country_text)
          if final_country == 'unknown':
              final_country = get_country_from_text(specific_loc_text)

          if pop_code.lower() not in non_meaningful_pop_names:
            if final_country.lower() not in non_meaningful_pop_names:
              pop_code_country_map[pop_code] = final_country
            if pop_name.lower() not in non_meaningful_pop_names:  
              pop_code_ethnicity_map[pop_code] = pop_name # Default ethnicity from Pop Name
            if specific_loc_text.lower() not in non_meaningful_pop_names:  
              pop_code_specific_loc_map[pop_code] = specific_loc_text

              # Specific rules for ethnicity/location in plain text:
              if pop_name.lower() == "khon mueang":
                  pop_code_ethnicity_map[pop_code] = "Khon Mueang"
              elif pop_name.lower() == "lawa":
                  pop_code_ethnicity_map[pop_code] = "Lawa"
              elif pop_name.lower() == "mon":
                  pop_code_ethnicity_map[pop_code] = "Mon"
              elif pop_name.lower() == "seak": # Added specific rule for Seak
                  pop_code_ethnicity_map[pop_code] = "Seak"
              elif pop_name.lower() == "nyaw": # Added specific rule for Nyaw
                  pop_code_ethnicity_map[pop_code] = "Nyaw"
              elif pop_name.lower() == "nyahkur": # Added specific rule for Nyahkur
                  pop_code_ethnicity_map[pop_code] = "Nyahkur"
              elif pop_name.lower() == "suay": # Added specific rule for Suay
                  pop_code_ethnicity_map[pop_code] = "Suay"
              elif pop_name.lower() == "soa": # Added specific rule for Soa
                  pop_code_ethnicity_map[pop_code] = "Soa"
              elif pop_name.lower() == "bru": # Added specific rule for Bru
                  pop_code_ethnicity_map[pop_code] = "Bru"
              elif pop_name.lower() == "khamu": # Added specific rule for Khamu
                  pop_code_ethnicity_map[pop_code] = "Khamu"

    return pop_code_country_map, pop_code_ethnicity_map, pop_code_specific_loc_map

def general_parse_population_code_to_country(plain_text_content, table_strings):
    pop_code_country_map = {}
    pop_code_ethnicity_map = {}
    pop_code_specific_loc_map = {}
    sample_id_to_pop_code = {}

    for table_str in table_strings:
        table_data = parse_literal_python_list(table_str)
        if not table_data or not isinstance(table_data[0], list):
            continue

        header_row = [col.lower() for col in table_data[0]]
        header_map = {col: idx for idx, col in enumerate(header_row)}

        # MJ17: Direct PopCode → Country
        if 'id' in header_map and 'country' in header_map:
            for row in table_strings[1:]:
                row = parse_literal_python_list(row)[0]
                if len(row) < len(header_row):
                    continue
                pop_code = str(row[header_map['id']]).strip()
                country = str(row[header_map['country']]).strip()
                province = row[header_map['province']].strip() if 'province' in header_map else 'unknown'
                pop_group = row[header_map['population group / region']].strip() if 'population group / region' in header_map else 'unknown'
                pop_code_country_map[pop_code] = country
                pop_code_specific_loc_map[pop_code] = province
                pop_code_ethnicity_map[pop_code] = pop_group

        # A1YU101 or EBK/KSK: SampleID → PopCode
        elif 'sample id' in header_map and 'population code' in header_map:
            for row in table_strings[1:]:
                row = parse_literal_python_list(row)[0]
                if len(row) < 2:
                    continue
                sample_id = row[header_map['sample id']].strip().upper()
                pop_code = row[header_map['population code']].strip().upper()
                sample_id_to_pop_code[sample_id] = pop_code

        # PopCode → Country (A1YU101/EBK mapping)
        elif 'population code' in header_map and 'country' in header_map:
            for row in table_strings[1:]:
                row = parse_literal_python_list(row)[0]
                if len(row) < 2:
                    continue
                pop_code = row[header_map['population code']].strip().upper()
                country = row[header_map['country']].strip()
                pop_code_country_map[pop_code] = country

    return pop_code_country_map, pop_code_ethnicity_map, pop_code_specific_loc_map, sample_id_to_pop_code

def chunk_text(text, chunk_size=500, overlap=50):
    """Splits text into chunks (by words) with overlap."""
    chunks = []
    words = text.split()
    num_words = len(words)

    start = 0
    while start < num_words:
        end = min(start + chunk_size, num_words)
        chunk = " ".join(words[start:end])
        chunks.append(chunk)

        if end == num_words:
            break
        start += chunk_size - overlap # Move start by (chunk_size - overlap)
    return chunks

def build_vector_index_and_data(doc_path, index_path="faiss_index.bin", chunks_path="document_chunks.json", structured_path="structured_lookup.json"):
    """
    Reads document, builds structured lookup, chunks remaining text, embeds chunks,
    and builds/saves a FAISS index.
    """
    print("Step 1: Reading document and extracting structured data...")
    # plain_text_content, table_strings, document_title = read_docx_text(doc_path) # Get document_title here

    # sample_id_map, contiguous_ranges_data = parse_sample_id_to_population_code(plain_text_content)
    # pop_code_to_country, pop_code_to_ethnicity, pop_code_to_specific_loc = parse_population_code_to_country(plain_text_content, table_strings)

    # master_structured_lookup = {}
    # master_structured_lookup['document_title'] = document_title # Store document title
    # master_structured_lookup['sample_id_map'] = sample_id_map
    # master_structured_lookup['contiguous_ranges'] = dict(contiguous_ranges_data)
    # master_structured_lookup['pop_code_to_country'] = pop_code_to_country
    # master_structured_lookup['pop_code_to_ethnicity'] = pop_code_to_ethnicity # NEW: Store pop_code to ethnicity map
    # master_structured_lookup['pop_code_to_specific_loc'] = pop_code_to_specific_loc # NEW: Store pop_code to specific_loc map


    # # Final consolidation: Use sample_id_map to derive full info for queries
    # final_structured_entries = {}
    # for sample_id, pop_code in master_structured_lookup['sample_id_map'].items():
    #     country = master_structured_lookup['pop_code_to_country'].get(pop_code, 'unknown')
    #     ethnicity = master_structured_lookup['pop_code_to_ethnicity'].get(pop_code, 'unknown') # Retrieve ethnicity
    #     specific_location = master_structured_lookup['pop_code_to_specific_loc'].get(pop_code, 'unknown') # Retrieve specific location

    #     final_structured_entries[sample_id] = {
    #         'population_code': pop_code,
    #         'country': country,
    #         'type': 'modern',
    #         'ethnicity': ethnicity, # Store ethnicity
    #         'specific_location': specific_location # Store specific location
    #     }
    # master_structured_lookup['final_structured_entries'] = final_structured_entries
    plain_text_content, table_strings, document_title = read_docx_text(doc_path)
    pop_code_to_country, pop_code_to_ethnicity, pop_code_to_specific_loc, sample_id_map = general_parse_population_code_to_country(plain_text_content, table_strings)

    final_structured_entries = {}
    if sample_id_map:
        for sample_id, pop_code in sample_id_map.items():
            country = pop_code_to_country.get(pop_code, 'unknown')
            ethnicity = pop_code_to_ethnicity.get(pop_code, 'unknown')
            specific_loc = pop_code_to_specific_loc.get(pop_code, 'unknown')
            final_structured_entries[sample_id] = {
                'population_code': pop_code,
                'country': country,
                'type': 'modern',
                'ethnicity': ethnicity,
                'specific_location': specific_loc
            }
    else:
        for pop_code in pop_code_to_country.keys():
            country = pop_code_to_country.get(pop_code, 'unknown')
            ethnicity = pop_code_to_ethnicity.get(pop_code, 'unknown')
            specific_loc = pop_code_to_specific_loc.get(pop_code, 'unknown')
            final_structured_entries[pop_code] = {
                'population_code': pop_code,
                'country': country,
                'type': 'modern',
                'ethnicity': ethnicity,
                'specific_location': specific_loc
            }
    if not final_structured_entries:
      # traditional way of A1YU101
      sample_id_map, contiguous_ranges_data = parse_sample_id_to_population_code(plain_text_content)
      pop_code_to_country, pop_code_to_ethnicity, pop_code_to_specific_loc = parse_population_code_to_country(plain_text_content, table_strings)
      if sample_id_map:
        for sample_id, pop_code in sample_id_map.items():
            country = pop_code_to_country.get(pop_code, 'unknown')
            ethnicity = pop_code_to_ethnicity.get(pop_code, 'unknown')
            specific_loc = pop_code_to_specific_loc.get(pop_code, 'unknown')
            final_structured_entries[sample_id] = {
                'population_code': pop_code,
                'country': country,
                'type': 'modern',
                'ethnicity': ethnicity,
                'specific_location': specific_loc
            }
      else:
          for pop_code in pop_code_to_country.keys():
              country = pop_code_to_country.get(pop_code, 'unknown')
              ethnicity = pop_code_to_ethnicity.get(pop_code, 'unknown')
              specific_loc = pop_code_to_specific_loc.get(pop_code, 'unknown')
              final_structured_entries[pop_code] = {
                  'population_code': pop_code,
                  'country': country,
                  'type': 'modern',
                  'ethnicity': ethnicity,
                  'specific_location': specific_loc
              }
    
    master_lookup = {
        'document_title': document_title,
        'pop_code_to_country': pop_code_to_country,
        'pop_code_to_ethnicity': pop_code_to_ethnicity,
        'pop_code_to_specific_loc': pop_code_to_specific_loc,
        'sample_id_map': sample_id_map,
        'final_structured_entries': final_structured_entries
    }
    print(f"Structured lookup built with {len(final_structured_entries)} entries in 'final_structured_entries'.")

    with open(structured_path, 'w') as f:
        json.dump(master_lookup, f, indent=4)
    print(f"Structured lookup saved to {structured_path}.")

    print("Step 2: Chunking document for RAG vector index...")
    # replace the chunk here with the all_output from process_inputToken and fallback to this traditional chunk
    clean_text, clean_table = "", ""
    if plain_text_content:
      clean_text = data_preprocess.normalize_for_overlap(plain_text_content)
    if table_strings:
      clean_table = data_preprocess.normalize_for_overlap(". ".join(table_strings))
    all_clean_chunk = clean_text + clean_table
    document_chunks = chunk_text(all_clean_chunk)
    print(f"Document chunked into {len(document_chunks)} chunks.")
    
    print("Step 3: Generating embeddings for chunks (this might take time and cost API calls)...")

    embedding_model_for_chunks = genai.GenerativeModel('models/text-embedding-004')

    chunk_embeddings = []
    for i, chunk in enumerate(document_chunks):
        embedding = get_embedding(chunk, task_type="RETRIEVAL_DOCUMENT")
        if embedding is not None and embedding.shape[0] > 0:
            chunk_embeddings.append(embedding)
        else:
            print(f"Warning: Failed to get valid embedding for chunk {i}. Skipping.")
            chunk_embeddings.append(np.zeros(768, dtype='float32'))

    if not chunk_embeddings:
        raise ValueError("No valid embeddings generated. Check get_embedding function and API.")

    embedding_dimension = chunk_embeddings[0].shape[0]
    index = faiss.IndexFlatL2(embedding_dimension)
    index.add(np.array(chunk_embeddings))

    faiss.write_index(index, index_path)
    with open(chunks_path, "w") as f:
        json.dump(document_chunks, f)

    print(f"FAISS index built and saved to {index_path}.")
    print(f"Document chunks saved to {chunks_path}.")
    return master_lookup, index, document_chunks, all_clean_chunk


def load_rag_assets(index_path="faiss_index.bin", chunks_path="document_chunks.json", structured_path="structured_lookup.json"):
    """Loads pre-built RAG assets (FAISS index, chunks, structured lookup)."""
    print("Loading RAG assets...")
    master_structured_lookup = {}
    if os.path.exists(structured_path):
        with open(structured_path, 'r') as f:
            master_structured_lookup = json.load(f)
        print("Structured lookup loaded.")
    else:
        print("Structured lookup file not found. Rebuilding is likely needed.")

    index = None
    chunks = []
    if os.path.exists(index_path) and os.path.exists(chunks_path):
        try:
            index = faiss.read_index(index_path)
            with open(chunks_path, "r") as f:
                chunks = json.load(f)
            print("FAISS index and chunks loaded.")
        except Exception as e:
            print(f"Error loading FAISS index or chunks: {e}. Will rebuild.")
            index = None
            chunks = []
    else:
        print("FAISS index or chunks files not found.")

    return master_structured_lookup, index, chunks
# Helper function for query_document_info
def exactInContext(text, keyword):
# try keyword_prfix
  # code_pattern = re.compile(r'([A-Z0-9]+?)(\d+)$', re.IGNORECASE)
  # # Attempt to parse the keyword into its prefix and numerical part using re.search
  # keyword_match = code_pattern.search(keyword)
  # keyword_prefix = None
  # keyword_num = None
  # if keyword_match:
  #     keyword_prefix = keyword_match.group(1).lower()
  #     keyword_num = int(keyword_match.group(2))
  text = text.lower()
  idx = text.find(keyword.lower())
  if idx == -1:
    # if keyword_prefix:
    #   idx = text.find(keyword_prefix)
    # if idx == -1:
    #   return False
    return False
  return True
def chooseContextLLM(contexts, kw):
  # if kw in context
  for con in contexts:
    context = contexts[con]
    if context:
      if exactInContext(context, kw):
        return con, context    
  #if cannot find anything related to kw in context, return all output
  if contexts["all_output"]:
    return "all_output", contexts["all_output"]
  else:
    # if all_output not exist
    # look of chunk and still not exist return document chunk
    if contexts["chunk"]: return "chunk", contexts["chunk"]
    elif contexts["document_chunk"]:  return "document_chunk", contexts["document_chunk"]
    else: return None, None  
def clean_llm_output(llm_response_text, output_format_str):
    results = []
    lines = llm_response_text.strip().split('\n')
    output_country, output_type, output_ethnicity, output_specific_location = [],[],[],[]
    for line in lines:
        extracted_country, extracted_type, extracted_ethnicity, extracted_specific_location = "unknown", "unknown", "unknown", "unknown"
        line = line.strip()
        if output_format_str == "ethnicity, specific_location/unknown": # Targeted RAG output
            parsed_output = re.search(r'^\s*([^,]+?),\s*(.+?)\s*$', llm_response_text)
            if parsed_output:
                extracted_ethnicity = parsed_output.group(1).strip()
                extracted_specific_location = parsed_output.group(2).strip()
            else:
                print("  DEBUG: LLM did not follow expected 2-field format for targeted RAG. Defaulting to unknown for ethnicity/specific_location.")
                extracted_ethnicity = 'unknown'
                extracted_specific_location = 'unknown'
        elif output_format_str == "modern/ancient/unknown, ethnicity, specific_location/unknown":
          parsed_output = re.search(r'^\s*([^,]+?),\s*([^,]+?),\s*(.+?)\s*$', llm_response_text)
          if parsed_output:
              extracted_type = parsed_output.group(1).strip()
              extracted_ethnicity = parsed_output.group(2).strip()
              extracted_specific_location = parsed_output.group(3).strip()
          else:
              # Fallback: check if only 2 fields
              parsed_output_2_fields = re.search(r'^\s*([^,]+?),\s*([^,]+?)\s*$', llm_response_text)
              if parsed_output_2_fields:
                  extracted_type = parsed_output_2_fields.group(1).strip()
                  extracted_ethnicity = parsed_output_2_fields.group(2).strip()
                  extracted_specific_location = 'unknown'
              else:
                  # even simpler fallback: 1 field only
                  parsed_output_1_field = re.search(r'^\s*([^,]+?)\s*$', llm_response_text)
                  if parsed_output_1_field:
                      extracted_type = parsed_output_1_field.group(1).strip()
                      extracted_ethnicity = 'unknown'
                      extracted_specific_location = 'unknown'
                  else:
                      print("  DEBUG: LLM did not follow any expected simplified format. Attempting verbose parsing fallback.")
                      type_match_fallback = re.search(r'Type:\s*([A-Za-z\s-]+)', llm_response_text)
                      extracted_type = type_match_fallback.group(1).strip() if type_match_fallback else 'unknown'
                      extracted_ethnicity = 'unknown'
                      extracted_specific_location = 'unknown'
        else:
          parsed_output = re.search(r'^\s*([^,]+?),\s*([^,]+?),\s*([^,]+?),\s*(.+?)\s*$', line)
          if parsed_output:
              extracted_country = parsed_output.group(1).strip()
              extracted_type = parsed_output.group(2).strip()
              extracted_ethnicity = parsed_output.group(3).strip()
              extracted_specific_location = parsed_output.group(4).strip()
          else:
              print(f"  DEBUG: Line did not follow expected 4-field format: {line}")
              parsed_output_2_fields = re.search(r'^\s*([^,]+?),\s*([^,]+?)\s*$', line)
              if parsed_output_2_fields:
                  extracted_country = parsed_output_2_fields.group(1).strip()
                  extracted_type = parsed_output_2_fields.group(2).strip()
                  extracted_ethnicity = 'unknown'
                  extracted_specific_location = 'unknown'
              else:
                  print(f"  DEBUG: Fallback to verbose-style parsing: {line}")
                  country_match_fallback = re.search(r'Country:\s*([A-Za-z\s-]+)', line)
                  type_match_fallback = re.search(r'Type:\s*([A-Za-z\s-]+)', line)
                  extracted_country = country_match_fallback.group(1).strip() if country_match_fallback else 'unknown'
                  extracted_type = type_match_fallback.group(1).strip() if type_match_fallback else 'unknown'
                  extracted_ethnicity = 'unknown'
                  extracted_specific_location = 'unknown'

        results.append({
            "country": extracted_country,
            "type": extracted_type,
            "ethnicity": extracted_ethnicity,
            "specific_location": extracted_specific_location
            #"country_explain":extracted_country_explain,
            #"type_explain": extracted_type_explain
        })
    # if more than 2 results
    if output_format_str == "ethnicity, specific_location/unknown":
      for result in results:
        if result["ethnicity"] not in output_ethnicity:
          output_ethnicity.append(result["ethnicity"])
        if result["specific_location"] not in output_specific_location:  
          output_specific_location.append(result["specific_location"])
      return " or ".join(output_ethnicity), " or ".join(output_specific_location)     
    elif output_format_str == "modern/ancient/unknown, ethnicity, specific_location/unknown":
      for result in results:
        if result["type"] not in output_type:
          output_type.append(result["type"])
        if result["ethnicity"] not in output_ethnicity:
          output_ethnicity.append(result["ethnicity"])
        if result["specific_location"] not in output_specific_location:  
          output_specific_location.append(result["specific_location"])

      return " or ".join(output_type)," or ".join(output_ethnicity), " or ".join(output_specific_location)    
    else:
      for result in results:
        if result["country"] not in output_country:
          output_country.append(result["country"])
        if result["type"] not in output_type:
          output_type.append(result["type"])
        if result["ethnicity"] not in output_ethnicity:
          output_ethnicity.append(result["ethnicity"])
        if result["specific_location"] not in output_specific_location:  
          output_specific_location.append(result["specific_location"])
      return " or ".join(output_country)," or ".join(output_type)," or ".join(output_ethnicity), " or ".join(output_specific_location)           

# def parse_multi_sample_llm_output(raw_response: str, output_format_str):
#     """
#     Parse LLM output with possibly multiple metadata lines + shared explanations.
#     """
#     lines = [line.strip() for line in raw_response.strip().splitlines() if line.strip()]
#     metadata_list = []
#     explanation_lines = []
#     if output_format_str == "country_name, modern/ancient/unknown":
#         parts = [x.strip() for x in lines[0].split(",")]
#         if len(parts)==2:
#           metadata_list.append({
#               "country": parts[0],
#               "sample_type": parts[1]#,
#               #"ethnicity": parts[2],
#               #"location": parts[3]
#           })
#         if 1<len(lines):
#           line = lines[1]
#           if "\n" in line:  line = line.split("\n")
#           if ". " in line: line = line.split(". ")
#           if isinstance(line,str): line = [line]
#           explanation_lines += line
#     elif output_format_str == "modern/ancient/unknown":
#       metadata_list.append({
#           "country": "unknown",
#           "sample_type": lines[0]#,
#           #"ethnicity": parts[2],
#           #"location": parts[3]
#       })
#       explanation_lines.append(lines[1])

#     # Assign explanations (optional) to each sample — same explanation reused
#     for md in metadata_list:
#         md["country_explanation"] = None
#         md["sample_type_explanation"] = None

#         if md["country"].lower() != "unknown" and len(explanation_lines) >= 1:
#             md["country_explanation"] = explanation_lines[0]

#         if md["sample_type"].lower() != "unknown":
#             if len(explanation_lines) >= 2:
#                 md["sample_type_explanation"] = explanation_lines[1]
#             elif len(explanation_lines) == 1 and md["country"].lower() == "unknown":
#                 md["sample_type_explanation"] = explanation_lines[0]
#             elif len(explanation_lines) == 1:
#                 md["sample_type_explanation"] = explanation_lines[0]
#     return metadata_list

def parse_multi_sample_llm_output(raw_response: str, output_format_str):
    """
    Parse LLM output with possibly multiple metadata lines + per-field explanations.

    Supports two explanation layouts the LLM might produce:
      A. One explanation line per field in order (newline-separated)
      B. All explanations on one block with **field_name:** markers
    """
    metadata_list = {}
    raw_lines = raw_response.strip().split("\n")
    first_line = raw_lines[0].strip() if raw_lines else ""
    explanation_lines_raw = [x for x in raw_lines[1:] if x.strip()]

    output_answers = re.split(r",\s*", first_line)
    output_formats = output_format_str.split(", ") if output_format_str else []

    # ── Build per-field explanation map ───────────────────────────────────────
    # Strategy A: try **field_name:** markers anywhere in the explanation block
    full_expl_text = " ".join(explanation_lines_raw)
    field_expl_map: dict = {}
    for fmt in output_formats:
        escaped = re.escape(fmt)
        # Match **field:** ... up to next **field:** or end
        pattern = rf'\*{{1,2}}{escaped}\*{{0,2}}\s*[:\-]?\s*(.+?)(?=\*{{1,2}}[A-Za-z_/]+\*{{0,2}}\s*[:\-]|$)'
        m = re.search(pattern, full_expl_text, re.IGNORECASE | re.DOTALL)
        if m:
            sentence = m.group(1).strip().split("\n")[0]  # first sentence only
            field_expl_map[fmt] = sentence

    # Strategy B: ordered lines (one per field)
    ordered_lines = explanation_lines_raw
    if not field_expl_map and len(ordered_lines) == 1 and ". " in ordered_lines[0]:
        # Single paragraph — split into sentences
        ordered_lines = [s.strip() for s in ordered_lines[0].split(". ") if s.strip()]

    # ── Assign answers + per-field explanations ───────────────────────────────
    for o, output in enumerate(output_formats):
        metadata_list[output] = {"answer": "", output + "_explanation": ""}

        # Answer
        if o < len(output_answers):
            ans = output_answers[o].strip()
            try:
                if ": " in ans:
                    ans = ans.split(": ", 1)[1]
            except Exception:
                pass
            metadata_list[output]["answer"] = ans
            if "unknown" in metadata_list[output]["answer"].lower():
                metadata_list[output]["answer"] = "unknown"
        else:
            metadata_list[output]["answer"] = "unknown"

        # Explanation — one sentence, assigned to this field specifically
        if metadata_list[output]["answer"] != "unknown":
            if output in field_expl_map:
                explain = field_expl_map[output]
            elif o < len(ordered_lines):
                explain = ordered_lines[o]
            elif ordered_lines:
                explain = ordered_lines[-1]
            else:
                explain = ""
            # Strip leading **field:** prefix if present
            explain = re.sub(r'^\*{1,2}[A-Za-z_/\-]+\*{0,2}\s*[:\-]\s*', '', explain).strip()
            metadata_list[output][output + "_explanation"] = explain
        else:
            metadata_list[output][output + "_explanation"] = "unknown"

    print("parsed metadata_list keys:", list(metadata_list.keys()))
    return metadata_list

def merge_metadata_outputs(metadata_list):
    """
    Merge a list of metadata dicts into one, combining differing values with 'or'.
    Assumes all dicts have the same keys.
    """
    if not metadata_list:
        return {}

    merged = {}
    keys = metadata_list[0].keys()

    for key in keys:
        values = [md[key] for md in metadata_list if key in md]
        unique_values = list(dict.fromkeys(values))  # preserve order, remove dupes
        if "unknown" in unique_values:
          unique_values.pop(unique_values.index("unknown"))
        if len(unique_values) == 1:
            merged[key] = unique_values[0]
        else:
            merged[key] = " or ".join(unique_values)

    return merged

import time
import random

def safe_call_llm(prompt, model="gemini-2.5-flash-lite", max_retries=5):
    retry_delay = 20
    for attempt in range(max_retries):
        try:
            resp_text, resp_model = call_llm_api(prompt, model)
            return resp_text, resp_model
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower():
                print(f"\n⚠️ Rate limit hit (attempt {attempt+1}/{max_retries}).")

                retry_after = None
                for word in error_msg.split():
                    if "retry" in word.lower() and "s" in word:
                        try:
                            retry_after = float(word.replace("s","").replace(".",""))
                        except:
                            pass

                wait_time = retry_after if retry_after else retry_delay
                print(f"⏳ Waiting {wait_time:.1f} seconds before retrying...")
                time.sleep(wait_time)

                retry_delay *= 2
            else:
                raise e

    raise RuntimeError("❌ Failed after max retries because of repeated rate limits.")

def outputs_from_multiPrompts(raw_response: str, output_format_str, acc_prompts):
  # Split the text based on the pattern '**Prompt X:'
  raw_response = re.split(r'\*\*Prompt \d+:', text)

  # Remove any empty sections from the split list
  prompts = [prompt.strip() for prompt in raw_response if prompt.strip()]

  # Create a list of output strings
  outputs = {}
  accs = list(acc_prompts.keys())
  # Loop through the prompts and combine the header and body
  for i in range(0, len(prompts)):
      prompt_header = prompts[i].strip()  # This is the "USA, unknown, Venezuela" or similar part
      prompt_header = re.sub(r'^\*\*\n', '', prompt_header)  # Remove any leading '**\n'
      accession, output = accs[i], ""
      if i + 1 < len(prompts):  # Check if there is a next body text
          prompt_body = prompts[i + 1].strip()  # This is the body of the response
          # Remove any unwanted '**\n' before the prompt content
          output = f"{prompt_header}\n\n{prompt_body}"
      else:
          # If no body exists, add only the header (though this case shouldn't occur in this example)
          output = f"{prompt_header}\n\n"
      metadata_list = parse_multi_sample_llm_output(output, output_format_str)
      outputs[accession] = metadata_list    
  return outputs   

def multi_prompts(dictsAccs, output_format_str, niche_cases=None, prompt_template="default",
                  standardization_schema=None):
  """Build per-accession prompts.

  standardization_schema: dict {field_name: description} from a schema CSV
  (e.g. cMD data dictionary + codebook).  When provided:
    - Each requested field is annotated with its schema definition AND allowed
      values so the LLM constrains its output to the canonical vocabulary.
    - Output column names must exactly match the schema field names.
  """
  prompts = {}
  if niche_cases:
    fields_list = ", ".join(niche_cases)
    if standardization_schema:
      # Build rich, per-field instructions: definition + allowed values
      schema_lines = []
      for f in niche_cases:
        entry = standardization_schema.get(f, {})
        if isinstance(entry, dict):
          desc    = entry.get("description", "")
          allowed = entry.get("allowed_values", [])
        else:
          desc    = str(entry)
          allowed = []
        line = f"  - {f}"
        if desc:
          line += f": {desc}"
        if allowed:
          # Boolean fields need special handling so LLM doesn't confuse "FALSE" with "absent"
          bool_vals = {v.strip().lower() for v in allowed}
          if bool_vals <= {"true", "false", "0", "1", "yes", "no"}:
            line += (
              f" [BOOLEAN — output TRUE if sample IS a control/reference, "
              f"FALSE if sample is a case/disease/treatment group. "
              f"Allowed: {', '.join(str(v) for v in allowed[:10])}. "
              f"Do NOT output 'unknown' when you can determine whether it is a case or control from the text.]"
            )
          else:
            line += f" [allowed values: {', '.join(str(v) for v in allowed[:20])}]"
        schema_lines.append(line)

      schema_hint = (
        "STANDARDIZATION RULES — use these exact field definitions and "
        "allowed values from the user-provided schema:\n"
        + "\n".join(schema_lines)
        + "\n\nIMPORTANT: Use ONLY the allowed values listed above. "
        "Choose the closest match when exact wording differs. "
        "Write 'unknown' ONLY when the information is genuinely absent from ALL source texts.\n"
      )
    else:
      schema_hint = ""

    niche_prompt = (
      f"Extract the following metadata fields: {fields_list}.\n"
      f"{schema_hint}"
      f"For each field: infer the most specific value from the source text. "
      f"Write 'unknown' only when truly absent.\n"
    )
  else:
    niche_prompt = ""

  for acc_pos in range(len(list(dictsAccs.keys()))):
    acc = list(dictsAccs.keys())[acc_pos]
    acc_cleaned = acc.split('.')[0] if acc else acc
    accession_found_in_text = False
    context_for_llm = dictsAccs[acc]
    if prompt_template == "default":
      field_count = len(output_format_str.split(", "))
      prompt_for_llm = (
      f"Prompt {acc_pos+1}: "
      f"Given the following text snippets, analyze the biological sample with "
      f"accession number {acc_cleaned}.\n"
      f"Identify its primary associated geographic location (country preferred; "
      f"fall back to region/continent if no country mentioned; write 'unknown' "
      f"if no geographic clues are present).\n"
      f"Determine if the sample source is 'modern' (living individual) or "
      f"'ancient' (prehistoric/archaeological); assume 'modern' if not specified.\n"
      f"{niche_prompt}"
      f"\nOUTPUT FORMAT (follow exactly):\n"
      f"Line 1: exactly {field_count} comma-separated values for: {output_format_str}\n"
      f"Lines 2–{field_count+1}: one sentence per field in the SAME ORDER — "
      f"no field-name labels, no bullet points, just the sentence.\n"
      f"Example for 3 fields:\n"
      f"  Italy, modern, type 2 diabetes\n"
      f"  The BioSample record states geo_loc_name: Italy: Ferrara.\n"
      f"  Sample was collected from living subjects enrolled in 2018.\n"
      f"  Subject belongs to the T2D+P+ group per Table 3 of the linked paper.\n"
      f"\nText Snippets:\n{context_for_llm}")
      if acc_cleaned.lower() in context_for_llm.lower():
        accession_found_in_text = True
      prompts[acc] = [prompt_for_llm, accession_found_in_text]
  return prompts

def standardize_with_llm(extracted_values: dict, schema: dict, acc: str) -> dict:
    """
    Post-extraction LLM standardization.
    Maps extracted free-text values to canonical schema-defined values.
    Uses Anthropic Claude first (better instruction-following), Gemini as fallback.
    """
    if not schema or not extracted_values:
        return extracted_values

    # Only standardize fields that exist in the schema
    fields_to_std = {k: v for k, v in extracted_values.items()
                     if k in schema and v and v.lower() != "unknown"}
    if not fields_to_std:
        return extracted_values

    schema_lines = []
    for field, val in fields_to_std.items():
        entry = schema.get(field, {})
        if isinstance(entry, dict):
            desc = entry.get("description", "")
            allowed = entry.get("allowed_values", [])
        else:
            desc = str(entry)
            allowed = []
        line = f"  {field}: current='{val}'"
        if desc:
            line += f", definition='{desc}'"
        if allowed:
            bool_vals = {v.strip().lower() for v in allowed}
            if bool_vals <= {"true", "false", "0", "1", "yes", "no"}:
                line += (f", BOOLEAN — TRUE=is a control, FALSE=is a case/disease. "
                         f"Allowed: {', '.join(str(v) for v in allowed[:10])}")
            else:
                line += f", allowed=[{', '.join(str(v) for v in allowed[:15])}]"
        schema_lines.append(line)

    prompt = (
        f"You are a biomedical metadata standardizer for sample {acc}.\n\n"
        f"Map each extracted value to its canonical schema value:\n"
        + "\n".join(schema_lines) + "\n\n"
        "Rules:\n"
        "1. Use ONLY the allowed values listed; pick the closest match.\n"
        "2. For BOOLEAN fields: if the sample is clearly a case/disease/treatment, output FALSE for 'control'. "
        "If it is clearly in the control/reference group, output TRUE.\n"
        "3. If you cannot determine the correct standardized value, keep the original.\n"
        "4. Return ONLY a JSON object: {\"field\": \"standardized_value\"}.\n"
        "No markdown, no explanation.\n"
    )

    try:
        response_text, _ = call_llm_api(prompt)
        raw = response_text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        standardized = dict(extracted_values)
        for k, v in result.items():
            if k in extracted_values and v is not None:
                standardized[k] = str(v).strip()
        return standardized
    except Exception as e:
        print(f"[standardize_with_llm] WARNING: {e}")
        return extracted_values


async def getMoreInfoForAcc(iso=None, acc=None, saveLinkFolder=None, niche_cases=None, limit_context=250000):
  linksWithTexts, links, context_for_llm = {}, [], ""
  meta_expand = smart_fallback.fetch_ncbi(acc)
  raw_tem_links = smart_fallback.smart_google_search(acc, meta_expand)
  tem_links = pipeline.unique_preserve_order(raw_tem_links)
  print("this is tem links with acc: ", tem_links)
  # filter the quality link
  print("start the smart filter link")
  #success_process, output_process = run_with_timeout(smart_fallback.filter_links_by_metadata,args=(tem_links,saveLinkFolder),kwargs={"accession":acc},timeout=90)
  output_process = await smart_fallback.async_filter_links_by_metadata(
      tem_links, saveLinkFolder, accession=acc
  )
  print('inside getMoreInfoForAcc and here is outputProcess: ', output_process)
  if output_process:
    linksWithTexts.update(output_process)
    print("yeah we have linksWithTexts and len: ", len(linksWithTexts))
    print("yes succeed for smart filter link")
    links += list(linksWithTexts.keys())
    print("link keys: ", links)
  else: 
    print("not have output_process")
    links += tem_links      
  if links:
    # use build context for llm function to reduce token
    texts_reduce = []
    linksWithTexts_reduce = {}
    reduce_context_for_llm = ""
    print("links:", links)
    for link in links:
      print("link: ", link)
      new_all_output = await pipeline.process_link_allOutput(link, 
                iso, acc, saveLinkFolder, linksWithTexts_reduce, context_for_llm)
      print("done all output")
      context_for_llm += new_all_output
      texts_reduce.append(new_all_output)
      linksWithTexts_reduce[link] = {"all_output": new_all_output}
    # tasks = [
    #     pipeline.process_link_allOutput(link, iso, acc, saveLinkFolder, linksWithTexts, all_output)
    #     for link in links
    # ]
    # results = await asyncio.gather(*tasks)
    # print("this is result:", results)
    # # combine results
    # for new_all_output in results:
    #   context_for_llm += new_all_output
    print("len of context after merge all: ", len(context_for_llm))

  if len(context_for_llm) > 500000: 
    context_for_llm = data_preprocess.normalize_for_overlap(context_for_llm)
    if len(context_for_llm) > 500000:
      if links:
        input_prompt = ["country_name", "modern/ancient/unknown"] 
        if niche_cases: input_prompt += niche_cases 
        reduce_context_for_llm = data_preprocess.build_context_for_llm(texts_reduce, acc, input_prompt, limit_context)
      if reduce_context_for_llm:
        print("reduce context for llm")
        context_for_llm = reduce_context_for_llm
      else:
        print("no reduce context for llm despite>1M")
        context_for_llm = context_for_llm[:limit_context]
  return context_for_llm, linksWithTexts, links

def _extract_additional_fields(context_text: str, niche_cases: list) -> dict:
    """
    Pass 2 — Generalized metadata extraction.
    Prompts Gemini to pull ALL key-value metadata from the source text that
    is NOT already covered by niche_cases (predefined fields).

    Returns a dict of {field_name: value} — all strings, no None values.
    Safe: always returns a dict (empty on any failure).
    """
    if not context_text or not context_text.strip():
        return {}

    # Fields already handled by Pass 1 — exclude from Pass 2
    exclude_fields = ['country_name', 'modern/ancient/unknown'] + list(niche_cases or [])

    generalized_prompt = (
        "You are a scientific metadata extractor. Read the following text from a genomic database record.\n\n"
        "Extract ALL metadata fields that describe the biological sample or specimen.\n"
        "Include EVERY key-value pair you can find, even if not obviously biological.\n"
        f"Do NOT include fields already in this list: {', '.join(exclude_fields)}\n\n"
        "Return ONLY a JSON object where:\n"
        "- Each key is the field name (lowercase, underscores for spaces, e.g. 'collection_date')\n"
        "- Each value is the extracted value as a string\n"
        "- If a field has no clear value, skip it entirely (do not return null or empty string)\n\n"
        "Text to extract from:\n"
        "---\n"
        f"{context_text[:30000]}\n"
        "---\n\n"
        "Return only valid JSON. No explanation. No markdown. No code blocks.\n"
        'Example: {"host_age": "45", "sequencing_platform": "Illumina NextSeq 500", "tissue_type": "oral mucosa", "collection_date": "2018"}'
    )

    try:
        response_text, _ = call_llm_api(generalized_prompt)
        raw = response_text.strip()

        # Strip markdown fence if model wraps output in ```json ... ```
        if raw.startswith('```'):
            parts = raw.split('```')
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith('json'):
                raw = raw[4:]

        result = json.loads(raw.strip())
        # Ensure all values are non-empty strings; drop empties and None
        cleaned = {}
        for k, v in result.items():
            k_str = str(k).strip()
            v_str = str(v).strip() if v is not None else ''
            if k_str and v_str:
                cleaned[k_str] = v_str
        return cleaned

    except Exception as e:
        print(f'[_extract_additional_fields] WARNING: generalized extraction failed: {e}')
        return {}


async def query_document_info(niche_cases, saveLinkFolder, llm_api_function, prompts,
                              standardization_schema=None):
    """
    Queries the document using a hybrid approach:
    1. Local structured lookup (fast, cheap, accurate for known patterns).
    2. RAG with semantic search and LLM (general, flexible, cost-optimized).
    """
    print("inside the model.query_doc_info")
    outputs, links, accession_found_in_text = {}, [], False
    
    genai.configure(api_key=os.getenv("NEW_GOOGLE_API_KEY"))    
    # Gemini 2.5 Flash-Lite pricing per 1,000 tokens
    PRICE_PER_1K_INPUT_LLM = 0.00010      # $0.10 per 1M input tokens
    PRICE_PER_1K_OUTPUT_LLM = 0.00040     # $0.40 per 1M output tokens
    
    # Embedding-001 pricing per 1,000 input tokens
    PRICE_PER_1K_EMBEDDING_INPUT = 0.00015  # $0.15 per 1M input tokens
    global_llm_model_for_counting_tokens = genai.GenerativeModel("gemini-2.5-flash-lite")#('gemini-1.5-flash-latest')

    # Determine fields to ask LLM for and output format based on what's known/needed
    output_format_str = "country_name, modern/ancient/unknown"
    method_used = 'rag_llm' # Will be updated based on the method that yields a result
    if niche_cases:
      output_format_str += ", " + ", ".join(niche_cases)
    # Calculate embedding cost for the primary query word
    total_query_cost, current_embedding_cost = 0, 0
    created_prompts = multi_prompts(prompts, output_format_str, niche_cases=niche_cases,
                                    prompt_template="default",
                                    standardization_schema=standardization_schema)
    print("done create prompt and length: ", len(created_prompts))
    prompt_for_llm = []
    for acc in created_prompts:
      outputs[acc] = {"predicted_output":"",
                      "method_used": method_used,
                      "total_query_cost":None,
                      "links": [],
                      "accession_found_in_text":created_prompts[acc][1],
                      }
      prompt_for_llm.append(created_prompts[acc][0])  
    
    prompt_for_llm = "\n".join(prompt_for_llm) #there is only 1 prompt created #+ "\n" + "Give answer for each prompt"
    print("length of prompt: ", len(prompt_for_llm))
    print("use 2.5 flash gemini")
    llm_response_text, model_instance = call_llm_api(prompt_for_llm)
    print("\n--- DEBUG INFO FOR RAG ---")
    print("Retrieved Context Sent to LLM (first 500 chars):")
    print(prompt_for_llm[:500] + "..." if len(prompt_for_llm) > 500 else prompt_for_llm)
    print("\nRaw LLM Response:")
    print(llm_response_text)
    print("--- END DEBUG INFO ---")
        
    llm_cost = 0
    if model_instance:
        try:
            input_llm_tokens = global_llm_model_for_counting_tokens.count_tokens(prompt_for_llm).total_tokens
            output_llm_tokens = global_llm_model_for_counting_tokens.count_tokens(llm_response_text).total_tokens
            print(f"  DEBUG: LLM Input tokens: {input_llm_tokens}")
            print(f"  DEBUG: LLM Output tokens: {output_llm_tokens}")
            llm_cost = (input_llm_tokens / 1000) * PRICE_PER_1K_INPUT_LLM + \
                       (output_llm_tokens / 1000) * PRICE_PER_1K_OUTPUT_LLM
            print(f"  DEBUG: Estimated LLM cost: ${llm_cost:.6f}")
        except Exception as e:
            print(f"  DEBUG: Error counting LLM tokens: {e}")
            llm_cost = 0

    total_query_cost += current_embedding_cost + llm_cost
    print(f"  DEBUG: Total estimated cost for this RAG query: ${total_query_cost:.6f}")
    
    metadata_list = parse_multi_sample_llm_output(llm_response_text, output_format_str)
    multi_metadata_lists = [metadata_list]
    list_accs = list(prompts.keys())  
    if acc:
      acc_cleaned = acc.split(".")[0]
    else: acc_cleaned = acc
    for metadata_list_pos in range(len(multi_metadata_lists)):
      metadata_list = multi_metadata_lists[metadata_list_pos]
      print(metadata_list)
      acc = list_accs[metadata_list_pos]
      again_output_format, general_knowledge_prompt = "", ""
      output_acc = {}
      # if at least 1 answer is unknown, then do smart queries to get more sources besides doi
      unknown_count = sum(1 for v in metadata_list.values() if v.get("answer").lower() == "unknown")
      if unknown_count >= 1:
        print("at least 1 unknown outputs")
        context_for_llm, linksWithTexts, more_links = await getMoreInfoForAcc(iso=None, acc=acc, saveLinkFolder=saveLinkFolder, niche_cases=niche_cases, limit_context=250000)
        links += more_links
        if acc_cleaned.lower() in context_for_llm.lower():
          accession_found_in_text = True
          # update again accession found in text due to new context for llm
          outputs[acc]["accession_found_in_text"] = accession_found_in_text
        # update links for output of acc
        outputs[acc]["links"] = links  
      else:
        context_for_llm = prompts[acc]
      for key in metadata_list:
        answer = metadata_list[key]["answer"] 
        if answer.lower() in " ".join(["unknown", "unspecified","could not get response from llm api.", "undefined"]):
          print("have to do again")
          again_output_format = key
          print("output format:", again_output_format)
          general_knowledge_prompt = (
        f"Given the following text snippets, analyze the entity/concept of this accession number {acc_cleaned} "
        #f"or the mitochondrial DNA sample if these identifiers are not explicitly found. "
        f"Identify and extract {again_output_format}"
        f"If not explicitly stated, infer the most specific related or contextually relevant value. "
        f"If no information is found, write 'unknown'. "
        f"Provide only {again_output_format}. "
        f"For non-'unknown' field in {again_output_format}, write one sentence explaining how it was inferred from the text "
        f"Format your answer so that:\n"
        f"1. The **first line** contains only the {again_output_format} answer.\n"
        f"2. The **second line onward** contains the explanations based on the non-unknown {again_output_format} answer.\n"
        f"\nText Snippets:\n{context_for_llm}")
          print("len of general prompt:", len(general_knowledge_prompt))
          if general_knowledge_prompt:    
            print("use 2.5 flash gemini")
            llm_response_text, model_instance = call_llm_api(general_knowledge_prompt)
            print("\n--- DEBUG INFO FOR RAG ---")
            print("Retrieved Context Sent to LLM (first 500 chars):")
            print(context_for_llm[:500] + "..." if len(context_for_llm) > 500 else context_for_llm)
            print("\nRaw LLM Response:")
            print(llm_response_text)
            print("--- END DEBUG INFO ---")
            llm_cost = 0
            if model_instance:
                try:
                    input_llm_tokens = global_llm_model_for_counting_tokens.count_tokens(prompt_for_llm).total_tokens
                    output_llm_tokens = global_llm_model_for_counting_tokens.count_tokens(llm_response_text).total_tokens
                    print(f"  DEBUG: LLM Input tokens: {input_llm_tokens}")
                    print(f"  DEBUG: LLM Output tokens: {output_llm_tokens}")
                    llm_cost = (input_llm_tokens / 1000) * PRICE_PER_1K_INPUT_LLM + \
                                (output_llm_tokens / 1000) * PRICE_PER_1K_OUTPUT_LLM
                    print(f"  DEBUG: Estimated LLM cost: ${llm_cost:.6f}")
                except Exception as e:
                    print(f"  DEBUG: Error counting LLM tokens: {e}")
                    llm_cost = 0

            total_query_cost += current_embedding_cost + llm_cost
            print("total query cost in again: ", total_query_cost)
            metadata_list_niche = parse_multi_sample_llm_output(llm_response_text, again_output_format)
            print(f"metadata list output for {again_output_format}: {metadata_list}")
            for key_niche in metadata_list_niche:
              if key_niche not in outputs.keys():
                output_acc[key_niche] = metadata_list_niche[key_niche]
                        
        else:
            output_acc[key] = metadata_list[key]  
      # ── LLM-based standardization pass ───────────────────────────────────
      # Run after extraction; maps free-text values to canonical schema values.
      if standardization_schema and output_acc:
          try:
              extracted_flat = {
                  k: output_acc[k]["answer"]
                  for k in output_acc
                  if isinstance(output_acc[k], dict) and output_acc[k].get("answer", "").lower() not in ("", "unknown")
              }
              if extracted_flat:
                  standardized = standardize_with_llm(extracted_flat, standardization_schema, acc)
                  for field, std_val in standardized.items():
                      if field in output_acc and std_val:
                          output_acc[field]["answer"] = std_val
                  print(f"[Standardization] {acc}: {standardized}")
          except Exception as _std_err:
              print(f"[Standardization] WARNING: {_std_err}")

      outputs[acc]["predicted_output"] = output_acc
      outputs[acc]["total_query_cost"] = total_query_cost

      # ── PASS 2: generalized extraction of ALL additional metadata ─────────
      # Uses all source text to extract every metadata attribute mentioned.
      try:
          predefined_keys = set(['country_name', 'modern/ancient/unknown']
                                 + list(niche_cases or []))
          # Use the full prompt context (contains all source texts) for richer extraction
          pass2_context = context_for_llm if context_for_llm else ""
          all_additional = _extract_additional_fields(pass2_context, niche_cases or [])
          additional_only = {
              k: v for k, v in all_additional.items()
              if k not in predefined_keys
          }
          outputs[acc]['_additional_fields'] = additional_only
          print(f'[Pass 2] {acc}: {len(additional_only)} additional fields -> '
                f'{list(additional_only.keys())}')
      except Exception as _pass2_err:
          print(f'[Pass 2] WARNING: failed for {acc}: {_pass2_err}')
          outputs[acc]['_additional_fields'] = {}
      # ── END PASS 2 ────────────────────────────────────────────────────────

      print("total cost: ", total_query_cost)
      print(f"total output of {acc}: {outputs[acc]}")
    return outputs