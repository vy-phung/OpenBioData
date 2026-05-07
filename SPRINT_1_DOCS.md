# OpenBioData — Sprint 1 Technical Documentation

Tasks 1, 2, and 3 from `OpenBioData_TechSpec.pdf`.  
**Status:** All three tasks complete and tested.

---

## Table of Contents

1. [Overview](#overview)
2. [Logging](#logging)
3. [Task 1 — NCBI Accession Resolver](#task-1--ncbi-accession-resolver)
4. [Task 2 — Generalized Metadata Extraction](#task-2--generalized-metadata-extraction)
5. [Task 3 — Generalized Confidence Scoring](#task-3--generalized-confidence-scoring)
6. [How to Run All Tests](#how-to-run-all-tests)
7. [Spec Compliance Checklist](#spec-compliance-checklist)

---

## Overview

The end-to-end goal: a user types `PRJNA976261` into the web interface, the tool
resolves all 12 linked BioSamples (Task 1), extracts both predefined and
additional metadata from each using Gemini (Task 2), scores confidence for every
extracted field (Task 3), and outputs a two-sheet Excel file.

```
User input (any NCBI ID)
        │
        ▼
ncbi_resolver.resolve_accessions()        ← Task 1
        │  returns {SAMN: {bioproject, biosample, accession, experiment}}
        ▼
model.query_document_info()               ← Task 2
        │  Pass 1: predefined fields (country, sample_type, niche_cases)
        │  Pass 2: all other key-value metadata → _additional_fields dict
        ▼
confidence_score.compute_confidence_score_and_tier()   ← Task 3
        │  field-agnostic scoring from NCBI signals
        │  calculate_confidence() for text-search based scoring
        ▼
mtdna_backend.save_to_excel()
        │  Sheet 1: predefined fields only  (cMD Metadata)
        │  Sheet 2: predefined + all additional fields  (All Attributes)
        ▼
Output Excel (.xlsx)
```

---

## Logging

All three task modules write structured logs to **`logs/openbiodata.log`** via
the central `openbiodata_logger.py` module.

### Log format

```
[YYYY-MM-DD HH:MM:SS.mmm] [LEVEL   ] [openbiodata.module_name] message
```

Example:
```
[2024-01-15 10:30:45.123] [INFO    ] [openbiodata.ncbi_resolver] resolve_accessions: Input 'PRJNA976261' -> detected type: bioproject
[2024-01-15 10:30:45.234] [INFO    ] [openbiodata.ncbi_resolver]   [BioProject] Strategy 2 found 36 UIDs via elink
[2024-01-15 10:30:52.101] [WARNING ] [openbiodata.ncbi_resolver]   [BioProject] WARNING: no BioSamples found for PRJNA000000
```

### Behaviour
- File is **appended** on every run — never overwritten.
- `INFO` and above go to both the log file and `stdout`.
- `DEBUG` goes to the log file only (detailed scoring breakdowns).
- Each module gets its own child logger so log lines are always traceable.

### Using the logger in any module

```python
from openbiodata_logger import get_logger
log = get_logger(__name__)

log.info("Resolving %s", accession_id)
log.warning("No BioSample found for %s — using accession as key", acc)
log.error("NCBI API call failed: %s", exc)
log.debug("calculate_confidence: field=%s score=%d", field_name, score)
```

---

## Task 1 — NCBI Accession Resolver

**File:** `ncbi_resolver.py`  
**Spec section:** 1.1 – 1.5

### What it does

Takes any single NCBI identifier and returns a standardised Python dict keyed
by BioSample ID. Every value dict always has exactly four string fields:

```python
{
  'SAMN23469632': {
    'bioproject':  'PRJNA783802',   # '' if not found — never None
    'biosample':   'SAMN23469632',  # '' if not found — never None
    'accession':   'OL757400',      # '' if not found — never None
    'experiment':  'SRR17084312'    # '' if not found — never None
  }
}
```

### Supported input types

| Input example     | Detected type      | Resolver called             |
|-------------------|--------------------|-----------------------------|
| `PRJNA976261`     | `bioproject`       | `resolve_from_bioproject`   |
| `SAMN23469632`    | `biosample`        | `resolve_from_biosample`    |
| `OL757400`        | `genbank`          | `resolve_from_genbank`      |
| `SRR17084312`     | `sra_run`          | `resolve_from_sra`          |
| `SRX12345678`     | `sra_experiment`   | `resolve_from_sra`          |
| `ERR123456`       | `sra_run`          | `resolve_from_sra`          |
| `DRR123456`       | `sra_run`          | `resolve_from_sra`          |
| `UNKNOWN_XYZ`     | `unknown`          | `resolve_from_genbank` (fallback) |

### Key design decisions

1. **No Biopython `elink`** — `Entrez.read()` crashes on NCBI's DOCTYPE
   external entity references in elink responses. Instead we use `esearch` with
   field tags (`[BioSample]`, `[BioProject]`) and parse `efetch` flat-files.

2. **BioProject 3-strategy fallback**
   - Strategy 1: `esearch biosample [BioProject]` field tag
   - Strategy 2: Raw HTTP elink URL, parse `<Id>` tags from XML
   - Strategy 3: Search SRA with `[bioproject]`, parse `expxml` for `SAM...` accession

3. **Empty string, never `None`** — `_empty_record()` guarantees all four keys
   always exist and are strings. This prevents downstream NaN/null bugs.

4. **Rate limiting** — `_safe_sleep()` adds 0.15s between every NCBI API call.
   If you see `RemoteDisconnected` errors, increase `_SLEEP` to `0.4`.

### Public API

```python
from ncbi_resolver import resolve_accessions, detect_accession_type

# Detect type only
detect_accession_type('SAMN23469632')   # -> 'biosample'
detect_accession_type('PRJNA783802')    # -> 'bioproject'

# Resolve a single accession (any type)
result = resolve_accessions('PRJNA976261')
# Returns dict with one entry per BioSample found

# Resolve a BioSample directly
result = resolve_accessions('SAMN23469632')
# {'SAMN23469632': {'bioproject': 'PRJNA783802', ...}}
```

### Error handling

- Every NCBI call is wrapped in `try/except`. On failure the function returns
  `''` for that field — it never crashes.
- Unknown identifiers fall back to `resolve_from_genbank`; if not found there,
  they return a dict keyed by the original input with all empty-string values.
- BioProject with no BioSamples returns `{}` (empty dict, not an error).

---

## Task 2 — Generalized Metadata Extraction

**Files:** `model.py`, `pipeline.py`, `mtdna_backend.py`  
**Spec section:** 2.1 – 2.3

### What it does

Adds a second LLM extraction pass (Pass 2) inside `query_document_info()` that
captures **all** metadata key-value pairs from the source document that were not
already extracted by the predefined field list (Pass 1).

The extra fields are stored in an `_additional_fields` dict and propagated
through the entire pipeline to produce a two-sheet Excel output.

### Two-pass extraction flow

**Pass 1** (existing, unchanged):
- Extracts predefined fields: `country_name`, `modern/ancient/unknown`, and
  whatever fields the user listed in `niche_cases`.

**Pass 2** (new — `_extract_additional_fields`):
- Sends the same document context to Gemini with a generalised prompt.
- Excludes all Pass 1 field names so there is no duplication.
- Returns a `{field_name: value}` dict of whatever else was found.
- Stored in `outputs[acc]['_additional_fields']`.

### _additional_fields propagation

```
model.query_document_info()
  └─ outputs[acc]['_additional_fields'] = {...}

pipeline.pipeline_classify_sample_location_cached()
  └─ acc_score['_additional_fields'] = predicted_output_info[acc].get('_additional_fields', {})

mtdna_backend.summarize_results()
  └─ row['_additional_fields'] = outputs[key].get('_additional_fields', {}) or {}

mtdna_backend.save_to_excel()
  └─ Sheet 1: drops _additional_fields key (predefined columns only)
  └─ Sheet 2: flattens _additional_fields into one column per unique key
```

### Excel output structure

**Sheet 1 — "cMD Metadata"** (journal-ready, clean):
```
Sample ID | Predicted Country | Country Explanation | ... | Sources | Time cost | Confidence Score
```

**Sheet 2 — "All Attributes"** (everything the LLM found):
```
Sample ID | Predicted Country | ... | disease_status | host_age | body_site | tissue_type | ...
```
- Columns are union of all `_additional_fields` keys across all samples.
- Cells are **blank** (not `'N/A'`, not `'nan'`) when a sample lacks a field.
- `is_resume=True` merges with an existing file by Sample ID, adding new columns
  to Sheet 2 if later batches produce new field types.

### Error handling

- `_extract_additional_fields` returns `{}` on any LLM or JSON parse failure
  and logs a warning. It never interrupts the main pipeline.
- Pass 2 is wrapped in its own `try/except` in `query_document_info` — if it
  fails entirely, `_additional_fields = {}` is set and processing continues.
- `save_to_excel` handles both list-of-dicts and DataFrame inputs; empty rows
  produce an early return with a warning log.

---

## Task 3 — Generalized Confidence Scoring

**File:** `confidence_score.py`  
**Spec section:** 3.1 – 3.2

### What it does

Provides two complementary scoring functions that work for **any** metadata
field — not just `country` or `sample_type`.

### `calculate_confidence(field_name, predicted_value, sources)`

Text-search based scorer. Use when you have raw source documents.

```python
result = calculate_confidence(
    field_name='disease_status',
    predicted_value='Type 2 Diabetes',
    sources={
        'ncbi_biosample': 'Patient diagnosed with Type 2 Diabetes...',
        'linked_paper':   'T2D cohort from the UK Biobank...',
    }
)
# {'score': 40, 'label': 'medium', 'flags': ['publication_confirmed'], 'explanation': '...'}
```

**Scoring table:**

| Component | Logic | Points |
|---|---|---|
| Source count | +10 per source containing the value | max +40 |
| Publication evidence | value in `ncbi_publication` / `linked_paper` source | +20 |
| BioSample evidence | value in `ncbi_biosample` / `ncbi_accession` source | +10 |
| Accession keyword | `ncbi_accession` source confirms value | +10 |
| Conflict marker | `##` in predicted value | -20 |

**Edge cases:**
- Empty or `None` predicted value → `score=0`, `label='not found'`, flag `no_value`
- `##` in value (upstream conflict signal) → flag `conflict_detected`, `-20` penalty
- No sources provided → `score=0`
- Score always clamped to `[0, 100]`

### `compute_confidence_score_and_tier(signals, rules)`

Signals-based scorer. Used by the main pipeline for structured NCBI metadata.

**Now field-agnostic:** reads generic `predicted_field` / `genbank_field` signal
keys, with fallback to the legacy `predicted_country` / `genbank_country` keys
for backward compatibility. Which field is being scored is identified by the
optional `field_name` signal key (defaults to `'country'`).

```python
signals = {
    'field_name':             'disease_status',   # new generic key
    'predicted_field':        'healthy',           # new generic key
    'genbank_field':          'healthy',           # new generic key
    'has_geo_loc_name':       False,
    'has_pubmed':             True,
    'accession_found_in_text': True,
    'num_publications':       2,
    'missing_key_fields':     False,
    'known_failure_pattern':  False,
}
score, tier, explanations = compute_confidence_score_and_tier(signals)
```

**Backward-compatible:** existing pipeline signals using `predicted_country` /
`genbank_country` still work without any changes.

### Error handling

- `standardize_location` is imported lazily to avoid pulling in the full
  `faiss`/`model` stack when `confidence_score` is used standalone.
- If `standardize_location` is unavailable, country normalization degrades
  gracefully (uses raw string comparison instead).
- All inputs are coerced to strings before comparison — no `AttributeError`
  on unexpected types.

---

## How to Run All Tests

```bash
# From the repo root:
python test_tasks_1_3.py
```

Expected output:
```
Results: 46 passed, 0 failed out of 46 checks
Overall: ALL CHECKS PASSED
```

### What the test covers

| Section | Checks |
|---|---|
| Task 1 — Type detection | 12 accession patterns correctly classified |
| Task 1 — UNKNOWN input | Returns dict, does not crash |
| Task 1 — BioSample | Resolves all 4 required fields, no None values |
| Task 1 — SRR run | Resolves back to parent BioSample |
| Task 1 — BioProject | Returns 12 BioSamples for PRJNA976261 (Svetlana's case) |
| Task 2 — Data contract | `_additional_fields` key present, is a dict |
| Task 2 — Excel output | Two sheets, correct columns, blanks not N/A |
| Task 3 — Empty value | Score 0, label 'not found', flag 'no_value' |
| Task 3 — Source hits | Score increases with more confirming sources |
| Task 3 — Conflict ## | `conflict_detected` flag set, score penalised |
| Task 3 — Any field | Works for 'country', 'disease_status', etc. |
| Task 3 — Backward compat | Old `predicted_country`/`genbank_country` signals still work |
| Task 3 — Empty signals | Does not crash |

### Checking the log file

After any run (test or live), inspect the log:

```bash
# Latest 50 lines
tail -50 logs/openbiodata.log

# Only warnings and errors
grep -E "\[WARNING|\[ERROR" logs/openbiodata.log

# All Task 1 activity
grep "ncbi_resolver" logs/openbiodata.log
```

---

## Spec Compliance Checklist

### Task 1
- [x] `detect_accession_type` returns correct string for all 6 types
- [x] Returns dict keyed by BioSample ID (SAMN.../SAMEA...)
- [x] All four required keys present: `bioproject`, `biosample`, `accession`, `experiment`
- [x] Empty string used instead of `None` / `null` when identifier not found
- [x] BioProject returns one entry per BioSample (up to `MAX_SAMPLES=100`)
- [x] GenBank / SRR inputs resolved to parent BioSample
- [x] `UNKNOWN_ID` does not crash — returns dict with empty fields
- [x] `ERR` (ENA) and `DRR` (DDBJ) runs also detected as `sra_run`

### Task 2
- [x] Pass 2 extracts ALL additional key-value metadata not in predefined list
- [x] `_additional_fields` dict propagates through model → pipeline → summarize_results
- [x] Sheet 1 ("cMD Metadata") contains predefined fields only
- [x] Sheet 2 ("All Attributes") contains predefined + all additional fields
- [x] Missing cells in Sheet 2 are blank (not `'N/A'`, not `'nan'`, not `'None'`)
- [x] `is_resume=True` merges correctly by Sample ID

### Task 3
- [x] `calculate_confidence(field_name, predicted_value, sources)` function added
- [x] Works for any field name, not just `country` or `sample_type`
- [x] Score = 0 when predicted value is empty
- [x] `conflict_detected` flag set when `##` in predicted value
- [x] `compute_confidence_score_and_tier` uses generic `predicted_field`/`genbank_field` signal keys
- [x] Backward compatible with legacy `predicted_country`/`genbank_country` signals
- [x] `standardize_location` imported lazily — no crash if full model stack not loaded

### All tasks
- [x] Structured file logging with millisecond timestamps (`logs/openbiodata.log`)
- [x] Log file appends — never overwrites previous runs
- [x] All exceptions caught and logged — nothing crashes the pipeline
- [x] `print()` calls replaced with `log.info()` / `log.warning()` / `log.debug()`
