# PRJNA976261 re-run — completeness/reliability table-selection fix

## Verdict: fix did not change model behavior — match rate unchanged at 3/12

Implemented the completeness-of-comparison fix in both locations in
`model.py` (`multi_prompts()`'s `_disease_hint` and
`_extract_additional_fields()`'s `generalized_prompt`), scoped only to
categorical/group-status fields as specified: enumerate every
candidate table/section that maps the sample's own identifier (whatever
it's called) to a category, judge each candidate's reliability structurally
(unique-row-per-identifier or non-overlapping-range, vs. restarting/repeating
schemes), and prefer the reliable candidate regardless of prominence or
order in the text. No numbers, identifier names, or table titles referenced
in the instruction text (verified — see guardrail check below).

Re-ran PRJNA976261 (all 12 ground-truth samples) after the change. Result:
**still 3/12**, identical count to the pre-fix run. Directly checked which
table each sample's disease-field explanation cites: **zero of the 12 rows
cite Table 3** (the table with the reliable global ID→Type mapping);
**10 of 12 still cite Table 1** (the ambiguous per-group-restart table), and
2 fall back to prose with no table citation at all. The instruction had no
measurable effect on which table the model chose.

## Per-sample results

Table 3's actual ID→Type mapping (verified against ground truth in the prior
turn: matches all 12 samples exactly) is shown as "GT type" for reference.

| Accession | ind# | GT type (from Table 3) | Table cited by model | Match? | Disease field value(s) |
|---|---|---|---|---|---|
| SAMN35361955 | ind1 | T2D+P+ | Table 1 | ✅ CORRECT | "type 2 diabetes with moderate-severe periodontitis" |
| SAMN35361956 | ind2 | T2D+P- | Table 1 | ❌ WRONG | "type 2 diabetes mellitus and moderate-severe periodontitis" (perio wrongly included) |
| SAMN35361957 | ind3 | T2D+P+ | Table 1 | ✅ CORRECT | "periodontitis and type 2 diabetes" |
| SAMN35361958 | ind4 | T2D-P+ | prose/other | ❌ WRONG | no disease-type field emitted at all |
| SAMN35361959 | ind5 | T2D-P- (control) | Table 1 | ❌ WRONG | "type 2 diabetes mellitus with periodontitis" (both wrongly present) |
| SAMN35361960 | ind6 | T2D+P- | prose/other | ❌ WRONG | "type 2 diabetes and moderate-severe periodontitis" (perio wrongly included) |
| SAMN35361961 | ind7 | T2D+P+ | Table 1 | ✅ CORRECT | "moderate to severe periodontitis and poorly controlled type 2 diabetes" |
| SAMN35361962 | ind8 | T2D-P- (control) | Table 1 | ❌ WRONG | "periodontitis and type 2 diabetes (t2d+p+)" |
| SAMN35361963 | ind9 | T2D-P+ | Table 1 | ❌ WRONG | "type 2 diabetes mellitus and moderate-severe periodontitis" (t2d wrongly included) |
| SAMN35361964 | ind10 | T2D-P- (control) | Table 1 | ❌ WRONG | "t2d+p+" / both conditions present (both wrongly present) |
| SAMN35361965 | ind11 | T2D-P+ | Table 1 | ❌ WRONG | "type 2 diabetes mellitus and periodontitis comorbidity" (t2d wrongly included) |
| SAMN35361966 | ind12 | T2D+P- | Table 1 | ❌ WRONG | "type 2 diabetes mellitus and moderate-severe periodontitis" (perio wrongly included) |

**Match rate: 3/12** (SAMN35361955, SAMN35361957, SAMN35361961 — all three
are T2D+P+ samples, i.e. every case where Table 1's wrong default answer
happens to coincide with the correct answer).

Representative explanation text (SAMN35361959, a true control per ground
truth / Table 3 ID 5 → T2D-P-):
> "Subject ind5 (ID '05') assigned to T2D+P+ group per study design table
> (Table 1, patient #1); BioProject description confirms four study groups
> based on presence/absence of type 2 DM and moderate-severe periodontitis."

Still citing "Table 1, patient #1" — the same wrong row pattern seen before
this fix, with no reference to Table 3 anywhere.

## Guardrail check

```
grep -niE "T2D|periodontitis|Table 3|ind12|PRJNA976261" model.py
```
Only hit: a pre-existing, unrelated example in the `[Sources: ...]` tag
format instructions (`'Table 3', 'Abstract', 'species description', ...`) —
a generic illustration of what a location string can look like, not a
disease/group-specific reference. No hardcoded contamination introduced by
this change.

## Assessment

The prompt-level fix, phrased exactly as specified (structural
reliability test, no paper-specific references), did not change the
model's table-selection behavior in this run. Table 1 remains the
model's default despite:
- explicit instruction to check every candidate table matching the
  sample's own identifying attribute,
- explicit instruction that a restarting/repeating numbering scheme is
  unreliable,
- explicit instruction to prefer a reliable candidate even if a less
  reliable one is more prominent.

This suggests the issue is not (or not only) a missing instruction, but
that the model isn't applying this kind of multi-candidate structural
comparison from a single dense instruction paragraph embedded in an
already-long prompt — it continues pattern-matching to the first
disease-like table (Table 1) it encounters. Reporting this rather than
attempting a further prompt-wording iteration, per your request to check in
before proceeding further.

## Files

- Full run data: `PRJNA976261_p2d.events.json` (scratchpad)
- Output Excel: `test-data/outputs/PRJNA976261_step1_output.xlsx`
