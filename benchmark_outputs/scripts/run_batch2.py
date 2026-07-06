import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_runner import run_group

MARKO_FIELDS = "taxonomic name, country, province, collection_date, sex, elevation, associated taxa"
OUCRU_FIELDS = "country, sample_type(modern/ancient), ethnicity, province/city"

GROUP_A = [  # marko peer_extraction
    {"accession": "OL757400", "metadata": MARKO_FIELDS, "upload_files": []},
    {"accession": "OL757401", "metadata": MARKO_FIELDS, "upload_files": []},
]

GROUP_B = [  # oucru ground_truth_edge_case
    {"accession": "DQ834260", "metadata": OUCRU_FIELDS, "upload_files": []},
    {"accession": "DQ834259", "metadata": OUCRU_FIELDS, "upload_files": []},
    {"accession": "GU810027", "metadata": OUCRU_FIELDS, "upload_files": []},
    {"accession": "KF006361", "metadata": OUCRU_FIELDS, "upload_files": []},
    {"accession": "ON792208", "metadata": OUCRU_FIELDS, "upload_files": []},
]

GROUP_C = [  # KJ442651 peer_extraction, 2 local files
    {"accession": "KJ442651",
     "metadata": "strain, oxygen_tolerance, growth_temperature, ph_optimum_range, cell_morphology",
     "upload_files": [
         "/workspaces/OpenBioData/test-data/KJ442651/ijsem007129.pdf",
         "/workspaces/OpenBioData/test-data/KJ442651/mmc3_j.bpj.2014.05.043.pdf",
     ]},
]


async def main():
    for group, label in [(GROUP_A, "marko"), (GROUP_B, "oucru_edge_case"), (GROUP_C, "KJ442651")]:
        results = await run_group(group, label)
        for acc, r in results.items():
            print(f"\n{acc}:")
            for field, data in r["fields"].items():
                print(f"  {field}: {data['answer']}")


if __name__ == "__main__":
    asyncio.run(main())
