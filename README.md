# OpenBioData - NCBI Metadata Recovery Tool

**Tool:** https://app.openbiodata.it.com

---

## What this is

Problem while working with public genomic datasets: NCBI BioSample and SRA records are often missing important fields - disease status, isolation source, geographic location, host - even when that information is clearly written in the paper that deposited the data.

So I built a tool to recover it automatically. You paste an accession (BioProject, BioSample, SRR, GEO, GenBank) or a paper link, and it traces back to the source publication and supplementary tables, pulls the missing fields, and returns them with a confidence score and a direct citation (PMID + table/section) so you can verify exactly where each value came from.

---

## How to use it

**Option 1 - Paste accession IDs**

Paste any NCBI accession: BioProject, BioSample, SRR, GenBank, or GEO series. One per line or comma-separated. The tool finds the linked paper automatically.

If the paper is paywalled, use the "+ Files" button to upload the PDF and any supplementary tables — it will match each file to the right sample row.

**Option 2 - Paste a paper link**

Paste a DOI or PubMed link. The tool finds all NCBI accessions linked to that paper and runs all of them at once — no need to know the accession IDs.

**Optional: specify which fields you want**

In the "Metadata Fields to Extract" box, list the fields that matter for your study (e.g. `disease_status, country, host, isolation_source`). This makes the output more accurate than leaving it blank and asking for everything.

**Optional: add a standardization schema**

If you have a controlled vocabulary (e.g. a CSV from cMD or your own ontology), paste the URL. The tool will map extracted values to your schema's allowed terms.

---

## Current limits and why

**30 samples maximum.**

The tool uses an LLM API (Claude) under the hood to read papers and extract metadata. I'm running this on my own infrastructure right now and wanted to make sure it actually holds up before opening it fully. 30 samples is enough to test it on a real dataset and see if it's useful for your workflow.

If you have a BioProject with 500 samples, it counts each sample toward the 30 - not the project as one unit.

**Login is required to save your runs.**

I built login mainly so your runs don't disappear. The tool is early and I rerun samples a lot to debug and improve accuracy - if your data is saved, you can reload and rerun without it counting toward your limit again. It also keeps my API costs from getting exhausted by anonymous batch runs while I'm still figuring out whether this works at scale beyond my own laptop.

If you want to stay anonymous, you can still try the first 30 samples without logging in.

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
 
## What the tool does NOT do
 
- It does not modify the original NCBI records
- It does not fabricate values — if no evidence is found, the field is marked `unknown`
- It does not bypass paywalled articles — it uses CrossRef metadata and PubMed abstracts in those cases, which gives less evidence and a lower confidence score
- It does not guarantee correctness — the confidence score tells you how much evidence was found, not whether the original depositor was right
---
 
## Transparency — where to check the code
 
| What | File | Location |
|---|---|---|
| Confidence score rules and weights | `confidence_score.py` | `set_rules()` line 44 |
| Score calculation logic | `confidence_score.py` | `compute_confidence_score_and_tier()` line 192 |
| NCBI metadata fetch | `mtdna_classifier.py` | `fetch_ncbi_metadata()` line 37 |
| LLM prompt construction | `model.py` | `multi_prompts()` line 1083 |
| LLM API call with fallback | `model.py` | `call_llm_api()` line 94 |
| Source text gathering | `pipeline.py` | `extractSources()` line 295 |
| Non-NCBI database support | `non_ncbi_resolver.py` | — |
| Output row construction | `api.py` | `_rows_from_new_pipeline()` line 151 |

## How the confidence score works

Four signals combine into a 0–100 score:

- **Direct evidence** (+10 to +40): did the value come from NCBI's structured field, the paper, or just a web search?
- **Cross-source consistency** (−30 to +20): does the extracted value agree with what NCBI already has?
- **Evidence density** (+0 to +20): how many publications confirmed it?
- **Risk penalties** (−10 to −20): was the field missing or did the model return "unknown"?

Score 70+ = High (strong multi-source agreement). 40–69 = Medium. Below 40 = Low.

---

## About the code

The code is on GitHub: https://github.com/vy-phung/OpenBioData

To be upfront: I wrote the core logic and pipeline, but I also used Claude Code (Anthropic's coding tool) to help build parts of it - especially the LLM extraction layer and the User Interface. The reason I used an LLM API for extraction rather than rule-based parsing is that it handles the messiness of real papers better: tables in weird formats, values buried in methods sections, supplementary files with inconsistent structure. It's genuinely more accurate than anything I could write with regex.

The confidence scoring, source-fetching logic, and output structure are all deterministic code you can read and audit in the repo.

My friend Gowtham also helped fix some bugs early on. Thanks Gowtham!

---

## Contact

Vy Phung
vyphung1901@gmail.com
