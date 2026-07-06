"""
Runs the REAL pipeline (not a hand-reconstructed approximation) for
SAMN35361964 with two new source PDFs and a custom (non-default) set of
predefined metadata fields.

Every extraction/fetch/LLM step below calls the actual functions the
production app uses -- no new extraction/parsing logic is written here:

  - PDF text+table extraction: api._extract_text_from_upload() -- the exact
    function api.py's /upload-context endpoint calls for user-uploaded PDFs
    (PyMuPDF text, NER.PDF table extraction).
  - NCBI record + all related-accession resolution: ncbi_resolver.resolve_accessions()
    -- resolves a bare BioSample accession into its bioproject/biosample/
    genbank-accession/SRA-experiment record (all linked identifiers).
  - Full pipeline (NCBI fetch -> combined "The source - X: ..." context ->
    docx save -> LLM query with custom niche_cases): additional_pipeline.pipeline_with_gemini()
    -- the same top-level function api.py's /analyze endpoint calls.
  - The "The source - <key>: <text> -----END OF THIS SOURCE <key> ----"
    labeling comes from pipeline_with_gemini's own Step 4 context-building
    loop (additional_pipeline.py:1055-1079) -- not reproduced by hand here.

user_url_sources={filename: text} is used (not user_context_text) so each
PDF gets its OWN source key -- pipeline_with_gemini assigns
acc_score["source_texts"][key] = text directly per key
(additional_pipeline.py:586-591), which Step 4 then turns into its own
"The source - <filename>: ..." block, exactly matching the "for each file"
labeling requested.

Usage: python run_new_context_SAMN35361964.py
"""
import asyncio
import os

import additional_pipeline
import data_preprocess
import ncbi_resolver
from api import _extract_text_from_upload

ACC = "SAMN35361964"
PDF_PATHS = [
    "test-data/PRJNA976261/FarinaR_2019.pdf",
    "test-data/PRJNA976261/Molecular Oral Microbiology - 2023 - Favale - "
    "Functional profile of oral plaque microbiome  Further insight into the.pdf",
]
OUT_DOCX = f"test-data/PRJNA976261/{ACC}_new.docx"

NICHE_CASES = [
    "study_name", "subject_id", "sample_id", "target_condition",
    "control", "body_site", "sequencing_platform", "host_species",
]


def extract_pdf(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    filename = os.path.basename(path)
    print(f"Extracting text+tables from {filename} ({len(raw)} bytes) via api._extract_text_from_upload()...")
    text = _extract_text_from_upload(raw, filename)
    print(f"  -> {len(text)} chars extracted for {filename}")
    return filename, text


async def main():
    # ── Step A: extract text+tables from the 2 PDFs using the real upload-context function ──
    user_url_sources = {}
    for path in PDF_PATHS:
        filename, text = extract_pdf(path)
        user_url_sources[filename] = text

    # ── Step B: resolve SAMN35361964 into all related NCBI accession records ──
    print(f"\nResolving {ACC} via ncbi_resolver.resolve_accessions()...")
    accessions = ncbi_resolver.resolve_accessions(ACC)
    print(f"Resolved record: {accessions}")

    # ── Step C: run the real pipeline (NCBI fetch + combined context + LLM query) ──
    print(f"\nRunning additional_pipeline.pipeline_with_gemini() for {ACC} "
          f"with niche_cases={NICHE_CASES}...")
    accs_output, source_texts, big_context = await additional_pipeline.pipeline_with_gemini(
        accessions=accessions,
        niche_cases=NICHE_CASES,
        user_url_sources=user_url_sources,
        save_df=None,
    )

    # ── Step D: save the combined big_context (all sources, "The source - X: ..." format) ──
    data_preprocess.save_text_to_docx(big_context, OUT_DOCX)
    print(f"\nSaved combined context docx: {OUT_DOCX} ({len(big_context)} chars)")
    print(f"Source keys included: {list(source_texts.keys())}")

    # ── Step E: print the predefined-metadata results ──
    acc_score = accs_output[ACC]
    print("\n" + "#" * 100)
    print(f"PREDEFINED METADATA RESULTS FOR {ACC}")
    print("#" * 100)
    for field in NICHE_CASES:
        field_data = acc_score.get(field, {})
        print(f"\n=== {field} ===")
        if not field_data:
            print("  (no answer produced)")
            continue
        for answer, explanations in field_data.items():
            print(f"  answer: {answer}")
            for expl in explanations:
                print(f"    - {expl}")

    print("\n" + "#" * 100)
    print("PASS 2 ADDITIONAL FIELDS (_additional_fields)")
    print("#" * 100)
    additional = acc_score.get("_additional_fields", {})
    if not additional:
        print("  (none)")
    for field, val in additional.items():
        print(f"\n=== {field} ===")
        print(f"  value: {val.get('value')}")
        print(f"  explanation: {val.get('explanation', '')}")

    return accs_output, source_texts, big_context


if __name__ == "__main__":
    asyncio.run(main())
