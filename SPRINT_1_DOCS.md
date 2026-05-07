# OpenBioData — Sprint 1 Technical Documentation

Tasks 1–5 from `OpenBioData_TechSpec.pdf`.
**Status:** All five tasks complete and tested (108/108 checks pass).

---

## Table of Contents

1. [Overview](#overview)
2. [How to Run the Tool](#how-to-run-the-tool)
3. [Logging](#logging)
4. [Task 1 — NCBI Accession Resolver](#task-1--ncbi-accession-resolver)
5. [Task 2 — Generalized Metadata Extraction](#task-2--generalized-metadata-extraction)
6. [Task 3 — Generalized Confidence Scoring](#task-3--generalized-confidence-scoring)
7. [Task 4 — Web Interface Input Handling](#task-4--web-interface-input-handling)
8. [Task 5 — Standardize Output Schema](#task-5--standardize-output-schema)
9. [How to Run All Tests](#how-to-run-all-tests)
10. [Spec Compliance Checklist](#spec-compliance-checklist)
11. [Known Issues and Technical Notes](#known-issues-and-technical-notes)

---

## Overview

The end-to-end flow: a user types any NCBI identifier into the web interface,
the tool resolves it to BioSamples, extracts metadata via Gemini, scores
confidence, and produces a two-sheet Excel file.

```
User input (any NCBI ID, comma/newline separated)
        │
        ▼
input_handler.build_pipeline_input()          ← Task 4
        │  parse → resolve via NCBI → pipeline-ready dict
        │  "Could not resolve X — skipping" for unknowns
        ▼
ncbi_resolver.resolve_accessions()            ← Task 1
        │  {SAMN: {bioproject, biosample, accession, experiment}}
        ▼
model.query_document_info()                   ← Task 2
        │  Pass 1: predefined fields (country, sample_type, niche_cases)
        │  Pass 2: _additional_fields from generalized LLM extraction
        ▼
confidence_score.compute_confidence_score_and_tier()   ← Task 3
        │  field-agnostic scoring
        ▼
mtdna_backend.save_to_excel()                 ← Task 5
        │  Sheet 1: "cMD Metadata"   — predefined fields, spec column names
        │  Sheet 2: "All Attributes" — predefined + all additional fields
        ▼
Output: batch_output_live.xlsx
```

---

## How to Run the Tool

### Web interface (Gradio — this is the webpage)

```bash
pip install -r requirements.txt
python app.py
```

The terminal prints a local URL like `http://127.0.0.1:7860` — open it in
your browser. This is the full tool.

**Input box:** paste any NCBI ID — accepts BioProject (`PRJNA976261`),
BioSample (`SAMN23469632`), GenBank (`OL757400`), SRR/SRX, or any mix
comma/newline separated.

**Labels field:** optional custom metadata fields to extract (niche_cases),
e.g. `disease_status, subject_id, body_site`.

**▶ Run Audit:** submits and runs the full pipeline. Progress shown live.

**Download Output:** appears when done — Excel with two sheets.

Deployed live at the Hugging Face Space `VyLala/BioMetadataAudit`.

### Tests only (no API keys needed)

```bash
python test_tasks_1_2_3.py
```

---

## Logging

All modules log to **`logs/openbiodata.log`** via `openbiodata_logger.py`.

```
[2024-01-15 10:30:45.123] [INFO    ] [openbiodata.ncbi_resolver] Resolving PRJNA976261...
[2024-01-15 10:30:52.101] [WARNING ] [openbiodata.input_handler] Could not resolve FOO — skipping
```

- File **appends** on every run — never overwritten
- `INFO`+ → log file and console; `DEBUG` (scoring detail) → file only
- `logs/` in `.gitignore` — not committed

```python
from openbiodata_logger import get_logger
log = get_logger(__name__)
log.info("Resolving %s", accession_id)
log.warning("No BioSample for %s", acc)
```

---

## Task 1 — NCBI Accession Resolver

**File:** `ncbi_resolver.py`

Resolves any NCBI identifier to a dict keyed by BioSample ID. All four value
fields are always strings — never `None`.

```python
from ncbi_resolver import resolve_accessions, detect_accession_type

detect_accession_type('SAMN23469632')   # -> 'biosample'
resolve_accessions('PRJNA976261')       # -> 12-entry dict
resolve_accessions('UNKNOWN_XYZ')       # -> {acc: empty_record}, never crashes
```

**Supported types:** `bioproject` (PRJNA/PRJEB), `biosample` (SAMN/SAMEA),
`genbank` (OL/MT/NC_/etc.), `sra_run` (SRR/ERR/DRR), `sra_experiment` (SRX),
`unknown` (fallback tries GenBank).

**BioProject 3-strategy fallback:**
1. `esearch biosample [BioProject]`
2. Raw HTTP elink URL
3. SRA fallback via `expxml` parsing

---

## Task 2 — Generalized Metadata Extraction

**Files:** `model.py`, `pipeline.py`, `mtdna_backend.py`

Adds a second LLM pass that captures every metadata key-value pair the LLM
finds beyond the predefined list. Results flow through `_additional_fields`
all the way to Sheet 2 of the Excel output.

**Pass 1:** predefined fields (country, sample type, user's niche_cases).
**Pass 2 (`_extract_additional_fields`):** everything else the LLM finds.

**Excel:**
- Sheet 1 `"cMD Metadata"` — predefined only, journal-ready
- Sheet 2 `"All Attributes"` — predefined + all extra fields; blank cells where
  a field wasn't found for that sample (never `'N/A'`)

---

## Task 3 — Generalized Confidence Scoring

**File:** `confidence_score.py`

### `calculate_confidence(field_name, predicted_value, sources)`

Works for any field, not just country.

| Component | Points |
|---|---|
| Source count | +10 per hit, capped at +40 |
| Publication confirmed | +20 |
| BioSample confirmed | +10 |
| Accession keyword | +10 |
| Conflict marker `##` | -20 |
| Empty value | score = 0, label = `not found` |

### `compute_confidence_score_and_tier(signals, rules)`

Signals-based, pipeline entry point. Now field-agnostic:
- Generic keys: `predicted_field`, `genbank_field`, `field_name`
- Legacy keys: `predicted_country`, `genbank_country` still work

---

## Task 4 — Web Interface Input Handling

**Files:** `input_handler.py` (new), `app.py`, `mtdna_backend.py`

### `input_handler.py`

```python
from input_handler import parse_user_input, build_pipeline_input, get_pipeline_accession

# Split any raw text into clean tokens
parse_user_input("PRJNA976261, OL757400\nSRR17084312")
# -> ['PRJNA976261', 'OL757400', 'SRR17084312']

# Resolve all tokens, get skipped messages for failures
resolved_dict, skipped = build_pipeline_input("PRJNA976261")
# resolved_dict = 12-entry dict; skipped = []

# Best accession to feed pipeline: GenBank > SRR > SAMN > fallback
get_pipeline_accession(entry, fallback="SAMN001")
```

### `app.py` changes
- Input placeholder: `"Enter accession IDs... Accepts BioProject, BioSample, GenBank, SRR/SRX"`
- Button: **"▶ Run Audit"**
- Resolution step: shows `"Resolving accessions..."` before pipeline starts
- Unresolvable IDs: inline `"Could not resolve [ID] — skipping"` message
- `is_valid_accession()` now uses `detect_accession_type()` — accepts all NCBI types

---

## Task 5 — Standardize Output Schema

**File:** `mtdna_backend.py`

### Column names (spec 5.3 — lowercase field names)

| Old | New |
|---|---|
| `Predicted Country` | `Predicted country` |
| `Country Explanation` | `country explanation` |
| `Predicted Sample Type` | `Predicted sample type` |
| `Sample Type Explanation` | `sample type explanation` |
| `Predicted disease_status` | `Predicted disease status` |

### `truncate_cell()` NaN protection

`None`, `float('nan')`, `"nan"`, `"None"`, `"null"` → all become `""`.
No null values ever appear in Excel cells.

### Confidence score format (spec 5.4)
```
medium (40)
Accession keyword found in extracted external text.
No contradiction detected across available sources.
```
Tier is lowercase. Score is integer. Lines joined with `\n`.

---

## How to Run All Tests

```bash
python test_tasks_1_2_3.py
```

Expected output:
```
Results: 108 passed, 0 failed out of 108 checks
Overall: ALL CHECKS PASSED
```

| Task | Checks |
|---|---|
| Task 1 | 26 — type detection, 4 resolver types, PRJNA976261 → 12 BioSamples |
| Task 2 | 6 — `_additional_fields` structure, two-sheet Excel |
| Task 3 | 21 — `calculate_confidence` edge cases, backward compat |
| Task 4 | 31 — input parsing, resolution errors, `get_pipeline_accession` priority, `is_valid_accession` |
| Task 5 | 24 — `truncate_cell` null forms, column names, confidence format |

---

## Spec Compliance Checklist

### Task 1
- [x] Type detection correct for all patterns incl. `DRR` (DDBJ)
- [x] Dict keyed by BioSample ID; all 4 required keys always present
- [x] Empty string not `None` when not found
- [x] BioProject expands to all BioSamples (max 100)
- [x] Unknown ID does not crash

### Task 2
- [x] Pass 2 captures all metadata beyond predefined list
- [x] `_additional_fields` propagates through full pipeline
- [x] Sheet 1 predefined only; Sheet 2 predefined + extras
- [x] Missing extra-field cells blank (not `'N/A'`)
- [x] `is_resume=True` merges by Sample ID

### Task 3
- [x] `calculate_confidence` works for any field name
- [x] Score 0 on empty value; `conflict_detected` flag on `##`
- [x] `compute_confidence_score_and_tier` uses generic signal keys
- [x] Backward compatible with legacy signal keys
- [x] Lazy `standardize_location` import — no crash without faiss

### Task 4
- [x] `parse_user_input` splits all separators, deduplicates
- [x] `build_pipeline_input` returns skipped list for unknowns
- [x] `get_pipeline_accession` priority: GenBank > SRR > SAMN > fallback
- [x] Placeholder text and button label match spec
- [x] "Resolving accessions..." progress shown
- [x] "Could not resolve [ID] — skipping" inline
- [x] `is_valid_accession` accepts all NCBI types

### Task 5
- [x] Column names lowercase with spaces per spec 5.3
- [x] `truncate_cell` handles all null forms
- [x] Confidence score format: `tier (score)\nflag1\nflag2`
- [x] No `"nan"`, `"None"` in any output cell

### All tasks
- [x] File logging with ms timestamps, appending (`logs/openbiodata.log`)
- [x] All exceptions caught — nothing crashes the pipeline
- [x] 108 checks, 0 failures

---

## Known Issues and Technical Notes

**NCBI API reliability**
Every call wrapped in `try/except`, returns `''` on failure. BioProject
resolution shows "Resolving accessions..." progress. If rate-limited,
increase `_SLEEP` in `ncbi_resolver.py` from `0.15` to `0.4`.

**NaN bug (fixed)**
`truncate_cell()` coerces all null variants to `""`. Pattern:
`str(row.get('field', '') or '').strip()` applied everywhere.

**SARS-CoV-2 contamination (deferred)**
NCBI "associated records" sometimes pulls COVID-19 records into BioProject
results. Logged as warning. Full taxonomy-distance filter deferred to Sprint 2.

**Collector name normalisation (deferred)**
Chinese name order mismatch (~64% accuracy issue). Fix: reverse if first token
is in known Chinese surname list. Deferred to Sprint 2.

**faiss not installed locally**
`confidence_score.py` imports `standardize_location` lazily (try/except).
Tests pass without faiss. Full stack only needed at HF deployment runtime.
