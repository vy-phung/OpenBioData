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


def fetch_standardization_schema(urls) -> dict:
    """
    Fetch one or more CSV standardization files and return a rich schema dict:
      { field_name: { "description": str, "allowed_values": list } }

    Handles two file types automatically:
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

    for url in urls:
        raw_url = _github_blob_to_raw(url.strip())
        try:
            resp = _requests.get(raw_url, timeout=15)
            resp.raise_for_status()
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
    return schema

"""accessions = { acc: {"bioproject":"",
                        "biosample": "",
                        "accession": "",},
                  acc1: {"bioproject":"",
                        "biosample": "",
                        "accession": "",},
}"""

# Main execution
async def pipeline_with_gemini(accessions, bioproject_id=None, ncbi_urls=None, other_links=None, niche_cases=None, save_df=None, standardization_urls=None, user_context_text=None):
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
    _gemini_key = os.getenv("NEW_GOOGLE_API_KEY")
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

    acc_prompts = {}
    bioproject_info = {}
    accs_output = {}
    print("accessions: ", accessions)
    for acc in accessions:
      print("start gemini: ", acc)
      start = time.time()
      total_query_cost = 0
      jsonSM, links, article_text, pubmeds, all_output, doi = {},[], "", [], "", ""
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
      # get the data from the links of NCBI
      ncbi_texts, ncbi_text_links = {}, {}
      ncbi_texts = NCBI.extract_NCBI_directly(acc)
      """accessions = {"OL757400": {"bioproject":"PRJNA783802",
                        "biosample": "SAMN23469632",
                        "accession": "OL757400",
                        "experiment":"SRR17084312"},}"""
      if accessions[acc]["bioproject"]:
        bioproject_id = accessions[acc]["bioproject"]

        print("get bioproject from acc input: ", bioproject_id)
      for ncbi_source in accessions[acc]:
        if ncbi_source == "bioproject" and accessions[acc]["bioproject"]:
          if not bioproject_info:
            print("get bioproject info")
            bioproject_info = NCBI.extract_NCBI_directly(bioproject_id)
            acc_score["source_texts"]["NCBI_bioproject"] = {bioproject_id: bioproject_info[bioproject_id]}
          else:
            if bioproject_id not in bioproject_info:
              bioproject_info = NCBI.extract_NCBI_directly(bioproject_id)
              acc_score["source_texts"]["NCBI_bioproject"] = {bioproject_id: bioproject_info[bioproject_id]}
            else: acc_score["source_texts"]["NCBI_bioproject"] = {bioproject_id: bioproject_info[bioproject_id]}
          # check or get pubmed from bioproject
          if not pubmeds:
            print("inside pubmed getting and bioproject: ", bioproject_id)
            pubmeds = acc_score["source_texts"]["NCBI_bioproject"][bioproject_id]["pubmed"]
        elif ncbi_source == "biosample" and accessions[acc]["biosample"]:
          biosample_id = accessions[acc]["biosample"]
          ncbi_texts = NCBI.extract_NCBI_directly(biosample_id)
          acc_score["source_texts"]["NCBI_biosample"] = ncbi_texts
        elif ncbi_source == "accession" and accessions[acc]["accession"]:
          accession_id = accessions[acc]["accession"]
          ncbi_texts = NCBI.extract_NCBI_directly(accession_id)
          acc_score["source_texts"]["NCBI_accession"] = ncbi_texts
          if not pubmeds:
            pubmed = acc_score["source_texts"]["NCBI_accession"][accession_id]["pubmed_id"]
            if pubmed:
              pubmeds.append(pubmed)
            doi = acc_score["source_texts"]["NCBI_accession"][accession_id]["doi"]
        elif ncbi_source == "experiment" and accessions[acc]["experiment"]:
          experiment_id = accessions[acc]["experiment"]
          ncbi_texts = NCBI.extract_NCBI_directly(experiment_id)
          acc_score["source_texts"]["NCBI_experiment"] = ncbi_texts

      if acc_score["source_texts"]:
        source_kws = list(acc_score["source_texts"].keys())
        for s in source_kws:
          if "NCBI" in s:
            acc_score["signals"]["in_NCBI"] = True
            break
      print("source text after ncbi: ", acc_score["source_texts"])
      # set up step: create the folder to save document
      # firstly get the doi url from pubmed id which is from bioproject
      if pubmeds:
        id_folder = "_".join(pubmeds)
        for pubID in pubmeds:
          id = str(pubID)
          # save in signals that pubmed exists
          acc_score["signals"]["has_pubmed"] = True
          if not doi:
            doi = NCBI.get_doi_via_europepmc(id)
          links.append('https://doi.org/' + doi)
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
      if links:
        for link in links: # this includes the doi, other links parameter, sup_links from doi
          if 'https://doi.org/' in link: # check doi first
            # get the file to create listOfFile for each id
            print("link of doi: ", link)
            if extractHTML is None:
                continue  # HTML extractor unavailable (lightweight deploy) — skip this link
            html = extractHTML.HTML(htmlContent=None, htmlLink=link, htmlFile="")
            jsonSM = html.getSupMaterial()
            article_text = await html.async_getListSection() # html.getListSection()
            if len(article_text) == 0:
              # try crossAPI
              metadata_text = html.fetch_crossref_metadata(link)
              if metadata_text:
                print(f"✅ CrossRef metadata fetched for {link}")
                other_explain = "Because full-text is restricted by the publisher, our system uses abstracts and metadata to remain compliant while still supporting exploratory analysis, search, and literature linking."
                article_text = html.mergeTextInJson(metadata_text)
              # also try searching pubmed with the title and extract abstract and add to article text
              # Step 1: Search for the paper
              print("search the paper's abstract on pubmed")
              try:
                #handle = Entrez.esearch(db="pubmed", term=doi, retmax=1)
                handle = Entrez.esearch(db="pubmed", term=f"{doi}[doi]", retmax=1)
                record = Entrez.read(handle)
                id_list = record.get("IdList", [])

                if not id_list:
                    print("No PubMed results found.")
                else:
                    pubmed_id = id_list[0]
                    fetch_handle = Entrez.efetch(db="pubmed", id=pubmed_id, rettype="xml", retmode="xml")
                    fetch_record = Entrez.read(fetch_handle)

                    # Safe extraction
                    article = fetch_record.get("PubmedArticle", [])
                    if not article:
                        print("No PubmedArticle entry returned.")
                    else:
                        article = article[0]  # the real payload
                        try:
                            abstract_sections = (
                                article["MedlineCitation"]["Article"]
                                .get("Abstract", {})
                                .get("AbstractText", [])
                            )
                            full_abstract = " ".join(str(s) for s in abstract_sections)

                            if full_abstract.strip():
                                print("Abstract found (len={}):".format(len(full_abstract)))
                                #print(full_abstract)
                                article_text += full_abstract
                            else:
                                print("This article has **no abstract available on PubMed**.")

                        except KeyError:
                            print("Abstract field missing in this PubMed record.")
              except RuntimeError as e:
                  print(f"PubMed search failed for DOI {doi}: {e}")
              except Exception as e:
                  print(f"Unexpected error during PubMed search: {e}")

            if article_text:
              if "Just a moment...Enable JavaScript and cookies to continue".lower() not in article_text.lower() or "403 Forbidden Request".lower() not in article_text.lower():
                acc_score["source_texts"][link] = article_text
                #all_output += article_text
            if jsonSM:
              sup_links = sum((jsonSM[key] for key in jsonSM),[])
              if sup_links:
                all_links += sup_links
                for l in sup_links:
                  #acc_score["source_texts"][l] = ""
                  more_all_output = await pipeline.process_link_allOutput(link=l, iso=None, acc=acc, saveLinkFolder=saveLinkFolder, linksWithTexts=acc_score["source_texts"], all_output="")
                  acc_score["source_texts"][l] = more_all_output
                  print(f"len new output  of sup_link {l}: {len(more_all_output)}")
          else: # if other links not doi
            if other_links:
                all_links += other_links
                for l in other_links:
                  #acc_score["source_texts"][l] = ""
                  more_all_output = await pipeline.process_link_allOutput(link=l, iso=None, acc=acc, saveLinkFolder=saveLinkFolder, linksWithTexts=acc_score["source_texts"], all_output="")
                  acc_score["source_texts"][l] = more_all_output
                  print(f"len new all output after sup link {l}: {len(more_all_output)}")
      # links that are not included before and need smart search
      all_accs = {}
      """Example: accessions = {
        bioproject: PRJNA976261,
        biosample: SAMN35361966,
        accession: None (or if not then it is KU131308)
      }"""
      if accessions[acc]["bioproject"]:
        all_accs["bioproject"] = accessions[acc]["bioproject"]
      if accessions[acc]["biosample"]:
        all_accs["biosample"] = accessions[acc]["biosample"]
      if accessions[acc]["accession"]:
        all_accs["accession"] = accessions[acc]["accession"]
      _search_acc = (all_accs.get("biosample") or all_accs.get("accession")
                     or all_accs.get("bioproject") or acc)
      more_all_output, more_linksWithTexts, more_links = await model.getMoreInfoForAcc(
          iso=None, acc=_search_acc, saveLinkFolder=saveLinkFolder, niche_cases=niche_cases)
      #if more_all_output: all_output = more_all_output
      if more_linksWithTexts: acc_score["source_texts"].update(more_linksWithTexts)
      if more_links: all_links = more_links

      if user_context_text:
        acc_score["source_texts"]["user_uploaded_file"] = user_context_text

      text = ""
      for source in acc_score["source_texts"]:
        # check if the extracted text of that source too long or not
        source_text = acc_score["source_texts"][source]
        print(f"len of {source}: {len(source_text)}")
        if len(source_text) > 1000000:
          # reduce the text
          source_text = data_preprocess.normalize_for_overlap(source_text)
          if len(source_text) > 1000000:
            print("REDUCE CONTEXT FOR LLM MODEL")
            reduce_context_for_llm = data_preprocess.build_context_for_llm([source_text], acc, niche_cases, 500000)
            if reduce_context_for_llm:
              print("reduce context for llm")
              source_text = reduce_context_for_llm
            else:
              print("no reduce context for llm despite>1M")
              source_text = source_text[:500000]
        print(f"add text of {source} into big text")
        text += f'The source - {source}: {source_text}' + f"-----END OF THIS SOURCE {source} ----\n"
      if text and len(text) > 1000000:
        text = data_preprocess.normalize_for_overlap(text)
        if len(text) > 1000000:
          print("REDUCE CONTEXT FOR LLM MODEL")
          reduce_context_for_llm = data_preprocess.build_context_for_llm([source_text], acc, niche_cases, 500000)
          if reduce_context_for_llm:
            print("reduce context for llm")
            source_text = reduce_context_for_llm
          else:
            print("no reduce context for llm despite>1M")
            source_text = source_text[:1000000]
      print("length of final all_text: ", len(text))
      # add text into acc_prompts for multi batch cause they are context for llm for each prompt
      print("start to save the all output and its length: ", len(text))
      file_all_path = saveLinkFolder + "/extracted_text_" + acc + ".docx"
      data_preprocess.save_text_to_docx(text, file_all_path)
      acc_score["file_all_output"] = file_all_path
      acc_prompts = {acc: text}
      print("start model")
      predicted_output_info = await model.query_document_info(
        niche_cases=niche_cases,
        saveLinkFolder=saveLinkFolder,
        llm_api_function=model.call_llm_api,
        prompts=acc_prompts,
        standardization_schema=standardization_schema)
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
