"""
Runs the REAL pipeline for SAMN35361964 with the user-uploaded PDF
(FarinaR_2019.pdf), following the exact same code path api.py's
/upload-context + /analyze endpoints use, but stops BEFORE any LLM call.

No new extraction/parsing/merging logic is written here -- every step calls
the actual production function:

  - PDF text+table extraction: api._extract_text_from_upload()
  - NCBI accession resolution: ncbi_resolver.resolve_accessions()
  - Full context pipeline (NCBI/ENA fetch, DOI/web-search fetch, combined
    "The source - X: ..." context build, local DOCX save, Google Drive
    upload): additional_pipeline.pipeline_with_gemini()

To stop before the LLM is actually called, model.query_document_info is
monkeypatched to raise immediately after recording the exact `prompts` dict
it was given (prompts[acc] IS context_for_llm, verbatim, per model.py:1267).
additional_pipeline.py already wraps this call in try/except (see
additional_pipeline.py:1146-1163), so the raise is caught there, the
partial acc_score is stored, and the loop ends normally -- nothing in
model.py's actual body ever runs.

Usage: python trace_context_for_llm_SAMN35361964.py
"""
import asyncio
import io
import json
import os
import sys

import additional_pipeline
import model
import ncbi_resolver
from api import _extract_text_from_upload

ACC = "SAMN35361964"
PDF_PATH = "test-data/PRJNA976261/FarinaR_2019.pdf"
CONTEXT_OUT = "test-data/PRJNA976261/contextLLM_SAMN35361964.txt"
LOG_OUT = "test-data/PRJNA976261/_run_log_SAMN35361964.txt"
META_OUT = "test-data/PRJNA976261/_run_meta_SAMN35361964.json"

_captured_prompts = {}


async def _stub_query_document_info(*args, **kwargs):
    _captured_prompts.update(kwargs.get("prompts") or {})
    raise RuntimeError(
        "STOPPED-BEFORE-LLM: model.query_document_info replaced with a stub "
        "per user request -- no LLM API call was made."
    )


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, s):
        for st in self._streams:
            st.write(s)

    def flush(self):
        for st in self._streams:
            st.flush()


async def main():
    log_buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = _Tee(real_stdout, log_buf)

    try:
        # ── Step A: extract text+tables from the uploaded PDF ──────────────
        with open(PDF_PATH, "rb") as f:
            raw = f.read()
        filename = os.path.basename(PDF_PATH)
        print(f"[A] Extracting text+tables from {filename} ({len(raw)} bytes) "
              f"via api._extract_text_from_upload()...")
        pdf_text = _extract_text_from_upload(raw, filename)
        print(f"[A] -> {len(pdf_text)} chars extracted for {filename}")

        # ── Step B: resolve SAMN35361964 into its NCBI record ──────────────
        print(f"\n[B] Resolving {ACC} via ncbi_resolver.resolve_accessions()...")
        accessions = ncbi_resolver.resolve_accessions(ACC)
        print(f"[B] Resolved record: {accessions}")

        # ── Step C: monkeypatch the LLM entry point so nothing in model.py's
        #     real body (and no Anthropic/Gemini API call) ever executes ──
        model.query_document_info = _stub_query_document_info

        per_accession_context = {ACC: pdf_text}

        print(f"\n[C] Running additional_pipeline.pipeline_with_gemini() for {ACC} "
              f"(niche_cases=None -> default country/location classification)...")
        accs_output, source_texts, combined_text = await additional_pipeline.pipeline_with_gemini(
            accessions=accessions,
            niche_cases=None,
            per_accession_context=per_accession_context,
            save_df=None,
        )

        # ── Step D: persist context_for_llm and run metadata ───────────────
        with open(CONTEXT_OUT, "w", encoding="utf-8") as f:
            f.write(combined_text)
        print(f"\n[D] Saved combined context_for_llm ({len(combined_text)} chars) -> {CONTEXT_OUT}")

        print("\n[D] Source keys and lengths (in acc_score['source_texts']):")
        source_lengths = {}
        for k, v in source_texts.items():
            v_str = v if isinstance(v, str) else str(v)
            source_lengths[k] = len(v_str)
            print(f"    {k}: {len(v_str)} chars")

        acc_score = accs_output.get(ACC, {})
        meta = {
            "accession": ACC,
            "resolved_accessions_record": accessions,
            "pdf_chars_extracted": len(pdf_text),
            "source_text_keys_in_order": list(source_texts.keys()),
            "source_text_lengths": source_lengths,
            "combined_context_for_llm_chars": len(combined_text),
            "local_docx_path": acc_score.get("file_all_output", ""),
            "signals": acc_score.get("signals", {}),
            "captured_llm_prompts_keys": list(_captured_prompts.keys()),
            "captured_llm_prompt_matches_combined_text": (
                _captured_prompts.get(ACC) == combined_text
            ),
        }
        with open(META_OUT, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, default=str)
        print(f"[D] Saved run metadata -> {META_OUT}")

    finally:
        sys.stdout = real_stdout
        with open(LOG_OUT, "w", encoding="utf-8") as f:
            f.write(log_buf.getvalue())


if __name__ == "__main__":
    asyncio.run(main())
