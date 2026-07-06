# Process trace: SAMN35361964 + FarinaR_2019.pdf → `context_for_llm`

This is a **real run** of the production pipeline (no hand-reconstruction, no
LLM call), driven by `trace_context_for_llm_SAMN35361964.py` at the repo root.
Every step below calls the actual production function; the only intervention
was replacing `model.query_document_info` with a stub that raises
immediately (so the run stops right after Google Drive save and before any
Anthropic/Gemini API call — no LLM logic in `model.py` executed).

Inputs:
- Accession: `SAMN35361964`
- Uploaded file: `test-data/PRJNA976261/FarinaR_2019.pdf` (1,584,626 bytes)

Outputs of this run:
- `contextLLM_SAMN35361964.txt` — the exact `context_for_llm` string that
  would have been embedded in the LLM prompt (161,717 chars)
- `_run_meta_SAMN35361964.json` — structured summary of every stage
- `_run_log_SAMN35361964.txt` — full stdout of the run

---

## Stage A — PDF upload & extraction (`api._extract_text_from_upload`, api.py:678)

Same function `api.py`'s `/upload-context` endpoint calls for a user-uploaded PDF.

| Step | Output |
|---|---|
| PyMuPDF page-text extraction | raw page text |
| Table extraction (`NER/PDF/pdf.py` `PDF.extractTable()`, Tabula) | 6 candidate tables found |
| `table_reliability.detect_candidate_id_tables` | 2 tables marked **RELIABLE**, 4 **UNRELIABLE** (reliability block appended to the text so the LLM is told which ID→category table to trust) |
| **Result** | **134,344 chars** of text (page text + serialized tables + reliability block) |

This becomes the `user_uploaded_file` source in Stage C.

## Stage B — Accession resolution (`ncbi_resolver.resolve_accessions`)

```
Input: 'SAMN35361964' -> detected type: biosample
Resolved: {'bioproject': 'PRJNA976261', 'biosample': 'SAMN35361964',
           'accession': '', 'experiment': 'SRR24828457'}
```

This record is what gets passed as the `accessions` argument into
`pipeline_with_gemini`, exactly as `api.py`'s `/analyze` endpoint would build it.

## Stage C — `additional_pipeline.pipeline_with_gemini(...)`

Ran with `niche_cases=None` (default country/location classification — no
custom metadata fields were requested) and `per_accession_context =
{"SAMN35361964": <134,344-char PDF text>}`.

### Step 1 — NCBI/ENA fetch (additional_pipeline.py:422-511)

| Source key | Output |
|---|---|
| `NCBI_bioproject` | 1,696 chars — BioProject PRJNA976261 title/description + linked PubMed IDs `37257865`, `31153098` |
| `NCBI_biosample` | 2,138 chars — this sample's BioSample record |
| `NCBI_experiment` | 4,757 chars — SRR24828457 experiment record |

`in_NCBI=True`, `has_pubmed=True` signals set. The two linked PubMed IDs
resolve to two DOIs, which become the fetch queue for Step 2:
`https://doi.org/10.1111/omi.12418` and `https://doi.org/10.1016/j.archoralbio.2019.05.025`
(the latter **is FarinaR_2019's own DOI**).

### Step 2 — DOI/publication fetch (additional_pipeline.py:597-743)

| DOI | What happened | Output |
|---|---|---|
| `10.1111/omi.12418` (Favale et al., companion paper) | Direct page fetch blocked (403) 4×, fell back to **CrossRef metadata** | 18,213 chars |
| `10.1016/j.archoralbio.2019.05.025` (**FarinaR_2019 itself**) | Publisher page returned only a JS redirect stub | **11 chars** (`"Redirecting"`) — effectively empty |

This is the key finding: **the live web fetch for FarinaR_2019's own paper
fails** (paywalled/blocked, only a redirect stub comes back). The uploaded
PDF is the *only* source that actually supplies this paper's full text —
which is exactly why the "inaccessible paper — upload the PDF" warning path
(additional_pipeline.py:1025-1049) exists.

### Step 3 — Web search + PubMed-URL follow-up

Ran but did not add usable new source text for this sample (`pubmed.ncbi.nlm.nih.gov` follow-up links were queued but not fetchable in this environment — logged as `data_preprocess not available — skipping`, a pre-existing optional-dependency gap in `pipeline.process_link_allOutput`, unrelated to this run's PDF upload).

### Step 3.9 — User-uploaded file merged in (additional_pipeline.py:1007-1011)

```python
_scoped_context = per_accession_context.get("SAMN35361964", "")  # our 134,344-char PDF text
acc_score["source_texts"]["user_uploaded_file"] = _scoped_context
```

### Step 4 — Build combined text from ALL sources (additional_pipeline.py:1051-1094)

Each source is stringified and appended as
`The source - {key}: {text}-----END OF THIS SOURCE {key} ----`:

| Source key | Chars |
|---|---|
| `NCBI_bioproject` | 1,696 |
| `NCBI_biosample` | 2,138 |
| `NCBI_experiment` | 4,757 |
| `https://doi.org/10.1111/omi.12418` | 18,213 |
| `https://doi.org/10.1016/j.archoralbio.2019.05.025` | 11 |
| `user_uploaded_file` (FarinaR_2019.pdf) | 134,344 |
| **Combined total (`text` / `context_for_llm`)** | **161,717** |

(Well under the 800,000-char Anthropic-budget cap, so no truncation/reduction was triggered.)

## Stage D — Local DOCX save + Google Drive upload (additional_pipeline.py:1096-1141)

- Saved locally: `/tmp/tmpi52ye9i2/extracted_text_SAMN35361964.docx`
- Uploaded to **Google Drive**, folder `mtDNA-Location-Classifer/data/37257865_31153098/` (folder name = the two PubMed IDs joined, since this sample has linked publications), filename `SAMN35361964.docx`:
  ```
  🗑️ Deleted existing 'SAMN35361964.docx' in Drive folder 1erzjo3JqUjr2i-YzCyObgu5RGBBfakSS
  ✅ Uploaded 'SAMN35361964.docx' to Google Drive folder ID: 1erzjo3JqUjr2i-YzCyObgu5RGBBfakSS
  ✅ Saved DOCX to Google Drive: data/37257865_31153098/SAMN35361964.docx
  ```
  (A same-name file already existed there from a prior run and was overwritten.)

This upload contains the **same 161,717-char merged text** that becomes `context_for_llm` — not the raw PDF, not images.

## Stage E — Stopped before the LLM

```
start model
[LLM] query_document_info failed for SAMN35361964: STOPPED-BEFORE-LLM: model.query_document_info
replaced with a stub per user request -- no LLM API call was made.
```

`acc_prompts = {"SAMN35361964": text}` was built and handed to
`model.query_document_info(...)`, confirming this is exactly the
`context_for_llm` that `model.py:1267` would have assigned and embedded into
the Anthropic/Gemini prompt (`model.py:1313`) — verified by capturing the
`prompts` argument at the stub and diffing it against the saved file
(`captured_llm_prompt_matches_combined_text: true` in the metadata JSON).
Nothing inside `model.py`'s real body, and no Anthropic/Gemini API call, executed.

---

## Final `context_for_llm`

Saved verbatim to `test-data/PRJNA976261/contextLLM_SAMN35361964.txt`
(161,717 chars / 2,093 lines). Structure:

```
The source - NCBI_bioproject: {...}-----END OF THIS SOURCE NCBI_bioproject ----
The source - NCBI_biosample: {...}-----END OF THIS SOURCE NCBI_biosample ----
The source - NCBI_experiment: {...}-----END OF THIS SOURCE NCBI_experiment ----
The source - https://doi.org/10.1111/omi.12418: {...}-----END OF THIS SOURCE ... ----
The source - https://doi.org/10.1016/j.archoralbio.2019.05.025: Redirecting-----END OF THIS SOURCE ... ----
The source - user_uploaded_file: Contents lists available at ScienceDirect
Archives of Oral Biology ... [full FarinaR_2019 PDF text + serialized tables
+ table-reliability block]-----END OF THIS SOURCE user_uploaded_file ----
```
