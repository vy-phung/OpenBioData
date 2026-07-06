"""
Re-runs the real pipeline for the 3 samples known (from
test_output_field_assembly_investigation.md / test_output_clean_tables_fix.md)
to produce a value/explanation self-contradiction in the `disease` field, to
confirm the new deterministic negation-contradiction check added to
model.py: _extract_additional_fields() now flags all 3.

Does not modify any pipeline file (aside from the fix under test, already
applied to model.py). Uses the same real call path as run_new_context_
SAMN35361964.py: api._extract_text_from_upload() for the 2 real PDFs,
ncbi_resolver.resolve_accessions() for each accession, and
additional_pipeline.pipeline_with_gemini() with niche_cases=None (matching
additional_pipeline.py:1151's production call and the earlier
test_context_swap*.py scripts that reproduced this exact bug) -- so
`disease` comes out of Pass 2 (model._extract_additional_fields()), the
function actually patched.

Usage: python rerun_selfcheck_3samples.py
"""
import asyncio
import os

import additional_pipeline
import ncbi_resolver
from api import _extract_text_from_upload

SAMPLES = ["SAMN35361958", "SAMN35361963", "SAMN35361964"]

PDF_PATHS = [
    "test-data/PRJNA976261/FarinaR_2019.pdf",
    "test-data/PRJNA976261/Molecular Oral Microbiology - 2023 - Favale - "
    "Functional profile of oral plaque microbiome  Further insight into the.pdf",
]

CATEGORICAL_KEYWORDS = (
    "disease", "condition", "diagnosis", "periodont", "diabet",
    "control", "status", "group", "phenotype", "health",
)


def extract_pdf(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    filename = os.path.basename(path)
    print(f"Extracting text+tables from {filename} ({len(raw)} bytes)...")
    text = _extract_text_from_upload(raw, filename)
    print(f"  -> {len(text)} chars extracted for {filename}")
    return filename, text


async def run_one(acc: str, user_url_sources: dict) -> dict:
    print(f"\nResolving {acc} via ncbi_resolver.resolve_accessions()...")
    accessions = ncbi_resolver.resolve_accessions(acc)
    print(f"Running additional_pipeline.pipeline_with_gemini() for {acc} (niche_cases=None)...")
    accs_output, source_texts, big_context = await additional_pipeline.pipeline_with_gemini(
        accessions=accessions,
        niche_cases=None,
        user_url_sources=user_url_sources,
        save_df=None,
    )
    return accs_output[acc]


def find_categorical_fields(result: dict) -> dict:
    found = {}
    for field, val in result.get("_additional_fields", {}).items():
        if any(kw in field.lower() for kw in CATEGORICAL_KEYWORDS):
            found[field] = val
    return found


async def main():
    user_url_sources = {}
    for path in PDF_PATHS:
        filename, text = extract_pdf(path)
        user_url_sources[filename] = text

    report_sections = []
    summary_rows = []

    for acc in SAMPLES:
        result = await run_one(acc, user_url_sources)
        categorical = find_categorical_fields(result)

        section = [f"## {acc}", ""]
        additional = result.get("_additional_fields", {})
        if not additional:
            section.append("_(no Pass 2 fields produced)_")
        for field, val in additional.items():
            section.append(f"\n**{field}**")
            section.append(f"- value: `{val.get('value')}`")
            section.append(f"- explanation: {val.get('explanation', '')}")

        flagged = any("##SELF-CONTRADICTION" in v.get("value", "") for v in categorical.values())
        summary_rows.append({
            "acc": acc,
            "categorical": {k: v.get("value") for k, v in categorical.items()},
            "flagged": flagged,
        })
        report_sections.append("\n".join(section))

    summary_lines = [
        "## Summary\n",
        "| Accession | Categorical field(s) | ##SELF-CONTRADICTION flagged? |",
        "|---|---|---|",
    ]
    for row in summary_rows:
        cat_str = "; ".join(f"{k}={v!r}" for k, v in row["categorical"].items()) or "_(none)_"
        summary_lines.append(f"| {row['acc']} | {cat_str} | {'YES' if row['flagged'] else 'NO'} |")

    report = (
        "# Self-check re-run: 3 known self-contradiction samples\n\n"
        "Real pipeline run (`additional_pipeline.pipeline_with_gemini()`, "
        "`niche_cases=None`) for SAMN35361958, SAMN35361963, SAMN35361964 -- "
        "the same 3 samples in which `test_output_field_assembly_investigation.md` "
        "found the model's `disease` field value contradicting its own explanation.\n\n"
        + "\n".join(summary_lines)
        + "\n\n---\n\n"
        + "\n\n---\n\n".join(report_sections)
    )
    with open("test_output_selfcheck_fix.md", "w") as f:
        f.write(report)
    print("\n\nWrote test_output_selfcheck_fix.md")


if __name__ == "__main__":
    asyncio.run(main())
