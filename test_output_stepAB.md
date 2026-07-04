# Step A/B test output

`stepAB_prompt.md` was not found on disk or in git history (likely lost in the
codespace restart before this session started). This report reconstructs the
verification from the task instructions relayed by the user plus the actual
state of the working tree.

## 1. Implementation completeness check

| File | Status |
|---|---|
| `field_aliases.py` (`field_name_matches`) | Complete. Deterministic `FIELD_ALIASES` table + reverse-alias lookup, falling back to an LLM comparison (`_llm_field_name_match`) only when at least one side is unrecognized. Defaults safely to `False` on any parse/call failure. Parses clean with `ast.parse`. |
| `metadata_merge.py` (`merge_metadata_into_table`) | Complete (new file). Corroborates matching values (appends "Confirmed by …"), and on a genuine value clash appends an inline `##CONFLICT: a=x, b=y` marker to the field's value rather than silently overwriting. Parses clean. |
| `api.py` call-site swap | Complete. `import field_aliases` → `import metadata_merge`; the old per-key `canonicalize_field_name` promotion loop was replaced with a `pass2_table` built via `merge_metadata_into_table`, and a `field_sources` dict now feeds a "corroborated by X, Y" source line when more than one source agrees. |
| `additional_pipeline.py` call-site swap | Complete. The schema-alignment block now accumulates an `aligned_batch` dict and merges it in one `metadata_merge.merge_metadata_into_table` call instead of the old per-key `field_aliases.canonicalize_field_name` promotion. |

**One issue found and fixed during this check:** `additional_pipeline.py` still
had `import field_aliases` (line 45) left over after the swap, with no
remaining direct `field_aliases.*` call in the file (it's used transitively
through `metadata_merge`). Removed the dead import.

No other partial edits, leftover references, or syntax problems found.

**Gap identified (not fixed, flagged for follow-up):** `additional_pipeline.py`'s
merge call site only runs inside `if _schema_keys and not _is_ontology_mode and
acc_score.get("_additional_fields")` — i.e. only when the request supplies a
`standardization_url`. Neither test run below supplied one (matching how the
task described the two test cases), so **that specific call site was not
exercised by these tests**, only code-reviewed. The `api.py` call site *is*
unconditional and was exercised and confirmed working (see §3 conflict-marker
evidence). Recommend a follow-up run with a `standardization_url` set to
exercise the `additional_pipeline.py` path end-to-end.

## 2. Test methodology

Both tests ran the real `/analyze` pipeline in-process (`api.analyze()` called
directly against real Anthropic/Gemini/NCBI/ENA APIs — no mocking of the
extraction logic itself). Two things were intentionally neutralized to avoid
side effects on shared infrastructure, since `GCP_CREDS_JSON` in this
environment points at a real, shared "Report" Google Sheet workbook used by
the deployed app for usage tracking/analytics/result-caching:

- Usage-limit checks (`_get_user_config_from_gsheet` / `_get_anon_usage_from_gsheet`) stubbed to a permissive limit instead of consulting/writing the real `UserUsage`/`AnonUsage` sheets.
- Analytics logging and the `KnownCachedSamples` result cache (`_log_analytics`, `_log_to_gsheet`, `_cache_get`, `_cache_save`) stubbed to no-ops, forcing a full re-extraction rather than reading a possibly-stale cache and instead of writing test data into the shared cache.

Everything else — LLM calls, NCBI/ENA lookups, PDF/table extraction, schema
alignment, and the field_aliases/metadata_merge code under test — ran for
real, unmodified.

Context PDFs/xlsx were uploaded via the same `_process_one_upload` extraction
path `/upload-context` uses. One pre-existing, unrelated gap surfaced here:
PDF table extraction for `s41591-018-0061-3.pdf` failed with `No module named
'wordsegment'` — an optional dependency not listed in `requirements.txt` and
not installed. Non-fatal (caught, logged, continued with text-only
extraction); not part of Step A/B, flagged for awareness only.

## 3. PRJNA976261 (ground truth available)

**Pipeline used: rich (`additional_pipeline.pipeline_with_gemini`).** Confirmed
by `[Pass 2]`/`[schema-align]` log activity and the RAG-LLM debug traces for
every one of the 12 samples — the legacy `pipeline.py`/`mtdna_backend.py`
fallback path was never entered.

- `sample_limit=12`, all 12 ground-truth accessions (`SAMN35361955`–`66`) resolved and processed. No unhandled exceptions.
- Output Excel: `PRJNA976261_output.xlsx` (attached).

**Alias-merge correctness (the thing Step A/B specifically changes):**
checked every output row for two different keys that both resolve to the same
`FIELD_ALIASES` canonical concept — **zero unmerged duplicate-alias columns
across all 12 rows.** Also directly observed the conflict-marker round trip
working exactly as designed: `metadata_merge._extend_conflict_marker` embeds
`##CONFLICT: platform=Illumina, Pass 2 (LLM)=NextSeq 500`-style markers when
two sources disagree on the same canonicalized field (e.g. BioSample's
`platform` vs. the LLM's `instrument_model` reading), and `api.py`'s
pre-existing `_emit_field` correctly strips that marker back out into the
row's `conflict` column, leaving a clean display value. This exact pattern
appeared in 10 of 12 rows.

**Value-level accuracy vs. `biosample_metadata.xlsx`:**

| Field | Match rate |
|---|---|
| `geo_loc_name` vs. ground-truth `geographic_location` | 12/12 exact |
| `collection_date` | 12/12 exact |
| `disease`/`disease_status` vs. ground-truth `disease_t2d`/`disease_periodontitis`/`control` flags | 2/12 |

The disease-status mismatch is a real data-quality issue, but it is an LLM
**extraction-accuracy** problem, not an alias/merge problem: in most
mismatches the pipeline emitted a generic study-level description ("type 2
diabetes mellitus and/or periodontitis") rather than the sample-specific
Table 1 status, including for 3 of the 3 actual control (disease-free)
subjects. This is unrelated to the field_aliases/metadata_merge changes under
review — flagging it as a separate, pre-existing pipeline-quality issue worth
a future look, out of scope for this check.

## 4. PRJEB14215 (no ground truth — output only)

**Pipeline used: rich.** Same confirmation method as above (Pass 2/RAG-LLM
activity for all 5 samples).

- `sample_limit=5` (smoke-test cap; the underlying BioProject covers a
  105-patient bariatric-surgery cohort, sample_limit=12 as used for the other
  test would have taken substantially longer without adding verification
  value since there's no ground truth to check against).
- Output Excel: `PRJEB14215_output.xlsx` (attached).
- 4 of 5 samples produced rich metadata (20-45 fields each: geo location,
  host/organism, disease/NAFLD-activity-score, sequencing platform, ENA study
  metadata, etc.).
- `SAMEA4019842` came back with almost no fields. Log shows why: `Warning: No
  valid record or empty record list from NCBI for SAMEA4019842` — an
  NCBI-side resolution gap for this specific accession, not a Step A/B or
  pipeline-crash issue. Handled gracefully (no exception), just sparse output.
- One `Anthropic API error: prompt is too long (208,661 > 200,000 tokens)` on
  the largest sample, automatically and successfully retried against Gemini —
  pre-existing fallback behavior in `model.call_llm_api`, working as intended.

**Alias-merge observation (caveat, not a functional bug):** unlike
PRJNA976261, several rows here retain messy near-duplicate columns from ENA's
deeply-nested attribute names, e.g. `geo_loc_name` alongside
`country_geo_loc_name`, and `host_disease_status` alongside
`host_disease_status__nafld_activity_score`. This is consistent with
`field_name_matches`'s documented safe-fallback design: when a name isn't in
the static `FIELD_ALIASES` table, it defers to an LLM comparison that
defaults to "different" (→ duplicate columns) on any doubt or failure, since
a false "same" verdict is the worse outcome. Given how machine-generated
these ENA field names look, it's plausible the LLM fallback is reasonably
declining to merge them. Since **this specific `additional_pipeline.py` merge
call site never ran in this test** (no `standardization_url` supplied, see
§1), this messiness is coming entirely from the `api.py` merge path acting on
raw ENA/NCBI field names with no schema to normalize them against first — not
unexpected, but worth another look if cleaner ENA output is a priority.

## 5. Overall verdict

Both Step A/B code changes are implemented, syntactically valid, and free of
partial edits. The `api.py` call site is confirmed working correctly end to
end, including the conflict-marker round trip. The `additional_pipeline.py`
call site is implemented correctly by code review but **was not exercised**
by either test (both lacked a `standardization_url`) — recommend a follow-up
run supplying one before considering that path fully verified in practice.
