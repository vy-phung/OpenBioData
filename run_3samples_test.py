"""
Runs the REAL app pipeline end-to-end for three samples that all share the
same two uploaded PDFs and the same 10 requested metadata fields, to verify
the reasoning-before-answer reorder (+ restored schema_hint) in model.py:

  accessions:     SAMN35361964, SAMN35361965, SAMN35361966
  user uploads:   test-data/PRJNA976261/FarinaR_2019.pdf
                  test-data/PRJNA976261/Molecular Oral Microbiology - 2023 - Favale -
                  Functional profile of oral plaque microbiome  Further insight into the.pdf
  metadata:       study_name, subject_id, sample_id, target_condition, control,
                  body_site, sequencing_platform, host_species, age, gender
  standardization schema: none (falls back to the built-in default schema)

No pipeline file is modified by this script. Same real functions as the
single-sample runner: api._extract_text_from_upload(), ncbi_resolver.resolve_accessions(),
additional_pipeline.pipeline_with_gemini(). model.call_llm_api is monkeypatched with a
passthrough recorder per accession (captures prompt + response + which API/model
answered; still calls the real function underneath).

Usage: python run_3samples_test.py
"""
import asyncio
import json
import os

import additional_pipeline
import ncbi_resolver
from api import _extract_text_from_upload

import model

ACCESSIONS = ["SAMN35361964", "SAMN35361965", "SAMN35361966"]
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
_captured_calls = []  # reset per accession


def _capturing_call_llm_api(prompt, model_name=None):
    response_text, model_instance = _original_call_llm_api(prompt, model_name)
    if model_instance is None:
        api_used, model_id = "Anthropic", "claude-haiku-4-5-20251001"
    else:
        api_used, model_id = "Google Gemini", (model_name or "gemini-2.5-flash-lite")
    _captured_calls.append({"prompt": prompt, "response": response_text, "api": api_used, "model": model_id})
    print(f"[capture] call_llm_api #{len(_captured_calls)} -> {api_used} / {model_id} "
          f"(prompt {len(prompt)} chars, response {len(response_text)} chars)")
    return response_text, model_instance


def _prompt_filename(acc: str, index: int) -> str:
    if acc == "SAMN35361964":
        return "call_llm_api_prompt.txt" if index == 1 else f"call_llm_api_prompt_{index}.txt"
    return f"call_llm_api_prompt_{acc}.txt" if index == 1 else f"call_llm_api_prompt_{acc}_{index}.txt"


def _answer_filename(acc: str, index: int) -> str:
    if acc == "SAMN35361964":
        return f"call_llm_api_prompt_{index}_answer.txt"
    return f"call_llm_api_prompt_{acc}_{index}_answer.txt"


def _context_filename(acc: str) -> str:
    return f"contextLLM_{acc}.txt"


async def process_accession(acc: str, pdf_text: str, file_label: str) -> dict:
    global _captured_calls
    _captured_calls = []
    run_report = {"accession": acc}

    print(f"\n{'=' * 80}\nResolving {acc} via ncbi_resolver.resolve_accessions()...")
    accessions = ncbi_resolver.resolve_accessions(acc)
    print(f"Resolved record: {accessions}")
    run_report["ncbi_resolution"] = accessions

    model.call_llm_api = _capturing_call_llm_api
    try:
        print(f"Running additional_pipeline.pipeline_with_gemini() for {acc} "
              f"with niche_cases={NICHE_CASES}...")
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

    ctx_path = os.path.join(OUT_DIR, _context_filename(acc))
    with open(ctx_path, "w", encoding="utf-8") as f:
        f.write(combined_all_text)
    print(f"Saved combined LLM context: {ctx_path} ({len(combined_all_text)} chars)")
    run_report["combined_context"] = {"path": ctx_path, "chars": len(combined_all_text)}
    run_report["source_text_keys"] = {k: len(str(v)) for k, v in source_texts.items()}

    llm_calls = []
    for i, call in enumerate(_captured_calls, start=1):
        prompt_path = os.path.join(OUT_DIR, _prompt_filename(acc, i))
        answer_path = os.path.join(OUT_DIR, _answer_filename(acc, i))
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(call["prompt"])
        with open(answer_path, "w", encoding="utf-8") as f:
            f.write(call["response"])
        print(f"Saved prompt #{i}: {prompt_path} ({len(call['prompt'])} chars) -> {call['api']} / {call['model']}")
        print(f"Saved answer #{i}: {answer_path} ({len(call['response'])} chars)")
        llm_calls.append({
            "index": i, "prompt_path": prompt_path, "answer_path": answer_path,
            "api": call["api"], "model": call["model"],
            "prompt_chars": len(call["prompt"]), "response_chars": len(call["response"]),
        })
    run_report["llm_calls"] = llm_calls

    acc_score = accs_output[acc]
    run_report["predefined_field_results"] = {
        field: acc_score.get(field, {}) for field in NICHE_CASES
    }
    run_report["pass2_additional_fields"] = acc_score.get("_additional_fields", {})
    run_report["signals"] = acc_score.get("signals", {})

    meta_path = os.path.join(OUT_DIR, f"_run_meta_{acc}_3samples.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(run_report, f, indent=2, default=str)
    print(f"Saved run metadata: {meta_path}")

    return run_report


async def main():
    ctx_parts = []
    filenames = []
    for pdf_path in PDF_PATHS:
        with open(pdf_path, "rb") as f:
            raw = f.read()
        filename = os.path.basename(pdf_path)
        filenames.append(filename)
        print(f"Extracting text+tables from {filename} ({len(raw)} bytes)...")
        text = _extract_text_from_upload(raw, filename)
        print(f"  -> {len(text)} chars extracted")
        if text.strip():
            ctx_parts.append(text)
    pdf_text = "\n\n".join(ctx_parts)
    file_label = ", ".join(filenames)
    print(f"Combined user_context_text: {len(pdf_text)} chars from {len(filenames)} file(s)")

    all_reports = {}
    for acc in ACCESSIONS:
        all_reports[acc] = await process_accession(acc, pdf_text, file_label)

    print(f"\n{'#' * 80}\nSUMMARY\n{'#' * 80}")
    for acc, report in all_reports.items():
        print(f"\n{acc}:")
        for field in NICHE_CASES:
            data = report["predefined_field_results"].get(field, {})
            if data:
                answer = list(data.keys())[0]
                print(f"  {field}: {answer}")
            else:
                print(f"  {field}: (no answer)")

    return all_reports


if __name__ == "__main__":
    asyncio.run(main())
