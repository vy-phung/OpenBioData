import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_runner import run_group

ROWS = [
    {"accession": "KU521484", "metadata": "country, sample_type(modern/ancient), ethnicity, province/city", "upload_files": []},
    {"accession": "AY963572", "metadata": "country, sample_type(modern/ancient), ethnicity, province/city", "upload_files": []},
    {"accession": "KC505116", "metadata": "country, sample_type(modern/ancient), ethnicity, province/city", "upload_files": []},
]


async def main():
    results = await run_group(ROWS, "pilot_oucru_ground_truth")
    for acc, r in results.items():
        print(f"\n{acc}:")
        for field, data in r["fields"].items():
            print(f"  {field}: {data['answer']}")


if __name__ == "__main__":
    asyncio.run(main())
