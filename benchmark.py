"""
benchmark.py — Compare pipeline output against manual-curation ground truth.

Usage:
  python benchmark.py --accession SAMN35361955

Compares against:
  1. biosample_metadata.xlsx  (Sheet 1 = cMD Metadata, Sheet 2 = Full Raw Attributes)
  2. FarinaR_2019_manual_curation.tsv

Prints a field-by-field comparison table and overall match statistics.
"""
import argparse
import json
import os
import sys
import asyncio
import pandas as pd
from pathlib import Path


# ── Load ground truth ────────────────────────────────────────────────────────

def load_excel_ground_truth(accession: str):
    path = Path(__file__).parent / "biosample_metadata.xlsx"
    results = {}

    xl = pd.ExcelFile(path)

    # Sheet 1 — cMD Metadata (row 1 = header, row 2+ = data)
    df1 = xl.parse("cMD Metadata", header=1)
    df1.columns = [str(c).strip() for c in df1.columns]
    # Find the accession column
    acc_col = next((c for c in df1.columns if c in ("sample_id", "biosample_accession")), None)
    if acc_col:
        row = df1[df1[acc_col].astype(str).str.strip() == accession]
        if not row.empty:
            results["cMD_Metadata"] = row.iloc[0].dropna().to_dict()

    # Sheet 2 — Full Raw Attributes
    df2 = xl.parse("Full Raw Attributes")
    df2.columns = [str(c).strip() for c in df2.columns]
    acc_col2 = next((c for c in df2.columns if "biosample" in c.lower() or "accession" in c.lower()), None)
    if acc_col2:
        row2 = df2[df2[acc_col2].astype(str).str.strip() == accession]
        if not row2.empty:
            results["Full_Raw_Attributes"] = row2.iloc[0].dropna().to_dict()

    return results


def load_tsv_ground_truth(accession: str):
    path = Path(__file__).parent / "FarinaR_2019_manual_curation.tsv"
    df = pd.read_csv(path, sep="\t")
    df.columns = [str(c).strip() for c in df.columns]
    acc_col = next(
        (c for c in df.columns if c.lower() in ("biosample", "ncbi_accession", "sample_id")),
        None
    )
    if acc_col:
        row = df[df[acc_col].astype(str).str.strip() == accession]
        if not row.empty:
            return row.iloc[0].dropna().to_dict()
    return {}


# ── Compare ──────────────────────────────────────────────────────────────────

def _normalize(v):
    if v is None:
        return ""
    s = str(v).strip().lower()
    # common synonyms
    s = s.replace("false", "no").replace("true", "yes").replace("case", "no")
    s = s.replace("t2d;periodontitis", "type 2 diabetes and/or periodontitis")
    return s


def compare_outputs(pipeline_row: dict, ground_truth: dict, label: str):
    """Print field-by-field comparison."""
    print(f"\n{'='*60}")
    print(f" Benchmark vs: {label}")
    print(f"{'='*60}")
    print(f"{'Field':<35} {'Pipeline':<30} {'GT':<30} Match")
    print(f"{'-'*35} {'-'*30} {'-'*30} -----")

    matches = 0
    total = 0
    for field, gt_val in sorted(ground_truth.items()):
        if field.startswith("_") or pd.isna(gt_val):
            continue
        pred_val = pipeline_row.get(field, "")
        norm_pred = _normalize(pred_val)
        norm_gt   = _normalize(gt_val)
        matched = norm_gt in norm_pred or norm_pred in norm_gt or norm_pred == norm_gt
        symbol = "✅" if matched else "❌"
        print(f"{field:<35} {str(pred_val)[:28]:<30} {str(gt_val)[:28]:<30} {symbol}")
        total += 1
        if matched:
            matches += 1

    print(f"\n  {matches}/{total} fields matched ({100*matches//total if total else 0}%)")
    return matches, total


# ── Run pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(accession: str, metadata_fields: list, std_urls: list = None):
    from input_handler import build_pipeline_input
    from additional_pipeline import pipeline_with_gemini

    resolved_dict, _ = build_pipeline_input(accession, max_samples=1)
    if not resolved_dict:
        print(f"Could not resolve {accession} via NCBI")
        return {}

    result = await pipeline_with_gemini(
        resolved_dict,
        niche_cases=metadata_fields,
        standardization_urls=std_urls,
        user_context_text=None,
    )
    accs_output = result[0] if isinstance(result, tuple) else result
    return accs_output


def flatten_pipeline_row(accs_output: dict, accession: str, niche_fields: list) -> dict:
    """Turn pipeline output into a flat dict for comparison."""
    data = accs_output.get(accession, {})
    row = {"biosample_accession": accession}
    for field in niche_fields:
        field_data = data.get(field, {}) or {}
        if isinstance(field_data, dict) and field_data:
            answers = [k for k in field_data if k]
            row[field] = "; ".join(answers) if answers else "unknown"
        else:
            row[field] = "unknown"
    # Additional Pass 2 fields
    extra = data.get("_additional_fields", {}) or {}
    row.update(extra)
    return row


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Benchmark pipeline against ground truth")
    parser.add_argument("--accession", default="SAMN35361955")
    parser.add_argument("--fields", default="disease_status,subject_id,sample_id,control")
    parser.add_argument("--std-urls", default="", help="Comma-separated standardization CSV URLs")
    parser.add_argument("--skip-pipeline", action="store_true",
                        help="Skip pipeline run; only show ground truth")
    args = parser.parse_args()

    accession = args.accession
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    std_urls = [u.strip() for u in args.std_urls.split(",") if u.strip()]

    print(f"\n{'#'*60}")
    print(f"  BENCHMARK: {accession}")
    print(f"  Fields:    {', '.join(fields)}")
    print(f"{'#'*60}")

    # Load ground truth
    excel_gt = load_excel_ground_truth(accession)
    tsv_gt   = load_tsv_ground_truth(accession)

    if not excel_gt and not tsv_gt:
        print(f"⚠  {accession} not found in either benchmark file.")
        return

    # Print ground truth
    print("\n── Ground Truth (biosample_metadata.xlsx) ──")
    for sheet, gt in excel_gt.items():
        print(f"\n  [{sheet}]")
        for k, v in gt.items():
            if not str(k).startswith("Unnamed"):
                print(f"    {k}: {v}")

    print("\n── Ground Truth (FarinaR_2019_manual_curation.tsv) ──")
    for k, v in tsv_gt.items():
        print(f"    {k}: {v}")

    if args.skip_pipeline:
        return

    # Run pipeline
    print(f"\n── Running pipeline on {accession} ... ──")
    try:
        accs_output = asyncio.run(run_pipeline(accession, fields, std_urls))
    except Exception as exc:
        print(f"Pipeline error: {exc}")
        import traceback; traceback.print_exc()
        return

    pipeline_row = flatten_pipeline_row(accs_output, accession, fields)
    print(f"\nPipeline output: {json.dumps(pipeline_row, indent=2, default=str)}")

    # Compare
    total_m, total_t = 0, 0
    for sheet, gt in excel_gt.items():
        m, t = compare_outputs(pipeline_row, gt, f"Excel/{sheet}")
        total_m += m; total_t += t

    if tsv_gt:
        m, t = compare_outputs(pipeline_row, tsv_gt, "FarinaR_2019_manual_curation.tsv")
        total_m += m; total_t += t

    print(f"\n{'='*60}")
    print(f"  OVERALL: {total_m}/{total_t} fields matched across all benchmarks")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
