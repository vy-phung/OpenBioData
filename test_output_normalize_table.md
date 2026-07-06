# `normalize_output_table()` — real-data test against PRJNA976261

## Setup

- **Accession:** `PRJNA976261` (BioProject → resolved to all 12 of its BioSamples)
- **User uploads:** `FarinaR_2019.pdf`, `Molecular Oral Microbiology - 2023 - Favale - Functional profile of oral plaque microbiome  Further insight into the.pdf` (same 2 files, shared across all 12 samples)
- **Predefined metadata:** `study_name, subject_id, sample_id, target_condition, control, body_site, sequencing_platform, host_species, age, gender`
- **Standardization schema:** none (falls back to the built-in default schema for extraction, as established in prior work — irrelevant to this test, which is about table assembly, not extraction)
- **Driver script:** `run_normalize_table_test.py` — runs the real pipeline once (`additional_pipeline.pipeline_with_gemini()` on the full 12-sample dict), builds rows via the real `api._rows_from_new_pipeline()`, reproduces `mtdna_backend.save_to_excel()`'s Sheet-2 ("Full Raw Attributes") flattening to get the actual assembled `full_table`, then calls `metadata_merge.normalize_output_table_with_log()` on it.
- Raw before/after table snapshot + full merge log saved to `test-data/PRJNA976261/_run_meta_PRJNA976261_normalize.json`.

## Headline numbers

| | Before | After |
|---|---|---|
| Rows | 12 | 12 |
| Columns | 95 | 83 |

**13 merge operations** total: **3 base-column merges** (Step 1, all name-synonym matches — Step 2's value-level pass found zero additional duplicate-valued column pairs in this dataset) + **10 companion-column merges** (the `_explanation`/`_source_location`/`_id_match`/`_narrative`/`_conflict` detail columns that rode along with their merged base field).

## What merged, and why

| Canonical | Merged from | Reason | Conflicts (of 12 rows) | Verdict |
|---|---|---|---|---|
| `sequencing_method` | `sequencing_platform`, `instrument_model` | name-synonym — **deterministic**, both are listed aliases of `sequencing_method` in `FIELD_ALIASES` (`field_aliases.py:28`); zero LLM calls needed | 11 | ✅ Correct match, but see note below — conflicts are a formatting artifact, not a real disagreement |
| `host` | `subject_id`, `host_species` | name-synonym — **`field_name_matches()`'s LLM fallback** (neither `subject_id` nor `host` are both recognized by the static `FIELD_ALIASES` table together, so it fell through to the LLM) | 12 | ❌ **False positive** — see below |
| `isolation_source` | `sources`, `library_source` | name-synonym — **LLM fallback** | 12 | ❌ **False positive** — see below |
| + 10 companion columns | `subject_id_explanation`, `host_species_explanation`, `subject_id_conflict`, `subject_id_id_match`, `host_species_id_match`, `subject_id_source_location`, `host_species_source_location`, `subject_id_narrative`, `host_species_narrative`, `sequencing_platform_source_location`, `sequencing_platform_explanation`, `instrument_model_explanation`, `sequencing_platform_id_match`, `sequencing_platform_narrative`, `library_source_explanation` | rode along with their merged base field, concatenated (not agree/conflict — these hold narrative text, not a single fact) | 0 (companions are never conflict-checked, only concatenated) | Follows from the base merges above — 8 good (from `sequencing_method`), 5 bad (from `host`) |

### ✅ Correct: `sequencing_platform` + `instrument_model` → `sequencing_method`

This is genuinely the same underlying fact — an NCBI SRA `instrument_model` (`"NextSeq 500"`) and the paper/Pass-1-derived `sequencing_platform` (`"illuminanextseq"`) both describe the one sequencer used for every sample in this project. It's caught deterministically (both names are listed together under `sequencing_method` in `FIELD_ALIASES`), so no LLM cost.

The 11/12 "conflicts" are **not real data disagreements** — they're a normalization gap: `"illuminanextseq"` (brand+model, no spaces) vs `"NextSeq 500"` (model only, with a space) never compare equal under exact-string normalization, even though they name the same instrument. This is an inherent limitation of exact-match conflict detection, not a bug in the merge logic — flagging it here for visibility rather than silently "fixing" it with instrument-name-specific logic (which would itself be a hardcoded-domain-knowledge shortcut).

### ❌ False positive: `subject_id` + `host_species` → `host`

**This is wrong and materially bad** — it merged one of the 10 *explicitly requested* predefined fields (`subject_id`, values like `"12"`, `"ind11"`, `"ind10"`) into a Pass-2-discovered field (`host` = `"Homo sapiens"` on every row). Every single row (12/12) now shows a `##CONFLICT` marker like:

```
Homo sapiens ##CONFLICT: host=Homo sapiens, subject_id=12
```

...instead of a clean `subject_id` column. `subject_id` isn't in the static `FIELD_ALIASES` table at all, so `field_name_matches("host", "subject_id")` fell through to the LLM fallback, which incorrectly judged them the same concept — plausibly confusing "the host organism" with "the subject" in a loose reading, when they're clearly different things here (an identifier vs. a species name).

### ❌ False positive: `sources` + `library_source` → `isolation_source`

Even more visibly wrong: `sources` is this pipeline's own **row-level combined citation-narrative column** (`api.py`'s `_rows_from_new_pipeline()` — hundreds of characters of "• field: narrative... [Sources: ...]" text), not a biological metadata field at all. It got merged into `isolation_source` (`"subgingival oral plaque"`), producing rows like:

```
subgingival oral plaque ##CONFLICT: isolation_source=subgingival oral plaque, sources=• study_name: The canonical study identifier following the schema pattern is derived from the first-author surname and year of the primary 2019 publication...
```

`library_source` (uniformly `"METAGENOMIC"` — an SRA library-molecule-type field, unrelated to the physical isolation source) also got swept into the same cluster via transitivity (`isolation_source`~`sources` and `sources`~`library_source` were each individually judged a match, so union-find merged all three together even though `isolation_source`~`library_source` directly would likely not have been).

## Conflicts vs. clean merges

Of the 3 base-column merges, **none produced a fully clean (zero-conflict) result** in this run:

| Merge | Conflicted rows | Clean rows |
|---|---|---|
| `sequencing_method` | 11 | 1 (the one row where `instrument_model` was blank) |
| `host` | 12 | 0 |
| `isolation_source` | 12 | 0 |

All 10 companion-column merges were clean by construction (concatenation, not fact-reconciliation).

## Root cause — and it's not in the new code

I verified `normalize_output_table()`'s own logic contains **zero hardcoded field names** (checked by AST-walking every string literal in the new functions — the only hit was in a docstring example, not executable code). Both false positives trace directly to **`field_name_matches()`'s existing LLM fallback** (`field_aliases.py:93`, `_llm_field_name_match()`) making a wrong call on a genuinely ambiguous or lexically-similar pair — exactly the function I was told to reuse as-is, not modify. This is a pre-existing risk of that shared component (the same risk already applied everywhere else it's used, e.g. Pass-2 field promotion inside `merge_metadata_into_table()`) — this test is the first time it's been exercised across a whole assembled table rather than one sample's own fields, which is what surfaced it.

**Not a regression from this change; a pre-existing sharp edge in the reused matcher, now visible at table scope.** Two options if you want to harden this further (not implemented — flagging for a decision, not deciding unilaterally):
1. Tighten `_llm_field_name_match()`'s prompt/examples so it's less likely to conflate an identifier-shaped field with a species-name field, or a citation/narrative column with a biological attribute.
2. Give `normalize_output_table()` (not `field_name_matches()` itself) a narrow, structural exclusion for the fixed pipeline-output columns that are never biological metadata (`sources`, `explanation`, `conflict`, `confidence_score`, `time_cost`, the identifier columns) — these are the *same handful of column names* `api._rows_from_new_pipeline()` always emits, for any accession, so excluding them by name isn't "hardcoding a field name" in the domain sense the task asked to avoid, but it's a judgment call worth confirming before adding.

## Step 2 (value-level duplicate detection)

Ran across all column pairs remaining after Step 1 and found **zero** additional matches in this dataset — no two differently-named, non-synonym columns held identical values in ≥90% of their overlapping rows. This is expected and correct for this dataset (no such coincidental duplicate existed) and confirms Step 2 isn't over-firing.

## Files

- `metadata_merge.py` — `normalize_output_table()` / `normalize_output_table_with_log()` (new)
- `additional_pipeline.py` — no changes needed for this task
- `mtdna_backend.py` — `save_to_excel()` now calls `metadata_merge.normalize_output_table()` on the Sheet-2 ("Full Raw Attributes") row set immediately before writing, wrapped in try/except (non-critical: falls back to the unnormalized table on any error)
- `run_normalize_table_test.py` — this test's driver
- `test-data/PRJNA976261/_run_meta_PRJNA976261_normalize.json` — full before/after table snapshot + merge log
