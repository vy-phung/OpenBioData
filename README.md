# OpenBioData — NCBI Metadata Recovery Tool

**Tool:** https://app.openbiodata.it.com

---

## What this is

I kept running into the same problem while working with public genomic datasets: NCBI BioSample and SRA records are often missing important fields - disease status, isolation source, geographic location, host - even when that information is clearly written in the paper that deposited the data.

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

**30 samples maximum per run.**

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

## How the confidence score works

Four signals combine into a 0–100 score:

- **Direct evidence** (+10 to +40): did the value come from NCBI's structured field, the paper, or just a web search?
- **Cross-source consistency** (−30 to +20): does the extracted value agree with what NCBI already has?
- **Evidence density** (+0 to +20): how many publications confirmed it?
- **Risk penalties** (−10 to −20): was the field missing or did the model return "unknown"?

Score 70+ = High (strong multi-source agreement). 40–69 = Medium. Below 40 = Low.

---

## Is this open source?

The code is on GitHub: https://github.com/vy-phung/OpenBioData

To be upfront: I wrote the core logic and pipeline, but I also used Claude Code (Anthropic's coding tool) to help build parts of it — especially the LLM extraction layer and the User Interface. The reason I used an LLM API for extraction rather than rule-based parsing is that it handles the messiness of real papers better: tables in weird formats, values buried in methods sections, supplementary files with inconsistent structure. It's genuinely more accurate than anything I could write with regex.

The confidence scoring, source-fetching logic, and output structure are all deterministic code you can read and audit in the repo.

---

## Who should use it


If you work with large public datasets and want to test it on something you know the ground truth for, send me an accession and I'll run it and share the result. That's the most useful feedback at this stage.

---

## Contact

Vy Phung
vyphung1901@gmail.com
