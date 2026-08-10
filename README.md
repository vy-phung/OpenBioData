# OpenBioData - NCBI Metadata Recovery Tool

Open source tool that traces NCBI accessions back to their source papers to recover metadata the database record is missing.

---

## The problem

NCBI BioSample and SRA records are often missing important fields — disease status, isolation source, geographic location, host — even when that information is clearly written in the paper that deposited the data. Tracing accessions back to their source publications by hand, one at a time, is slow and easy to get wrong.

OpenBioData automates that trace. Give it an accession (BioProject, BioSample, SRR, GEO, GenBank) or a paper link, and it finds the source publication and supplementary tables, pulls the missing fields, and returns them with a confidence score and a direct citation (PMID + table/section) so you can verify exactly where each value came from.

---

## Try it hosted

https://app.openbiodata.it.com/ — up to 10 samples without an account. Signing in raises your cap to 30 samples.

Why the limit: this runs on my own infrastructure and calls the Claude API per sample, so a cap protects against runaway cost while I validate accuracy at scale. If you have a BioProject with 500 samples, it counts each sample toward your limit, not the project as one unit. Signing in also saves your run history so you don't lose progress when reloading.

If you want to stay anonymous, you can try the first 10 samples without logging in.

---

## Run it yourself

Requirements: Python 3.x, and an LLM API key — either an Anthropic API key (Claude,
recommended for extraction quality) or a Gemini API key (cheaper, works standalone too).
Check you have it with `python --version` — if that fails, install from python.org first.

```bash
git clone https://github.com/vy-phung/OpenBioData
cd OpenBioData
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY and/or GOOGLE_API_KEY
python api.py # (optional)
```
Example: 

```
$ python openbiodata.py MN908947
> Loading backend…
> Parsing accession input…
> Resolving accession via NCBI…
> Processing 1 sample(s)…
> [1/1] Fetching NCBI data for SAMN50212654…
> [1/1] Searching literature for SAMN50212654…
> ⚠ Cannot access paper(s) — they may require a subscription or block server access. Upload the PDF(s) directly to improve accuracy.
> [1/1] Data gathered for SAMN50212654, queued for batched LLM inference…
> Running batched LLM inference for 1 sample(s) (batch 1/1)…
> [1/1] ✓ SAMN50212654 done (41.800 seconds)

==================================================================================
SAMN50212654   BioProject: PRJNA1295507   SRA: SRR34737024   GenBank: MN908947
==================================================================================
Confidence: 30 (🔴 Low) — weakest field: collected_by (30)

• organism: BioSample Organism field and NCBI_experiment SCIENTIFIC_NAME both consistently identify the organism as Severe acute respiratory syndrome coronavirus 2 (taxonomy ID 2697049).
• host: BioSample attribute host and NCBI_experiment SAMPLE_ATTRIBUTE both specify the host as Homo sapiens.
• host_disease: BioSample attribute host_disease and NCBI_experiment SAMPLE_ATTRIBUTE both specify COVID-19 as the host disease.
• isolation_source: BioSample attribute isolation_source and NCBI_experiment SAMPLE_ATTRIBUTE both specify Nasal/Oral as the isolation source.
… (12 more fields — collection_device, collection_date, geo_loc_name, isolate, collected_by, library_strategy, library_source, library_selection, instrument_model, gisaid_accession, sequenced_by, sample_name, dna_extraction_kit — each with its own citation back to the exact NCBI record field it came from)

All linked sources:
https://doi.org/10.3390/v17101313
NCBI_bioproject
NCBI_biosample
NCBI_experiment

Time cost: 41.800 seconds
==================================================================================

1 sample(s) processed.
```

Then open http://localhost:8000 in your browser — paste an accession, submit, and watch the result stream in.

Try it against the sample accessions in `accessions.csv` to confirm it's working before pointing it at your own data.

This is early-stage — if you hit a blocker getting it running locally, please open an issue rather than assuming it's you.

---

## How to use it

**Option 1 — Paste accession IDs**

Paste any NCBI accession: BioProject, BioSample, SRR, GenBank, or GEO series. One per line or comma-separated. The tool finds the linked paper automatically.

If the paper is paywalled, use the "+ files" button on that paper's row to upload the PDF and any supplementary tables — they'll be matched to that paper only.

**Option 2 — Paste a paper link**

Paste a DOI or PubMed link with no accession IDs entered. The tool finds every NCBI accession linked to that paper and runs all of them at once — no need to know the accession IDs in advance.

**Optional: specify which fields you want**

In the "Metadata Fields to Extract" box, list the fields that matter for your study (e.g. `disease_status, country, host, isolation_source`). This makes the output more accurate than leaving it blank and asking for everything.

**Optional: add a standardization schema**

If you have a controlled vocabulary (e.g. a CSV from cMD or your own ontology), paste the URL. The tool will map extracted values to your schema's allowed terms.

---

## What it outputs

One row per accession. Columns include:

- BioSample ID, BioProject, SRA accession
- Each requested metadata field
- Confidence score (0–100) and tier (High / Medium / Low)
- One-line explanation of where the value came from
- Source citation (PMID + table or section)
- Flags where the NCBI record and the paper disagree

Excel export available.

---

## Step by step — what happens when you submit

1. **Fetch the NCBI record** — pulls structured fields from BioSample, SRA, or GenBank via API (country, sample type, collection date, linked PubMed ID, DOI)
2. **Find the linked paper** — uses the DOI to fetch article HTML and supplementary files. If the paper is paywalled, falls back to PubMed abstract. Also searches for any papers that cite the accession by name
3. **Record signals** — notes whether NCBI has a geo_loc field, whether a PubMed record exists, whether the accession ID appears in the paper text, how many publications were found
4. **LLM extraction pass 1** — all gathered text goes to Claude (with Gemini as fallback). It answers: what is the country, disease status, host, and each requested field? It gives a short explanation per answer
5. **LLM extraction pass 2** — a second pass picks up any metadata fields that appear in the text but weren't explicitly requested (sequencing platform, collection method, geographic region, etc.). These go in the "Full Raw Attributes" sheet
6. **Confidence scoring** — four signals combine into a 0–100 score with a tier (High / Medium / Low) and a reason
7. **Output table** — one row per accession with all fields, explanations, citations, confidence scores, and source links

---

## How the confidence score works

Four signals combine into a 0–100 score:

- **Direct evidence** (+10 to +40): did the value come from NCBI's structured field, the paper, or just a web search?
- **Cross-source consistency** (−30 to +20): does the extracted value agree with what NCBI already has?
- **Evidence density** (+0 to +20): how many publications confirmed it?
- **Risk penalties** (−10 to −20): was the field missing or did the model return "unknown"?

Score 70+ = High (strong multi-source agreement). 40–69 = Medium. Below 40 = Low.

---

## What the tool does NOT do

- It does not modify the original NCBI records
- It does not fabricate values — if no evidence is found, the field is marked `unknown`
- It does not bypass paywalled articles — it uses CrossRef metadata and PubMed abstracts in those cases, which gives less evidence and a lower confidence score
- It does not guarantee correctness — the confidence score tells you how much evidence was found, not whether the original depositor was right

---
 
## Known limitations
 
Real, current ones — not hedging.
 
- **The four processing stages (trace back, mine literature, cross-check,
  recover/verify/expand) aren't fully independent yet.** They currently run
  interleaved inside a few large functions rather than as clean, separately
  callable phases. If you're building on top of this expecting to call
  "just cross-check," that's not supported today — see `ARCHITECTURE.md`.
- **Accession-type detection has three separate implementations** in the
  codebase with inconsistent coverage. Being consolidated — see open issues.
None of these block real use — they're documented so you know exactly what
you're looking at, and so anyone who wants to help knows exactly where.
See `CONTRIBUTING.md` for open issues, labeled by scope.

---

## Transparency — where to check the code

For a fuller walkthrough of how each stage works, see [HOW_IT_WORKS.md](./HOW_IT_WORKS.md).

| What | File | Location |
|---|---|---|
| Confidence score rules and weights | `confidence_score.py` | `set_rules()` line 44 |
| Score calculation logic | `confidence_score.py` | `compute_confidence_score_and_tier()` line 192 |
| NCBI metadata fetch | `NCBI.py` | `extract_NCBI_directly()` line 736; calls `mtdna_classifier.fetch_ncbi_metadata()` as one branch |
| LLM prompt construction | `model.py` | `multi_prompts()` line 1096 |
| LLM API call with fallback | `model.py` | `call_llm_api()` line 94 |
| Source text gathering | `pipeline_legacy_utils.py` | `extractSources()` line 290 |
| Non-NCBI database support | `non_ncbi_resolver.py` | — |
| Output row construction | `api.py` | `_rows_from_new_pipeline()` line 289 |

The confidence scoring, source-fetching logic, and output structure are all deterministic code you can read and audit in the repo — none of it is hidden behind the LLM calls.

---

## About the code

I wrote the core logic and pipeline, but I also used Claude Code (Anthropic's coding tool) to help build parts of it — especially the LLM extraction layer and the user interface. The reason I used an LLM API for extraction rather than rule-based parsing is that it handles the messiness of real papers better: tables in weird formats, values buried in methods sections, supplementary files with inconsistent structure. It's genuinely more accurate than anything I could write with regex.

My friend Gowtham also helped fix some bugs early on. Thanks Gowtham!

---

## Data & Privacy

If you use the hosted version, see [PRIVACY.md](./PRIVACY.md) for how your data is handled. If you self-host, nothing is sent to me — but the tool still sends paper/accession text to Anthropic (and Google, if you configure the Gemini fallback) to run extraction, same as any tool built on their APIs.

---

## Contributing

This is early-stage and I'm actively improving extraction accuracy. Ways to help:

- **Report a bad extraction** — open an issue with the accession ID and what was wrong
- **Suggest a metadata field or standardization schema** — open an issue
- **Code contributions** — PRs welcome, especially around the LLM extraction prompts (`model.py`) and confidence scoring (`confidence_score.py`)

---

## License

MIT — see [LICENSE](./LICENSE).

---

## Contact

Vy Phung
vyphung1901@gmail.com
