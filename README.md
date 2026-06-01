# BioMetadataAudit 🧬

**AI-powered metadata extraction and standardization for NCBI accessions.**  
Built by [OpenBioData](https://openbiodata.lovable.app/)

## 🔬 Live Tool
👉 **[app.openbiodata.it.com](https://app.openbiodata.it.com)**

## What It Does
Paste any NCBI accession (BioProject, BioSample, SRR, GenBank) and get 
standardized metadata instantly — no manual curation required.

- Extracts disease status, organism, body site, country, sequencing platform
- Standardizes against cMD and custom schemas
- Confidence scores with source provenance
- Excel export
- Works with batch accessions

## Who It's For
Researchers doing meta-analyses, systematic reviews, or anyone working 
with public NCBI/SRA data who needs clean, structured biosample metadata.

## Company
**OpenBioData** — building open, AI-driven metadata infrastructure for 
biological research.  
🌐 [openbiodata.it.com](https://openbiodata.lovable.app/)  
📧 vy@openbiodata.it.com

---

## How It Works — A Guide for Researchers

This section explains how the tool retrieves, infers, and scores biological sample metadata — written so you can evaluate whether the system is credible and reliable enough for your research.

### What problem does this tool solve?

Public biological databases (NCBI BioSample, GenBank, SRA, PRIDE, etc.) often contain incomplete or inconsistently recorded metadata. A sample may be missing its collection country, sample type, disease status, or host information — even when that information is clearly stated in the linked publication. This tool systematically recovers that missing metadata by cross-referencing multiple sources and rates how confident it is in each answer.

---

### The Three Inputs — and Why Each One Matters

**1. Accession ID(s)**  
This is the unique identifier for your biological sample in a public database (e.g., `SAMN12345678`, `SRR1234567`, `MSV000080918`). It is the anchor: the tool uses it to fetch the official database record and to locate the publication that first deposited the sample.

**Supported databases:** NCBI BioSample, SRA, GenBank, and non-NCBI databases including MassIVE, PRIDE, MetaboLights, MGnify, BioStudies, EGA, and PDB.

**2. Metadata Fields to Extract**  
You specify which metadata fields matter for your study — for example: `country`, `disease_status`, `host_age`, `tissue_type`. This tells the LLM exactly what to look for rather than guessing.

**3. Standardization Schema URL** *(optional but recommended)*  
A CSV file or ontology URL (e.g., from GO, OBO, or UBERON) that defines controlled vocabulary for each field. When provided, the tool maps extracted values onto your schema's allowed terms. Without this, values are returned as free text; with it, outputs are standardized and directly compatible with your database or analysis pipeline.

---

### Why Upload a Context File?

An uploaded file (e.g., a supplementary table, a manual curation spreadsheet, or a methods section) gives the LLM direct, high-quality text that is:

- Not behind a paywall — the tool cannot always access full-text journal articles
- Specific to your samples — general web searches may find unrelated papers with similar accession patterns
- Machine-readable immediately — no HTML parsing or PDF extraction errors

When a context file is present, the tool reads it first before searching the web. This directly raises the confidence score because more source documents confirm the same value.

---

### Step-by-Step: What Happens When You Submit Accession IDs

```
┌─────────────────────────────────────────────────────────────────┐
│  Step 1 — Fetch official database record                        │
│  NCBI API returns: country, sample type, collection date,       │
│  isolate name, PubMed ID, title, DOI, BioSample features        │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│  Step 2 — Retrieve linked publication text                      │
│  Uses DOI → full article HTML, supplementary files, CrossRef    │
│  metadata. If no DOI or article is paywalled → fetches PubMed   │
│  abstract. Also searches web for the sample isolate name +      │
│  accession to find any citing papers.                           │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│  Step 3 — Signals recorded                                      │
│  • has_geo_loc_name: NCBI has a country field?                  │
│  • has_pubmed: a linked PubMed record exists?                   │
│  • accession_found_in_text: accession ID appears in the paper?  │
│  • num_publications: how many source documents were found?      │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│  Step 4 — LLM extraction (Pass 1)                               │
│  All gathered text is passed to the LLM (Claude Haiku or        │
│  Gemini Flash-Lite). The model answers: what is the country,    │
│  sample type, and each requested metadata field? It also        │
│  provides a short explanation for each answer.                  │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│  Step 5 — LLM extraction (Pass 2)                               │
│  A second generalized pass extracts any metadata fields that    │
│  appear in the text but were not requested by name — e.g.,      │
│  collection method, sequencing platform, geographic region.     │
│  These appear in the "Full Raw Attributes" sheet.               │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│  Step 6 — Confidence scoring                                    │
│  Four signals are combined into a 0–100 score (see below).      │
│  Output: numeric score + tier (High / Medium / Low) + reason    │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│  Step 7 — Output table                                          │
│  One row per accession. Columns: BioSample ID, BioProject,      │
│  SRA accession, each metadata field, explanation, confidence    │
│  score, source links, processing time.                          │
└─────────────────────────────────────────────────────────────────┘
```

---

### How the Confidence Score Is Calculated

The confidence score (0–100) is calculated using four independent signals. The same logic applies to every metadata field.

**Signal 1 — Direct Evidence (up to +40 points)**

| Evidence available | Points |
|---|---|
| GenBank field present **AND** linked publication found **AND** accession appears in that publication's text | **+40** |
| GenBank field present **AND** linked publication found | **+30** |
| GenBank field present only | **+20** |
| Accession ID appears in web-searched text only | **+10** |

**Signal 2 — Cross-Source Consistency (up to +20 or −30 points)**

| Situation | Points |
|---|---|
| Predicted value matches NCBI structured metadata | **+20** |
| No contradiction detected across sources | **+10** |
| Predicted value conflicts with NCBI metadata | **−30** |

**Signal 3 — Evidence Density (up to +20 points)**

| Publications found | Points |
|---|---|
| 2 or more | **+20** |
| Exactly 1 | **+10** |
| None | **0** |

**Signal 4 — Risk Penalties (up to −20 points)**

| Situation | Points |
|---|---|
| Requested metadata fields are missing/unknown | **−10** |
| Accession matches a known failure pattern (model returned "unknown") | **−20** |

**Score → Tier mapping**

| Score | Tier |
|---|---|
| 70 – 100 | 🟢 **High** — strong multi-source agreement |
| 40 – 69 | 🟡 **Medium** — partial evidence, some uncertainty |
| 0 – 39 | 🔴 **Low** — limited or conflicting evidence |

**Example walkthrough**

A sample where NCBI has a country field (+20), the LLM predicted the same country (+20), and one linked publication was found (+10) gives a total of **50 → Medium (🟡)**. If that publication also mentions the accession ID by name, the direct evidence signal rises to +30, giving **70 → High (🟢)**.

---

### What LLM Is Used and Why?

The tool uses **Claude Haiku** (Anthropic) as the primary LLM, with **Gemini 2.5 Flash-Lite** as a fallback. These are chosen because they are fast, cost-efficient, handle long-context inputs (full papers, supplementary tables), and follow structured output formats reliably. The LLM is used **only for reading and extracting** — it does not invent information. If no relevant text is found, the field is marked `unknown`.

---

### What the Tool Does NOT Do

- It does not modify the original database records
- It does not generate or fabricate values — if evidence is absent, the field is marked `unknown`
- It does not bypass paywalled articles (it uses CrossRef metadata and PubMed abstracts in those cases)
- It does not guarantee correctness — the confidence score reflects evidence strength, not truth

---

### Transparency: Where to Check the Code

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

---

### Frequently Asked Questions

**Q: Can I trust a "High" confidence score?**  
A score of 70+ means the value was confirmed in NCBI's structured field, found in the linked publication, and the sources agreed. It does not mean the original depositor was correct — it means the tool found strong, consistent evidence across multiple sources.

**Q: Why is a sample scored "Low" even though I know the answer?**  
The score reflects what was *findable* by the pipeline. If the publication is paywalled, the accession is not cited in text, or the sample has no linked PubMed record, fewer signals fire. Uploading your own context file (e.g., a supplementary table) will increase the score.

**Q: What if the tool and NCBI disagree?**  
Both values appear in the output. The confidence score receives a −30 penalty for conflicts, and the explanation column states the conflict explicitly. You can review and decide manually.

**Q: Is the same logic used for all metadata fields?**  
Yes. The confidence scoring is field-agnostic — the same four signals and weights apply whether the field is `country`, `disease_status`, `host_age`, or a custom field you specify.
