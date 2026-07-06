# SAMN35361964 — real pipeline run trace

This is a trace of one **actual, live** run of the app pipeline (not a hand-reconstruction)
for the scenario:

| Input | Value |
|---|---|
| Accession | `SAMN35361964` |
| User upload | `test-data/PRJNA976261/FarinaR_2019.pdf` (single PDF, no link) |
| Metadata fields | `study_name, subject_id, sample_id, target_condition, control, body_site, sequencing_platform, host_species` |
| Standardization schema | none |

Run driver: `run_SAMN35361964_single_pdf.py` (repo root). It calls the exact same functions
`api.py`'s `/analyze` endpoint calls — no extraction/prompt logic was re-implemented — and
monkeypatches `model.call_llm_api` with a passthrough recorder so the real prompts/answers
could be captured without changing pipeline behavior. Full console log:
`/tmp/claude-1000/-workspaces-OpenBioData/b50dba0b-0235-435f-9c2e-0135a15cb98e/scratchpad/run_log.txt`.

Run time: **59.5 seconds** end-to-end (NCBI fetch + web search + PDF parse + 2 LLM calls).

---

## Stage-by-stage trace

### 1. PDF upload → text (`api._extract_text_from_upload`, `api.py:678`)

This is the exact function `api.py`'s `/upload-context` endpoint calls on any uploaded file.
For a `.pdf` it uses PyMuPDF for text and `NER.PDF` for table extraction (same as a real
user-uploaded PDF in the app).

- **Input:** `FarinaR_2019.pdf`, 1,584,626 bytes
- **Output:** 133,218 characters of extracted text (paper body + reconstructed tables)
- This text is *not yet* labeled — it's just a raw string at this point.

### 2. Accession → NCBI record (`ncbi_resolver.resolve_accessions`, `ncbi_resolver.py:1128`)

This is what `input_handler.build_pipeline_input()` calls internally, which is what `api.py`'s
`/analyze` calls to turn your typed accession into a structured record.

**Input:** `"SAMN35361964"` → detected type: `biosample`

**Output:**
```python
{'SAMN35361964': {'bioproject': 'PRJNA976261', 'biosample': 'SAMN35361964',
                   'accession': '', 'experiment': 'SRR24828457'}}
```
This is the `accessions` dict handed to `additional_pipeline.pipeline_with_gemini()` — one BioSample
fanned out to its parent BioProject and linked SRA experiment.

### 3. `additional_pipeline.pipeline_with_gemini()` — per-source fetch (Step 1–3, `additional_pipeline.py:287` onward)

Since no `standardization_urls` were given, `standardization_schema = {}` and your 8
`niche_cases` pass through completely untouched (the auto-populate-from-schema branch at
`additional_pipeline.py:325` never fires because `standardization_schema` is falsy).

For `SAMN35361964`, each source was fetched/attached and stored under its own key in
`acc_score["source_texts"]`. Actual character counts from this run:

| Source key | Chars | What it is |
|---|---|---|
| `NCBI_bioproject` | 1,696 | BioProject title/description fetched via NCBI (Step 1) |
| `NCBI_biosample` | 2,138 | BioSample XML attributes (host, geo_loc_name, isolation_source, etc.) |
| `NCBI_experiment` | 4,757 | SRA experiment XML (platform, library strategy, etc.) |
| `https://doi.org/10.1111/omi.12418` | 18,213 | A related paper discovered via web search (Favale et al., cites the same project) |
| `https://doi.org/10.1016/j.archoralbio.2019.05.025` | 11 | The Farina et al. 2019 paper DOI — found but blocked/inaccessible live (11 chars = essentially empty; your uploaded PDF *is* this paper, which is why it still worked) |
| `user_uploaded_file` | 133,218 | Your uploaded `FarinaR_2019.pdf`, stored under this fixed key because it was passed as `user_context_text` (a single plain-file upload, not a pasted link) — see `additional_pipeline.py:1010-1011` |

`model.getMoreInfoForAcc()` (`model.py:1562`) ran the web search that found the two DOI links
above — it does **not** call an LLM itself, it only searches Google/PubMed/EuropePMC and scrapes
pages.

### 4. Combined context text (Step 4, `additional_pipeline.py:1051-1094`)

All 6 sources above are concatenated into one string, each wrapped as:
```
The source - <key>: <source_text>-----END OF THIS SOURCE <key> ----
```
- **Combined length: 160,591 characters** (after `data_preprocess.normalize_for_overlap` — no
  truncation was needed; well under the 800,000-char cap)
- Saved to DOCX locally at `/tmp/tmp_5qe1o4j/extracted_text_SAMN35361964.docx`, then
  **uploaded to the shared Google Drive** at `data/37257865_31153098/SAMN35361964.docx`
  (`additional_pipeline.py:1112-1141`; confirmed in the run log: *"✅ Uploaded 'SAMN35361964.docx'
  to Google Drive folder ID: 1erzjo3JqUjr2i-YzCyObgu5RGBBfakSS"*)
- **Also saved per your request to:** [`contextLLM_SAMN35361964.txt`](./contextLLM_SAMN35361964.txt)
  (160,591 chars — this is the literal `text` variable, i.e. `acc_prompts[acc]`, i.e. what
  `context_for_llm` refers to inside `model.py`)

This one string is fed to `model.query_document_info()` as `prompts = {acc: text}`
(`additional_pipeline.py:1143-1156`) — everything downstream reads from this single blob.

### 5. `model.query_document_info()` (`model.py:1871`) — builds and fires both LLM prompts

Two independent calls to `model.call_llm_api()` happen here, in this order:

#### Call 1 — Pass 1 / structured extraction (`model.multi_prompts()`, `model.py:1096`)

- Output-format field list (10 fields): `country_name, modern/ancient/unknown, study_name,
  subject_id, sample_id, target_condition, control, body_site, sequencing_platform, host_species`
  (`country_name` and `modern/ancient/unknown` auto-prepended since your 8 fields don't
  cover either)
- Because `target_condition` and `control` match disease/condition keywords, the per-subject
  table-lookup instruction block was injected. Because `study_name` is present, the
  "don't answer with an accession number" block was injected too (both confirmed present
  in the saved prompt file).
- **Prompt saved to:** [`call_llm_api_prompt.txt`](./call_llm_api_prompt.txt) — 166,895 characters
- **Answer saved to:** [`call_llm_api_prompt_1_answer.txt`](./call_llm_api_prompt_1_answer.txt) — 5,040 characters
- **API / model used:** **Anthropic Claude, `claude-haiku-4-5-20251001`** (the primary path in
  `model.call_llm_api()`, `model.py:94-116` — succeeded on first try, so Gemini fallback was
  never invoked)

Parsed result line 1 (the 10 pipe-separated values, in field order):
```
Italy | modern | Farina_2019 | 10 | ind10 | moderate-severe periodontitis |
T2D-P- (no type 2 diabetes, no periodontitis) | subgingival oral plaque |
Illumina NextSeq 500 | Homo sapiens
```

#### Call 2 — Pass 2 / generalized sweep (`model._extract_additional_fields()`, `model.py:1688`)

- Same 160,591-char source blob, same accession — but this time the prompt tells the model to
  find **every other** attribute the 10 Pass-1 fields didn't cover (the exclude list in the
  prompt is exactly those 10 field names).
- **Prompt saved to:** [`call_llm_api_prompt_2.txt`](./call_llm_api_prompt_2.txt) — 166,649 characters
- **Answer saved to:** [`call_llm_api_prompt_2_answer.txt`](./call_llm_api_prompt_2_answer.txt) — 2,800 characters
- **API / model used:** **Anthropic Claude, `claude-haiku-4-5-20251001`** (same primary path,
  same reasoning as Call 1)

Parsed result (JSON, 11 additional fields found): `host`, `isolation_source`,
`collection_date`, `geo_loc_name`, `lat_lon`, `library_strategy`, `library_source`,
`library_selection`, `instrument_model`, `dna_extraction_kit`, `organism`.

> Both calls landed on Anthropic because `ANTHROPIC_API_KEY` is set in this environment and
> `call_llm_api()` tries Anthropic first, only falling through to Gemini
> (`gemini-2.5-flash-lite`, tried across `NEW_GOOGLE_API_KEY` → `GOOGLE_API_KEY` →
> `NEW_GEMINI_API` in order) if Anthropic raises. Neither call needed the fallback.

### 6. What did *not* run

Because no standardization schema URL was supplied, these three schema-dependent,
LLM-calling functions were skipped entirely (confirmed by code path, not just by inference —
`standardization_schema == {}` for the whole run):

- `model.standardize_with_llm()` — `model.py:1319`
- `model.align_to_schema()` — `model.py:1401`, gated at `additional_pipeline.py:1259-1260`
- `model.annotate_with_ontologies()` — `model.py:1490`, gated on ontology-mode schema URLs

So **exactly 2 LLM calls** were made for this sample, matching the two prompt/answer file
pairs above.

---

## Final field values for this sample

| Field | Value |
|---|---|
| study_name | Farina_2019 |
| subject_id | 10 |
| sample_id | ind10 |
| target_condition | moderate-severe periodontitis |
| control | T2D-P- (no type 2 diabetes, no periodontitis) |
| body_site | subgingival oral plaque |
| sequencing_platform | Illumina NextSeq 500 |
| host_species | Homo sapiens |

Every value above cites its source (NCBI attribute, paper section/table, or supplementary file)
in the saved answer files — see `call_llm_api_prompt_1_answer.txt` for the full narrative +
citation for each.

---

## Files produced by this run

| File | Contents |
|---|---|
| `contextLLM_SAMN35361964.txt` | The full combined multi-source context text (160,591 chars) fed into both Pass 1 and Pass 2 |
| `call_llm_api_prompt.txt` | Prompt #1 (Pass 1 / structured extraction) sent to `call_llm_api()` |
| `call_llm_api_prompt_1_answer.txt` | Claude's raw response to prompt #1 |
| `call_llm_api_prompt_2.txt` | Prompt #2 (Pass 2 / generalized sweep) sent to `call_llm_api()` |
| `call_llm_api_prompt_2_answer.txt` | Claude's raw response to prompt #2 |
| `_run_meta_SAMN35361964_single_pdf.json` | Full structured run metadata (all of the above plus signals, resolution record, per-source char counts) |
