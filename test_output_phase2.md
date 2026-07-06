# Phase 2 test output — Bug 0-4 accuracy fixes

## Summary verdict

**Partial success — do not consider this fully done.** Bugs 0, 2, 3, and 4 are
implemented and verified working correctly end-to-end. Bug 1 (table-ID lookup
priority) changed the model's *behavior* as designed (it now actively searches
for and cites per-sample table rows with an explicit `[ID-match: true/false]`
flag, instead of falling back to generic study-level prose) but did **not**
raise the disease/group-status match rate to an acceptable level for
PRJNA976261: **3 of 12 samples** match ground truth on this run, versus 2/12
in the pre-Phase-2 baseline. Root cause identified below — it is a genuine
source-data limitation, not a fixable prompt-wording problem, but I want to be
explicit that the specific accuracy goal for Bugs 1/2 was not met.

## What actually shipped (Steps 1-5)

- **Bug 0** (`model.py`): removed all T2D/periodontitis-specific text from
  the few-shot output example and `_disease_hint`; replaced with generic
  placeholders (`GroupA`/`ConditionX`/`Spain`). Removed the hardcoded
  `disease_t2d`/`disease_periodontitis`/`hypoglycemic_medication`/
  `metabolic_control` priority set in `additional_pipeline.py`; auto-niche
  field ordering now reads the schema's own `required` column (added CSV
  parsing for it — confirmed present in the real cMD data dictionary).
- **Bug 1** (`model.py`, two locations): added a generic "PRIORITY RULE" (a
  numbered-table row matching the sample's own numeric ID beats general
  prose) to **both** `multi_prompts()`'s `_disease_hint` (used when
  `niche_cases`/`metadata_fields` are supplied) and `_extract_additional_fields()`'s
  `generalized_prompt` (used for the free-form Pass-2 extraction — the path
  PRJNA976261's test actually exercises, since it supplies no fields). This
  second location was **missed on the first pass** — the initial fix only
  touched `multi_prompts()`, and PRJNA976261's results were unchanged until I
  found and fixed this. Also added a DISAMBIGUATION clause after finding that
  the paper's Table 1 splits subjects into 4 per-condition-group sub-lists,
  each independently numbered `#1`/`#2`/`#3` — a bare index alone is
  ambiguous across groups. See root-cause section below for why this
  refinement still wasn't sufficient.
- **Bug 2** (`model.py`, both locations): `control` now has an explicit
  definition (fully-unaffected/reference group, not just "not the primary
  condition") wherever it's mentioned, with instructions to output `unknown`
  rather than guess "case" when unsure. A code comment notes multi-axis field
  extraction was considered and deferred.
- **Bug 3** (`metadata_merge.py`, `additional_pipeline.py`, `api.py`): new
  `is_duplicate_identifier_value()` helper rejects a field's value (→
  `unknown`) when it exactly duplicates a *different* identifier column's
  value (biosample/bioproject/sra/genbank accession), exempting a field
  legitimately reporting its own matching identifier. Wired into
  `merge_metadata_into_table()` **and** directly into
  `additional_pipeline.py`'s niche-case answer acceptance (discovered on
  testing that niche-case answers bypass `merge_metadata_into_table`
  entirely, so the check needed a second call site — see confirmed rejections
  below). Added a `multi_prompts()` instruction for study_name-type fields to
  use the paper's own naming convention, not an accession.
- **Bug 4** (`model.py`, `additional_pipeline.py`, `confidence_score.py`,
  `api.py`): model's output contract extended with a per-field `[ID-match:
  true|false]` tag; `additional_pipeline.py` parses it for
  categorical/group-type fields and sets a new row-level
  `any_key_field_lacked_id_linkage` signal; `confidence_score.py` adds a
  weighted penalty **and** a hard cap (tier can never read "High" when the
  signal is true — verified independently in isolation); `api.py` wires the
  signal through unchanged by the merge-shape change (it's a row-level bool
  computed upstream, not derived from `field_sources`).

## Root cause: why Bug 1/2's per-sample accuracy is still low

The paper's Table 1 ("Characteristics of the study population") has **no
column linking a subject to a BioSample accession or an `ind#` label**. It is
structured as 4 independent group blocks, each internally numbered `#1`-`#3`:

```
T2D + P+ #1  Male  67  1990  7.3  24  18  30
             #2  Female  60  2000  8.0  24  29  36
             #3  Male    70  2003  7.4  20  26  44
T2D + P- #1  Male  47  2012  8.0  26   0  18
...
```

Cross-checking the raw NCBI BioSample XML confirms the sample's own `id`
attribute (e.g. `id="12"` for SAMN35361966) is **just a restatement of its own
`ind12` submitter label** — not a foreign key into Table 1. BioSample XML for
these samples carries no age/sex/clinical attributes either. So there is no
field, in any of the source text actually provided (the two uploaded PDFs +
NCBI records), that states which specific Table-1 row corresponds to which
`ind#`/accession. The model's confident "BioSample id attribute maps to
Table 1 row N" claims are not grounded in anything present in the text — it
was consistently defaulting to the first row it encountered (`T2D + P+ #1`)
regardless of which sample was being analyzed (verified: for both
SAMN35361959 and SAMN35361964, the model reported the *exact* clinical values
of `T2D + P+ #1` — age 67, male, HbA1c 7.3%, 24 teeth, 18 sites — despite
these being two different, and actually control, samples per ground truth).

This means the disease/group ground truth for this dataset can only be
determined from information **not present** in the two source documents
supplied to the pipeline — the `biosample_metadata.xlsx` answer key must have
been compiled with access to something else (author correspondence, a
supplementary sample sheet, etc.). No prompt refinement can invent a linking
key that doesn't exist in the text. I made two attempts at the
disambiguation wording (see Bug 1 above) and confirmed via direct citation
inspection that the model's underlying behavior didn't change — it isn't
actually parsing group sub-structure, it's pattern-matching to the first/most
salient table entry.

## PRJNA976261 — full comparison to ground truth (12/12 samples, rich pipeline)

Disease/group match rate: **3/12** (SAMN35361955, 35361957 correct by full
condition match; see full table below for all 12).

| Accession | ind# | GT (t2d/perio/control) | Match? | Output disease field(s) |
|---|---|---|---|---|
| SAMN35361955 | ind1 | yes/yes/no | ✅ | `disease`: "T2D + P+ (type 2 diabetes mellitus with moderate-severe periodontitis)" |
| SAMN35361956 | ind2 | yes/no/no | ❌ | `disease`: "type 2 diabetes and periodontitis" (perio wrongly included) |
| SAMN35361957 | ind3 | yes/yes/no | ✅ | `disease`: "Type 2 Diabetes Mellitus and moderate-severe periodontitis" |
| SAMN35361958 | ind4 | no/yes/no | ❌ | `disease`: "no periodontitis, type 2 diabetes mellitus" (both flags inverted) |
| SAMN35361959 | ind5 | no/no/**yes (control)** | ❌ | `disease`: "type 2 diabetes mellitus; periodontitis" (both wrongly present) |
| SAMN35361960 | ind6 | yes/no/no | ❌ | `disease`: "moderate to severe periodontitis; type 2 Diabetes Mellitus" (perio wrongly included) |
| SAMN35361961 | ind7 | yes/yes/no | ✅* | `disease`: "type 2 diabetes and moderate-severe periodontitis" (mentions both; scored correct by presence, though citation quality varies) |
| SAMN35361962 | ind8 | no/no/**yes (control)** | ❌ | `disease_status`: "type 2 diabetes with periodontitis" (both wrongly present) |
| SAMN35361963 | ind9 | no/yes/no | ❌ | `disease`: "type 2 diabetes and moderate-severe periodontitis" (t2d wrongly included) |
| SAMN35361964 | ind10 | no/no/**yes (control)** | ❌ | `disease`: "Type 2 diabetes and moderate to severe periodontitis" (both wrongly present) |
| SAMN35361965 | ind11 | no/yes/no | ❌ | `disease`: "Type 2 Diabetes Mellitus AND moderate-severe periodontitis" (t2d wrongly included) |
| SAMN35361966 | ind12 | yes/no/no | ❌ | `disease`: "type 2 diabetes mellitus and moderate-severe periodontitis" (perio wrongly included) |

*ind7 counted correct by keyword-presence scoring (both conditions genuinely
present per GT); note the earlier finding that the model tends to converge on
"both conditions present" as a default regardless of sample — this is
consistent with the root-cause finding, not evidence of a real per-sample
match for every case scored "correct."

**Other checks requested:**
- Explanations do cite tables/IDs now (`"BioSample numeric id attribute is '10'... corresponds to subject ID 10 in Table 1"`, `[ID-match: true]`) rather than generic BioProject prose — a real behavioral change — but the citations are frequently wrong per the root-cause analysis above.
- No literal `T2D+P+`-style artifact appears on a sample that isn't actually T2D+P+ — the only occurrence (SAMN35361955) is on the one sample where it's correct.
- `study_name` never duplicates `bioproject`/`biosample_accession` in any of the 12 rows (values seen: "Farina et al. 2019", "Farina et al., 2019 / Favale et al., 2023", etc. — all real, non-accession strings).
- `library_strategy` shows `WGA` (whole-genome amplification) for 2 of 12 samples instead of `WGS` — a new inconsistency observed this run, likely LLM stochasticity, not connected to Bugs 0-4; flagging for awareness, not fixed (out of scope).
- Confidence score/tier reflects the new signal: `any_key_field_lacked_id_linkage=True` fired for 2/12 samples (SAMN35361957, SAMN35361965), both dropped from what would be 70/High to 55/Medium. The other 10 samples show `False` — because the model self-reports `[ID-match: true]` even when (per the root-cause finding) it's factually wrong. This is an inherent limit of a **self-reported** signal: it only flags cases where the model itself recognizes uncertainty, not cases where it's confidently incorrect. The signal and cap mechanism themselves are verified correct in isolation (unit-tested separately, see Bug 4 section).

Full per-sample field dump (all extracted columns, not just disease) available on request — omitted here for length; every row was captured in `PRJNA976261_p2c.events.json` in the scratchpad.

## Step 6 — anomaly re-check

1. **SAMN35361966 host/`ind12` conflict**: **Resolved.** No longer appears.
   `host` = "Homo sapiens" cleanly, with no `ind12` value bleeding into it.
   The `conflict` column now only shows an unrelated, harmless
   platform/instrument_model corroboration note.
2. **`disease`/`disease_status` alternating-null pattern**: **Confirmed not a
   merge bug** — re-ran the alias-duplicate detector (same one used in the
   Step A/B report) across all 12 rows: zero unmerged duplicate-alias
   columns. The pattern (each row uses either `disease` or `disease_status`
   but never both) is simply because each sample's own free-form Pass-2
   extraction independently picks a field name, and `field_name_matches`
   correctly merges duplicates *within* a row's own batch, not by
   normalizing names *across* different rows. Not a defect.

## PRJEB14215 — full output (5 samples, rich pipeline, schema URL supplied)

Ran with the cMD data dictionary schema URL and the 8 requested fields
(`study_name, subject_id, sample_id, target_condition, control, body_site,
sequencing_platform, host_species`), so **both** the `api.py` and
`additional_pipeline.py` `metadata_merge` call sites were exercised this
time (the `additional_pipeline.py` one needs a `standardization_url`, which
PRJNA976261's test never supplies — flagged as an untested gap in the prior
Step A/B report; now exercised).

| Accession | study_name | subject_id | sample_id | target_condition | control | body_site | sequencing_platform | host_species | confidence |
|---|---|---|---|---|---|---|---|---|---|
| SAMEA4019839 | florinash_yyyy | sp1330 | unknown | mondo:0000797 | unknown | feces | illumina | human | 25 🔴 Low |
| SAMEA4019840 | florinash_yyyy | sp1339 | unknown | mondo:0000136 | case | feces | illumina | homo sapiens | 25 🔴 Low |
| SAMEA4019841 | florinash_yyyy | sp1348 | unknown | mondo:0000152 | case | feces | illumina | human | 25 🔴 Low |
| SAMEA4019842 | florinash_yyyy | sp1362 | unknown | mondo:0000148 | case | feces | illumina | human | 25 🔴 Low |
| SAMEA4019843 | florinash_yyyy | sp1370 | unknown | fatty liver disease | case | feces | illumina | human | 25 🔴 Low |

**Bug 3 confirmed working here specifically:**
- `study_name` = "florinash_yyyy" (a real study identifier) for all 5 rows — before this fix it was literally `'prjeb14215'` (the lowercased bioproject accession). Log confirms the rejection path fired: `Rejected study_accession='PRJNA976261' from Pass 2 (LLM): duplicates bioproject's value` (seen on the PRJNA976261 run) and, on this run, `sample_id` was rejected for all 5 samples: `[niche-dup-check] Rejected sample_id='SAMEA4019840' for SAMEA4019840: duplicates an identifier value` (one such line per sample, confirmed in log).
- Trade-off worth noting: `sample_id` now reads "unknown" for all 5 rows because the model's only candidate value was identical to the BioSample accession. For the cMD schema specifically, `sample_id` is defined as "unique identifier for a biospecimen/sample" and is sometimes legitimately expected to equal the accession when no other identifier exists in the source data. The task's instructions specifically named `study_name`/`bioproject` as the example and didn't carve out an exception for `sample_id`, so I applied the general rule as written rather than special-casing it — but flagging this now since it's a real behavior change worth confirming is wanted.
- `target_condition` mixes plain text ("fatty liver disease") and raw MONDO ontology IDs (`mondo:0000797`) across different samples — inconsistent formatting, inherited from the schema's dynamic-enum ontology lookup behavior; not something Bugs 0-4 touch, flagged for awareness only.
- All 5 rows show `any_key_field_lacked_id_linkage=True` (confirmed via log grep), correctly capping confidence at Low — these ENA-sourced samples apparently gave the model no confident per-sample ID-table match at all for `control`/`target_condition`, which the new signal correctly surfaces.

No tracebacks, no crashes, in either run.

## Caveats on the test setup itself

- Both runs used the same in-process harness from Step A/B (`api.analyze()` called directly, Google Sheets usage/analytics/cache side effects stubbed out) — no HTTP server involved, per prior agreement.
- I made **three** attempts at PRJNA976261 during this session as I found and fixed successively deeper issues (the `_extract_additional_fields` miss, then the disambiguation wording); the results reported above are from the final (third) run, `PRJNA976261_p2c`.
- PRJEB14215 was only re-run twice (not a third time for the disambiguation wording change) since it has no ground truth to measure improvement against either way, and re-running costs real API spend for a change I'd already shown doesn't fix the underlying issue.

## CORRECTION (post-report): the "no linking key exists" root cause above is wrong

The user caught this: the paper **does** contain a table with an unambiguous
global ID-to-group mapping — **Table 3** ("Statistics of whole metagenome
shotgun sequencing of plaque samples"), which I never actually located and
read. I had only inspected Table 1 (per-group `#1`-`#3` numbering, genuinely
ambiguous) and Table 2, and wrongly generalized Table 1's ambiguity to the
whole paper.

**Verified directly:**

1. **Table 3 is present and intact** in the extracted context text (line
   ~436 of the uploaded `user_context.txt`), with a flat `ID` column (1-12)
   directly mapped to a `Type` column (`T2D+P+` / `T2D+P-` / `T2D-P+` /
   `T2D-P-`):
   ```
   Table 3
   Statistics of whole metagenome shotgun sequencing of plaque samples.
   ID Type N° tot reads ...
   1 T2D + P+ 65,284,068 ...
   3 T2D + P+ 61,186,636 ...
   2 T2D + P- 80,622,922 ...
   ...
   ```
2. **Table 3's `ID` → `Type` mapping matches ground truth exactly for all 12
   samples, zero exceptions** (verified programmatically: `ind1`→`T2D+P+`,
   `ind2`→`T2D+P-`, ... `ind12`→`T2D+P-`, all correct). A linking key
   genuinely exists in the source text.
3. **The model is citing the wrong table.** For both SAMN35361959 and
   SAMN35361965 (checked directly), the explanation cites **Table 1**, not
   Table 3 — and for SAMN35361959 it even fabricates a nonexistent `id`
   column on Table 1 (`"Table 1 shows Patient #3 in T2D+P+ group with
   id='05'"` — Table 1 has no ID column at all).

**Likely reason:** Table 1 ("Characteristics of the study population") is
introduced earlier in the text with rich per-subject narrative framing
(age/sex/clinical columns), so it reads to the model like "the" per-subject
lookup table. Table 3's title ("Statistics of whole metagenome shotgun
sequencing") doesn't advertise itself as a disease/group table even though
its `ID`/`Type` columns are exactly that — the model is defaulting to the
more narratively prominent table, not the more reliable one. My PRIORITY
RULE instruction tells the model to prefer *a* numbered table over prose,
but gives it no way to choose **between** multiple candidate tables when one
has an unambiguous global ID scheme and another has an ambiguous per-group
one.

**Status:** this is a real, addressable prompt-level gap (not a source-data
limitation as I previously concluded) — holding off on further prompt
changes pending direction on next steps.
