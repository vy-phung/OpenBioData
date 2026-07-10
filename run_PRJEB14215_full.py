"""
Runs the REAL app pipeline end-to-end for Task 1: PRJEB14215, all samples.

  accession:      PRJEB14215
  user uploads:   test-data/PRJEB14215/41591_2018_61_MOESM3_ESM.xlsx
                   test-data/PRJEB14215/s41591-018-0061-3.pdf
  metadata:       study_name, subject_id, sample_id, target_condition, control,
                   body_site, sequencing_platform, host_species
  standardization schema: none

No pipeline file is modified. Every step below calls the exact function the
production app calls for that step:

  - file -> text:            api._extract_text_from_upload()   (api.py's /upload-context path)
  - BioProject -> samples:   ncbi_resolver.resolve_accessions('PRJEB14215', max_samples=...)
                              (same function input_handler.build_pipeline_input() /
                              api.py's /analyze uses; called here with an explicit
                              max_samples big enough for ALL of PRJEB14215's samples,
                              since api.py's live endpoint hardcodes a 50-sample cap
                              that would otherwise truncate this project — the task
                              explicitly asks for all samples, not a subset).
  - full pipeline:           additional_pipeline.pipeline_with_gemini() (api.py:1450's _rich_pipeline)
  - row construction:        api._rows_from_new_pipeline()
  - excel export:            mtdna_backend.save_to_excel() (the app's existing "save to excel" function)

model.call_llm_api is monkeypatched with a passthrough recorder identical in spirit to
run_SAMN35361964_single_pdf.py's, PLUS a hard safety trip: if any call falls back to
Gemini (meaning ANTHROPIC_API_KEY was missing/invalid/rate-limited/etc for that call),
it immediately (a) prints a loud warning, (b) records the event, and (c) sets the
pipeline's cancel_event so pipeline_with_gemini stops before starting its NEXT sample
(cancel_event is checked once per sample, at the top of its accession loop — see
additional_pipeline.py:377). The already-in-flight sample is allowed to finish so we
don't discard partial NCBI/paper work, but no further samples are started once a Gemini
fallback is detected. This run must use Anthropic only, per explicit instruction.

Usage: python run_PRJEB14215_full.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import additional_pipeline
import ncbi_resolver
from api import _extract_text_from_upload, _rows_from_new_pipeline
from mtdna_backend import save_to_excel

import model

BIOPROJECT = "PRJEB14215"
UPLOAD_PATHS = [
    "test-data/PRJEB14215/41591_2018_61_MOESM3_ESM.xlsx",
    "test-data/PRJEB14215/s41591-018-0061-3.pdf",
]
OUT_DIR = "test-data/PRJEB14215"
# ENA reports 73 samples for PRJEB14215 as of this run; give real headroom
# above that instead of hardcoding 73, in case ENA adds runs before this executes.
MAX_SAMPLES_FOR_THIS_PROJECT = 150

NICHE_CASES = [
    "study_name", "subject_id", "sample_id", "target_condition", "control",
    "body_site", "sequencing_platform", "host_species",
]

_original_call_llm_api = model.call_llm_api
_captured_calls = []
_gemini_fallback_events = []
_cancel_event = asyncio.Event()


def _capturing_call_llm_api(prompt, model_name=None):
    response_text, model_instance = _original_call_llm_api(prompt, model_name)
    if model_instance is None:
        api_used = "Anthropic"
        model_id = "claude-haiku-4-5-20251001 or claude-sonnet-5"
    else:
        api_used = "Google Gemini"
        model_id = model_name or "gemini-2.5-flash-lite"
        event = {
            "call_index": len(_captured_calls) + 1,
            "prompt_preview": prompt[:500],
            "response_preview": response_text[:500],
        }
        _gemini_fallback_events.append(event)
        print("\n" + "=" * 80)
        print("!!! GEMINI FALLBACK DETECTED !!!")
        print(f"Call #{event['call_index']} did NOT use Anthropic (ANTHROPIC_API_KEY "
              f"missing/invalid/rate-limited, or another fallback trigger fired).")
        print("Setting cancel_event -- pipeline will stop before its next sample.")
        print("=" * 80 + "\n")
        _cancel_event.set()
    _captured_calls.append({
        "prompt": prompt,
        "response": response_text,
        "api": api_used,
        "model": model_id,
    })
    print(f"[capture] call_llm_api #{len(_captured_calls)} -> {api_used} / {model_id} "
          f"(prompt {len(prompt)} chars, response {len(response_text)} chars)")
    return response_text, model_instance


_checkpoint_accs_output = {}
_checkpoint_niche = list(NICHE_CASES)
_checkpoint_path = os.path.join(OUT_DIR, f"{BIOPROJECT}_output.xlsx")
_resumed_rows: list = []  # rows already completed by a prior (possibly crashed) run


def _load_checkpoint_rows(path: str) -> list:
    """Reconstruct row dicts (matching _rows_from_new_pipeline()'s output shape,
    including a rebuilt '_additional_fields' dict) from a previously-saved
    checkpoint .xlsx alone -- no separate state file needed. Lets a resumed
    run skip already-completed samples and still round-trip their full data
    (predefined fields + Pass-2 extras) into the final save_to_excel() call.
    """
    if not os.path.isfile(path):
        return []
    try:
        df1 = pd.read_excel(path, sheet_name="cMD Metadata", engine="openpyxl")
        df2 = pd.read_excel(path, sheet_name="Full Raw Attributes", engine="openpyxl")
    except Exception as exc:
        print(f"[resume] could not read checkpoint {path}: {exc}")
        return []

    sheet1_cols = set(df1.columns)
    df2_by_id = {r.get("biosample_accession"): r for _, r in df2.iterrows()}

    def _clean(v):
        return "" if (v is None or (isinstance(v, float) and pd.isna(v))) else v

    rows = []
    for _, r1 in df1.iterrows():
        sid = r1.get("biosample_accession")
        row = {k: _clean(v) for k, v in r1.to_dict().items()}
        extra = {}
        r2 = df2_by_id.get(sid)
        if r2 is not None:
            for col in df2.columns:
                if col in sheet1_cols:
                    continue
                val = _clean(r2.get(col))
                if str(val).strip():
                    extra[col] = val
        row["_additional_fields"] = extra
        rows.append(row)
    return rows


async def _progress_cb(msg):
    print(f"[progress] {msg}")
    # Incremental checkpoint: save after every completed sample so a crash
    # (e.g. the earlier unexplained process death mid-run-2, sample 16/73,
    # no traceback -- looked like an OOM or sandbox-level kill, not a Python
    # exception) doesn't throw away already-completed samples. Mirrors what
    # api.py's SSE endpoint does with the same '__partial_acc__' messages.
    # Includes any rows already resumed from a prior run's checkpoint so the
    # file always reflects the full accumulated progress, not just this
    # process's share of it.
    if isinstance(msg, dict) and "__partial_acc__" in msg:
        _checkpoint_accs_output.update(msg["__partial_data__"])
        try:
            new_rows = await _rows_from_new_pipeline(_checkpoint_accs_output, _checkpoint_niche)
            all_rows = _resumed_rows + new_rows
            if all_rows:
                save_to_excel(all_rows, "", "", _checkpoint_path, False)
                print(f"[checkpoint] saved {len(all_rows)} row(s) "
                      f"({len(_resumed_rows)} resumed + {len(new_rows)} new) -> {_checkpoint_path}")
        except Exception as exc:
            print(f"[checkpoint] failed to save intermediate progress: {exc}")
    elif isinstance(msg, dict) and "__auto_niche_cases__" in msg:
        if not NICHE_CASES:
            _checkpoint_niche[:] = msg["__auto_niche_cases__"] or []


async def main():
    run_report = {}

    # ── Step A: extract text from each uploaded file, joined like api.py's
    # /analyze does for multiple plain uploads (\n\n join, api.py:1000) ──
    ctx_parts = []
    file_reports = []
    filenames = []
    for path in UPLOAD_PATHS:
        with open(path, "rb") as f:
            raw = f.read()
        filename = os.path.basename(path)
        filenames.append(filename)
        print(f"Extracting text from {filename} ({len(raw)} bytes)...")
        text = _extract_text_from_upload(raw, filename)
        print(f"  -> {len(text)} chars extracted")
        file_reports.append({"file": path, "bytes": len(raw), "chars_extracted": len(text)})
        if text.strip():
            ctx_parts.append(text)
    user_context_text = "\n\n".join(ctx_parts)
    file_label = ", ".join(filenames)
    print(f"Combined user_context_text: {len(user_context_text)} chars from {len(filenames)} file(s)")
    run_report["file_extraction"] = file_reports

    # ── Step B: resolve PRJEB14215 -> ALL samples via ncbi_resolver.resolve_accessions() ──
    print(f"\nResolving {BIOPROJECT} via ncbi_resolver.resolve_accessions("
          f"max_samples={MAX_SAMPLES_FOR_THIS_PROJECT})...")
    accessions = ncbi_resolver.resolve_accessions(BIOPROJECT, max_samples=MAX_SAMPLES_FOR_THIS_PROJECT)
    print(f"Resolved {len(accessions)} sample(s) for {BIOPROJECT}")
    if len(accessions) >= MAX_SAMPLES_FOR_THIS_PROJECT:
        print(f"WARNING: resolved count hit the cap ({MAX_SAMPLES_FOR_THIS_PROJECT}) -- "
              f"there may be more samples than were captured. Investigate before trusting "
              f"this as 'all samples'.")
    run_report["ncbi_resolution_count"] = len(accessions)
    run_report["ncbi_resolution_keys"] = list(accessions.keys())

    # ── Step B.5: resume support -- reload any samples already completed by a
    # prior (possibly crashed) run of this script from the checkpoint .xlsx,
    # and skip re-processing them. ──
    global _resumed_rows
    _resumed_rows = _load_checkpoint_rows(_checkpoint_path)
    if _resumed_rows:
        resumed_ids = {r.get("biosample_accession") for r in _resumed_rows if r.get("biosample_accession")}
        before = len(accessions)
        accessions = {k: v for k, v in accessions.items() if k not in resumed_ids}
        print(f"[resume] loaded {len(_resumed_rows)} already-completed row(s) from "
              f"{_checkpoint_path} -- skipping them ({len(accessions)}/{before} sample(s) remain)")
        run_report["resumed_rows"] = len(_resumed_rows)

    accs_output: dict = {}
    if accessions:
        # ── Step C: monkeypatch the Anthropic/Gemini watchdog in, run the real pipeline ──
        model.call_llm_api = _capturing_call_llm_api
        try:
            print(f"\nRunning additional_pipeline.pipeline_with_gemini() for {len(accessions)} "
                  f"sample(s) of {BIOPROJECT} with niche_cases={NICHE_CASES}...")
            result = await additional_pipeline.pipeline_with_gemini(
                accessions=accessions,
                niche_cases=NICHE_CASES,
                user_context_text=user_context_text,
                user_file_label=file_label,
                standardization_urls=None,
                save_df=None,
                progress_cb=_progress_cb,
                cancel_event=_cancel_event,
            )
        finally:
            model.call_llm_api = _original_call_llm_api

        if result is not None:
            accs_output = result[0] if isinstance(result, tuple) else result
            accs_output.pop("__niche_cases__", None)
    else:
        print("[resume] all resolved samples were already completed -- nothing left to process")

    run_report["gemini_fallback_events"] = _gemini_fallback_events
    run_report["gemini_fallback_triggered"] = bool(_gemini_fallback_events)
    run_report["total_llm_calls"] = len(_captured_calls)
    run_report["anthropic_calls"] = sum(1 for c in _captured_calls if c["api"] == "Anthropic")
    run_report["gemini_calls"] = sum(1 for c in _captured_calls if c["api"] == "Google Gemini")
    run_report["samples_processed_this_run"] = len(accs_output)
    run_report["samples_resolved"] = len(accessions) + len(_resumed_rows)
    run_report["cancelled_early"] = _cancel_event.is_set()

    # ── Step D: build rows + save via the app's existing save_to_excel() ──
    new_rows = await _rows_from_new_pipeline(accs_output, NICHE_CASES) if accs_output else []
    rows = _resumed_rows + new_rows
    excel_path = os.path.join(OUT_DIR, f"{BIOPROJECT}_output.xlsx")
    if rows:
        save_to_excel(rows, "", "", excel_path, False)
        run_report["excel_path"] = excel_path
        run_report["rows_written"] = len(rows)
    else:
        print("No rows produced -- not writing Excel.")
        run_report["excel_path"] = None
        run_report["rows_written"] = 0

    meta_path = os.path.join(OUT_DIR, f"_run_meta_{BIOPROJECT}_full.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(run_report, f, indent=2, default=str)
    print(f"\nSaved run metadata: {meta_path}")

    if run_report["gemini_fallback_triggered"]:
        print("\n" + "#" * 80)
        print("# RUN HALTED EARLY (or completed with Gemini contamination):")
        print(f"# {len(_gemini_fallback_events)} call(s) fell back to Gemini.")
        print(f"# Samples processed this run before stop: {run_report['samples_processed_this_run']} "
              f"(+ {len(_resumed_rows)} resumed) / {run_report['samples_resolved']}")
        print("# See run metadata JSON for details. Reporting to user for a decision.")
        print("#" * 80)

    return run_report


if __name__ == "__main__":
    asyncio.run(main())
