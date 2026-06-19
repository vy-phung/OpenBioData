def _try_import(name):
    try:
        import importlib
        return importlib.import_module(name)
    except Exception:
        return None

NCBI              = _try_import("NCBI")
model             = _try_import("model")
pipeline          = _try_import("pipeline")
mtdna_classifier  = _try_import("mtdna_classifier")
smart_fallback    = _try_import("smart_fallback")
standardize_location = _try_import("standardize_location")
mtdna_backend     = _try_import("mtdna_backend")
data_preprocess   = _try_import("data_preprocess")

try:
    from NER.html import extractHTML
except Exception:
    extractHTML = None
import pandas as pd
from pathlib import Path
import subprocess
import os
import asyncio
import google.generativeai as genai
try:
    from google import genai as _genai_new  # noqa: F401
    from google.genai import types          # noqa: F401
except ImportError:
    pass  # new SDK optional — pipeline calls model.query_document_info instead
import re
import time
import multiprocessing
import gspread
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.oauth2.service_account import Credentials
from oauth2client.service_account import ServiceAccountCredentials
import io
import json
import copy
import requests as _requests
import csv as _csv
import field_aliases


def _github_blob_to_raw(url: str) -> str:
    """Convert a GitHub blob URL to its raw content URL."""
    if "github.com" in url and "/blob/" in url:
        url = url.replace("github.com", "raw.githubusercontent.com")
        url = url.replace("/blob/", "/")
    return url


_ONTOLOGY_URL_MARKERS = [
    'geneontology.org', 'ebi.ac.uk/ols', 'ontobee.org',
    'bioportal.bioontology.org', 'obofoundry.org', 'ebi.ac.uk/ontologies',
    'purl.obolibrary.org', 'purl.bioontology.org',
]

# Fields that are computed/derived stats — skip from auto-niche_cases
_COMPUTED_FIELD_PREFIXES = ('n_', 'pct_', 'num_')
_COMPUTED_FIELD_KEYWORDS = ('reads', 'species', 'genus', 'megan', 'mapped')


def _is_ontology_url(url: str) -> bool:
    return any(m in url for m in _ONTOLOGY_URL_MARKERS)


def fetch_standardization_schema(urls) -> dict:
    """
    Fetch one or more CSV standardization files and return a rich schema dict:
      { field_name: { "description": str, "allowed_values": list } }

    Also detects ontology documentation URLs (geneontology.org, OBO, etc.) and
    marks them so the pipeline can use an ontology-annotation pass instead of
    CSV-based allowed-value mapping.  Ontology URLs are stored under the special
    key '__ontology_urls__' and '__ontology_mode__' = True.

    Handles two CSV file types automatically:
      1. Data dictionary CSV: columns include field name + description/definition
      2. Codebook CSV: columns include field name + allowed value + value label
         (adds allowed_values list to existing schema entries)

    Returns {} on any failure.
    """
    if not urls:
        return {}
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.split(",") if u.strip()]

    schema: dict = {}
    ontology_urls_found: list = []

    # Separate ontology doc URLs from CSV URLs
    csv_urls = []
    for url in urls:
        if _is_ontology_url(url):
            ontology_urls_found.append(url)
            print(f"[standardization] Detected ontology URL (will use LLM annotation): {url}")
        else:
            csv_urls.append(url)

    urls = csv_urls  # only process CSVs below

    for url in urls:
        raw_url = _github_blob_to_raw(url.strip())
        try:
            resp = _requests.get(raw_url, timeout=15)
            resp.raise_for_status()

            # Detect HTML responses (e.g. protocols.io, web pages) — not parseable as CSV.
            # These URLs are still passed as other_links so the LLM can read them as context.
            content_type = resp.headers.get('content-type', '').lower()
            first_bytes  = resp.text.lstrip()[:100].lower()
            is_html = ('text/html' in content_type
                       or first_bytes.startswith('<!doctype')
                       or first_bytes.startswith('<html'))
            if is_html:
                print(f"[standardization] '{raw_url}' returned an HTML page (not a CSV). "
                      f"Checking for known protocol schemas; also passing as LLM context.")
                schema.setdefault('__web_context_urls__', []).append(url)

                # ── Known protocol → hard-coded OHE field set ─────────────────
                # protocols.io pages are JS-rendered so the raw HTML has no
                # readable content. Detect known protocol URLs by pattern and
                # inject the fields they define directly.
                _OHE_FIELDS = {
                    # NCBI One Health Enteric (OHE) mandatory/conditional fields
                    # Source: GenFS Metadata Cleanup Challenge protocol (protocols.io)
                    "source_type":          {"description": "Type of sample source (e.g. food, human, animal, environment)", "allowed_values": ["food", "human", "animal", "environment", "water", "veterinary", "other"]},
                    "collected_by":         {"description": "Name of the organization or person who collected the sample", "allowed_values": []},
                    "sequenced_by":         {"description": "Name of the organization or person who performed sequencing", "allowed_values": []},
                    "project_name":         {"description": "Name of the project or surveillance program (e.g. GenomeTrakr)", "allowed_values": []},
                    "purpose_of_sampling":  {"description": "Why the sample was collected (e.g. surveillance, outbreak investigation, research)", "allowed_values": ["surveillance", "outbreak investigation", "research", "regulatory compliance", "other"]},
                    "isolation_source":     {"description": "The most specific material from which the sample was isolated — always use the exact product/substrate name (e.g. 'Frozen Yellowfin Tuna', 'Enoki mushrooms', 'blood', 'environmental swab'). Do NOT generalize to broad categories like 'food product'.", "allowed_values": []},
                    "host":                 {"description": "Host organism ONLY for human or animal isolates (e.g. 'Homo sapiens', 'Sus scrofa'). Output 'unknown' for food/environmental samples — do NOT use the food item as the host.", "allowed_values": ["Homo sapiens", "unknown"]},
                    "food_origin":          {"description": "Country or region of origin for the food product (food isolates only)", "allowed_values": []},
                }
                _is_ohe_protocol = (
                    "protocols.io" in raw_url
                    and any(kw in raw_url.lower() for kw in ("genfs", "genomeTrakr", "one-health", "ohe", "metadata-cleanup", "metadata_cleanup"))
                ) or (
                    "protocols.io" in raw_url  # any protocols.io page gets OHE fields as a reasonable default
                )
                if _is_ohe_protocol:
                    _added = 0
                    for _f, _fmeta in _OHE_FIELDS.items():
                        if _f not in schema:
                            schema[_f] = _fmeta
                            _added += 1
                    if _added:
                        print(f"[standardization] Injected {_added} OHE field(s) from known protocol: {list(_OHE_FIELDS)[:10]}")
                else:
                    # Generic HTML: try regex on raw text (works if page is SSR)
                    _STOP_WORDS = {
                        'the', 'and', 'for', 'with', 'not', 'that', 'this', 'are', 'from',
                        'your', 'will', 'each', 'all', 'any', 'one', 'use', 'per', 'lab',
                        'can', 'has', 'its', 'may', 'add', 'new', 'tab', 'row', 'must',
                        'file', 'only', 'also', 'more', 'then', 'both', 'text', 'date',
                        'last', 'see', 'our', 'step',
                    }
                    _html_fields = re.findall(
                        r'(?:^|[\n\r])\s*[-•*]\s+([a-z][a-z0-9_]{2,})\s*(?:\(.*?\))?\s*(?:$|[\n\r])',
                        resp.text,
                    )
                    _added = 0
                    for _f in _html_fields:
                        if _f in _STOP_WORDS or _f in schema:
                            continue
                        schema[_f] = {"description": f"Field from {url}", "allowed_values": []}
                        _added += 1
                    if _added:
                        print(f"[standardization] Extracted {_added} field(s) from HTML page text")
                continue

            lines   = resp.text.splitlines()
            reader  = _csv.DictReader(lines)
            headers = reader.fieldnames or []
            if not headers:
                continue

            h_lower = [h.lower() for h in headers]

            # Detect field-name column
            name_col = next(
                (h for h in headers if any(k in h.lower() for k in ("name", "field", "variable", "column"))),
                headers[0]
            )
            # Detect description column
            desc_col = next(
                (h for h in headers if any(k in h.lower() for k in ("description", "definition", "label", "detail"))),
                headers[1] if len(headers) > 1 else None
            )
            # Detect allowed-value column (codebook pattern)
            val_col = next(
                (h for h in headers if any(k in h.lower() for k in ("value", "allowed", "code", "category", "option"))),
                None
            )

            is_codebook = val_col is not None and any(
                k in h.lower() for h in headers for k in ("value", "allowed", "code")
            )

            rows_read = 0
            for row in reader:
                field = (row.get(name_col) or "").strip()
                if not field:
                    continue
                desc  = (row.get(desc_col) or "").strip() if desc_col else ""
                val   = (row.get(val_col)  or "").strip() if val_col  else ""

                if field not in schema:
                    schema[field] = {"description": desc, "allowed_values": []}
                elif desc and not schema[field]["description"]:
                    schema[field]["description"] = desc

                if is_codebook and val and val not in schema[field]["allowed_values"]:
                    schema[field]["allowed_values"].append(val)
                rows_read += 1

            # Store raw schema text so standardization prompts can reference it directly.
            # Capped at 8000 chars to stay within prompt budgets.
            _raw_snippet = resp.text[:8000]
            existing = schema.get('__schema_text__', '')
            schema['__schema_text__'] = (
                (existing + f"\n\n--- Schema from {url} ---\n{_raw_snippet}").strip()
                if existing else f"--- Schema from {url} ---\n{_raw_snippet}"
            )
            print(f"[standardization] Loaded {rows_read} rows / {len(schema)} fields from {raw_url}"
                  f"{' (codebook)' if is_codebook else ' (dictionary)'}")
        except Exception as e:
            print(f"[standardization] Could not fetch {raw_url}: {e}")

    # Mark ontology mode so the pipeline can run ontology annotation
    if ontology_urls_found:
        schema['__ontology_mode__'] = True
        schema['__ontology_urls__'] = ontology_urls_found

    return schema

"""accessions = { acc: {"bioproject":"",
                        "biosample": "",
                        "accession": "",},
                  acc1: {"bioproject":"",
                        "biosample": "",
                        "accession": "",},
}"""

# Main execution
async def pipeline_with_gemini(accessions, bioproject_id=None, ncbi_urls=None, other_links=None, niche_cases=None, save_df=None, standardization_urls=None, user_context_text=None, user_url_sources=None, progress_cb=None, cancel_event=None, user_file_label=None, per_accession_context=None):
  # output: country, sample_type, ethnic, location, money_cost, time_cost, explain
  # there can be one accession number in the accessions
  # # Prices are per 1,000 tokens
  # Gemini 2.5 Flash-Lite pricing per 1,000 tokens
  PRICE_PER_1K_INPUT_LLM = 0.00010      # $0.10 per 1M input tokens
  PRICE_PER_1K_OUTPUT_LLM = 0.00040     # $0.40 per 1M output tokens

  # Embedding-001 pricing per 1,000 input tokens
  PRICE_PER_1K_EMBEDDING_INPUT = 0.00015  # $0.15 per 1M input tokens
  if not accessions:
    print("no input")
    return None
  else:
    from Bio import Entrez
    Entrez.email = "vyphung1901@gmail.com"
    # Configure Gemini if key is available (used as fallback in model.call_llm_api)
    _gemini_key = os.getenv("NEW_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("NEW_GEMINI_API")
    if _gemini_key:
        genai.configure(api_key=_gemini_key)

    # Fetch standardization schema from provided CSV URLs.
    # standardization_urls are schema-definition files — they must NOT be added to the
    # metadata-extraction context (links/source_texts), only used for standardization.
    # other_links may contain non-schema context URLs; they stay separate.
    _all_std_urls = list(standardization_urls or [])
    if other_links:
        for _lnk in other_links:
            if _lnk and _lnk not in _all_std_urls:
                _all_std_urls.append(_lnk)
    # Keep schema URLs in a frozen set so we can exclude them from source context later
    _schema_url_set: set = set(_all_std_urls)
    standardization_schema = fetch_standardization_schema(_all_std_urls) if _all_std_urls else {}
    # Extract and remove raw schema text from schema dict (used only in standardization prompt)
    _global_schema_text: str = standardization_schema.pop('__schema_text__', '') or ''
    _is_ontology_mode = bool(standardization_schema.get('__ontology_mode__'))

    # Auto-populate niche_cases from schema fields when user didn't specify any
    if standardization_schema and not niche_cases and not _is_ontology_mode:
        _schema_fields = [
            k for k in standardization_schema
            if not k.startswith('__')
            and not any(k.startswith(p) for p in _COMPUTED_FIELD_PREFIXES)
            and not any(kw in k for kw in _COMPUTED_FIELD_KEYWORDS)
        ]
        if _schema_fields:
            # Sort: put well-known bio-metadata fields first
            _priority = {
                'disease', 'control', 'body_site', 'organism', 'host', 'country',
                'age', 'gender', 'sex', 'tissue', 'ancestry', 'smoking_status',
                'sequencing_type', 'sequencing_platform', 'collection_date',
                'collection_location', 'specific_location', 'geographic_location',
                'latitude_longitude', 'sequencing_layout', 'dna_extraction_kit',
                'library_prep_kit', 'disease_t2d', 'disease_periodontitis',
                'study_name', 'PMID', 'hypoglycemic_medication', 'metabolic_control',
            }
            prioritized = [f for f in _schema_fields if f in _priority]
            others = [f for f in _schema_fields if f not in _priority]
            niche_cases = (prioritized + others)[:30]
            print(f"[auto-niche] Using {len(niche_cases)} schema fields as niche_cases: {niche_cases}")

    async def _progress(msg: str):
        if progress_cb:
            try:
                await progress_cb(msg)
            except Exception:
                pass

    # Notify api.py of the resolved niche_cases BEFORE first sample so
    # partial-result rows are built with the correct field list.
    if niche_cases:
        await _progress({"__auto_niche_cases__": niche_cases})

    acc_prompts = {}
    bioproject_info = {}
    accs_output = {}
    _total_accs = len(accessions)
    print("accessions: ", accessions)
    for _acc_idx, acc in enumerate(accessions):
      if cancel_event is not None and cancel_event.is_set():
          await _progress("⏹ Cancelled — stopping pipeline.")
          break
      # BioProject/GEO-series expansion leaves entries as lazy placeholders
      # (just the sample id + parent project) instead of fully resolving
      # every sample up front. Resolve this one sample's full record now —
      # right before it's processed — so the cancellation check above can
      # take effect between samples instead of only before/after the whole
      # project's resolution finishes.
      if accessions[acc].get("_lazy_kind"):
          from ncbi_resolver import resolve_lazy_entry
          await _progress(f"[{_acc_idx + 1}/{_total_accs}] Resolving {acc}…")
          accessions[acc] = await asyncio.to_thread(resolve_lazy_entry, acc, accessions[acc])
      print("start gemini: ", acc)
      await _progress(f"[{_acc_idx + 1}/{_total_accs}] Fetching NCBI data for {acc}…")
      start = time.time()
      total_query_cost = 0
      jsonSM, links, article_text, pubmeds, all_output, doi = {},[], "", [], "", ""
      _bioproject_extra_links: list = []  # non-DOI links added programmatically (umbrella, external resources)
      acc_score = {"query_cost":total_query_cost,
                   "time_cost":None,
                   "source":links,
                   "source_texts": {},
                   "file_all_output":"",
                   "signals":{ # default values
                              "in_NCBI": False,
                              "has_pubmed": False,
                              "accession_found_in_text": False,
                              "predicted_output": None,
                               "consistent_outputs":None,
                              "num_publications": 0,
                              "missing_key_fields": False,},
                              #"known_failure_pattern": False,},
                  }

      """Input `signals` dict is expected to contain:
        has_geo_loc_name: bool
        has_pubmed: bool
        accession_found_in_text: bool  # accession present in extracted external text
        predicted_country: str | None  # final model label / country prediction
        genbank_country: str | None    # from NCBI / GenBank metadata
        num_publications: int
        missing_key_fields: bool
        known_failure_pattern: bool"""
      if niche_cases:
        for niche in niche_cases:
          print("add niche: ", niche)
          acc_score[niche] = {}

      # Detect non-NCBI samples — skip NCBI fetch entirely for these
      _source_db  = accessions[acc].get("_source_database", "")
      _is_project = accessions[acc].get("_is_project", False)
      _is_non_ncbi = bool(_source_db) and not any(
          accessions[acc].get(k) for k in ("bioproject", "biosample", "accession", "experiment")
      )

      if _is_non_ncbi:
        await _progress(f"[{_acc_idx + 1}/{_total_accs}] Non-NCBI sample ({_source_db}) — skipping NCBI fetch for {acc}…")
      else:
        await _progress(f"[{_acc_idx + 1}/{_total_accs}] Fetching NCBI data for {acc}…")

      # ── Step 1: Fetch data from the sample's primary database ────────────────
      # Each sub-step is wrapped so one failure does not block the others.
      ncbi_texts, ncbi_text_links = {}, {}
      if not _is_non_ncbi and NCBI is not None:
        try:
          ncbi_texts = NCBI.extract_NCBI_directly(acc)
        except Exception as _e:
          print(f"[DB fetch] direct NCBI fetch for {acc} failed: {_e}")

      if not _is_non_ncbi and accessions[acc].get("bioproject"):
        bioproject_id = accessions[acc]["bioproject"]
        print("get bioproject from acc input: ", bioproject_id)

      if not _is_non_ncbi and NCBI is not None:
        for ncbi_source in accessions[acc]:
          try:
            if ncbi_source == "bioproject" and accessions[acc].get("bioproject"):
              if not bioproject_info:
                print("get bioproject info")
                bioproject_info = NCBI.extract_NCBI_directly(bioproject_id)
              else:
                if bioproject_id not in bioproject_info:
                  bioproject_info = NCBI.extract_NCBI_directly(bioproject_id)
              if bioproject_id in (bioproject_info or {}):
                acc_score["source_texts"]["NCBI_bioproject"] = {bioproject_id: bioproject_info[bioproject_id]}
                _bp_data_now = bioproject_info[bioproject_id]
                if not pubmeds:
                  pubmeds = list(_bp_data_now.get("pubmed", []) or [])
                # ── Fetch umbrella/parent BioProject (e.g. GenomeTrakr umbrella) ──
                for _umbrella_acc in (_bp_data_now.get("umbrella_projects") or []):
                  try:
                    _umbrella_info = NCBI.extract_NCBI_directly(_umbrella_acc)
                    if _umbrella_info:
                      acc_score["source_texts"][f"NCBI_umbrella_{_umbrella_acc}"] = _umbrella_info
                      print(f"[umbrella] Fetched umbrella project {_umbrella_acc}")
                  except Exception as _ue:
                    print(f"[umbrella] fetch failed for {_umbrella_acc}: {_ue}")
                # ── Queue external/related-resource URLs for link fetching ──────
                for _ext_url in (_bp_data_now.get("external_links") or []):
                  if _ext_url and _ext_url not in links:
                    links.append(_ext_url)
                    _bioproject_extra_links.append(_ext_url)
                    print(f"[external_link] Queued related resource: {_ext_url}")
            elif ncbi_source == "biosample" and accessions[acc].get("biosample"):
              biosample_id = accessions[acc]["biosample"]
              ncbi_texts = NCBI.extract_NCBI_directly(biosample_id)
              acc_score["source_texts"]["NCBI_biosample"] = ncbi_texts
            elif ncbi_source == "accession" and accessions[acc].get("accession"):
              accession_id = accessions[acc]["accession"]
              ncbi_texts = NCBI.extract_NCBI_directly(accession_id)
              acc_score["source_texts"]["NCBI_accession"] = ncbi_texts
              if not pubmeds:
                _acc_data = (ncbi_texts or {}).get(accession_id, {})
                if isinstance(_acc_data, dict):
                  pubmed = _acc_data.get("pubmed_id", "")
                  if pubmed:
                    pubmeds.append(pubmed)
                  doi = _acc_data.get("doi", "") or doi
            elif ncbi_source == "experiment" and accessions[acc].get("experiment"):
              experiment_id = accessions[acc]["experiment"]
              ncbi_texts = NCBI.extract_NCBI_directly(experiment_id)
              acc_score["source_texts"]["NCBI_experiment"] = ncbi_texts
          except Exception as _e:
            print(f"[DB fetch] {ncbi_source} fetch failed for {acc}: {_e}")

      # ── Step 1b: ENA-specific fetch for PRJEB/SAMEA samples ─────────────────
      # Fetch study-level and biosample-level metadata directly from EBI APIs.
      # This runs independently so database unavailability does not stop the pipeline.
      _biosample_id = accessions[acc].get("biosample", "")
      _bp_id = accessions[acc].get("bioproject", "") if not _is_non_ncbi else ""
      if not _is_non_ncbi and NCBI is not None:
        if _biosample_id.upper().startswith("SAMEA") or _biosample_id.upper().startswith("SAME"):
          try:
            _ena_bs_text = NCBI.fetch_ena_biosample_text(_biosample_id)
            if _ena_bs_text:
              acc_score["source_texts"][f"ENA_biosample_{_biosample_id}"] = _ena_bs_text
              acc_score["signals"]["in_NCBI"] = True
              print(f"[ENA] Fetched biosample text for {_biosample_id} ({len(_ena_bs_text)} chars)")
          except Exception as _e:
            print(f"[ENA] biosample fetch failed for {_biosample_id}: {_e}")

        if _bp_id and _bp_id.upper().startswith("PRJEB"):
          try:
            _ena_study_text = NCBI.fetch_ena_study_text(_bp_id)
            if _ena_study_text:
              acc_score["source_texts"][f"ENA_study_{_bp_id}"] = _ena_study_text
              acc_score["signals"]["in_NCBI"] = True
              print(f"[ENA] Fetched study text for {_bp_id} ({len(_ena_study_text)} chars)")
          except Exception as _e:
            print(f"[ENA] study fetch failed for {_bp_id}: {_e}")

      if acc_score["source_texts"]:
        source_kws = list(acc_score["source_texts"].keys())
        for s in source_kws:
          if "NCBI" in s or "ENA" in s:
            acc_score["signals"]["in_NCBI"] = True
            break
      print("source text after ncbi: ", list(acc_score["source_texts"].keys()))
      # set up step: create the folder to save document
      # firstly get the doi url from pubmed id which is from bioproject
      if pubmeds:
        id_folder = "_".join(pubmeds)
        acc_score["signals"]["has_pubmed"] = True
        # Use pre-resolved pubmed_dois from bioproject data when available (avoids redundant API calls)
        _bp_data = (acc_score.get("source_texts", {})
                    .get("NCBI_bioproject", {})
                    .get(bioproject_id, {}))
        _bp_doi_map = {d["pmid"]: d["doi"] for d in _bp_data.get("pubmed_dois", [])}
        _seen_doi_urls = set(links)
        for pubID in pubmeds:
          pub_doi = _bp_doi_map.get(str(pubID)) or NCBI.get_doi_via_europepmc(str(pubID))
          if pub_doi:
            doi_url = 'https://doi.org/' + pub_doi
            if doi_url not in _seen_doi_urls:
              _seen_doi_urls.add(doi_url)
              links.append(doi_url)
            if not doi:
              doi = pub_doi
      else:
        id_folder = "DirectSubmission"

      import tempfile as _tempfile
      saveLinkFolder = _tempfile.mkdtemp()

      # # Define document names
      # safe_title = sanitize_filename(saveTitle, 50)
      # all_filename = f"{safe_title}_all_merged_document.docx"
      # print(all_filename)
      # # check if the file chunk or file all output exist or not and reuse the link
      # all_path = "/"+all_filename
      # # # if chunk and all output not exist yet
      # file_all_path = saveLinkFolder + all_path

      # # LLM model
      # # Preprocess the input token
      # if os.path.exists(file_all_path):
      #   print("File all output exists!")
      #   acc_score["file_all_output"] = str(file_all_path)
      #   if not all_output:
      #     text_all, table_all, document_title_all = model.read_docx_text(file_all_path)
      #     all_output = data_preprocess.normalize_for_overlap(text_all) + "\n" + data_preprocess.normalize_for_overlap(". ".join(table_all))
      # Add other_links that are NOT schema/standardization URLs to the fetch list.
      # Schema URLs are only for standardization — they must not pollute source context.
      if other_links:
          for _ol in other_links:
              if _ol and _ol not in _schema_url_set and _ol not in links:
                  links.append(_ol)
      # Save schema_text to the temp folder so it's available for reference
      if _global_schema_text and saveLinkFolder:
          try:
              _schema_file = os.path.join(saveLinkFolder, "schema_reference.txt")
              with open(_schema_file, "w", encoding="utf-8") as _sf:
                  _sf.write(_global_schema_text)
              print(f"[schema] Saved schema reference text to {_schema_file}")
          except Exception as _ste:
              print(f"[schema] Could not save schema text: {_ste}")
      all_links = copy.deepcopy(links)
      print("all_links: ", all_links)
      # Pre-register user-pasted "add link" sources (each keyed by its own
      # URL, not lumped into one blob) BEFORE any of steps 2/3/3b run -- so
      # if web search or supplementary discovery rediscovers the exact same
      # URL later, the existing linksWithTexts-cache check in
      # pipeline.process_link_allOutput recognizes it as already fetched
      # and skips re-extracting it.
      if user_url_sources:
        for _u_url, _u_text in user_url_sources.items():
          if _u_text and not acc_score["source_texts"].get(_u_url):
            acc_score["source_texts"][_u_url] = _u_text
          if _u_url not in all_links:
            all_links.append(_u_url)
        # Tag these under their own stage so the UI doesn't mislabel
        # user-pasted links as NCBI-discovered ones.
        await _progress({"__links_update__": {"acc": acc, "links": list(user_url_sources.keys()),
                                              "stage": "user_link", "user_file": user_file_label}})
      await _progress({"__links_update__": {"acc": acc, "links": list(all_links), "stage": "initial", "user_file": user_file_label}})
      # ── Step 2: Fetch text from DOI/publication links ─────────────────────────
      # Each DOI is processed independently; any failure skips that link.
      if links:
        for link in links:
          if 'https://doi.org/' in link:
            print("link of doi: ", link)
            try:
              if extractHTML is None:
                continue  # HTML extractor unavailable — skip
              html = extractHTML.HTML(htmlContent=None, htmlLink=link, htmlFile="")
              jsonSM = html.getSupMaterial()
              article_text = await html.async_getListSection()
              if len(article_text) == 0:
                metadata_text = html.fetch_crossref_metadata(link)
                if metadata_text:
                  print(f"✅ CrossRef metadata fetched for {link}")
                  article_text = html.mergeTextInJson(metadata_text)
                # Try PubMed abstract
                print("search the paper's abstract on pubmed")
                _link_doi = link.replace('https://doi.org/', '') if 'https://doi.org/' in link else doi
                try:
                  handle = Entrez.esearch(db="pubmed", term=f"{_link_doi}[doi]", retmax=1)
                  record = Entrez.read(handle)
                  id_list = record.get("IdList", [])
                  if id_list:
                    pubmed_id = id_list[0]
                    fetch_handle = Entrez.efetch(db="pubmed", id=pubmed_id, rettype="xml", retmode="xml")
                    fetch_record = Entrez.read(fetch_handle)
                    article = fetch_record.get("PubmedArticle", [])
                    if article:
                      article = article[0]
                      abstract_sections = (
                          article.get("MedlineCitation", {})
                          .get("Article", {})
                          .get("Abstract", {})
                          .get("AbstractText", [])
                      ) or []
                      full_abstract = " ".join(str(s) for s in abstract_sections)
                      if full_abstract.strip():
                        print(f"Abstract found (len={len(full_abstract)}):")
                        article_text += full_abstract
                except Exception as _pme:
                  print(f"PubMed search failed for DOI {link}: {_pme}")

              _blocked = not article_text or (
                  "just a moment" in article_text.lower()
                  or "403 forbidden" in article_text.lower())
              if _blocked:
                # DOI page blocked — try PMC full-text as fallback
                _link_doi_for_pmc = link.replace("https://doi.org/", "")
                try:
                  handle2 = Entrez.esearch(db="pubmed", term=f"{_link_doi_for_pmc}[doi]", retmax=1)
                  rec2 = Entrez.read(handle2)
                  _pmc_pmid = (rec2.get("IdList") or [None])[0]
                  if _pmc_pmid:
                    _pmc_data = NCBI.fetch_pmc_fulltext(_pmc_pmid)
                    if _pmc_data["text"]:
                      article_text = _pmc_data["text"]
                      _blocked = False
                      print(f"[doi_pmc_fallback] PMC text: {len(article_text)} chars for {link}")
                    for _pmc_sl in _pmc_data.get("sup_links", []):
                      if _pmc_sl not in all_links:
                        all_links.append(_pmc_sl)
                        jsonSM.setdefault("PMC Supplementary Files", []).append(_pmc_sl)
                except Exception as _pmc_fb_err:
                  print(f"[doi_pmc_fallback] failed for {link}: {_pmc_fb_err}")
              if _blocked:
                # Still blocked after PMC -- try rendering the ORIGINAL URL
                # with a real headless browser. Unlike Unpaywall (next
                # fallback), this doesn't need an open-access copy to exist
                # elsewhere -- it just needs to get past the bot-protection
                # wall on the page that's already there.
                try:
                  _pw_html_text = await extractHTML.async_fetch_html_playwright(link)
                  if _pw_html_text:
                    _pw_html = extractHTML.HTML(htmlContent=_pw_html_text, htmlLink=link, htmlFile="")
                    _pw_text = await _pw_html.async_getListSection()
                    _pw_blocked = not _pw_text or (
                        "just a moment" in _pw_text.lower()
                        or "403 forbidden" in _pw_text.lower())
                    if not _pw_blocked and _pw_text:
                      article_text = _pw_text
                      _blocked = False
                      print(f"[doi_playwright_fallback] rendered text: {len(article_text)} chars for {link}")
                      try:
                        _pw_sup = _pw_html.getSupMaterial()
                        if _pw_sup:
                          jsonSM.setdefault("Playwright-rendered Supplementary Files", []).extend(
                              sum((_pw_sup[k] for k in _pw_sup if _pw_sup[k]), []))
                      except Exception:
                        pass
                except Exception as _pw_err:
                  print(f"[doi_playwright_fallback] failed for {link}: {_pw_err}")
              if _blocked:
                # Still blocked after PMC + Playwright -- try Unpaywall: many open-access
                # papers are blocked on the publisher's own page by bot
                # protection (Cloudflare etc.) unrelated to access tier.
                # Unpaywall often points to a repository/preprint mirror with
                # no such protection.
                try:
                  _oa_url = NCBI.get_unpaywall_oa_url(_link_doi_for_pmc)
                  if _oa_url:
                    _oa_html = extractHTML.HTML(htmlContent=None, htmlLink=_oa_url, htmlFile="")
                    _oa_text = await _oa_html.async_getListSection()
                    _oa_blocked = not _oa_text or (
                        "just a moment" in _oa_text.lower()
                        or "403 forbidden" in _oa_text.lower())
                    if not _oa_blocked and _oa_text:
                      article_text = _oa_text
                      _blocked = False
                      print(f"[doi_unpaywall_fallback] OA text: {len(article_text)} chars for {link} via {_oa_url}")
                      if _oa_url not in all_links:
                        all_links.append(_oa_url)
                      try:
                        _oa_sup = _oa_html.getSupMaterial()
                        if _oa_sup:
                          jsonSM.setdefault("Unpaywall OA Supplementary Files", []).extend(
                              sum((_oa_sup[k] for k in _oa_sup if _oa_sup[k]), []))
                      except Exception:
                        pass
                except Exception as _oa_err:
                  print(f"[doi_unpaywall_fallback] failed for {link}: {_oa_err}")
              if not _blocked and article_text:
                acc_score["source_texts"][link] = article_text

              # Process supplementary/linked files from the DOI page
              if jsonSM:
                try:
                  sup_links = sum((jsonSM[key] for key in jsonSM if jsonSM[key] is not None), [])
                  if sup_links:
                    all_links += sup_links
                    for l in sup_links:
                      try:
                        more_all_output = await pipeline.process_link_allOutput(
                            link=l, iso=None, acc=acc,
                            saveLinkFolder=saveLinkFolder,
                            linksWithTexts=acc_score["source_texts"],
                            all_output="")
                        if more_all_output:
                          acc_score["source_texts"][l] = more_all_output
                        print(f"len new output of sup_link {l}: {len(more_all_output or '')}")
                      except Exception as _sl_err:
                        print(f"[sup_link] {l} failed: {_sl_err}")
                except Exception as _sm_err:
                  print(f"[supplementary] processing failed for {link}: {_sm_err}")
            except Exception as _doi_err:
              print(f"[DOI fetch] {link} failed: {_doi_err}")

          elif re.search(r'pubmed\.ncbi\.nlm\.nih\.gov/(\d+)', link):
            # PubMed URL in initial links — queue its DOI for processing in Step 3b
            # by adding it to all_links (Step 3b will pick it up and resolve the DOI).
            if link not in all_links:
              all_links.append(link)

          else:  # non-DOI links: user-provided extra links + programmatic BioProject links
            _fetch_this_link = (
                (other_links and link in other_links)
                or link in _bioproject_extra_links
            )
            if _fetch_this_link:
              try:
                more_all_output = await pipeline.process_link_allOutput(
                    link=link, iso=None, acc=acc,
                    saveLinkFolder=saveLinkFolder,
                    linksWithTexts=acc_score["source_texts"],
                    all_output="")
                if more_all_output:
                  _link_label = f"external_{link}" if link in _bioproject_extra_links else link
                  acc_score["source_texts"][_link_label] = more_all_output
                print(f"len new all output after extra link {link}: {len(more_all_output or '')}")
              except Exception as _el_err:
                print(f"[extra_link] {link} failed: {_el_err}")
      await _progress({"__links_update__": {"acc": acc, "links": list(all_links), "stage": "supplementary", "user_file": user_file_label}})
      # ── Step 3: Build keyword context for web search ─────────────────────────
      # Determine the best search term and any extra metadata, regardless of
      # whether the database fetch above succeeded or failed.
      all_accs = {}
      if accessions[acc].get("bioproject"):
        all_accs["bioproject"] = accessions[acc]["bioproject"]
      if accessions[acc].get("biosample"):
        all_accs["biosample"] = accessions[acc]["biosample"]
      if accessions[acc].get("accession"):
        all_accs["accession"] = accessions[acc]["accession"]

      if _is_non_ncbi:
        _parent_project = accessions[acc].get("_parent_project", "")
        _base_acc = acc.split(" | ")[0].strip() if " | " in acc else acc
        _project_id = _parent_project or _base_acc
        try:
          from non_ncbi_resolver import get_search_keywords
          _search_keywords = get_search_keywords(_project_id, _source_db)
          _search_acc = _search_keywords[0]
          _extra_kws = _search_keywords[1:]
        except Exception:
          _search_acc = _project_id
          _extra_kws = []
        _db_hint_text = (
            f"This sample is from the {_source_db} database. "
            f"Project accession: {_project_id}. "
            + (f"Sub-sample/file identifier: {acc}. " if acc != _project_id else "")
            + (f"Related search terms: {', '.join(_extra_kws)}." if _extra_kws else "")
        )
        try:
            from non_ncbi_resolver import fetch_dataset_metadata
            _api_meta = fetch_dataset_metadata(_project_id, _source_db)
            if _api_meta:
                _db_hint_text += "\n" + _api_meta
        except Exception as _meta_err:
            print(f"[non_ncbi fetch_dataset_metadata] {_meta_err}")
        acc_score["source_texts"]["_db_hint"] = _db_hint_text
      else:
        _search_acc = (all_accs.get("biosample") or all_accs.get("accession")
                       or all_accs.get("bioproject") or acc)
        _extra_kws = []

      await _progress(f"[{_acc_idx + 1}/{_total_accs}] Searching literature for {acc}…")
      # ── Step 3 (continued): Web search — runs ALWAYS, independent of DB fetch ─
      # Even if database fetch (Step 1) failed entirely, this step still runs
      # and collects text from Google/PubMed/EuropePMC results.
      _extra_search_meta = {}
      _bp_src = {}
      if bioproject_id:
        _bp_src = (acc_score.get("source_texts", {})
                   .get("NCBI_bioproject", {})
                   .get(bioproject_id, {}))
        if isinstance(_bp_src, dict):
          _pub_titles = [t for t in _bp_src.get("publications", []) or []
                         if t and str(t).lower() not in ("unknown", "")]
          if _pub_titles:
            _extra_search_meta["title"] = _pub_titles[0]
            if len(_pub_titles) > 1:
              _extra_search_meta["alt_titles"] = _pub_titles[1:]
      _extra_search_meta["bioproject_id"] = all_accs.get("bioproject", "")
      _extra_search_meta["experiment_id"] = all_accs.get("experiment",
                                             accessions[acc].get("experiment", ""))
      try:
        more_all_output, more_linksWithTexts, more_links = await model.getMoreInfoForAcc(
            iso=None, acc=_search_acc, saveLinkFolder=saveLinkFolder, niche_cases=niche_cases,
            extra_metadata=_extra_search_meta)
        # Store web-search extracted texts under clearly labelled source keys
        if more_linksWithTexts:
          for _ws_link, _ws_text in more_linksWithTexts.items():
            _label = f"web_search_{_ws_link}"
            acc_score["source_texts"][_label] = _ws_text
        if more_links:
          all_links = list(all_links) + [l for l in more_links if l not in all_links]
        await _progress({"__links_update__": {"acc": acc, "links": list(all_links), "stage": "web_search", "user_file": user_file_label}})
      except Exception as _ws_err:
        print(f"[web search] failed for {acc}: {_ws_err}")

      # ── Step 3b: Follow PubMed links → DOI → full text + supplementary ────
      # Web search often discovers pubmed.ncbi.nlm.nih.gov URLs and adds them to
      # all_links, but Step 2 only processes doi.org links. Here we resolve each
      # new PubMed URL to its DOI and run it through the same extraction pipeline.
      _pubmed_url_re = re.compile(r'pubmed\.ncbi\.nlm\.nih\.gov/(\d+)')
      _processed_source_keys = set(acc_score.get("source_texts", {}).keys())
      for _pm_url in list(all_links):
        _pm_match = _pubmed_url_re.search(_pm_url)
        if not _pm_match:
          continue
        _pmid = _pm_match.group(1)
        if _pm_url in _processed_source_keys:
          continue  # abstract already stored under this key
        # Resolve PMID → DOI
        _pub_doi = NCBI.get_doi_via_europepmc(_pmid)
        if not _pub_doi:
          print(f"[pubmed_follow] no DOI found for PMID {_pmid}")
          continue
        _doi_url = f"https://doi.org/{_pub_doi}"
        if _doi_url not in all_links:
          all_links.append(_doi_url)
        if _doi_url in _processed_source_keys:
          continue  # DOI already extracted in Step 2
        print(f"[pubmed_follow] PMID {_pmid} → {_doi_url}")
        await _progress({"__links_update__": {"acc": acc, "links": list(all_links),
                                              "stage": "pubmed_doi", "user_file": user_file_label}})
        try:
          if extractHTML is None:
            continue
          _pm_html = extractHTML.HTML(htmlContent=None, htmlLink=_doi_url, htmlFile="")
          _pm_jsonSM = _pm_html.getSupMaterial()
          _pm_article_text = await _pm_html.async_getListSection()
          if not _pm_article_text:
            _pm_meta = _pm_html.fetch_crossref_metadata(_doi_url)
            if _pm_meta:
              _pm_article_text = _pm_html.mergeTextInJson(_pm_meta)
            # Abstract fallback via Entrez
            try:
              _eh = Entrez.esearch(db="pubmed", term=f"{_pub_doi}[doi]", retmax=1)
              _er = Entrez.read(_eh)
              _eids = _er.get("IdList", [])
              if _eids:
                _efh = Entrez.efetch(db="pubmed", id=_eids[0], rettype="xml", retmode="xml")
                _efr = Entrez.read(_efh)
                _pa = _efr.get("PubmedArticle", [])
                if _pa:
                  _abst = " ".join(str(s) for s in (
                    _pa[0].get("MedlineCitation", {})
                          .get("Article", {})
                          .get("Abstract", {})
                          .get("AbstractText", []) or []))
                  if _abst.strip():
                    _pm_article_text += _abst
            except Exception:
              pass
          _blocked_pm = not _pm_article_text or (
              "just a moment" in _pm_article_text.lower()
              or "403 forbidden" in _pm_article_text.lower())
          if _blocked_pm:
            # DOI page blocked — try PMC full-text as fallback
            try:
              _pmc_data = NCBI.fetch_pmc_fulltext(_pmid)
              if _pmc_data["text"]:
                _pm_article_text = _pmc_data["text"]
                _blocked_pm = False
                print(f"[pubmed_follow] PMC fallback: {len(_pm_article_text)} chars for PMID {_pmid}")
              # Add PMC supplementary file links to the queue
              for _pmc_sl in _pmc_data.get("sup_links", []):
                if _pmc_sl not in all_links:
                  all_links.append(_pmc_sl)
                  _pm_jsonSM.setdefault("PMC Supplementary Files", []).append(_pmc_sl)
            except Exception as _pmc_err:
              print(f"[pubmed_follow] PMC fallback failed for PMID {_pmid}: {_pmc_err}")
          if _blocked_pm:
            # Still blocked after PMC -- try rendering the original DOI URL
            # with a real headless browser (see Step 2's identical fallback).
            try:
              _pw_html_text_pm = await extractHTML.async_fetch_html_playwright(_doi_url)
              if _pw_html_text_pm:
                _pw_html_pm = extractHTML.HTML(htmlContent=_pw_html_text_pm, htmlLink=_doi_url, htmlFile="")
                _pw_text_pm = await _pw_html_pm.async_getListSection()
                _pw_blocked_pm = not _pw_text_pm or (
                    "just a moment" in _pw_text_pm.lower()
                    or "403 forbidden" in _pw_text_pm.lower())
                if not _pw_blocked_pm and _pw_text_pm:
                  _pm_article_text = _pw_text_pm
                  _blocked_pm = False
                  print(f"[pubmed_follow_playwright] rendered text: {len(_pm_article_text)} chars for PMID {_pmid}")
                  try:
                    _pw_sup_pm = _pw_html_pm.getSupMaterial()
                    if _pw_sup_pm:
                      _pm_jsonSM.setdefault("Playwright-rendered Supplementary Files", []).extend(
                          sum((_pw_sup_pm[k] for k in _pw_sup_pm if _pw_sup_pm[k]), []))
                  except Exception:
                    pass
            except Exception as _pw_pm_err:
              print(f"[pubmed_follow_playwright] failed for PMID {_pmid}: {_pw_pm_err}")
          if _blocked_pm:
            # Still blocked after PMC + Playwright -- try Unpaywall (see
            # Step 2's identical fallback for why: bot protection blocks
            # plain HTTP requests regardless of open-access status).
            try:
              _oa_url_pm = NCBI.get_unpaywall_oa_url(_pub_doi)
              if _oa_url_pm:
                _oa_html_pm = extractHTML.HTML(htmlContent=None, htmlLink=_oa_url_pm, htmlFile="")
                _oa_text_pm = await _oa_html_pm.async_getListSection()
                _oa_blocked_pm = not _oa_text_pm or (
                    "just a moment" in _oa_text_pm.lower()
                    or "403 forbidden" in _oa_text_pm.lower())
                if not _oa_blocked_pm and _oa_text_pm:
                  _pm_article_text = _oa_text_pm
                  _blocked_pm = False
                  print(f"[pubmed_follow_unpaywall] OA text: {len(_pm_article_text)} chars for PMID {_pmid} via {_oa_url_pm}")
                  if _oa_url_pm not in all_links:
                    all_links.append(_oa_url_pm)
                  try:
                    _oa_sup_pm = _oa_html_pm.getSupMaterial()
                    if _oa_sup_pm:
                      _pm_jsonSM.setdefault("Unpaywall OA Supplementary Files", []).extend(
                          sum((_oa_sup_pm[k] for k in _oa_sup_pm if _oa_sup_pm[k]), []))
                  except Exception:
                    pass
            except Exception as _oa_pm_err:
              print(f"[pubmed_follow_unpaywall] failed for PMID {_pmid}: {_oa_pm_err}")
          if not _blocked_pm and _pm_article_text:
              acc_score["source_texts"][_doi_url] = _pm_article_text
              _processed_source_keys.add(_doi_url)
          # Process supplementary files found on the DOI page
          if _pm_jsonSM:
            try:
              _pm_sup_links = sum((_pm_jsonSM[k] for k in _pm_jsonSM if _pm_jsonSM[k]), [])
              if _pm_sup_links:
                for _psl in _pm_sup_links:
                  if _psl not in all_links:
                    all_links.append(_psl)
                await _progress({"__links_update__": {"acc": acc, "links": list(all_links),
                                                      "stage": "pubmed_supplementary",
                                                      "user_file": user_file_label}})
                for _psl in _pm_sup_links:
                  try:
                    _psl_text = await pipeline.process_link_allOutput(
                        link=_psl, iso=None, acc=acc,
                        saveLinkFolder=saveLinkFolder,
                        linksWithTexts=acc_score["source_texts"],
                        all_output="")
                    if _psl_text:
                      acc_score["source_texts"][_psl] = _psl_text
                    print(f"[pubmed_sup] {_psl}: {len(_psl_text or '')} chars")
                  except Exception as _psle:
                    print(f"[pubmed_sup_link] {_psl}: {_psle}")
            except Exception as _psme:
              print(f"[pubmed_supplementary] {_doi_url}: {_psme}")
        except Exception as _pde:
          print(f"[pubmed_doi_follow] {_doi_url}: {_pde}")

      # Prefer context scoped to this specific accession (e.g. the PDF/supplementary
      # files the user attached to the one paper this accession was discovered from)
      # over the global, unscoped upload -- otherwise a file meant for sample 2's
      # paper gets broadcast into sample 1's context too, and the LLM attributes
      # sample 2's table values to sample 1.
      _scoped_context = (per_accession_context or {}).get(acc, "")
      if _scoped_context:
        acc_score["source_texts"]["user_uploaded_file"] = _scoped_context
      elif user_context_text:
        acc_score["source_texts"]["user_uploaded_file"] = user_context_text

      # ── Check for inaccessible paper links and warn the user ──────────────
      _paper_link_markers = ("doi.org", "pubmed.ncbi.nlm.nih", "europepmc.org",
                              "ncbi.nlm.nih.gov/pmc", "biorxiv.org", "medrxiv.org")
      _source_keys = set(acc_score.get("source_texts", {}).keys())
      _inaccessible_links = []
      for _pl in all_links:
        if any(m in _pl for m in _paper_link_markers):
          # Consider it inaccessible if it has no corresponding source text
          _has_text = any(
              k == _pl or k.startswith(f"web_search_{_pl}") or k.startswith(f"external_{_pl}")
              for k in _source_keys
          )
          if not _has_text:
            _inaccessible_links.append(_pl)
      if _inaccessible_links:
        await _progress({
            "__links_warning__": {
                "acc": acc,
                "inaccessible": _inaccessible_links,
                "message": (
                    "Cannot access paper(s) — they may require a subscription or block server "
                    "access. Upload the PDF(s) directly to improve accuracy."
                ),
            }
        })

      # ── Step 4: Build combined text from ALL sources ───────────────────────
      # Converts each source entry to a string (handles dict, list, None gracefully)
      # and labels it clearly so the LLM knows which source it came from.
      text = ""
      for source in list(acc_score["source_texts"].keys()):
        try:
          source_text = acc_score["source_texts"][source]
          # Convert any non-string type to string
          if source_text is None:
            source_text = ""
          elif isinstance(source_text, (dict, list)):
            source_text = str(source_text)
          else:
            source_text = str(source_text)
          print(f"len of {source}: {len(source_text)}")
          if data_preprocess is not None and len(source_text) > 1000000:
            source_text = data_preprocess.normalize_for_overlap(source_text)
            if len(source_text) > 1000000:
              print("REDUCE CONTEXT FOR LLM MODEL")
              reduce_context_for_llm = data_preprocess.build_context_for_llm(
                  [source_text], acc, niche_cases, 500000)
              if reduce_context_for_llm:
                source_text = reduce_context_for_llm
              else:
                source_text = source_text[:500000]
          elif len(source_text) > 1000000:
            source_text = source_text[:500000]
          print(f"add text of {source} into big text")
          text += f'The source - {source}: {source_text}' + f"-----END OF THIS SOURCE {source} ----\n"
        except Exception as _st_err:
          print(f"[source text] failed to process {source}: {_st_err}")

      # 800 000 chars ≈ 170 K tokens — safely under Anthropic's 200 K token limit.
      # Gemini handles up to 1 M tokens, but keeping this limit avoids Anthropic 400s
      # so the cheaper Anthropic path works and we don't fall through to the Gemini fallback.
      _CTX_CHAR_LIMIT = 800000
      if text and len(text) > _CTX_CHAR_LIMIT:
        if data_preprocess is not None:
          text = data_preprocess.normalize_for_overlap(text)
          if len(text) > _CTX_CHAR_LIMIT:
            text = text[:_CTX_CHAR_LIMIT]
        else:
          text = text[:_CTX_CHAR_LIMIT]
      print("length of final all_text: ", len(text))
      print("start to save the all output and its length: ", len(text))
      file_all_path = saveLinkFolder + "/extracted_text_" + acc + ".docx"
      try:
        if data_preprocess is not None:
          data_preprocess.save_text_to_docx(text, file_all_path)
          print(f"✅ Saved DOCX locally: {file_all_path}")
        else:
          # Fallback: write plain text if docx library unavailable
          txt_path = file_all_path.replace(".docx", ".txt")
          with open(txt_path, "w", encoding="utf-8") as _f:
            _f.write(text)
          file_all_path = txt_path
          print(f"✅ Saved TXT locally (docx unavailable): {file_all_path}")
      except Exception as _save_err:
        print(f"⚠ Save failed (non-critical): {_save_err}")
      acc_score["file_all_output"] = file_all_path

      # ── Upload extracted text DOCX to Google Drive ────────────────────────────
      # Path: mtDNA-Location-Classifer/data/<pubmed_id or DirectSubmission>/<safe_acc>.docx
      try:
        if pipeline is not None and hasattr(pipeline, 'drive_service') and pipeline.drive_service:
          _drive_svc = pipeline.drive_service
          _safe_doc_name = pipeline.sanitize_filename(acc, 80) + ".docx"
          _data_folder_id = (getattr(pipeline, 'GDRIVE_DATA_FOLDER_NAME', '')
                             or os.environ.get('GDRIVE_DATA_FOLDER_NAME', ''))
          if not _data_folder_id:
            # Navigate by name: find mtDNA-Location-Classifer then data/
            _root_q = ("name='mtDNA-Location-Classifer' and "
                       "mimeType='application/vnd.google-apps.folder' and trashed=false")
            _root_res = _drive_svc.files().list(q=_root_q, spaces='drive', fields='files(id)').execute()
            _root_files = _root_res.get('files', [])
            if _root_files:
              _root_id = _root_files[0]['id']
              _data_folder_id = pipeline.get_or_create_drive_folder('data', parent_id=_root_id)
          if _data_folder_id:
            _sub_id = pipeline.get_or_create_drive_folder(str(id_folder), parent_id=_data_folder_id)
            _upload_result = pipeline.upload_file_to_drive(file_all_path, _safe_doc_name, _sub_id)
            if _upload_result:
              print(f"✅ Saved DOCX to Google Drive: data/{id_folder}/{_safe_doc_name}")
            else:
              print("⚠ Google Drive upload returned no file ID")
          else:
            print("⚠ Google Drive folder 'mtDNA-Location-Classifer/data' not found — skipping Drive upload")
        else:
          print("⚠ Google Drive service not configured (GCP_CREDS_JSON not set) — DOCX saved locally only")
      except Exception as _gdrive_err:
        print(f"⚠ Google Drive upload failed (non-critical): {_gdrive_err}")

      acc_prompts = {acc: text}
      await _progress(f"[{_acc_idx + 1}/{_total_accs}] Running LLM inference for {acc}…")
      print("start model")
      try:
        # Inject schema_text into standardization_schema so standardize_with_llm can use it
        _schema_for_model = dict(standardization_schema)
        if _global_schema_text:
            _schema_for_model['__schema_text__'] = _global_schema_text
        predicted_output_info = await model.query_document_info(
          niche_cases=niche_cases,
          saveLinkFolder=saveLinkFolder,
          llm_api_function=model.call_llm_api,
          prompts=acc_prompts,
          standardization_schema=_schema_for_model)
      except Exception as _qdi_err:
        print(f"[LLM] query_document_info failed for {acc}: {_qdi_err}")
        await _progress(f"[{_acc_idx + 1}/{_total_accs}] ⚠ LLM inference failed for {acc} — saving partial result.")
        accs_output[acc] = acc_score
        if progress_cb:
          await progress_cb({"__partial_acc__": acc, "__partial_data__": {acc: acc_score}})
        continue
      for output_acc in predicted_output_info:
        # update everything from the output of model for each accession
        # firstly update predicted output of an accession
        predicted_outputs = predicted_output_info[output_acc]["predicted_output"]
        method_used = predicted_output_info[output_acc]["method_used"]
        for pred_out in predicted_outputs:
          print("the pred out: ", pred_out)
          # only for country, we have to standardize (match "country" or "country_name")
          if pred_out.lower() in ("country", "country_name"):
            # Normalize: always store under whichever key exists in acc_score
            _country_key = pred_out if pred_out in acc_score else (
                "country" if "country" in acc_score else pred_out
            )
            if _country_key not in acc_score:
                acc_score[_country_key] = {}
            country = predicted_outputs[pred_out]["answer"]
            country_explanation = predicted_outputs[pred_out][pred_out+"_explanation"]
            if country_explanation: country_explanation = "-" + country_explanation
            if country != "unknown" and len(country)>0:
              clean_country = model.get_country_from_text(country.lower())
              stand_country = standardize_location.smart_country_lookup(country.lower())
              if clean_country == "unknown" and stand_country.lower() == "not found":
                country = "unknown"
                # predicted country is unknown
                acc_score["signals"]["predicted_output"] = False#"unknown"
                acc_score["signals"]["known_failure_pattern"] = True
              if country.lower() != "unknown":
                stand_country = standardize_location.smart_country_lookup(country.lower())
                if stand_country.lower() != "not found":
                  if stand_country.lower() in acc_score[_country_key]:
                    if country_explanation:
                      acc_score[_country_key][stand_country.lower()].append(method_used + country_explanation)
                  else:
                    acc_score[_country_key][stand_country.lower()] = [method_used + country_explanation]
                  # predicted country is non unknown
                  acc_score["signals"]["predicted_output"] = True #stand_country.lower()
                else:
                  if country.lower() in acc_score[_country_key]:
                    if country_explanation:
                      if len(method_used + country_explanation) > 0:
                        acc_score[_country_key][country.lower()].append(method_used + country_explanation)
                  else:
                    if len(method_used + country_explanation) > 0:
                      acc_score[_country_key][country.lower()] = [method_used + country_explanation]
                  # predicted country is non unknown
                  acc_score["signals"]["predicted_output"] = True #country.lower()
            else:
              # predicted country is unknown
              acc_score["signals"]["predicted_output"] = False #"unknown"
              acc_score["signals"]["known_failure_pattern"] = True
          # for niche cases
          else:
            if pred_out in acc_score:
              print("pred out again: ", pred_out)
              answer = predicted_outputs[pred_out]["answer"]
              answer_explanation = predicted_outputs[pred_out][pred_out+"_explanation"]
              if answer_explanation: answer_explanation = "-" + answer_explanation
              if answer.lower() != "unknown":
                acc_score["signals"]["predicted_output"] = True
                if answer.lower() in acc_score[pred_out]:
                  if len(method_used + answer_explanation) > 0:
                    acc_score[pred_out][answer.lower()].append(method_used + answer_explanation)
                else:
                  if len(method_used + answer_explanation) > 0:
                    acc_score[pred_out][answer.lower()] = [method_used + answer_explanation]
              else: acc_score["signals"]["predicted_output"] = False

        # update total query cost
        acc_score["query_cost"] = predicted_output_info[output_acc]["total_query_cost"]
        # update more links if have from model
        more_model_links = predicted_output_info[output_acc]["links"]
        if more_model_links:
          acc_score["source"] += more_model_links
        # update signals
        acc_score["signals"]["accession_found_in_text"] = predicted_output_info[output_acc]["accession_found_in_text"]
        # Propagate Pass 2 additional fields from model.query_document_info
        if "_additional_fields" in predicted_output_info[output_acc]:
          acc_score["_additional_fields"] = predicted_output_info[output_acc]["_additional_fields"]

        # ── Schema alignment pass: map all extracted fields to schema vocabulary ──
        # Also dedupes Pass 2's raw-named fields (e.g. 'geo_loc_name') against the
        # canonical schema name (e.g. 'geographic_location_country_and_or_sea') so
        # the same fact never shows up as two separate columns.
        _schema_keys = {k for k in standardization_schema if not k.startswith('__')}
        if _schema_keys and not _is_ontology_mode and acc_score.get("_additional_fields"):
          try:
            _pass2_values = {
                k: (v.get('value', '') if isinstance(v, dict) else v)
                for k, v in acc_score["_additional_fields"].items()
            }
            aligned = model.align_to_schema(_pass2_values, standardization_schema, acc)
            for canonical, info in aligned.items():
              raw_key = info.get('from_field', '')
              raw_entry = acc_score["_additional_fields"].get(raw_key, {})
              explanation = raw_entry.get('explanation', '') if isinstance(raw_entry, dict) else ''
              if raw_key and raw_key != canonical:
                acc_score["_additional_fields"].pop(raw_key, None)
              if any(canonical.lower() == nc.lower() for nc in (niche_cases or [])):
                # Sheet 1 already has this field from Pass 1 (with its own citation) -- drop the dup.
                acc_score["_additional_fields"].pop(canonical, None)
                continue
              merged_key = field_aliases.canonicalize_field_name(
                  canonical, acc_score["_additional_fields"].keys())
              acc_score["_additional_fields"][merged_key] = {
                  'value': info.get('value', ''), 'explanation': explanation,
              }
            if aligned:
              print(f"[schema-align] {acc}: mapped {len(aligned)} field(s) to schema")
          except Exception as _sa_err:
            print(f"[schema-align] WARNING for {acc}: {_sa_err}")

        # ── Ontology annotation pass (only when GO/OBO URLs were provided) ────────
        if _is_ontology_mode:
          try:
            _all_extracted = {}
            # Collect Pass 1 answers
            for _k, _v in predicted_output_info[output_acc].get("predicted_output", {}).items():
              if isinstance(_v, dict) and _v.get("answer", "unknown").lower() != "unknown":
                _all_extracted[_k] = _v["answer"]
            # Collect Pass 2 fields (now {field: {"value", "explanation"}})
            for _k, _v in (acc_score.get("_additional_fields") or {}).items():
              _all_extracted[_k] = _v.get('value', '') if isinstance(_v, dict) else _v
            if _all_extracted:
              ontology_result = model.annotate_with_ontologies(
                  _all_extracted, text, acc)
              if ontology_result:
                acc_score["_ontology_annotations"] = ontology_result
                # Also add formatted strings to _additional_fields for Sheet 2
                for cat, items in ontology_result.items():
                  if items:
                    _joined = "\n".join(items) if isinstance(items, list) else str(items)
                    acc_score.setdefault("_additional_fields", {})[f"ontology_{cat}"] = \
                        {'value': _joined, 'explanation': ''}
                print(f"[ontology] {acc}: annotated {sum(len(v) for v in ontology_result.values() if isinstance(v, list))} ontology terms")
          except Exception as _ont_err:
            print(f"[ontology-annotation] WARNING for {acc}: {_ont_err}")

        print(f"end of this acc {acc}")
      end = time.time()
      elapsed = (end - start)
      acc_score["time_cost"] = f"{elapsed:.3f} seconds"
      final_source_links = acc_score["source"]
      # Collect source keys, excluding schema/standardization URLs which are not metadata sources
      _raw_source_keys = list(acc_score["source_texts"].keys())
      _filtered_source_keys = [
          k for k in _raw_source_keys
          if k not in _schema_url_set and not any(k == u or k.startswith(f"external_{u}") for u in _schema_url_set)
      ]
      if final_source_links:
        final_source_links += _filtered_source_keys
      else: final_source_links = _filtered_source_keys
      acc_score["source"] = pipeline.unique_preserve_order(final_source_links)
      acc_score["signals"]["num_publications"] += len(acc_score["source"])
      # Store the NCBI accession identifiers so downstream row-builders can
      # include bioproject/biosample/sra_accession columns in the output.
      acc_score["_accession_info"] = accessions[acc]
      accs_output[acc] = acc_score
      await _progress(f"[{_acc_idx + 1}/{_total_accs}] ✓ {acc} done ({acc_score.get('time_cost', '')})")
      # Signal a partial result so api.py can stream the row to the browser immediately
      await _progress({"__partial_acc__": acc, "__partial_data__": {acc: acc_score}})
    # Store the final auto-detected niche_cases so api.py can pass them to
    # _rows_from_new_pipeline for proper per-field citation display.
    accs_output["__niche_cases__"] = niche_cases or []
    print(accs_output)
    return accs_output, acc_score["source_texts"], text

# accessions = ["SAMN35361955", "SAMN35361966"]
#                 #, "SAMN35361956", "SAMN35361957", "SAMN35361958", "SAMN35361959",
# #               "SAMN35361960", "SAMN35361961", "SAMN35361962", "SAMN35361963", "SAMN35361964",
# #               "SAMN35361965", "SAMN35361966"]
# accessions = {"OL757400": {"bioproject":"PRJNA783802",
#                         "biosample": "SAMN23469632",
#                         "accession": "OL757400",
#                         "experiment":"SRR17084312"},
#               "OL757401": {"bioproject":"PRJNA783793",
#                         "biosample": "SAMN23469556",
#                         "accession": "OL757401",
#                         "experiment":"SRR17089164"},}
# ncbi_urls = {"SAMN35361955":["https://www.ncbi.nlm.nih.gov/bioproject/PRJNA976261",
#              "https://www.ncbi.nlm.nih.gov/biosample/SAMN35361955"],
#              "SAMN35361966":["https://www.ncbi.nlm.nih.gov/bioproject/PRJNA976261",
#              "https://www.ncbi.nlm.nih.gov/biosample/SAMN35361966"]}
# other_links = None
# # ["https://onlinelibrary.wiley.com/doi/10.1111/omi.12418"
# # "https://www.sciencedirect.com/science/article/abs/pii/S0003996919303449?via%3Dihub",
# # "https://onlinelibrary.wiley.com/action/downloadSupplement?doi=10.1111%2Fomi.12418&file=omi12418-sup-0001-SuppMat.docx"]
# niche_cases = ["disease_status", "subject_id", "sample_id", "control_group", "body_site"]
# niche_cases = ["country", "province", "taxonomic name", "locality", "GPS", "collection date",
#                "sex", "elevation", "collector", "institution"]#, "associated taxa"]
# outputs, sources, texts = await pipeline_with_gemini(accessions, bioproject_id="PRJNA783802", ncbi_urls=None, other_links=None, niche_cases=niche_cases, save_df=None)
