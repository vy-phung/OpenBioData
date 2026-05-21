import pandas as pd
from pathlib import Path
import subprocess
import os
import re
import google.generativeai as genai
try:
    import data_preprocess
except ImportError:
    data_preprocess = None
try:
    import model
except ImportError:
    model = None
try:
    import mtdna_classifier
except ImportError:
    mtdna_classifier = None
try:
    import smart_fallback
except ImportError:
    smart_fallback = None
try:
    import standardize_location
except ImportError:
    standardize_location = None
try:
    from NER.html import extractHTML
except ImportError:
    extractHTML = None
# Helper functions in for this pipeline
# Track time
import time
import multiprocessing
import gspread
import io
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.oauth2.service_account import Credentials
from oauth2client.service_account import ServiceAccountCredentials
#––– Authentication setup –––
GDRIVE_PARENT_FOLDER_NAME = "mtDNA-Location-Classifier"
GDRIVE_DATA_FOLDER_NAME = os.environ.get("GDRIVE_DATA_FOLDER_NAME", "")
try:
    _gcp_raw = os.environ.get("GCP_CREDS_JSON", "{}")
    GCP_CREDS_DICT = json.loads(_gcp_raw) if _gcp_raw.strip().startswith("{") else {}
    GDRIVE_CREDS = Credentials.from_service_account_info(GCP_CREDS_DICT, scopes=["https://www.googleapis.com/auth/drive"]) if GCP_CREDS_DICT else None
    drive_service = build("drive", "v3", credentials=GDRIVE_CREDS) if GDRIVE_CREDS else None
except Exception:
    GCP_CREDS_DICT = {}
    GDRIVE_CREDS = drive_service = None

def get_or_create_drive_folder(name, parent_id=None):
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = drive_service.files().list(q=query, spaces='drive', fields="files(id, name)").execute()
    items = results.get("files", [])
    if items:
        return items[0]["id"]
    file_metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    if parent_id:
        file_metadata["parents"] = [parent_id]
    file = drive_service.files().create(body=file_metadata, fields="id").execute()
    return file["id"]

# def build_fresh_drive():
#     return build("drive", "v3", credentials=Credentials.from_authorized_user_file("token.json"))
    
def find_drive_file(filename, parent_id):
    """
    Checks if a file with the given name exists inside the specified Google Drive folder.
    Returns the file ID if found, else None.
    """
    #drive = build_fresh_drive()
    try:
        print(f"🔍 Searching for '{filename}' in folder: {parent_id}")
        query = f"'{parent_id}' in parents and name = '{filename}' and trashed = false"
        results = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            pageSize=1
        ).execute()
        files = results.get('files', [])
        if files:
            print(f"✅ Found file: {files[0]['name']} with ID: {files[0]['id']}")
            return files[0]["id"]
        else:
            print("⚠️ File not found.")
            return None
    except Exception as e:
        print(f"❌ Error during find_drive_file: {e}")
        return None
def upload_file_to_drive(local_path, remote_name, folder_id):
    try:
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"❌ Local file does not exist: {local_path}")

        # Delete existing file on Drive if present
        existing = drive_service.files().list(
            q=f"name='{remote_name}' and '{folder_id}' in parents and trashed = false",
            fields="files(id)"
        ).execute().get("files", [])

        if existing:
            drive_service.files().delete(fileId=existing[0]["id"]).execute()
            print(f"🗑️ Deleted existing '{remote_name}' in Drive folder {folder_id}")

        file_metadata = {"name": remote_name, "parents": [folder_id]}
        media = MediaFileUpload(local_path, resumable=True)
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()

        print(f"✅ Uploaded '{remote_name}' to Google Drive folder ID: {folder_id}")
        return file["id"]

    except Exception as e:
        print(f"❌ Error during upload: {e}")
        return None

def download_file_from_drive(remote_name, folder_id, local_path):
    results = drive_service.files().list(q=f"name='{remote_name}' and '{folder_id}' in parents", fields="files(id)").execute()
    files = results.get("files", [])
    if not files:
        return False
    file_id = files[0]["id"]
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.FileIO(local_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return True
def download_drive_file_content(file_id):
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read().decode("utf-8")

import multiprocessing

def run_with_timeout(func, args=(), kwargs={}, timeout=30):
    def wrapper(q, *args, **kwargs):
        try:
            result = func(*args, **kwargs)
            q.put((True, result))
        except Exception as e:
            q.put((False, e))

    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=wrapper, args=(q, *args), kwargs=kwargs)
    p.start()
    p.join(timeout)

    if p.is_alive():
        p.terminate()
        p.join()
        print(f"⏱️ Timeout exceeded ({timeout} sec) — function killed.")
        return False, None

    if not q.empty():
        success, result = q.get()
        if success:
            return True, result
        else:
            raise result  # re-raise exception if needed

    return False, None

def time_it(func, *args, **kwargs):
    """
    Measure how long a function takes to run and return its result + time.
    """
    start = time.time()
    result = func(*args, **kwargs)
    end = time.time()
    elapsed = end - start
    print(f"⏱️ '{func.__name__}' took {elapsed:.3f} seconds")
    return result, elapsed
# --- Define Pricing Constants (for Gemini 1.5 Flash & text-embedding-004) ---    

def unique_preserve_order(seq):
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]

# def sanitize_filename(filename, max_length=100):
#     # Remove characters that are not letters, numbers, spaces, underscores, or hyphens
#     filename = re.sub(r'[<>:"/\\|?*\n\r\t]', '', filename)
#     # Replace spaces with underscores
#     filename = filename.replace(" ", "_")
#     # Limit length
#     return filename[:max_length]   
import re
import unicodedata

def sanitize_filename(filename, max_length=100):
    # Normalize unicode (optional but safer)
    filename = unicodedata.normalize("NFKD", filename)

    # Remove dangerous characters INCLUDING single quotes
    filename = re.sub(r"[<>:\"'`/\\|?*\n\r\t]", "", filename)

    # Replace spaces with underscores
    filename = re.sub(r"\s+", "_", filename)

    # Remove remaining characters that are not safe (keep letters, digits, _, -)
    filename = re.sub(r"[^A-Za-z0-9_\-]", "", filename)

    # Trim length
    return filename[:max_length]


# Some helpers
import aiohttp
import asyncio

async def fetch_url(session, url, timeout=15):
    try:
        async with session.get(url, timeout=timeout) as resp:
            text = await resp.text()
            return url, text
    except Exception as e:
        return url, None
async def fetch_all(links, timeout=15):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_url(session, l, timeout) for l in links]
        return await asyncio.gather(*tasks)

async def process_link_allOutput(link, iso, acc, saveLinkFolder, linksWithTexts, all_output):
    print(link)
    if len(data_preprocess.normalize_for_overlap(all_output)) > 600000:
        print("break here")
        return all_output   # nothing more for this link

    query_kw = iso if iso != "unknown" else acc

    # --- text extraction ---
    if linksWithTexts and link in linksWithTexts and linksWithTexts[link]!="":
        print("yeah art_text available")
        text_link = linksWithTexts[link]
    else:
        try:
            print("start preprocess and extract text")
            text_link = await data_preprocess.async_extract_text(link, saveLinkFolder)
        except Exception:
            text_link = ""

    # --- table extraction ---
    try:
        tables_link = await asyncio.wait_for(
            asyncio.to_thread(data_preprocess.extract_table, link, saveLinkFolder),
            timeout=10
        )
        print("this is len of table link: ", len(str(table_links)))
    except Exception:
        tables_link = []

    # --- merge ---
    try:
        print("just merge text and tables")
        print("len of text link before mergin: ", len(text_link))
        print("len of table link before merge: ", len(", ".join(tables_link)))
        try:
          final_input_link = text_link + ", ".join(tables_link)
        except:  
          final_input_link = str(text_link) + str(tables_link)
    except Exception:
        print("no succeed here in preprocess docu")
        final_input_link = ""
    # --- normalize output ---
    if len(final_input_link) > 1000000:
        final_input_link = data_preprocess.normalize_for_overlap(final_input_link)
        if len(final_input_link) > 1000000:
            final_input_link = final_input_link[:1000000]

    all_output += data_preprocess.normalize_for_overlap(all_output) + final_input_link

    return all_output

from Bio import Entrez
Entrez.email = "your_email@example.com"   # required by NCBI

async def extractSources(meta, linksWithTexts, links, all_output, acc, saveLinkFolder, niche_cases=None):
    article_text = ""
    iso, title, doi, pudID, features = meta["isolate"], meta["title"], meta["doi"], meta["pubmed_id"], meta["all_features"]
    if doi != "unknown":
        link = 'https://doi.org/' + doi
        # get the file to create listOfFile for each id
        print("link of doi: ", link)  
        # html = extractHTML.HTML("",link)
        html = extractHTML.HTML(htmlContent=None, htmlLink=link, htmlFile="")
        jsonSM = html.getSupMaterial()
        article_text = await html.async_getListSection() # html.getListSection()
        if len(article_text) == 0:
            # try crossAPI
            metadata_text = html.fetch_crossref_metadata(link)
            if metadata_text:
              print(f"✅ CrossRef metadata fetched for {link}")
              #other_explain = "Because full-text is restricted by the publisher, our system uses abstracts and metadata to remain compliant while still supporting exploratory analysis, search, and literature linking."
              article_text = html.mergeTextInJson(metadata_text)
            # also try searching pubmed with the title and extract abstract and add to article text
            # Step 1: Search for the paper
            print("search the paper's abstract on pubmed")
            handle = Entrez.esearch(db="pubmed", term=title, retmax=1)
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
        
        if article_text:
          if "Just a moment...Enable JavaScript and cookies to continue".lower() not in article_text.lower() or "403 Forbidden Request".lower() not in article_text.lower():
            linksWithTexts[link] = article_text
            links.append(link)
            all_output += article_text
        if jsonSM:
            sup_links = sum((jsonSM[key] for key in jsonSM),[])
            if sup_links:
              links += sup_links
              for l in sup_links:
                linksWithTexts[l] = ""
                more_all_output=await process_link_allOutput(l, iso, acc, saveLinkFolder, linksWithTexts, all_output)
                all_output += more_all_output
                if len(all_output) > 10000000:
                  all_output = data_preprocess.normalize_for_overlap(all_output)
                  print("reduce context for llm")
                  reduce_context_for_llm = ""
                  if len(all_output)>1000000:
                    texts_reduce = []
                    out_links_reduce = {}
                    texts_reduce.append(all_output)
                    out_links_reduce[link] = {"all_output": all_output}
                    input_prompt = ["country_name", "modern/ancient/unknown"] 
                    if niche_cases: input_prompt += niche_cases 
                    reduce_context_for_llm = data_preprocess.build_context_for_llm(texts_reduce, acc, input_prompt, 500000)
                    if reduce_context_for_llm:
                      print("reduce context for llm")
                      all_output = reduce_context_for_llm
                    else:
                      print("reduce context no succeed")
                      all_output = all_output[:500000]  
                  print("length of context after reducing: ", len(all_output))   
                print("len new all output after sup link: ", len(all_output))
          # no doi then google custom search api
    if doi=="unknown" or len(article_text) == 0 or "Just a moment...Enable JavaScript and cookies to continue".lower() in article_text.lower() or "403 Forbidden Request".lower() in article_text.lower():
        # might find the article
        print("no article text, start tem link")  
        more_all_output, more_linksWithTexts, more_links = await model.getMoreInfoForAcc(iso, acc, saveLinkFolder, niche_cases)
        if more_all_output: all_output += more_all_output
        if more_links: links += more_links
        if more_linksWithTexts: linksWithTexts.update(more_linksWithTexts)   
    return linksWithTexts, links, all_output

# Main execution
async def pipeline_with_gemini(accessions,stop_flag=None, save_df=None, niche_cases=None):
  # output: country, sample_type, ethnic, location, money_cost, time_cost, explain
  # Prices are per 1,000 tokens
  # Before each big step:
  if stop_flag is not None and stop_flag.value:
    print(f"🛑 Stop detected before starting {accession}, aborting early...")
    return {}
  # Gemini 2.5 Flash-Lite pricing per 1,000 tokens
  PRICE_PER_1K_INPUT_LLM = 0.00010      # $0.10 per 1M input tokens
  PRICE_PER_1K_OUTPUT_LLM = 0.00040     # $0.40 per 1M output tokens

  # Embedding-001 pricing per 1,000 input tokens
  PRICE_PER_1K_EMBEDDING_INPUT = 0.00015  # $0.15 per 1M input tokens  
  if not accessions:
    print("no input")
    return None
  else:  
    accs_output = {}
    genai.configure(api_key=os.getenv("NEW_GOOGLE_API_KEY"))  
    for acc in accessions:
      print("start gemini: ", acc)  
      start = time.time()
      total_cost_title = 0
      jsonSM, links, article_text = {},[], ""
      acc_score = { "isolate": "",
                    "country":{},
                   "sample_type":{},
                   "query_cost":total_cost_title,
                   "time_cost":None,
                   "source":links,
                    "file_all_output":"",
                   "_additional_fields": {},   # populated by Pass 2 in model.query_document_info
                   "signals":{ # default values
                              "has_geo_loc_name": False,
                              "has_pubmed": False,
                              "accession_found_in_text": False,
                              "predicted_country": None,
                              "genbank_country": None,
                              "num_publications": 0,
                              "missing_key_fields": False,
                              "known_failure_pattern": False,},
                  }
      if niche_cases:
        for niche in niche_cases:
          acc_score[niche] = {}
            
      meta = mtdna_classifier.fetch_ncbi_metadata(acc)
      country, spe_loc, ethnic, sample_type, col_date, iso, title, doi, pudID, features = meta["country"], meta["specific_location"], meta["ethnicity"], meta["sample_type"], meta["collection_date"], meta["isolate"], meta["title"], meta["doi"], meta["pubmed_id"], meta["all_features"]
      acc_score["isolate"] = iso
      print("meta: ",meta)  
      meta_expand = smart_fallback.fetch_ncbi(acc)
      print("meta expand: ", meta_expand)  
      # set up step: create the folder to save document
      all_output, linksWithTexts = "", {}
      if pudID: 
        id = str(pudID)
        saveTitle = title
        # save in signals that pubmed exists
        acc_score["signals"]["has_pubmed"] = True
      else: 
        try:
          author_name = meta_expand["authors"].split(',')[0]  # Use last name only
        except:
          author_name = meta_expand["authors"] 
        saveTitle = title + "_" + col_date + "_" + author_name
        if title.lower() == "unknown" and col_date.lower()=="unknown" and   author_name.lower() == "unknown":
            saveTitle += "_" + acc
        id = "DirectSubmission"
      data_folder_id = GDRIVE_DATA_FOLDER_NAME  # Use the shared folder directly
      sample_folder_id = get_or_create_drive_folder(str(id), parent_id=data_folder_id)
      print("sample folder id: ", sample_folder_id)
      
      safe_title = sanitize_filename(saveTitle, 50)
      all_filename = f"{safe_title}_all_merged_document.docx"  
      print("all filename: ", all_filename)  
      # Define local temp paths for reading/writing
      LOCAL_TEMP_DIR = "/mnt/data/generated_docs"
      os.makedirs(LOCAL_TEMP_DIR, exist_ok=True)
      file_all_path = os.path.join(LOCAL_TEMP_DIR, all_filename)
      if stop_flag is not None and stop_flag.value:
        print(f"🛑 Stop processing {accession}, aborting early...")
        return {}
      print("this is file all path: ", file_all_path)
      all_id = find_drive_file(all_filename, sample_folder_id)
    
      if all_id:
        print("✅ Files already exist in Google Drive. Downloading them...")
        all_exists = download_file_from_drive(all_filename, sample_folder_id, file_all_path)
        acc_score["file_all_output"] = str(all_filename)  
        print("all_id: ")
        print(all_id)  
        print("file all output saved in acc score: ", acc_score["file_all_output"])  
        file = drive_service.files().get(fileId="1LUJRTrq8yt4S4lLwCvTmlxaKqpr0nvEn", fields="id, name, parents, webViewLink").execute()
        print("📄 Name:", file["name"])
        print("📁 Parent folder ID:", file["parents"][0])
        print("🔗 View link:", file["webViewLink"])
      else:
        # 🔥 Remove any stale local copies
        if os.path.exists(file_all_path):
            os.remove(file_all_path)
            print(f"🗑️ Removed stale: {file_all_path}")  
      # Try to download if already exists on Drive
        all_exists = download_file_from_drive(all_filename, sample_folder_id, file_all_path)
      print("all exist: ", all_exists)  
      # first way: ncbi method
      print("country.lower: ",country.lower())  
      if country.lower() != "unknown":
        stand_country = standardize_location.smart_country_lookup(country.lower())
        print("stand_country: ", stand_country)  
        if stand_country.lower() != "not found":
          acc_score["country"][stand_country.lower()] = ["ncbi"]
        else: acc_score["country"][country.lower()] = ["ncbi"]   
        # write in a signals for existing country in ncbi
        acc_score["signals"]["has_geo_loc_name"] = True
        acc_score["signals"]["genbank_country"] = list(acc_score["country"].keys())[0] 
        acc_score["signals"]["num_publications"] += 1 # ncbi also counts as 1 source    
      if sample_type.lower() != "unknown":
        acc_score["sample_type"][sample_type.lower()] = ["ncbi"]
      # second way: LLM model
      # Preprocess the input token
      print(acc_score)  
      if stop_flag is not None and stop_flag.value:
        print(f"🛑 Stop processing {accession}, aborting early...")
        return {}    
      # check doi first
      if all_exists:
        print("File all output exists!")
        if not all_output:
            text_all, table_all, document_title_all = model.read_docx_text(file_all_path)
            all_output = data_preprocess.normalize_for_overlap(text_all) + "\n" + data_preprocess.normalize_for_overlap(". ".join(table_all))
        if str(all_filename) != "":
            print("first time have all path at all exist: ", str(all_filename))
            acc_score["file_all_output"] = str(all_filename)    
      print("acc sscore for file all output: ", acc_score["file_all_output"])  
      if len(acc_score["file_all_output"]) == 0 or doi!="unknown":  
          linksWithTexts, links, all_output = await extractSources(meta, linksWithTexts, links, all_output, acc, sample_folder_id, niche_cases)
          links = unique_preserve_order(links)
          print("this is links: ",links)
          acc_score["source"] = links
      else:
        print("inside the try of reusing chunk or all output")  
        #print("chunk filename: ", str(chunks_filename))       
        try:
            temp_source = False
            if save_df is not None and not save_df.empty:
                print("save df not none")  
                print("all filename: ",str(all_filename))
                print("acc score for file all output: ", acc_score["file_all_output"])
                if acc_score["file_all_output"]:
                  link = save_df.loc[save_df["file_all_output"]==acc_score["file_all_output"],"Sources"].iloc[0]
                  #link = row["Sources"].iloc[0]
                  print(link)
                  print("list of link")
                  print([x for x in link.split("\n") if x.strip()])
                  if "http" in link:    
                    print("yeah http in save df source")
                    acc_score["source"] = [x for x in link.split("\n") if x.strip()]#row["Sources"].tolist()   
                  else:  # temporary  
                    print("tempo source") 
                    #acc_score["source"] = [str(all_filename), str(chunks_filename)]
                    temp_source = True      
                else:  # temporary  
                  print("tempo source") 
                  #acc_score["source"] = [str(file_all_path), str(file_chunk_path)]  
                  temp_source = True
            else:  # temporary  
                print("tempo source") 
                  #acc_score["source"] = [str(file_all_path), str(file_chunk_path)]  
                temp_source = True      
            if temp_source:
                print("temp source is true so have to try again search link")
                linksWithTexts, links, all_output = await extractSources(meta, linksWithTexts, links, all_output, acc, sample_folder_id, niche_cases)
                links = unique_preserve_order(links)
                print("links: ", links)
                acc_score["source"] = links
        except:
            try:
                print("in the exception and start to get link")
                linksWithTexts, links, all_output = await extractSources(meta, linksWithTexts, links, all_output, acc, sample_folder_id, niche_cases)
                links = unique_preserve_order(links)
                print("this is links: ",links)
                acc_score["source"] = links
            except:
                print("except of except for source")  
                acc_score["source"] = []
      if stop_flag is not None and stop_flag.value:
        print(f"🛑 Stop processing {accession}, aborting early...")
        return {}
      all_output += "Collection_date: " + col_date +". Isolate: " + iso + ". Title: " + title + ". Features: " + features
      print("all output length: ", len(all_output))
      if len(all_output) > 750000: 
        all_output = data_preprocess.normalize_for_overlap(all_output)
        # use build context for llm function to reduce token
        print("reduce context for llm")
        reduce_context_for_llm = ""
        if len(all_output)>500000:
          texts_reduce = []
          out_links_reduce = {}
          texts_reduce.append(all_output)
          out_links_reduce[link] = {"all_output": all_output}
          input_prompt = ["country_name", "modern/ancient/unknown"] 
          if niche_cases: input_prompt += niche_cases 
          reduce_context_for_llm = data_preprocess.build_context_for_llm(texts_reduce, acc, input_prompt, 250000)
          if reduce_context_for_llm:
            print("reduce context for llm")
            all_output = reduce_context_for_llm
          else:
            print("reduce context no succeed")
            all_output = all_output[:250000]  
        print("length of context after reducing: ", len(all_output)) 
      text = ""
      for key in meta_expand:
        text += str(key) + ": " + meta_expand[key] + "\n"    
      if len(data_preprocess.normalize_for_overlap(all_output)) > 0:
        text += data_preprocess.normalize_for_overlap(all_output)          
      text += ". NCBI Features: " + features   
      print("start to save the all output and its length: ", len(text))
      data_preprocess.save_text_to_docx(all_output, file_all_path)
      result_all_upload = upload_file_to_drive(file_all_path, all_filename, sample_folder_id)
      print("UPLOAD RESULT FOR all_output: ", result_all_upload)
      print(f"🔗 Uploaded file: https://drive.google.com/file/d/{result_all_upload}/view")  

      acc_prompts = {acc: text}
      print("start model")
      predicted_output_info = await model.query_document_info(
        niche_cases=niche_cases,
        saveLinkFolder=sample_folder_id,
        llm_api_function=model.call_llm_api,
        prompts=acc_prompts)
      for output_acc in predicted_output_info:  
        # update everything from the output of model for each accession
        # firstly update predicted output of an accession
        predicted_outputs = predicted_output_info[output_acc]["predicted_output"]
        method_used = predicted_output_info[output_acc]["method_used"]
        for pred_out in predicted_outputs:
            # only for country, we have to standardize
            if pred_out == "country_name":
                country = predicted_outputs[pred_out]["answer"]
                country_explanation = predicted_outputs[pred_out][pred_out+"_explanation"]
                if country_explanation: country_explanation = "-" + country_explanation
                if country != "unknown" and len(country)>0:
                  clean_country = model.get_country_from_text(country.lower())
                  stand_country = standardize_location.smart_country_lookup(country.lower())
                  if clean_country == "unknown" and stand_country.lower() == "not found":  
                    country = "unknown"
                    # predicted country is unknown
                    acc_score["signals"]["predicted_country"] = "unknown"
                    acc_score["signals"]["known_failure_pattern"] = True
                  if country.lower() != "unknown":
                    stand_country = standardize_location.smart_country_lookup(country.lower())
                    if stand_country.lower() != "not found":
                      if stand_country.lower() in acc_score["country"]:
                        if country_explanation:
                          acc_score["country"][stand_country.lower()].append(method_used + country_explanation)
                      else:
                        acc_score["country"][stand_country.lower()] = [method_used + country_explanation]
                      # predicted country is non unknown
                      acc_score["signals"]["predicted_country"] = stand_country.lower()  
                    else:
                      if country.lower() in acc_score["country"]:
                        if country_explanation:
                          if len(method_used + country_explanation) > 0:
                            acc_score["country"][country.lower()].append(method_used + country_explanation)
                      else:
                        if len(method_used + country_explanation) > 0:
                          acc_score["country"][country.lower()] = [method_used + country_explanation]
                      # predicted country is non unknown
                      acc_score["signals"]["predicted_country"] = country.lower()    
                else:
                  # predicted country is unknown
                  acc_score["signals"]["predicted_country"] = "unknown"
                  acc_score["signals"]["known_failure_pattern"] = True
          # for sample type
            elif pred_out == "modern/ancient/unknown":
                sample_type = predicted_outputs[pred_out]["answer"]
                sample_type_explanation = predicted_outputs[pred_out][pred_out+"_explanation"]
                if sample_type_explanation: sample_type_explanation = "-" + sample_type_explanation
                if sample_type.lower() != "unknown":
                  if sample_type.lower() in acc_score["sample_type"]:
                    if len(method_used + sample_type_explanation) > 0:
                      acc_score["sample_type"][sample_type.lower()].append(method_used + sample_type_explanation)
                  else:
                    if len(method_used + sample_type_explanation)> 0:
                      acc_score["sample_type"][sample_type.lower()] = [method_used + sample_type_explanation]
          # for niche cases
            else:
                if pred_out in acc_score:  
                  answer = predicted_outputs[pred_out]["answer"]
                  answer_explanation = predicted_outputs[pred_out][pred_out+"_explanation"]
                  if answer_explanation: answer_explanation = "-" + answer_explanation
                  if answer.lower() != "unknown":
                    if answer.lower() in acc_score[pred_out]:
                      if len(method_used + answer_explanation) > 0:
                        acc_score[pred_out][answer.lower()].append(method_used + answer_explanation)
                    else:
                      if len(method_used + answer_explanation) > 0:
                        acc_score[pred_out][answer.lower()] = [method_used + answer_explanation]
                          
        # update total query cost
        acc_score["query_cost"] = predicted_output_info[output_acc]["total_query_cost"]
        # update more links if have from model
        more_model_links = predicted_output_info[output_acc]["links"]
        if more_model_links:
          acc_score["source"] += more_model_links
        # update signals
        # add if accession_found_in_text or not
        acc_score["signals"]["accession_found_in_text"] = predicted_output_info[output_acc]["accession_found_in_text"]
        # add into the number of publications
        acc_score["signals"]["num_publications"] += len(acc_score["source"])
        # propagate Pass 2 additional fields from model output
        acc_score["_additional_fields"] = predicted_output_info[output_acc].get("_additional_fields", {})
        print(f"end of this acc {acc}")
        
      end = time.time()
      elapsed = (end - start)
      acc_score["time_cost"] = f"{elapsed:.3f} seconds"
      accs_output[acc] = acc_score
    print(accs_output)  
    return accs_output