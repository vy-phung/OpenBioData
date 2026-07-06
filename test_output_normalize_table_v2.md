# `normalize_output_table()` v2 — re-test against PRJNA976261 after fixes

## What changed since v1

1. **`metadata_merge.py`**: added `_PIPELINE_INFRASTRUCTURE_COLUMNS` — a fixed exclusion set of the columns `api._rows_from_new_pipeline()` sets **unconditionally on every row, for any accession**: `biosample_accession`, `bioproject`, `sra_accession` (its initial row-dict literal, `api.py:324`) and `explanation`, `sources`, `confidence_score`, `conflict`, `time_cost` (set unconditionally at the end of every row, `api.py:579-583`). `genbank_accession` was deliberately **not** included — `api.py:329-330` only sets it `if genbank_acc:`, so it isn't unconditional the way the others are. These columns are now skipped entirely from Step 1/Step 2 candidacy — verified by direct inspection of `api._rows_from_new_pipeline()`, not assumed.
2. **`field_aliases.py`**: `_llm_field_name_match()`'s prompt now includes an explicit "IDENTIFIER field vs. DESCRIPTIVE ATTRIBUTE field" distinction, with `subject_id` vs. `host`/`host_species` as the illustrative example, plus a second distinction for narrative/citation columns (e.g. `sources`) vs. substantive data fields — both framed as general principles ("the same reasoning applies to any other ID-vs-attribute pair"), not as a hardcoded pair check.

**Verified by direct code inspection** (AST-walked every `Compare` node in both changed files): no `"PRJNA976261"`, `"disease"`, `"subject_id"`, or `"host"` literal is used in an executable comparison anywhere. The only appearances of `subject_id`/`host`/`host_species` are (a) the new illustrative prompt text sent to the LLM, (b) pre-existing `FIELD_ALIASES` table entries from before this task, (c) comments.

## Headline numbers

| | v1 (before fix) | v2 (after fix) |
|---|---|---|
| Rows | 12 | 12 |
| Columns before normalization | 95 | 117 |
| Columns after normalization | 83 | 106 |
| Base-column merges | 3 | 5 |
| Companion-column merges | 10 | 11 |

(Column counts aren't directly comparable between runs — Pass 2 is a free-form LLM extraction, so it discovered a different set of extra fields this run than last run's; that's expected run-to-run variance, not something either version of `normalize_output_table()` controls.)

## The 3 things you asked me to confirm

| Check | Result |
|---|---|
| `subject_id` no longer merges with `host`/`host_species` | ✅ Confirmed — `subject_id` doesn't appear in the merge log at all this run; it's present, untouched, in both the before and after column lists |
| `sources`/`library_source` no longer merge with `isolation_source` | ✅ Confirmed — none of `sources`, `library_source`, `isolation_source` appear in the merge log; all three present, untouched, before and after |
| `sequencing_platform` + `instrument_model` still merge | ✅ Confirmed — merged into `sequencing_method` again, same as v1 |

## Full merge log (v2)

| Canonical | Merged from | Reason | Conflicts (of 12) | Assessment |
|---|---|---|---|---|
| `sequencing_method` | `sequencing_platform`, `instrument_model` | name-synonym — deterministic (`FIELD_ALIASES`) | 6 | ✅ Correct (same as v1); conflicts are still the `"illuminanextseq"` vs `"NextSeq 500"` formatting artifact, not a real disagreement |
| `host` | `host_species` | name-synonym — deterministic (`FIELD_ALIASES`: `"host": [..., "host_species"]`) | 0 | ✅ Correct, clean, complementary (each row has exactly one of the two populated) |
| `sample_id` | `biosample_id`, `id` | name-synonym — LLM fallback | 0 | ✅ Correct — all three genuinely mean "this sample's own identifier"; no row had more than one populated, so the merge is purely additive |
| `target_condition` | `diabetic_status` | name-synonym — LLM fallback | 1 | ⚠️ **Borderline** — see below |
| `periodontal_status` | `sites_with_probing_depth_gte5mm_number` | name-synonym — LLM fallback | 0 | ⚠️ **Borderline, and produces one bad cell** — see below |
| + 11 companion columns | (explanation/narrative/source_location/id_match companions of the 5 base merges above) | rode along with their base field | 0 | Follows from the base merges — 4 sets good, 1 set (periodontal_status's) inherits the same concern |

## Two residual borderline cases — different failure pattern than v1's, not fixed by this change

These are **not** a repeat of the identifier-vs-attribute or narrative-vs-data-field mistakes the prompt tightening targeted — they're a different kind of over-eager match, so it's expected (not a bug) that this fix didn't catch them:

**`target_condition` + `diabetic_status`** — row 2 has `target_condition="periodontitis"`, `diabetic_status="non-diabetic"`. These are complementary facts about two different axes of this specific study (periodontitis status and diabetes status), not the same fact phrased differently — merging them produced a `##CONFLICT` marker (`"periodontitis ##CONFLICT: target_condition=periodontitis, diabetic_status=non-diabetic"`) for a row where there wasn't really a factual disagreement, just two different partial answers to two related-but-distinct questions.

**`periodontal_status` + `sites_with_probing_depth_gte5mm_number`** — this one actually produces a bad cell: row 10 had `sites_with_probing_depth_gte5mm_number="26"` (a raw measurement) and blank `periodontal_status`; after merging, that row's `periodontal_status` reads `"26"` — a raw count sitting in a column meant to hold a categorical clinical label like `"moderate-severe periodontitis"` (row 1's value). No `##CONFLICT` marker shows here because the two columns never had a value in the same row, so it looks silently correct.

Both trace to `field_name_matches()`'s LLM fallback treating "closely related, but different granularity" fields (a summary/derived label vs. one of its inputs, or two related-but-distinct clinical axes) as the same concept. The prompt tightening in this task specifically taught it to distinguish *identifiers* from *descriptive attributes*, and *narrative/citation columns* from *data fields* — it was not asked to (and didn't) generalize to *derived category vs. raw measurement* or *whole-condition vs. one-axis-of-the-condition*. That would need its own general principle added to the prompt (e.g. "a field that is a computed summary/classification of another field's raw value is not the same concept as that raw value itself") — flagging this as a further fix to consider, not deciding unilaterally to add it now since it's outside what was asked for this round.

## Files

- `metadata_merge.py` — `_PIPELINE_INFRASTRUCTURE_COLUMNS` exclusion set added
- `field_aliases.py` — `_llm_field_name_match()` prompt tightened
- `test-data/PRJNA976261/_run_meta_PRJNA976261_normalize.json` — v2 full before/after table snapshot + merge log (overwrote the v1 snapshot; v1's findings are preserved in `test_output_normalize_table.md`)
