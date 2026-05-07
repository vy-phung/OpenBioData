import gradio as gr
from collections import Counter
import csv
import os
from functools import lru_cache
#import app
from mtdna_classifier import classify_sample_location 
import data_preprocess, model, pipeline
import subprocess
import json
import pandas as pd
import io
import re
import tempfile
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from io import StringIO
import hashlib
import threading
import confidence_score

# @lru_cache(maxsize=3600)
# def classify_sample_location_cached(accession):
#     return classify_sample_location(accession)

#@lru_cache(maxsize=3600)
async def pipeline_classify_sample_location_cached(accession,stop_flag=None, save_df=None, niche_cases=None):
    print("inside pipeline_classify_sample_location_cached, and [accession] is ", [accession])
    print("len of save df: ", len(save_df))
    if niche_cases: niche_cases=niche_cases.split(", ")
    print("niche case in mtdna_backend.pipeline: ", niche_cases)    
    return await pipeline.pipeline_with_gemini([accession],stop_flag=stop_flag, save_df=save_df, niche_cases=niche_cases)    

# Count and suggest final location
# def compute_final_suggested_location(rows):
#     candidates = [
#         row.get("Predicted Location", "").strip()
#         for row in rows
#         if row.get("Predicted Location", "").strip().lower() not in ["", "sample id not found", "unknown"]
#     ] + [
#         row.get("Inferred Region", "").strip()
#         for row in rows
#         if row.get("Inferred Region", "").strip().lower() not in  ["", "sample id not found", "unknown"]
#     ]

#     if not candidates:
#         return Counter(), ("Unknown", 0)
#     # Step 1: Combine into one string and split using regex to handle commas, line breaks, etc.
#     tokens = []
#     for item in candidates:
#         # Split by comma, whitespace, and newlines
#         parts = re.split(r'[\s,]+', item)
#         tokens.extend(parts)

#     # Step 2: Clean and normalize tokens
#     tokens = [word.strip() for word in tokens if word.strip().isalpha()]  # Keep only alphabetic tokens

#     # Step 3: Count
#     counts = Counter(tokens)

#     # Step 4: Get most common
#     top_location, count = counts.most_common(1)[0]
#     return counts, (top_location, count)

# Store feedback (with required fields)

def store_feedback_to_google_sheets(accession, answer1, answer2, contact=""):
    if not answer1.strip() or not answer2.strip():
        return "⚠️ Please answer both questions before submitting."

    try:
        # ✅ Step: Load credentials from Hugging Face secret
        creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)

        # Connect to Google Sheet
        client = gspread.authorize(creds)
        sheet = client.open("feedback_mtdna").sheet1  # make sure sheet name matches

        # Append feedback
        sheet.append_row([accession, answer1, answer2, contact])
        return "✅ Feedback submitted. Thank you!"

    except Exception as e:
        return f"❌ Error submitting feedback: {e}"

import re

def is_valid_accession(acc):
    """
    Accept any NCBI identifier that ncbi_resolver can handle.
    Falls back to the original GenBank regex for unrecognised strings so
    existing file-upload behaviour is unchanged.
    """
    try:
        from ncbi_resolver import detect_accession_type
        return detect_accession_type(str(acc).strip()) != 'unknown'
    except Exception:
        # Legacy fallback regex if ncbi_resolver is unavailable
        return bool(re.match(r'^[A-Z]{1,4}_?\d{5,}(\.\d+)?$', str(acc).strip()))

# helper function to extract accessions
def extract_accessions_from_input(file=None, raw_text=""):
    print(f"RAW TEXT RECEIVED: {raw_text}")
    accessions, invalid_accessions = [], []
    seen = set()
    if file:
        try:
            if file.name.endswith(".csv"):
                df = pd.read_csv(file)
            elif file.name.endswith(".xlsx"):
                df = pd.read_excel(file)
            else:
                return [], "Unsupported file format. Please upload CSV or Excel."
            for acc in df.iloc[:, 0].dropna().astype(str).str.strip():
                if acc not in seen:
                    if is_valid_accession(acc):
                        accessions.append(acc)
                        seen.add(acc)
                    else:
                        invalid_accessions.append(acc)
                    
        except Exception as e:
            return [],[], f"Failed to read file: {e}"

    if raw_text:
        try:
            text_ids = [s.strip() for s in re.split(r"[\n,;\t]", raw_text) if s.strip()]
            for acc in text_ids:
                if acc not in seen:
                    if is_valid_accession(acc):
                            accessions.append(acc)
                            seen.add(acc)
                    else:
                        invalid_accessions.append(acc)
        except Exception as e:
            return [],[], f"Failed to read file: {e}"
            
    return list(accessions), list(invalid_accessions), None
# ✅ Add a new helper to backend: `filter_unprocessed_accessions()`
def get_incomplete_accessions(file_path):
    df = pd.read_excel(file_path)

    incomplete_accessions = []
    for _, row in df.iterrows():
        sample_id = str(row.get("Sample ID", "")).strip()

        # Skip if no sample ID
        if not sample_id:
            continue

        # Drop the Sample ID and check if the rest is empty
        other_cols = row.drop(labels=["Sample ID"], errors="ignore")
        if other_cols.isna().all() or (other_cols.astype(str).str.strip() == "").all():
            # Extract the accession number from the sample ID using regex
            match = re.search(r"\b[A-Z]{2,4}\d{4,}", sample_id)
            if match:
                incomplete_accessions.append(match.group(0))
    print(len(incomplete_accessions))
    return incomplete_accessions

# GOOGLE_SHEET_NAME = "known_samples"
# USAGE_DRIVE_FILENAME = "user_usage_log.json"
def truncate_cell(value, max_len=49000):
    """
    Coerce value to a clean string for Excel/Sheets output.

    Guarantees:
      - None  -> ""  (never "None")
      - NaN   -> ""  (never "nan")
      - float -> str without trailing .0 where possible
      - Cells longer than max_len are truncated with a marker
    """
    if value is None:
        return ""
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        # e.g. 3.0 -> "3", 3.5 -> "3.5"
        return str(int(value)) if value == int(value) else str(value)
    if not isinstance(value, str):
        value = str(value)
    # Catch string representations of null
    if value.strip().lower() in ("none", "nan", "nat", "null"):
        return ""
    return value[:max_len] + ("... [TRUNCATED]" if len(value) > max_len else "")

# Helper functions to load google sheet
# ===== GLOBAL GOOGLE SHEET CACHE =====

SHEET_CACHE = None
SHEET_HEADERS = None
SHEET_OBJ = None  # keep actual sheet reference (for writing later)

def load_sheet_once():
    """
    Loads the known_samples Google Sheet only once.
    Returns: (DataFrame, headers, sheet_object)
    """
    global SHEET_CACHE, SHEET_HEADERS, SHEET_OBJ

    if SHEET_CACHE is None:
        creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        spreadsheet = client.open("known_samples")
        sheet = spreadsheet.sheet1
        SHEET_OBJ = sheet  # keep for writing later

        data = sheet.get_all_values()
        SHEET_HEADERS = data[0]
        SHEET_CACHE = pd.DataFrame(data[1:], columns=SHEET_HEADERS)

        print("Loaded known_samples into cache.")

    # Always return copies so we don't mutate cache accidentally
    return SHEET_CACHE.copy(), list(SHEET_HEADERS), SHEET_OBJ

save_df, save_headers, SHEET_OBJ = load_sheet_once()  
print("🔒 Google Sheet cache loaded and ready.")

async def summarize_results(accession, stop_flag=None, niche_cases=None):
    # Early bail
    if stop_flag is not None and stop_flag.value:
        print(f"🛑 Skipping {accession} before starting.")
        return []
    # try cache first
    print("niche case in sum_result: ", niche_cases)
    cached = check_known_output(accession, niche_cases)
    if cached:
        print(f"✅ Using cached result for {accession}")
        
        row = {
            "Sample ID": cached.get("Sample ID", "unknown"),
            "Predicted Country": cached.get("Predicted Country", "unknown"),
            "Country Explanation": cached.get("Country Explanation", "unknown"),
            "Predicted Sample Type": cached.get("Predicted Sample Type", "unknown"),
            "Sample Type Explanation": cached.get("Sample Type Explanation", "unknown"),
            "Sources": cached.get("Sources", "No Links"),
            "Time cost": cached.get("Time cost", ""),
            "Confidence Score": cached.get("Confidence Score", "")
        }

        if niche_cases:
            row["Predicted " + niche_cases[0]] = cached.get("Predicted " + niche_cases[0], "unknown")
            row[niche_cases[0] + " Explanation"] = cached.get(niche_cases[0] + " Explanation", "unknown")

        return [row]
            
    # only run when nothing in the cache  
    try:
        print("try gemini pipeline: ",accession)
        # # ✅ Load credentials from Hugging Face secret
        # creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
        # scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        # creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        # client = gspread.authorize(creds)

        # spreadsheet = client.open("known_samples")
        # sheet = spreadsheet.sheet1

        # data = sheet.get_all_values()
        # if not data:
        #     print("⚠️ Google Sheet 'known_samples' is empty.")
        #     return None

        # save_df = pd.DataFrame(data[1:], columns=data[0])

        print("before pipeline, len of save df: ", len(save_df))
        if niche_cases: 
            niche_cases = ", ".join(niche_cases)
        print("this is niche case inside summarize result: ", niche_cases)    
        outputs = await pipeline_classify_sample_location_cached(accession, stop_flag, save_df, niche_cases)
        print("do the dummy output")
        # outputs = {"KY680825":{'isolate': 'NAT107', 
        #            'country': 
        #            {'ecuador': ['ncbi', 
        #                         'rag_llm-The geographic location "Ecuador" is explicitly listed under "geo_loc_name" for the isolate NAT107. The text mentions "217 novel modern mitogenomes", indicating the sample is from a living individual.']}, 
        #            'sample_type': 
        #            {'modern': ['rag_llm-The geographic location "Ecuador" is explicitly listed under "geo_loc_name" for the isolate NAT107. The text mentions "217 novel modern mitogenomes", indicating the sample is from a living individual.']}, 
        #            'query_cost': '0.000941', 'time_cost': '9.246 seconds', 
        #            'source': ['https://doi.org/10.1093/molbev/msx267', 'https://pubmed.ncbi.nlm.nih.gov/29099937/'], 
        #            'file_chunk': 'The_Paleo-Indian_Entry_into_South_America_Accordin_merged_document.docx', 
        #            'file_all_output': 'The_Paleo-Indian_Entry_into_South_America_Accordin_all_merged_document.docx', 
        #            'signals': {'has_geo_loc_name': True, 'has_pubmed': True, 'accession_found_in_text': True, 'predicted_country': 'ecuador', 'genbank_country': 'ecuador', 'num_publications': 3, 'missing_key_fields': False, 'known_failure_pattern': False}}
        #           }
        if stop_flag is not None and stop_flag.value:
            print(f"🛑 Skipped {accession} mid-pipeline.")
            return []
        
    except Exception as e:
        return []#, f"Error: {e}", f"Error: {e}", f"Error: {e}"

    if accession not in outputs:
        print("no accession in output ", accession)
        return []#, "Accession not found in results.", "Accession not found in results.", "Accession not found in results."

    row_score = []
    rows = []
    save_rows = []
    for key in outputs:
        pred_country, pred_sample, country_explanation, sample_explanation = "unknown","unknown","unknown","unknown" 
        checked_sections = ["country", "sample_type"]
        if niche_cases: niche_cases = niche_cases.split(", ")
        if niche_cases: checked_sections += niche_cases
        print("checked sections: ", checked_sections)
        for section, results in outputs[key].items():
            pred_output = []#"\n".join(list(results.keys()))
            output_explanation = ""
            print(section, results)
            if section not in checked_sections: continue
            for result, content in results.items():
              if len(result) == 0:  result = "unknown"
              if len(content) == 0: output_explanation = "unknown"
              else:
                output_explanation += 'Method: ' + "\nMethod: ".join(content)  + "\n"
              pred_output.append(result)
            pred_output = "\n".join(pred_output)      
            if section == "country":
              pred_country, country_explanation = pred_output, output_explanation
            elif section == "sample_type":
              pred_sample, sample_explanation = pred_output, output_explanation   
            else:
              pred_niche, niche_explanation = pred_output, output_explanation  
        if outputs[key]["isolate"].lower()!="unknown":
            label = key + "(Isolate: " + outputs[key]["isolate"] + ")"
        else: label = key  
        if len(outputs[key]["source"]) == 0:  outputs[key]["source"] = ["No Links"]
        # signals for confidence score
        signals_confidence_score = outputs[key]["signals"]
        rules = confidence_score.set_rules()
        print("start to compute confidence score")
        score, tier, explanations_score = confidence_score.compute_confidence_score_and_tier(signals_confidence_score,rules)
        confidence_values = f"{tier} ({score})" + "\n" + "\n".join(explanations_score)
        print("confidence_values: ", confidence_values)  
        # Collect Pass 2 additional fields for this accession
        additional_fields = outputs[key].get("_additional_fields", {}) or {}

        # ── spec 5.3: field names lowercase with spaces, not title-case ─────────
        _niche_display = (
            niche_cases[0].lower().replace("_", " ") if niche_cases else ""
        )

        if niche_cases:
            row = {
                "Sample ID": truncate_cell(label or "unknown"),
                "Predicted country": truncate_cell(pred_country or "unknown"),
                "country explanation": truncate_cell(country_explanation or "unknown"),
                "Predicted sample type": truncate_cell(pred_sample or "unknown"),
                "sample type explanation": truncate_cell(sample_explanation or "unknown"),
                f"Predicted {_niche_display}": truncate_cell(pred_niche or "unknown"),
                f"{_niche_display} explanation": truncate_cell(niche_explanation or "unknown"),
                "Sources": truncate_cell("\n".join(outputs[key]["source"]) or "No Links"),
                "Time cost": truncate_cell(outputs[key]["time_cost"]),
                "Confidence Score": confidence_values,
                "_additional_fields": additional_fields,
            }
            rows.append(row)

            save_row = {
                "Sample ID": truncate_cell(label or "unknown"),
                "Predicted Country": truncate_cell(pred_country or "unknown"),
                "Country Explanation": truncate_cell(country_explanation or "unknown"),
                "Predicted Sample Type": truncate_cell(pred_sample or "unknown"),
                "Sample Type Explanation": truncate_cell(sample_explanation or "unknown"),
                f"Predicted {_niche_display}": truncate_cell(pred_niche or "unknown"),
                f"{_niche_display} explanation": truncate_cell(niche_explanation or "unknown"),
                "Sources": truncate_cell("\n".join(outputs[key]["source"]) or "No Links"),
                "Query_cost": outputs[key]["query_cost"] or "",
                "Time cost": outputs[key]["time_cost"] or "",
                "file_all_output": truncate_cell(outputs[key]["file_all_output"] or ""),
                "Confidence Score": confidence_values,
                "_additional_fields": additional_fields,
            }
            save_rows.append(save_row)
        else:
            row = {
                "Sample ID": truncate_cell(label or "unknown"),
                "Predicted country": truncate_cell(pred_country or "unknown"),
                "country explanation": truncate_cell(country_explanation or "unknown"),
                "Predicted sample type": truncate_cell(pred_sample or "unknown"),
                "sample type explanation": truncate_cell(sample_explanation or "unknown"),
                "Sources": truncate_cell("\n".join(outputs[key]["source"]) or "No Links"),
                "Time cost": truncate_cell(outputs[key]["time_cost"]),
                "Confidence Score": confidence_values,
                "_additional_fields": additional_fields,
            }
            rows.append(row)

            save_row = {
                "Sample ID": truncate_cell(label or "unknown"),
                "Predicted Country": truncate_cell(pred_country or "unknown"),
                "Country Explanation": truncate_cell(country_explanation or "unknown"),
                "Predicted Sample Type": truncate_cell(pred_sample or "unknown"),
                "Sample Type Explanation": truncate_cell(sample_explanation or "unknown"),
                "Sources": truncate_cell("\n".join(outputs[key]["source"]) or "No Links"),
                "Query_cost": outputs[key]["query_cost"] or "",
                "Time cost": outputs[key]["time_cost"] or "",
                "file_all_output": truncate_cell(outputs[key]["file_all_output"] or ""),
                "Confidence Score": confidence_values,
                "_additional_fields": additional_fields,
            }
            save_rows.append(save_row)
        print("the final rows: ", rows)

    try:
        # Prepare as DataFrame
        # df_new = pd.DataFrame(save_rows)
        # print("done df_new and here are save_rows: ", save_rows)
        # # ✅ Setup Google Sheets
        # creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
        # scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        # creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        # client = gspread.authorize(creds)
        # spreadsheet = client.open("known_samples")
        # sheet = spreadsheet.sheet1
    
        # # ✅ Load existing data + headers
        # existing_data = sheet.get_all_values()
        # headers = existing_data[0] if existing_data else []
        # existing_df = pd.DataFrame(existing_data[1:], columns=headers) if len(existing_data) > 1 else pd.DataFrame()
    
        # # ✅ Extend headers if new keys appear in save_rows
        # print("df_new.col: ", df_new.columns)
        # for col in df_new.columns:
        #     print(col)
        #     if col not in headers:
        #         headers.append(col)
        #         # Add new column header in the sheet
        #         sheet.update_cell(1, len(headers), col)
    
        # # ✅ Align DataFrame with sheet headers (fill missing with "")
        # df_new = df_new.reindex(columns=headers, fill_value="")
        
        # # ✅ Build lookup: Sample ID → row index
        # if "Sample ID" in existing_df.columns:
        #     id_to_row = {sid: i + 2 for i, sid in enumerate(existing_df["Sample ID"])}
        # else:
        #     id_to_row = {}
    
        # for _, row in df_new.iterrows():
        #     sid = row.get("Sample ID", "")
        #     row_values = [truncate_cell(str(row.get(h, ""))) for h in headers]
        #     print("row_val of df_new: ", row_values)
        #     if sid in id_to_row:
        #         # ✅ Update existing row in correct header order
        #         sheet.update(f"A{id_to_row[sid]}:{chr(64+len(headers))}{id_to_row[sid]}", [row_values])
        #     else:
        #         # ✅ Append new row
        #         sheet.append_row(row_values)
        print("try new append to sheet function")
        append_to_sheet(save_rows)
    
        print("✅ Match results safely saved to known_samples with dynamic headers.")
    
    except Exception as e:
        print(f"❌ Failed to update known_samples: {e}")

        
    return rows

def append_to_sheet(rows):
    """
    Append new rows to the Google Sheet only once per batch.
    Uses cached sheet object.
    """
    global SHEET_CACHE, SHEET_HEADERS, SHEET_OBJ

    if SHEET_OBJ is None:
        raise RuntimeError("Sheet not loaded. Call load_sheet_once() first.")

    df_new = pd.DataFrame(rows)

    # Ensure columns exist
    for col in df_new.columns:
        if col not in SHEET_HEADERS:
            SHEET_HEADERS.append(col)
            SHEET_OBJ.update_cell(1, len(SHEET_HEADERS), col)

    df_new = df_new.reindex(columns=SHEET_HEADERS, fill_value="")

    # Append each row
    for _, row in df_new.iterrows():
        SHEET_OBJ.append_row([str(row[h]) for h in SHEET_HEADERS])

    print("✅ Batch saved to Google Sheet.")


def save_to_excel(all_rows, summary_text, flag_text, filename, is_resume=False):
    """Write results to a two-sheet Excel file.

    Sheet 1 ("cMD Metadata")  — predefined columns only (no _additional_fields).
    Sheet 2 ("All Attributes") — Sheet 1 columns + one column per unique key
        found in _additional_fields across all rows; blank when a sample lacks
        a particular field.

    all_rows may be either:
      • list of dicts  — the normal pipeline path; dicts may carry
                         '_additional_fields': {key: value, ...}
      • pd.DataFrame   — the save_batch_output / HTML-parse path;
                         no _additional_fields expansion is performed
    """
    def _coerce_df(df):
        """Replace NaN / None with '' and coerce every cell to str."""
        for col in df.columns:
            df[col] = df[col].apply(
                lambda x: "" if (x is None or (isinstance(x, float) and pd.isna(x)))
                          else str(x)
            )
        return df

    # ── Normalise input to list-of-dicts ─────────────────────────────────────
    if isinstance(all_rows, pd.DataFrame):
        rows = all_rows.to_dict(orient="records")
    else:
        rows = list(all_rows or [])

    if not rows:
        print("⚠️ No rows to save.")
        return

    # ── Build Sheet 1 rows (drop _additional_fields key) ─────────────────────
    sheet1_rows = [{k: v for k, v in r.items() if k != "_additional_fields"}
                   for r in rows]

    # ── Collect unique extra-field keys in insertion order ───────────────────
    seen_extra: set = set()
    extra_keys: list = []
    for r in rows:
        af = r.get("_additional_fields", {}) or {}
        if not isinstance(af, dict):
            # Might be a serialised string when coming from HTML path — skip
            continue
        for k in af:
            if k not in seen_extra:
                seen_extra.add(k)
                extra_keys.append(k)

    # ── Build Sheet 2 rows (Sheet 1 + flattened extra fields) ────────────────
    sheet2_rows = []
    for r in rows:
        s2 = {k: v for k, v in r.items() if k != "_additional_fields"}
        af = r.get("_additional_fields", {}) or {}
        if not isinstance(af, dict):
            af = {}
        for k in extra_keys:
            s2[k] = str(af.get(k, "") or "").strip()
        sheet2_rows.append(s2)

    df_sheet1 = _coerce_df(pd.DataFrame(sheet1_rows))
    df_sheet2 = _coerce_df(pd.DataFrame(sheet2_rows))

    # ── Resume: merge with existing file ─────────────────────────────────────
    if is_resume and os.path.exists(filename):
        try:
            existing1 = pd.read_excel(filename, sheet_name="cMD Metadata", engine="openpyxl")
            existing2 = pd.read_excel(filename, sheet_name="All Attributes", engine="openpyxl")
        except Exception as e:
            print(f"⚠️ Could not read existing Excel sheets ({e}); starting fresh.")
            existing1 = pd.DataFrame()
            existing2 = pd.DataFrame()

        def _merge(existing, new_df):
            if existing.empty or "Sample ID" not in existing.columns:
                return new_df
            existing = _coerce_df(existing)
            # Add columns present in new but absent in existing
            for col in new_df.columns:
                if col not in existing.columns:
                    existing[col] = ""
            existing = existing.set_index("Sample ID")
            new_idx = new_df.set_index("Sample ID")
            existing.update(new_idx)
            # Append rows whose Sample ID wasn't in the existing file
            new_only = new_idx.index.difference(existing.index)
            if not new_only.empty:
                existing = pd.concat([existing, new_idx.loc[new_only]])
            return _coerce_df(existing.reset_index())

        df_sheet1 = _merge(existing1, df_sheet1)
        df_sheet2 = _merge(existing2, df_sheet2)

    # ── Write ─────────────────────────────────────────────────────────────────
    try:
        with pd.ExcelWriter(filename, engine="openpyxl") as writer:
            df_sheet1.to_excel(writer, sheet_name="cMD Metadata", index=False)
            df_sheet2.to_excel(writer, sheet_name="All Attributes", index=False)
        print(f"✅ Excel saved: {filename} "
              f"(Sheet1: {len(df_sheet1)} rows | "
              f"Sheet2: {len(df_sheet2)} rows, {len(extra_keys)} extra cols)")
    except Exception as e:
        print(f"❌ Failed to write Excel file {filename}: {e}")


# save the batch input in JSON file
def save_to_json(all_rows, summary_text, flag_text, filename):
    output_dict = {
        "Detailed_Results": all_rows#,  # <-- make sure this is a plain list, not a DataFrame
        # "Summary_Text": summary_text,
        # "Ancient_Modern_Flag": flag_text
    }

    # If all_rows is a DataFrame, convert it
    if isinstance(all_rows, pd.DataFrame):
        output_dict["Detailed_Results"] = all_rows.to_dict(orient="records")

    with open(filename, "w") as external_file:
        json.dump(output_dict, external_file, indent=2)

# save the batch input in Text file
def save_to_txt(all_rows, summary_text, flag_text, filename):
    if isinstance(all_rows, pd.DataFrame):
        detailed_results = all_rows.to_dict(orient="records")
    output = ""
    #output += ",".join(list(detailed_results[0].keys())) + "\n\n"
    output += ",".join([str(k) for k in detailed_results[0].keys()]) + "\n\n"
    for r in detailed_results:
      output += ",".join([str(v) for v in r.values()]) + "\n\n"
    with open(filename, "w") as f:
        f.write("=== Detailed Results ===\n")
        f.write(output + "\n")

        # f.write("\n=== Summary ===\n")
        # f.write(summary_text + "\n")
        
        # f.write("\n=== Ancient/Modern Flag ===\n")
        # f.write(flag_text + "\n")

def save_batch_output(all_rows, output_type, summary_text=None, flag_text=None):
    tmp_dir = tempfile.mkdtemp()

    #html_table = all_rows.value  # assuming this is stored somewhere

    # Parse back to DataFrame
    #all_rows = pd.read_html(all_rows)[0]  # [0] because read_html returns a list
    all_rows = pd.read_html(StringIO(all_rows))[0]
    print(all_rows)

    if output_type == "Excel":
        file_path = f"{tmp_dir}/batch_output.xlsx"
        save_to_excel(all_rows, summary_text, flag_text, file_path)
    elif output_type == "JSON":
        file_path = f"{tmp_dir}/batch_output.json"
        save_to_json(all_rows, summary_text, flag_text, file_path)
        print("Done with JSON")
    elif output_type == "TXT":
        file_path = f"{tmp_dir}/batch_output.txt"
        save_to_txt(all_rows, summary_text, flag_text, file_path)
    else:
        return gr.update(visible=False)  # invalid option
    
    return gr.update(value=file_path, visible=True)
# save cost by checking the known outputs

# def check_known_output(accession):
#     if not os.path.exists(KNOWN_OUTPUT_PATH):
#         return None

#     try:
#         df = pd.read_excel(KNOWN_OUTPUT_PATH)
#         match = re.search(r"\b[A-Z]{2,4}\d{4,}", accession)
#         if match:
#           accession = match.group(0)
          
#         matched = df[df["Sample ID"].str.contains(accession, case=False, na=False)]
#         if not matched.empty:
#             return matched.iloc[0].to_dict()  # Return the cached row
#     except Exception as e:
#         print(f"⚠️ Failed to load known samples: {e}")
#         return None

# def check_known_output(accession):
#     try:
#         # ✅ Load credentials from Hugging Face secret
#         creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
#         scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
#         creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
#         client = gspread.authorize(creds)

#         # ✅ Open the known_samples sheet
#         spreadsheet = client.open("known_samples")  # Replace with your sheet name
#         sheet = spreadsheet.sheet1

#         # ✅ Read all rows
#         data = sheet.get_all_values()
#         if not data:
#             return None

#         df = pd.DataFrame(data[1:], columns=data[0])  # Skip header row

#         # ✅ Normalize accession pattern
#         match = re.search(r"\b[A-Z]{2,4}\d{4,}", accession)
#         if match:
#             accession = match.group(0)

#         matched = df[df["Sample ID"].str.contains(accession, case=False, na=False)]
#         if not matched.empty:
#             return matched.iloc[0].to_dict()

#     except Exception as e:
#         print(f"⚠️ Failed to load known samples from Google Sheets: {e}")
#         return None
# def check_known_output(accession):
#     print("inside check known output function")
#     try:
#         # ✅ Load credentials from Hugging Face secret
#         creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
#         scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
#         creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
#         client = gspread.authorize(creds)

#         spreadsheet = client.open("known_samples")
#         sheet = spreadsheet.sheet1

#         data = sheet.get_all_values()
#         if not data:
#             print("⚠️ Google Sheet 'known_samples' is empty.")
#             return None

#         df = pd.DataFrame(data[1:], columns=data[0])
#         if "Sample ID" not in df.columns:
#             print("❌ Column 'Sample ID' not found in Google Sheet.")
#             return None
        
#         match = re.search(r"\b[A-Z]{2,4}\d{4,}", accession)
#         if match:
#             accession = match.group(0)

#         matched = df[df["Sample ID"].str.contains(accession, case=False, na=False)]
#         if not matched.empty:
#             #return matched.iloc[0].to_dict()
#             row = matched.iloc[0]
#             country = row.get("Predicted Country", "").strip().lower()
#             sample_type = row.get("Predicted Sample Type", "").strip().lower()

#             if country and country != "unknown" and sample_type and sample_type != "unknown":
#                 return row.to_dict()
#             else:
#                 print(f"⚠️ Accession {accession} found but country/sample_type is unknown or empty.")
#                 return None
#         else:
#             print(f"🔍 Accession {accession} not found in known_samples.")
#             return None

#     except Exception as e:
#         import traceback
#         print("❌ Exception occurred during check_known_output:")
#         traceback.print_exc()
#         return None

import os
import re
import json
import time
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
from gspread.exceptions import APIError

# --- Global cache ---
_known_samples_cache = None

def load_known_samples():
    """Load the Google Sheet 'known_samples' into a Pandas DataFrame and cache it."""
    global _known_samples_cache
    try:
        creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        sheet = client.open("known_samples").sheet1
        data = sheet.get_all_values()

        if not data:
            print("⚠️ Google Sheet 'known_samples' is empty.")
            _known_samples_cache = pd.DataFrame()
        else:
            _known_samples_cache = pd.DataFrame(data[1:], columns=data[0])
            print(f"✅ Cached {_known_samples_cache.shape[0]} rows from known_samples")

    except APIError as e:
        print(f"❌ APIError while loading known_samples: {e}")
        _known_samples_cache = pd.DataFrame()
    except Exception as e:
        import traceback
        print("❌ Exception occurred while loading known_samples:")
        traceback.print_exc()
        _known_samples_cache = pd.DataFrame()

def check_known_output(accession, niche_cases=None):
    """Check if an accession exists in the cached 'known_samples' sheet."""
    global _known_samples_cache
    print("inside check known output function")

    try:
        # Load cache if not already loaded
        if _known_samples_cache is None:
            load_known_samples()

        if _known_samples_cache.empty:
            print("⚠️ No cached data available.")
            return None

        # Extract proper accession format (e.g. AB12345)
        match = re.search(r"\b[A-Z]{2,4}\d{4,}", accession)
        if match:
            accession = match.group(0)

        matched = _known_samples_cache[
            _known_samples_cache["Sample ID"].str.contains(accession, case=False, na=False)
        ]

        if not matched.empty:
            row = matched.iloc[0]
            country = row.get("Predicted Country", "").strip().lower()
            sample_type = row.get("Predicted Sample Type", "").strip().lower()
            output_niche = None
            if niche_cases: 
                niche_col = "Predicted " + niche_cases[0]
                print("this is niche_col: ", niche_col)
                if niche_col not in _known_samples_cache.columns:
                    print(f"⚠️ Niche column '{niche_col}' not found in known_samples. Skipping cache.")
                    return None
                output_niche = row.get(niche_col, "").strip().lower()
                print("output niche: ", output_niche)
                if country and country.lower() not in ["","unknown"] and sample_type and sample_type.lower() not in ["","unknown"] and output_niche and output_niche.lower() not in ["","unknown"]:
                    print(f"🎯 Found {accession} in cache")
                    return row.to_dict()
                else:
                    print(f"⚠️ Accession {accession} found but country/sample_type/{niche_cases[0]} unknown or empty.")
                    return None
            else:     
                if country and country.lower() not in ["","unknown"] and sample_type and sample_type.lower() not in ["","unknown"]:
                    print(f"🎯 Found {accession} in cache")
                    return row.to_dict()
                else:
                    print(f"⚠️ Accession {accession} found but country/sample_type unknown or empty.")
                    return None
        else:
            print(f"🔍 Accession {accession} not found in cache.")
            return None

    except Exception as e:
        import traceback
        print("❌ Exception occurred during check_known_output:")
        traceback.print_exc()
        return None



def hash_user_id(user_input):
    return hashlib.sha256(user_input.encode()).hexdigest()

# ✅ Load and save usage count

# def load_user_usage():
#     if not os.path.exists(USER_USAGE_TRACK_FILE):
#         return {}

#     try:
#         with open(USER_USAGE_TRACK_FILE, "r") as f:
#             content = f.read().strip()
#             if not content:
#                 return {}  # file is empty
#             return json.loads(content)
#     except (json.JSONDecodeError, ValueError):
#         print("⚠️ Warning: user_usage.json is corrupted or invalid. Resetting.")
#         return {}  # fallback to empty dict
# def load_user_usage():
#     try:
#         creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
#         scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
#         creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
#         client = gspread.authorize(creds)

#         sheet = client.open("user_usage_log").sheet1
#         data = sheet.get_all_records()  # Assumes columns: email, usage_count

#         usage = {}
#         for row in data:
#             email = row.get("email", "").strip().lower()
#             count = int(row.get("usage_count", 0))
#             if email:
#                 usage[email] = count
#         return usage
#     except Exception as e:
#         print(f"⚠️ Failed to load user usage from Google Sheets: {e}")
#         return {}
# def load_user_usage():
#     try:
#         parent_id = pipeline.get_or_create_drive_folder("mtDNA-Location-Classifier")
#         iterate3_id = pipeline.get_or_create_drive_folder("iterate3", parent_id=parent_id)

#         found = pipeline.find_drive_file("user_usage_log.json", parent_id=iterate3_id)
#         if not found:
#             return {}  # not found, start fresh

#         #file_id = found[0]["id"]
#         file_id = found
#         content = pipeline.download_drive_file_content(file_id)
#         return json.loads(content.strip()) if content.strip() else {}

#     except Exception as e:
#         print(f"⚠️ Failed to load user_usage_log.json from Google Drive: {e}")
#         return {}
def load_user_usage():
    try:
        creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        sheet = client.open("user_usage_log").sheet1
        data = sheet.get_all_values()
        print("data: ", data)
        print("🧪 Raw header row from sheet:", data[0])
        print("🧪 Character codes in each header:")
        for h in data[0]:
            print([ord(c) for c in h])

        if not data or len(data) < 2:
            print("⚠️ Sheet is empty or missing rows.")
            return {}

        headers = [h.strip().lower() for h in data[0]]
        if "email" not in headers or "usage_count" not in headers:
            print("❌ Header format incorrect. Must have 'email' and 'usage_count'.")
            return {}
            
        permitted_index = headers.index("permitted_samples") if "permitted_samples" in headers else None
        df = pd.DataFrame(data[1:], columns=headers)

        usage = {}
        permitted = {}
        for _, row in df.iterrows():
            email = row.get("email", "").strip().lower()
            try:
                #count = int(row.get("usage_count", 0))
                try:
                    count = int(float(row.get("usage_count", 0)))
                except Exception:
                    print(f"⚠️ Invalid usage_count for {email}: {row.get('usage_count')}")
                    count = 0

                if email:
                    usage[email] = count
                    if permitted_index is not None:
                        try:
                            permitted_count = int(float(row.get("permitted_samples", 50)))
                            permitted[email] = permitted_count
                        except:
                            permitted[email] = 50
                        
            except ValueError:
                print(f"⚠️ Invalid usage_count for {email}: {row.get('usage_count')}")
        return usage, permitted

    except Exception as e:
        print(f"❌ Error in load_user_usage: {e}")
        return {}, {}



# def save_user_usage(usage):
#     with open(USER_USAGE_TRACK_FILE, "w") as f:
#         json.dump(usage, f, indent=2)

# def save_user_usage(usage_dict):
#     try:
#         creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
#         scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
#         creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
#         client = gspread.authorize(creds)

#         sheet = client.open("user_usage_log").sheet1
#         sheet.clear()  # clear old contents first

#         # Write header + rows
#         rows = [["email", "usage_count"]] + [[email, count] for email, count in usage_dict.items()]
#         sheet.update(rows)
#     except Exception as e:
#         print(f"❌ Failed to save user usage to Google Sheets: {e}")
# def save_user_usage(usage_dict):
#     try:
#         parent_id = pipeline.get_or_create_drive_folder("mtDNA-Location-Classifier")
#         iterate3_id = pipeline.get_or_create_drive_folder("iterate3", parent_id=parent_id)

#         import tempfile
#         tmp_path = os.path.join(tempfile.gettempdir(), "user_usage_log.json")
#         print("💾 Saving this usage dict:", usage_dict)
#         with open(tmp_path, "w") as f:
#             json.dump(usage_dict, f, indent=2)

#         pipeline.upload_file_to_drive(tmp_path, "user_usage_log.json", iterate3_id)

#     except Exception as e:
#         print(f"❌ Failed to save user_usage_log.json to Google Drive: {e}")
# def save_user_usage(usage_dict):
#     try:
#         creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
#         scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
#         creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
#         client = gspread.authorize(creds)

#         spreadsheet = client.open("user_usage_log")
#         sheet = spreadsheet.sheet1

#         # Step 1: Convert new usage to DataFrame
#         df_new = pd.DataFrame(list(usage_dict.items()), columns=["email", "usage_count"])
#         df_new["email"] = df_new["email"].str.strip().str.lower()

#         # Step 2: Load existing data
#         existing_data = sheet.get_all_values()
#         print("🧪 Sheet existing_data:", existing_data)

#         # Try to load old data
#         if existing_data and len(existing_data[0]) >= 1:
#             df_old = pd.DataFrame(existing_data[1:], columns=existing_data[0])

#             # Fix missing columns
#             if "email" not in df_old.columns:
#                 df_old["email"] = ""
#             if "usage_count" not in df_old.columns:
#                 df_old["usage_count"] = 0

#             df_old["email"] = df_old["email"].str.strip().str.lower()
#             df_old["usage_count"] = pd.to_numeric(df_old["usage_count"], errors="coerce").fillna(0).astype(int)
#         else:
#             df_old = pd.DataFrame(columns=["email", "usage_count"])

#         # Step 3: Merge
#         df_combined = pd.concat([df_old, df_new], ignore_index=True)
#         df_combined = df_combined.groupby("email", as_index=False).sum()

#         # Step 4: Write back
#         sheet.clear()
#         sheet.update([df_combined.columns.tolist()] + df_combined.astype(str).values.tolist())
#         print("✅ Saved user usage to user_usage_log sheet.")

#     except Exception as e:
#         print(f"❌ Failed to save user usage to Google Sheets: {e}")
def save_user_usage(usage_dict):
    try:
        creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        spreadsheet = client.open("user_usage_log")
        sheet = spreadsheet.sheet1

        # Build new df
        df_new = pd.DataFrame(list(usage_dict.items()), columns=["email", "usage_count"])
        df_new["email"] = df_new["email"].str.strip().str.lower()
        df_new["usage_count"] = pd.to_numeric(df_new["usage_count"], errors="coerce").fillna(0).astype(int)

        # Read existing data
        existing_data = sheet.get_all_values()
        if existing_data and len(existing_data[0]) >= 2:
            df_old = pd.DataFrame(existing_data[1:], columns=existing_data[0])
            df_old["email"] = df_old["email"].str.strip().str.lower()
            df_old["usage_count"] = pd.to_numeric(df_old["usage_count"], errors="coerce").fillna(0).astype(int)
        else:
            df_old = pd.DataFrame(columns=["email", "usage_count"])

        # ✅ Overwrite specific emails only
        df_old = df_old.set_index("email")
        for email, count in usage_dict.items():
            email = email.strip().lower()
            df_old.loc[email, "usage_count"] = count
        df_old = df_old.reset_index()

        # Save
        sheet.clear()
        sheet.update([df_old.columns.tolist()] + df_old.astype(str).values.tolist())
        print("✅ Saved user usage to user_usage_log sheet.")

    except Exception as e:
        print(f"❌ Failed to save user usage to Google Sheets: {e}")




# def increment_usage(user_id, num_samples=1):
#     usage = load_user_usage()
#     if user_id not in usage:
#         usage[user_id] = 0
#     usage[user_id] += num_samples
#     save_user_usage(usage)
#     return usage[user_id]
# def increment_usage(email: str, count: int):
#     usage = load_user_usage()
#     email_key = email.strip().lower()
#     usage[email_key] = usage.get(email_key, 0) + count
#     save_user_usage(usage)
#     return usage[email_key]
def increment_usage(email: str, count: int = 1):
    usage, permitted = load_user_usage()
    email_key = email.strip().lower()
    #usage[email_key] = usage.get(email_key, 0) + count
    current = usage.get(email_key, 0)
    new_value = current + count
    max_allowed = permitted.get(email_key) or 50
    usage[email_key] = max(current, new_value)  # ✅ Prevent overwrite with lower
    print(f"🧪 increment_usage saving: {email_key=} {current=} + {count=} => {usage[email_key]=}")
    print("max allow is: ", max_allowed)
    save_user_usage(usage)
    return usage[email_key], max_allowed


# run the batch
def summarize_batch(file=None, raw_text="", resume_file=None, user_email="", 
                    stop_flag=None, output_file_path=None, 
                    limited_acc=50, yield_callback=None):
    if user_email:
      limited_acc += 10
    accessions, error = extract_accessions_from_input(file, raw_text)
    if error:
        #return [], "", "", f"Error: {error}"
        return [], f"Error: {error}", 0, "", ""
    if resume_file:
      accessions = get_incomplete_accessions(resume_file)
    tmp_dir = tempfile.mkdtemp()
    if not output_file_path:
      if resume_file:
        output_file_path = os.path.join(tmp_dir, resume_file) 
      else:  
        output_file_path = os.path.join(tmp_dir, "batch_output_live.xlsx")

    all_rows = []
    # all_summaries = []
    # all_flags = []
    progress_lines = []
    warning = ""
    if len(accessions) > limited_acc:  
      accessions = accessions[:limited_acc]
      warning = f"Your number of accessions is more than the {limited_acc}, only handle first {limited_acc} accessions"
    for i, acc in enumerate(accessions):
        if stop_flag and stop_flag.value:
            line = f"🛑 Stopped at {acc} ({i+1}/{len(accessions)})"
            progress_lines.append(line)
            if yield_callback:
              yield_callback(line)
            print("🛑 User requested stop.")
            break
        print(f"[{i+1}/{len(accessions)}] Processing {acc}")
        try:
            # rows, summary, label, explain = summarize_results(acc)
            rows = summarize_results(acc)
            all_rows.extend(rows)
            # all_summaries.append(f"**{acc}**\n{summary}")
            # all_flags.append(f"**{acc}**\n### 🏺 Ancient/Modern Flag\n**{label}**\n\n_Explanation:_ {explain}")
            #save_to_excel(all_rows, summary_text="", flag_text="", filename=output_file_path)
            save_to_excel(all_rows, summary_text="", flag_text="", filename=output_file_path, is_resume=bool(resume_file))
            line = f"✅ Processed {acc} ({i+1}/{len(accessions)})"
            progress_lines.append(line)
            if yield_callback:
              yield_callback(f"✅ Processed {acc} ({i+1}/{len(accessions)})")
        except Exception as e:
            print(f"❌ Failed to process {acc}: {e}")
            continue
            #all_summaries.append(f"**{acc}**: Failed - {e}")
        #progress_lines.append(f"✅ Processed {acc} ({i+1}/{len(accessions)})")
        limited_acc -= 1
    """for row in all_rows:
          source_column = row[2]  # Assuming the "Source" is in the 3rd column (index 2)
          
          if source_column.startswith("http"):  # Check if the source is a URL
              # Wrap it with HTML anchor tags to make it clickable
              row[2] = f'<a href="{source_column}" target="_blank" style="color: blue; text-decoration: underline;">{source_column}</a>'"""
    if not warning:
      warning = f"You only have {limited_acc} left"
    if user_email.strip():
        user_hash = hash_user_id(user_email)
        total_queries = increment_usage(user_hash, len(all_rows))
    else:
        total_queries = 0
    yield_callback("✅ Finished!")

    # summary_text = "\n\n---\n\n".join(all_summaries)
    # flag_text = "\n\n---\n\n".join(all_flags)
    #return all_rows, summary_text, flag_text, gr.update(visible=True), gr.update(visible=False)
    #return all_rows, gr.update(visible=True), gr.update(visible=False)
    return all_rows, output_file_path, total_queries, "\n".join(progress_lines), warning