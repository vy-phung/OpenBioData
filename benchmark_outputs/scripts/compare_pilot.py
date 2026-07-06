import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare_utils import score_field, write_comparison_report, append_consolidated_row

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCHMARK_FILE = os.path.join(ROOT, "..", "test-data", "oucru_output", "updated_4934_samples_output.xlsx")
CONSOLIDATED_CSV = os.path.join(ROOT, "reports", "consolidated_results.csv")

# Ground truth pulled from updated_4934_samples_output.xlsx, Sheet1, "Sample ID" column
# matching pattern "ACCESSION(Isolate: ...)".
GROUND_TRUTH = {
    "KU521484": {"country": "Brunei", "sample_type(modern/ancient)": "modern",
                 "ethnicity": "Bruneian Malay", "province/city": "Brunei (Borneo)"},
    "AY963572": {"country": "Cambodia", "sample_type(modern/ancient)": "modern",
                 "ethnicity": "Unknown", "province/city": "Unknown"},
    "KC505116": {"country": "Cambodia", "sample_type(modern/ancient)": "modern",
                 "ethnicity": "Bru (Brao)", "province/city": "Ratanakiri"},
}

NICHE_CASES = ["country", "sample_type(modern/ancient)", "ethnicity", "province/city"]


def main():
    for acc, truth in GROUND_TRUTH.items():
        acc_dir = os.path.join(ROOT, "by_accession", acc)
        extraction = json.load(open(os.path.join(acc_dir, "extraction_result.json")))
        extracted_fields = extraction.get("fields", {})

        field_results = {}
        for field in NICHE_CASES:
            extracted_val = extracted_fields.get(field, {}).get("answer", "")
            truth_val = truth[field]
            verdict, note = score_field(extracted_val, truth_val)
            field_results[field] = {
                "extracted": extracted_val, "ground_truth": truth_val,
                "verdict": verdict, "note": note,
            }

        report = write_comparison_report(
            acc_dir=acc_dir, accession=acc, benchmark_type="ground_truth",
            benchmark_file="test-data/oucru_output/updated_4934_samples_output.xlsx",
            known_issue="None", field_results=field_results,
        )
        append_consolidated_row(
            CONSOLIDATED_CSV, acc, "ground_truth", field_results,
            report["accuracy_pct"], known_issue_status="n/a",
        )
        print(f"{acc}: accuracy={report['accuracy_pct']}%")
        for field, r in field_results.items():
            print(f"  {field}: {r['verdict']} (extracted='{r['extracted']}' truth='{r['ground_truth']}')")


if __name__ == "__main__":
    main()
