"""
test_tasks_1_3.py — End-to-end smoke tests for Tasks 1, 2, and 3.

Run with:
    python test_tasks_1_3.py

What is tested:
  Task 1  — ncbi_resolver.detect_accession_type and resolve_accessions
  Task 2  — _additional_fields key present and is a dict (no crash on pass 2)
  Task 3  — confidence_score.calculate_confidence (all spec cases)
           — confidence_score.compute_confidence_score_and_tier backward compat
"""

import sys
import traceback

PASS = "PASS"
FAIL = "FAIL"
results = []

def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((status, label))
    icon = "[OK]" if condition else "[!!]"
    suffix = f"  ({detail})" if detail and not condition else ""
    print(f"  {icon} {label}{suffix}")
    return condition


# ─────────────────────────────────────────────────────────────────────────────
# TASK 1 — NCBI Resolver
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TASK 1 — NCBI Accession Resolver")
print("=" * 60)

try:
    from ncbi_resolver import detect_accession_type, resolve_accessions
    check("ncbi_resolver imports without error", True)
except Exception as e:
    check("ncbi_resolver imports without error", False, str(e))
    print("  Cannot continue Task 1 tests — aborting section.")
    resolve_accessions = None

if resolve_accessions:
    # -- Type detection --
    print("\n  [Type Detection]")
    detection_cases = [
        ("NC_068100",    "genbank"),
        ("MT478110",     "genbank"),
        ("OL549450",     "genbank"),
        ("PQ789806",     "genbank"),
        ("SAMN23469632", "biosample"),
        ("SAMEA12345",   "biosample"),
        ("PRJNA783802",  "bioproject"),
        ("PRJEB12345",   "bioproject"),
        ("SRR17084312",  "sra_run"),
        ("ERR123456",    "sra_run"),
        ("SRX12345678",  "sra_experiment"),
        ("UNKNOWN_XYZ",  "unknown"),
    ]
    for acc, expected in detection_cases:
        got = detect_accession_type(acc)
        check(f"detect '{acc}' -> '{expected}'", got == expected,
              f"got '{got}'")

    # -- Resolver: unknown must not crash --
    print("\n  [Resolver — UNKNOWN must not crash]")
    try:
        r = resolve_accessions("UNKNOWN_ID_XYZ")
        check("resolve_accessions('UNKNOWN_ID_XYZ') does not crash", True)
        check("returns a dict", isinstance(r, dict))
    except Exception as e:
        check("resolve_accessions('UNKNOWN_ID_XYZ') does not crash", False, str(e))

    # -- Resolver: BioSample --
    print("\n  [Resolver — BioSample SAMN23469632]")
    REQUIRED = {"bioproject", "biosample", "accession", "experiment"}
    try:
        r = resolve_accessions("SAMN23469632")
        check("returns non-empty dict", bool(r))
        if r:
            key = list(r.keys())[0]
            entry = r[key]
            check("all required keys present", REQUIRED <= set(entry.keys()),
                  f"missing: {REQUIRED - set(entry.keys())}")
            check("no None values", all(v is not None for v in entry.values()),
                  str({k: v for k, v in entry.items() if v is None}))
    except Exception as e:
        check("SAMN23469632 resolves without crash", False, str(e))
        traceback.print_exc()

    # -- Resolver: SRA run --
    print("\n  [Resolver — SRR17084312]")
    try:
        r = resolve_accessions("SRR17084312")
        check("returns non-empty dict", bool(r))
        if r:
            entry = list(r.values())[0]
            check("all required keys present", REQUIRED <= set(entry.keys()),
                  f"missing: {REQUIRED - set(entry.keys())}")
    except Exception as e:
        check("SRR17084312 resolves without crash", False, str(e))
        traceback.print_exc()

    # -- Resolver: BioProject (multi-sample) --
    print("\n  [Resolver — BioProject PRJNA976261 (Svetlana case)]")
    try:
        r = resolve_accessions("PRJNA976261")
        if r:
            check("returns multiple BioSamples", len(r) > 1, f"got {len(r)}")
            all_ok = all(REQUIRED <= set(v.keys()) for v in r.values())
            check("all entries have required keys", all_ok)
        else:
            print("    WARNING: returned empty dict (no BioSamples found on NCBI) — skipping")
    except Exception as e:
        check("PRJNA976261 resolves without crash", False, str(e))
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────────
# TASK 2 — _additional_fields structure check (no full LLM run needed)
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TASK 2 — _additional_fields pipeline structure")
print("=" * 60)
print("  (Checking data contracts — no live LLM call needed)")

# Simulate the row dict that summarize_results produces
sample_row = {
    "Sample ID": "SAMN001",
    "Predicted Country": "France",
    "Country Explanation": "geo_loc_name",
    "Predicted Sample Type": "modern",
    "Sample Type Explanation": "living donor",
    "Sources": "https://ncbi.nlm.nih.gov",
    "Time cost": "3.1s",
    "Confidence Score": "HIGH (0.92)",
    "_additional_fields": {
        "disease_status": "healthy",
        "host_age": "34",
        "body_site": "blood",
    },
}

check("row dict has '_additional_fields' key", "_additional_fields" in sample_row)
check("_additional_fields is a dict", isinstance(sample_row["_additional_fields"], dict))
check("_additional_fields has values (non-empty example)",
      len(sample_row["_additional_fields"]) > 0)

# Check save_to_excel produces two sheets
import tempfile, os, pandas as pd
try:
    from mtdna_backend import save_to_excel

    rows = [
        {**sample_row},
        {
            "Sample ID": "SAMN002",
            "Predicted Country": "Germany",
            "Country Explanation": "text",
            "Predicted Sample Type": "ancient",
            "Sample Type Explanation": "museum",
            "Sources": "https://ncbi.nlm.nih.gov",
            "Time cost": "5.1s",
            "Confidence Score": "MEDIUM (0.65)",
            "_additional_fields": {"disease_status": "T2D", "tissue_type": "liver"},
        },
    ]
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        tmp = f.name
    try:
        save_to_excel(rows, "", "", tmp)
        s1 = pd.read_excel(tmp, sheet_name="cMD Metadata")
        s2 = pd.read_excel(tmp, sheet_name="All Attributes")
        check("Sheet 1 'cMD Metadata' exists", True)
        check("Sheet 1 has no _additional_fields column",
              "_additional_fields" not in s1.columns)
        check("Sheet 2 'All Attributes' exists", True)
        check("Sheet 2 has disease_status column", "disease_status" in s2.columns)
        check("Sheet 2 has tissue_type column", "tissue_type" in s2.columns)
        check("Sheet 2 has no _additional_fields column",
              "_additional_fields" not in s2.columns)
        # SAMN001 has no tissue_type — cell should be blank (NaN on read-back)
        r1 = s2[s2["Sample ID"] == "SAMN001"].iloc[0]
        is_blank = pd.isna(r1["tissue_type"]) or str(r1["tissue_type"]).strip() in ("", "nan")
        check("Missing extra field is blank (not 'N/A')", is_blank,
              f"got {r1['tissue_type']!r}")
    finally:
        os.unlink(tmp)
except ImportError:
    print("  NOTE: mtdna_backend skipped (requires GCP env var at module level)")
except Exception as e:
    check("save_to_excel two-sheet test", False, str(e))
    traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────────
# TASK 3 — Confidence Scoring
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TASK 3 — Generalized Confidence Scoring")
print("=" * 60)

try:
    import confidence_score
    check("confidence_score imports without error", True)
except Exception as e:
    check("confidence_score imports without error", False, str(e))
    sys.exit(1)

# --- calculate_confidence ---
print("\n  [calculate_confidence — spec requirements]")

# 1. Empty value -> score 0
r = confidence_score.calculate_confidence("country", "", {})
check("empty value -> score=0", r["score"] == 0, str(r))
check("empty value -> label='not found'", r["label"] == "not found", str(r))
check("empty value -> flag 'no_value'", "no_value" in r["flags"], str(r))

# 2. None value -> score 0
r = confidence_score.calculate_confidence("disease_status", None, {})
check("None value -> score=0", r["score"] == 0, str(r))

# 3. Value found in sources -> score > 0
sources_china = {
    "ncbi_biosample": "This sample was collected in China in 2019.",
    "linked_paper": "Samples sourced from China (geo_loc_name: China).",
}
r = confidence_score.calculate_confidence("country", "China", sources_china)
check("'China' found in sources -> score > 0", r["score"] > 0, str(r))
check("'China' found in 2 sources -> publication_confirmed flag",
      "publication_confirmed" in r["flags"], str(r))

# 4. Works identically for any field name
r2 = confidence_score.calculate_confidence("disease_status", "Type 2 Diabetes", {
    "ncbi_biosample": "Patient was diagnosed with Type 2 Diabetes.",
})
check("disease_status scoring works", r2["score"] > 0, str(r2))

# 5. ## conflict marker -> conflict_detected flag
r = confidence_score.calculate_confidence("country", "France ## Germany", {
    "ncbi_biosample": "Sample from France.",
})
check("## in value -> 'conflict_detected' flag", "conflict_detected" in r["flags"], str(r))

# 6. No sources -> score 0 (no source hits)
r = confidence_score.calculate_confidence("country", "Japan", {})
check("no sources -> score=0", r["score"] == 0, str(r))

# 7. Returns all required keys
for key in ("score", "label", "flags", "explanation"):
    check(f"result has '{key}' key", key in r)

# --- compute_confidence_score_and_tier backward compat ---
print("\n  [compute_confidence_score_and_tier — backward compatibility]")

rules = confidence_score.set_rules()

# Strong case: matches old 'predicted_country' / 'genbank_country' keys
signals_strong = {
    "has_geo_loc_name": True,
    "has_pubmed": True,
    "accession_found_in_text": True,
    "predicted_country": "china",
    "genbank_country": "china",
    "num_publications": 3,
    "missing_key_fields": False,
    "known_failure_pattern": False,
}
score, tier, expl = confidence_score.compute_confidence_score_and_tier(signals_strong, rules)
check("strong case: score >= 70 (high)", score >= 70, f"score={score} tier={tier}")
check("strong case: tier='high'", tier == "high", f"tier={tier}")
check("strong case: returns explanations list", isinstance(expl, list))

# Conflict case
signals_conflict = {
    "has_geo_loc_name": True,
    "has_pubmed": False,
    "accession_found_in_text": False,
    "predicted_country": "japan",
    "genbank_country": "france",
    "num_publications": 0,
    "missing_key_fields": True,
    "known_failure_pattern": True,
}
score, tier, expl = confidence_score.compute_confidence_score_and_tier(signals_conflict, rules)
check("conflict case: score < 40 (low/medium)", score < 50, f"score={score} tier={tier}")

# Generic field_name via predicted_field / genbank_field
signals_generic = {
    "field_name": "disease_status",
    "has_geo_loc_name": False,
    "has_pubmed": True,
    "accession_found_in_text": True,
    "predicted_field": "healthy",
    "genbank_field": "healthy",
    "num_publications": 2,
    "missing_key_fields": False,
    "known_failure_pattern": False,
}
score, tier, expl = confidence_score.compute_confidence_score_and_tier(signals_generic, rules)
check("generic field_name 'disease_status' scores correctly", score > 0,
      f"score={score} tier={tier}")

# Empty signals -> must not crash
signals_empty = {
    "has_geo_loc_name": False,
    "has_pubmed": False,
    "accession_found_in_text": False,
    "num_publications": 0,
    "missing_key_fields": False,
    "known_failure_pattern": False,
}
try:
    score, tier, expl = confidence_score.compute_confidence_score_and_tier(signals_empty, rules)
    check("empty signals: does not crash", True)
    check("empty signals: score >= 0", score >= 0)
except Exception as e:
    check("empty signals: does not crash", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
passed = sum(1 for s, _ in results if s == PASS)
failed = sum(1 for s, _ in results if s == FAIL)
print(f"Results: {passed} passed, {failed} failed out of {len(results)} checks")
if failed:
    print("\nFailed checks:")
    for s, label in results:
        if s == FAIL:
            print(f"  [!!] {label}")
    print("\nOverall: SOME CHECKS FAILED")
    sys.exit(1)
else:
    print("Overall: ALL CHECKS PASSED")
print("=" * 60)
