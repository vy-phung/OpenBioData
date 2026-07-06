"""
Runs the REAL app pipeline end-to-end for one specific user scenario:

  accession:      SAMN35361964
  user uploads:   test-data/PRJNA976261/FarinaR_2019.pdf
                  test-data/PRJNA976261/Molecular Oral Microbiology - 2023 - Favale -
                  Functional profile of oral plaque microbiome  Further insight into the.pdf
  metadata:       study_name, subject_id, sample_id, target_condition, control,
                  body_site, sequencing_platform, host_species, age, gender
  standardization schema: none

No pipeline file is modified. Every step below calls the exact function the
production app calls for that step:

  - PDF -> text:            api._extract_text_from_upload()   (api.py's /upload-context path)
  - accession -> record:    ncbi_resolver.resolve_accessions() (what input_handler.build_pipeline_input()
                             calls internally, which is what api.py's /analyze calls)
  - multi-file join:        api.py's /analyze concatenates multiple plain-uploaded files
                             (no SOURCE_URL markers) with "\\n\\n" into one flat
                             user_context_text blob (api.py:1028-1035) -- reproduced here
                             identically instead of using per-file user_url_sources, so this
                             matches what actually happens when 2 files are uploaded together.
  - full pipeline:          additional_pipeline.pipeline_with_gemini()  (api.py:1450's _rich_pipeline)

model.call_llm_api is monkeypatched with a passthrough recorder (captures the
prompt + response + which underlying API/model answered) -- it still calls the
real function underneath, so LLM behavior is unaffected; only the raw
prompt/response text is additionally captured, in call order.

Usage: python run_SAMN35361964_single_pdf.py
"""
import asyncio
import json
import os

import additional_pipeline
import ncbi_resolver
from api import _extract_text_from_upload

import model

ACC = "SAMN35361964"
PDF_PATHS = [
    "test-data/PRJNA976261/FarinaR_2019.pdf",
    "test-data/PRJNA976261/Molecular Oral Microbiology - 2023 - Favale - "
    "Functional profile of oral plaque microbiome  Further insight into the.pdf",
]
OUT_DIR = "test-data/PRJNA976261"

NICHE_CASES = [
    "study_name", "subject_id", "sample_id", "target_condition", "control",
    "body_site", "sequencing_platform", "host_species", "age", "gender",
]

_original_call_llm_api = model.call_llm_api
_captured_calls = []


def _capturing_call_llm_api(prompt, model_name=None):
    response_text, model_instance = _original_call_llm_api(prompt, model_name)
    if model_instance is None:
        # model.call_llm_api returns (text, None) only on the Anthropic
        # success path -- the Gemini path always returns a truthy
        # GenerativeModel instance as the 2nd element.
        api_used = "Anthropic"
        model_id = "claude-haiku-4-5-20251001"
    else:
        api_used = "Google Gemini"
        model_id = model_name or "gemini-2.5-flash-lite"
    _captured_calls.append({
        "prompt": prompt,
        "response": response_text,
        "api": api_used,
        "model": model_id,
    })
    print(f"[capture] call_llm_api #{len(_captured_calls)} -> {api_used} / {model_id} "
          f"(prompt {len(prompt)} chars, response {len(response_text)} chars)")
    return response_text, model_instance


async def main():
    run_report = {}

    # ── Step A: extract text from each uploaded PDF (api._extract_text_from_upload),
    # then join them exactly like api.py's /analyze does for multiple plain uploads ──
    ctx_parts = []
    pdf_reports = []
    filenames = []
    for pdf_path in PDF_PATHS:
        with open(pdf_path, "rb") as f:
            raw = f.read()
        filename = os.path.basename(pdf_path)
        filenames.append(filename)
        print(f"Extracting text+tables from {filename} ({len(raw)} bytes)...")
        text = _extract_text_from_upload(raw, filename)
        print(f"  -> {len(text)} chars extracted")
        pdf_reports.append({"file": pdf_path, "bytes": len(raw), "chars_extracted": len(text)})
        if text.strip():
            ctx_parts.append(text)
    pdf_text = "\n\n".join(ctx_parts)
    file_label = ", ".join(filenames)
    print(f"Combined user_context_text: {len(pdf_text)} chars from {len(filenames)} file(s)")
    run_report["pdf_extraction"] = pdf_reports

    # ── Step B: resolve SAMN35361964 into its NCBI record ──
    print(f"\nResolving {ACC} via ncbi_resolver.resolve_accessions()...")
    accessions = ncbi_resolver.resolve_accessions(ACC)
    print(f"Resolved record: {accessions}")
    run_report["ncbi_resolution"] = accessions

    # ── Step C: monkeypatch the recorder in, run the real pipeline ──
    model.call_llm_api = _capturing_call_llm_api
    try:
        print(f"\nRunning additional_pipeline.pipeline_with_gemini() for {ACC} "
              f"with niche_cases={NICHE_CASES}, user_context_text=<{file_label}>...")
        accs_output, source_texts, combined_all_text = await additional_pipeline.pipeline_with_gemini(
            accessions=accessions,
            niche_cases=NICHE_CASES,
            user_context_text=pdf_text,
            user_file_label=file_label,
            standardization_urls=None,
            save_df=None,
        )
    finally:
        model.call_llm_api = _original_call_llm_api

    # ── Step D: save the combined context text (what Pass 1 + Pass 2 both use) ──
    ctx_path = os.path.join(OUT_DIR, f"contextLLM_{ACC}.txt")
    with open(ctx_path, "w", encoding="utf-8") as f:
        f.write(combined_all_text)
    print(f"\nSaved combined LLM context: {ctx_path} ({len(combined_all_text)} chars)")
    run_report["combined_context"] = {"path": ctx_path, "chars": len(combined_all_text)}
    run_report["source_text_keys"] = {k: len(str(v)) for k, v in source_texts.items()}

    # ── Step E: save each captured prompt/answer pair ──
    prompt_files = []
    for i, call in enumerate(_captured_calls, start=1):
        prompt_path = os.path.join(
            OUT_DIR, "call_llm_api_prompt.txt" if i == 1 else f"call_llm_api_prompt_{i}.txt"
        )
        answer_path = os.path.join(OUT_DIR, f"call_llm_api_prompt_{i}_answer.txt")
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(call["prompt"])
        with open(answer_path, "w", encoding="utf-8") as f:
            f.write(call["response"])
        print(f"Saved prompt #{i}: {prompt_path} ({len(call['prompt'])} chars) "
              f"-> {call['api']} / {call['model']}")
        print(f"Saved answer #{i}: {answer_path} ({len(call['response'])} chars)")
        prompt_files.append({
            "index": i, "prompt_path": prompt_path, "answer_path": answer_path,
            "api": call["api"], "model": call["model"],
            "prompt_chars": len(call["prompt"]), "response_chars": len(call["response"]),
        })
    run_report["llm_calls"] = prompt_files

    # ── Step F: dump final predefined-field + Pass-2 results for the report ──
    acc_score = accs_output[ACC]
    predefined_results = {}
    for field in NICHE_CASES:
        field_data = acc_score.get(field, {})
        predefined_results[field] = field_data
    run_report["predefined_field_results"] = predefined_results
    run_report["pass2_additional_fields"] = acc_score.get("_additional_fields", {})
    run_report["signals"] = acc_score.get("signals", {})
    run_report["file_all_output_docx"] = acc_score.get("file_all_output", "")

    meta_path = os.path.join(OUT_DIR, f"_run_meta_{ACC}_single_pdf.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(run_report, f, indent=2, default=str)
    print(f"\nSaved run metadata: {meta_path}")

    return run_report


if __name__ == "__main__":
    asyncio.run(main())
