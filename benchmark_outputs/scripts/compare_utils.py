"""Shared field-comparison scoring for the accession benchmark.

Scoring rule (documented in BENCHMARK_REPORT.txt):
  match    - normalized extracted value equals, or is a clean substring/
             superset of, the normalized ground-truth value (case-insensitive).
             Also counted when both ground truth and extraction are
             empty/"unknown" (correctly abstaining matches a truly unknown
             ground truth).
  partial  - meaningful token overlap but not a clean match (e.g. one word of
             a multi-word ethnicity/location matches).
  mismatch - extraction produced a value that contradicts ground truth.
  missing  - ground truth has a real (non-unknown) value but extraction
             produced nothing.
"""
import csv
import json
import os
import re

UNKNOWN_TOKENS = {"", "unknown", "n/a", "na", "none", "not specified", "not stated"}


def normalize(v) -> str:
    if v is None:
        return ""
    v = str(v).strip().lower()
    v = v.replace("–", "-").replace("—", "-")  # en/em dash -> hyphen
    v = re.sub(r"\s+", " ", v)
    v = re.sub(r"(\d)\s+([a-z°])", r"\1\2", v)  # "820 m" == "820m", "28 °c" == "28°c"
    v = re.sub(r"[°]", "", v)  # degree symbol optional
    return v


def is_unknown(v: str) -> bool:
    return normalize(v) in UNKNOWN_TOKENS


def score_field(extracted, truth) -> tuple:
    """Returns (verdict, note)."""
    e, t = normalize(extracted), normalize(truth)
    if is_unknown(t):
        if is_unknown(e):
            return "match", "both unknown/not stated"
        return "partial", f"ground truth is unknown; extraction offered '{extracted}'"
    if is_unknown(e):
        return "missing", f"ground truth='{truth}'; extraction produced nothing"
    if e == t:
        return "match", "exact match"
    if e in t or t in e:
        return "match", "substring match"
    # token overlap for multi-word values (e.g. "Bru (Brao)" vs "brao")
    e_tokens = set(re.findall(r"[a-z0-9]+", e))
    t_tokens = set(re.findall(r"[a-z0-9]+", t))
    if e_tokens & t_tokens:
        return "partial", f"partial token overlap ({e_tokens & t_tokens})"
    return "mismatch", f"extracted='{extracted}' vs truth='{truth}'"


def write_comparison_report(acc_dir: str, accession: str, benchmark_type: str,
                             benchmark_file: str, known_issue: str, field_results: dict,
                             special_checks: list = None) -> dict:
    scored = [f for f in field_results.values() if f["verdict"] in ("match", "partial", "mismatch", "missing")]
    n = len(scored) or 1
    weight = {"match": 1.0, "partial": 0.5, "mismatch": 0.0, "missing": 0.0}
    accuracy = sum(weight[f["verdict"]] for f in scored) / n * 100
    report = {
        "accession": accession,
        "benchmark_type": benchmark_type,
        "benchmark_file": benchmark_file,
        "known_issue": known_issue,
        "field_results": field_results,
        "accuracy_pct": round(accuracy, 1),
        "special_checks": special_checks or [],
    }
    os.makedirs(acc_dir, exist_ok=True)
    with open(os.path.join(acc_dir, "comparison_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


CSV_HEADER = ["accession", "benchmark_type", "fields_checked", "matches", "partial",
              "mismatches", "missing", "accuracy_pct", "known_issue_status"]


def append_consolidated_row(csv_path: str, accession: str, benchmark_type: str,
                             field_results: dict, accuracy_pct: float, known_issue_status: str):
    """Idempotent: re-running a row's comparison replaces its prior line instead
    of duplicating it, so scoring-rule fixes can be safely recomputed."""
    counts = {"match": 0, "partial": 0, "mismatch": 0, "missing": 0}
    for f in field_results.values():
        if f["verdict"] in counts:
            counts[f["verdict"]] += 1
    new_row = [
        accession, benchmark_type, sum(counts.values()), counts["match"],
        counts["partial"], counts["mismatch"], counts["missing"],
        round(accuracy_pct, 1), known_issue_status,
    ]
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    existing_rows = []
    if os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            existing_rows = [r for r in reader if r and r[0] != accession]
    existing_rows.append(new_row)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        w.writerows(existing_rows)
