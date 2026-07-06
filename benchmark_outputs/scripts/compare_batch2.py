import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare_utils import score_field, write_comparison_report, append_consolidated_row

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSOLIDATED_CSV = os.path.join(ROOT, "reports", "consolidated_results.csv")

MARKO_FIELDS = ["taxonomic name", "country", "province", "collection_date", "sex", "elevation", "associated taxa"]
OUCRU_FIELDS = ["country", "sample_type(modern/ancient)", "ethnicity", "province/city"]

# Ground truth from test-data/marko_output/Marko_outputs.xlsx, Sheet1
MARKO_TRUTH = {
    "OL757400": {
        "taxonomic name": "janus sp. 2 gyn-2021a",  # first/primary NCBI-sourced name; alt names noted below
        "country": "china", "province": "zhejiang province",
        "collection_date": "2021-04-12", "sex": "female",
        "elevation": "820m", "associated taxa": "severe acute respiratory syndrome coronavirus 2",
    },
    "OL757401": {
        "taxonomic name": "janus sp. 1 gyn-2021a",
        "country": "china", "province": "hunan province",
        "collection_date": "2021-04-27", "sex": "female",
        "elevation": "569m", "associated taxa": "severe acute respiratory syndrome coronavirus 2",
    },
}

# Ground truth from test-data/oucru_output/dr_duc_edge_case_output.xlsx ("Actual *" columns)
OUCRU_TRUTH = {
    "DQ834260": {"country": "Vietnam", "sample_type(modern/ancient)": "unknown",
                 "ethnicity": "Kinh", "province/city": "Vietnam"},
    "DQ834259": {"country": "Vietnam", "sample_type(modern/ancient)": "unknown",
                 "ethnicity": "Kinh", "province/city": "Vietnam"},
    "GU810027": {"country": "Thailand", "sample_type(modern/ancient)": "unknown",
                 "ethnicity": "Moken", "province/city": "Andaman Sea coast"},
    "KF006361": {"country": "Philippines", "sample_type(modern/ancient)": "unknown",
                 "ethnicity": "Filipino (or Tagalog)", "province/city": "Philippines Bohol"},
    "ON792208": {"country": "Malaysia", "sample_type(modern/ancient)": "unknown",
                 "ethnicity": "Malay", "province/city": "Kelantan"},
}

# Ground truth from test-data/KJ442651/KJ442651_metadata_extraction.xlsx, Request1_defined_metadata
KJ442651_TRUTH = {
    "KJ442651": {
        "strain": "PR-1T (=TSD-367T =JCM 39464T =ATCC TSD-367)",
        "oxygen_tolerance": "Microaerophilic",
        "growth_temperature": "20-28 C (optimum 28 C)",
        "ph_optimum_range": "7.0-8.0 (optimum 7.0)",
        "cell_morphology": "Spiral / S-shaped (Gram-negative spirillum, single or double helix turn), "
                            "motile with a single flagellum at each pole",
    }
}


def run_group(truth_map, fields, benchmark_type, benchmark_file, known_issue="None", note_ambiguous=False):
    for acc, truth in truth_map.items():
        acc_dir = os.path.join(ROOT, "by_accession", acc)
        extraction = json.load(open(os.path.join(acc_dir, "extraction_result.json")))
        extracted_fields = extraction.get("fields", {})

        field_results = {}
        for field in fields:
            extracted_val = extracted_fields.get(field, {}).get("answer", "")
            truth_val = truth.get(field, "")
            verdict, note = score_field(extracted_val, truth_val)
            if note_ambiguous and verdict == "partial" and "ground truth is unknown" in note:
                note += " (edge-case set: not penalized as a mismatch)"
            field_results[field] = {
                "extracted": extracted_val, "ground_truth": truth_val,
                "verdict": verdict, "note": note,
            }

        report = write_comparison_report(
            acc_dir=acc_dir, accession=acc, benchmark_type=benchmark_type,
            benchmark_file=benchmark_file, known_issue=known_issue, field_results=field_results,
        )
        append_consolidated_row(
            CONSOLIDATED_CSV, acc, benchmark_type, field_results,
            report["accuracy_pct"], known_issue_status="n/a",
        )
        print(f"{acc}: accuracy={report['accuracy_pct']}%")
        for field, r in field_results.items():
            print(f"  {field}: {r['verdict']} (extracted='{r['extracted']}' truth='{r['ground_truth']}')")


if __name__ == "__main__":
    run_group(MARKO_TRUTH, MARKO_FIELDS, "peer_extraction_not_official_ground_truth",
              "test-data/marko_output/Marko_outputs.xlsx")
    run_group(OUCRU_TRUTH, OUCRU_FIELDS, "ground_truth_edge_case",
              "test-data/oucru_output/dr_duc_edge_case_output.xlsx", note_ambiguous=True)
    run_group(KJ442651_TRUTH, list(next(iter(KJ442651_TRUTH.values())).keys()),
              "peer_extraction_not_official_ground_truth",
              "test-data/KJ442651/KJ442651_metadata_extraction.xlsx")
