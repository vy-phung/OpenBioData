"""
Runs the REAL app pipeline for the whole PRJNA976261 BioProject (fans out to
all its BioSamples) with the same 2 uploaded PDFs and 10 predefined fields
used in prior tests, builds the actual multi-sample output table exactly as
api.py/mtdna_backend.py would, and reports what metadata_merge.
normalize_output_table_with_log() does to it -- which column pairs merged,
why, and how many ##CONFLICT flags vs. clean merges resulted.

  accession:      PRJNA976261 (BioProject -- resolves to ~12 BioSamples)
  user uploads:   test-data/PRJNA976261/FarinaR_2019.pdf
                  test-data/PRJNA976261/Molecular Oral Microbiology - 2023 - Favale -
                  Functional profile of oral plaque microbiome  Further insight into the.pdf
  metadata:       study_name, subject_id, sample_id, target_condition, control,
                  body_site, sequencing_platform, host_species, age, gender
  standardization schema: none

No pipeline file's *behavior* is changed by this script -- it calls the same
real functions api.py/mtdna_backend.py call: api._extract_text_from_upload(),
ncbi_resolver.resolve_accessions(), additional_pipeline.pipeline_with_gemini(),
api._rows_from_new_pipeline(), and (for reporting) metadata_merge.
normalize_output_table_with_log() -- the same function mtdna_backend.
save_to_excel() now calls internally before writing Sheet 2.

Usage: python run_normalize_table_test.py
"""
import asyncio
import json
import os

import additional_pipeline
import ncbi_resolver
import metadata_merge
from api import _extract_text_from_upload, _rows_from_new_pipeline

ACCESSION = "PRJNA976261"
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


def _build_full_table(rows: list) -> list:
    """Reproduce mtdna_backend.save_to_excel()'s Sheet-2 ("Full Raw
    Attributes") flattening: Sheet-1 columns + one column per unique
    _additional_fields key found across all rows -- the actual "all rows,
    all columns" table normalize_output_table() is meant to run on.
    """
    seen_extra, extra_keys = set(), []
    for r in rows:
        af = r.get("_additional_fields", {}) or {}
        if not isinstance(af, dict):
            continue
        for k in af:
            if k not in seen_extra:
                seen_extra.add(k)
                extra_keys.append(k)

    full_table = []
    for r in rows:
        row = {k: v for k, v in r.items() if k != "_additional_fields"}
        af = r.get("_additional_fields", {}) or {}
        if not isinstance(af, dict):
            af = {}
        for k in extra_keys:
            row[k] = str(af.get(k, "") or "").strip()
        full_table.append(row)
    return full_table


async def main():
    ctx_parts, filenames = [], []
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

    print(f"\nResolving {ACCESSION} via ncbi_resolver.resolve_accessions()...")
    accessions = ncbi_resolver.resolve_accessions(ACCESSION)
    print(f"Resolved {len(accessions)} sample(s): {list(accessions.keys())}")

    print(f"\nRunning additional_pipeline.pipeline_with_gemini() for all "
          f"{len(accessions)} sample(s) with niche_cases={NICHE_CASES}...")
    accs_output, source_texts, combined_all_text = await additional_pipeline.pipeline_with_gemini(
        accessions=accessions,
        niche_cases=NICHE_CASES,
        user_context_text=pdf_text,
        user_file_label=file_label,
        standardization_urls=None,
        save_df=None,
    )

    rows = _rows_from_new_pipeline(accs_output, NICHE_CASES)
    print(f"\nBuilt {len(rows)} row(s) via api._rows_from_new_pipeline()")

    full_table_before = _build_full_table(rows)
    # Deep-copy for a clean "before" snapshot (normalize mutates in place)
    import copy
    full_table_before_snapshot = copy.deepcopy(full_table_before)
    columns_before = list(dict.fromkeys(k for row in full_table_before for k in row.keys()))

    full_table_after, merge_log = metadata_merge.normalize_output_table_with_log(full_table_before)
    columns_after = list(dict.fromkeys(k for row in full_table_after for k in row.keys()))

    report = {
        "accession": ACCESSION,
        "n_rows": len(rows),
        "columns_before": columns_before,
        "n_columns_before": len(columns_before),
        "columns_after": columns_after,
        "n_columns_after": len(columns_after),
        "merge_log": merge_log,
    }

    meta_path = os.path.join(OUT_DIR, "_run_meta_PRJNA976261_normalize.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "report": report,
            "full_table_before": full_table_before_snapshot,
            "full_table_after": full_table_after,
        }, f, indent=2, default=str)
    print(f"Saved: {meta_path}")

    print(f"\n{'#' * 80}\nCOLUMNS BEFORE ({len(columns_before)}): {columns_before}")
    print(f"\nCOLUMNS AFTER ({len(columns_after)}): {columns_after}")
    print(f"\nMERGE LOG ({len(merge_log)} merges):")
    for m in merge_log:
        print(f"  {m}")

    return report


if __name__ == "__main__":
    asyncio.run(main())
