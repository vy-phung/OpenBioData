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
async def pipeline_with_gemini(accessions, bioproject_id=None, ncbi_urls=None, other_links=None, niche_cases=None, save_df=None, standardization_urls=None, user_context_text=None, progress_cb=None, cancel_event=None, user_file_label=None):
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

    # Fetch standardization schema from provided CSV URLs
    # Merge standardization_urls (dedicated param) and other_links (may include std URLs)
    _all_std_urls = list(standardization_urls or [])
    if other_links:
        for _lnk in other_links:
            if _lnk and _lnk not in _all_std_urls:
                _all_std_urls.append(_lnk)
    standardization_schema = fetch_standardization_schema(_all_std_urls) if _all_std_urls else {}
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
      if other_links: links += other_links
      all_links = copy.deepcopy(links)
      print("all_links: ", all_links)
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

              if article_text:
                _blocked = ("just a moment" in article_text.lower()
                            or "403 forbidden" in article_text.lower())
                if not _blocked:
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

      if user_context_text:
        acc_score["source_texts"]["user_uploaded_file"] = user_context_text

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
        predicted_output_info = await model.query_document_info(
          niche_cases=niche_cases,
          saveLinkFolder=saveLinkFolder,
          llm_api_function=model.call_llm_api,
          prompts=acc_prompts,
          standardization_schema=standardization_schema)
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
          # only for country, we have to standardize
          if pred_out.lower() == "country":
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
                  if stand_country.lower() in acc_score[pred_out]:
                    if country_explanation:
                      acc_score[pred_out][stand_country.lower()].append(method_used + country_explanation)
                  else:
                    acc_score[pred_out][stand_country.lower()] = [method_used + country_explanation]
                  # predicted country is non unknown
                  acc_score["signals"]["predicted_output"] = True #stand_country.lower()
                else:
                  if country.lower() in acc_score[pred_out]:
                    if country_explanation:
                      if len(method_used + country_explanation) > 0:
                        acc_score[pred_out][country.lower()].append(method_used + country_explanation)
                  else:
                    if len(method_used + country_explanation) > 0:
                      acc_score[pred_out][country.lower()] = [method_used + country_explanation]
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
        _schema_keys = {k for k in standardization_schema if not k.startswith('__')}
        if _schema_keys and not _is_ontology_mode and acc_score.get("_additional_fields"):
          try:
            aligned = model.align_to_schema(
                acc_score["_additional_fields"],
                standardization_schema, acc)
            if aligned:
              acc_score["_additional_fields"].update(aligned)
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
            # Collect Pass 2 fields
            _all_extracted.update(acc_score.get("_additional_fields", {}))
            if _all_extracted:
              ontology_result = model.annotate_with_ontologies(
                  _all_extracted, text, acc)
              if ontology_result:
                acc_score["_ontology_annotations"] = ontology_result
                # Also add formatted strings to _additional_fields for Sheet 2
                for cat, items in ontology_result.items():
                  if items:
                    acc_score.setdefault("_additional_fields", {})[f"ontology_{cat}"] = \
                        "\n".join(items) if isinstance(items, list) else str(items)
                print(f"[ontology] {acc}: annotated {sum(len(v) for v in ontology_result.values() if isinstance(v, list))} ontology terms")
          except Exception as _ont_err:
            print(f"[ontology-annotation] WARNING for {acc}: {_ont_err}")

        print(f"end of this acc {acc}")
      end = time.time()
      elapsed = (end - start)
      acc_score["time_cost"] = f"{elapsed:.3f} seconds"
      final_source_links = acc_score["source"]
      if final_source_links:
        final_source_links += list(acc_score["source_texts"].keys())
      else: final_source_links = list(acc_score["source_texts"].keys())
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
