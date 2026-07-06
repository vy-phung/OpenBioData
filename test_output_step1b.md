# PRJNA976261 re-run — worked example + visible enumeration

## Verdict: small improvement (3/12 → 4/12), and two new bugs surfaced

Implemented both changes exactly as specified, in both `model.py` locations
(`multi_prompts()`'s `_disease_hint` and `_extract_additional_fields()`'s
`generalized_prompt`):

1. **One generic, fictional worked example** — a "Table A" (restarting
   per-group numbering, unreliable) vs. "Table B" (single continuous list,
   reliable) scenario, with the correct choice and a one-line reason,
   demonstrating the exact structural distinction without naming any real
   paper/table/condition.
2. **Forced visible enumeration** — categorical/group-type fields must now
   emit `[Candidates: <name>=<reliable|unreliable> (<reason>); ...]` then
   `[Chosen: <name>]` before the narrative, for every candidate found (even
   if there's only one), rather than silently picking one.

Guardrail re-checked clean (only the pre-existing generic `'Table 3'`
location-format example, unrelated).

**Match rate: 4/12** (up from 3/12 last run) — SAMN35361955, 35361957,
35361961, 35361964 correct. This is a genuine, if modest, improvement — one
additional sample flipped to correct — but not the outcome intended.

## Diagnostic: which underlying model/API answered

Checked directly: **0 occurrences of "Anthropic API error"** and **0
occurrences of "Gemini error"** anywhere in this run's log. Since
`model.py`'s `call_llm_api()` always tries Claude (`claude-haiku-4-5-20251001`)
first and only logs a fallback message on failure before trying Gemini, this
confirms **100% of calls in this run — for all 12 samples, every field —
were answered directly by Claude, with zero Gemini fallback.** (Contrast
with the earlier PRJEB14215 test, where a large supplementary-table upload
pushed one sample's prompt over Claude's 200k-token limit and triggered a
real Gemini fallback — that specific failure mode doesn't apply here; these
two PDFs stay well under the limit.) Confirmed the same for the prior run
(`PRJNA976261_p2d`) too — this appears to be a stable characteristic of this
dataset's context size, not a one-off.

## Did the visible enumeration actually happen? Yes — confirmed in raw output

Grepping the raw LLM responses for `[Candidates: ...]` across the whole run
shows the tag firing consistently, e.g.:

```
[Candidates: Table_1=reliable (unique per-patient row); study_prose=unreliable]
[Candidates: Table_1_Farina_2019=reliable (explicit percentage per subject row)]
[Candidates: bioproject_description=unreliable (...); NCBI_biosample_attributes=unreliable (...); Table_1_Farina_2019=reliable (explicit row-by-row subject assignment to groups)]
```

So the forcing mechanism works mechanically — the model does now produce an
explicit, visible comparison instead of silently committing to an answer.
**But the comparison itself is usually wrong**: of ~98 `[Chosen: ...]` tags
across the run, only **1** resolved to Table 3 (the actually-reliable
table); the rest chose a Table 1 variant (92) or generic prose (2). In the
large majority of cases, the model explicitly labels **Table 1 as
"reliable"** — e.g. `Table_1=reliable (unique per-patient row)` — which is
factually wrong (Table 1's numbering restarts `#1`-`#3` within each of 4
groups, which is exactly the "restarting" pattern the worked example says is
unreliable). The mechanism forces a visible judgment, but the judgment
itself is frequently mistaken in a way that happens to rationalize the
answer the model was already going to give.

## A genuine, isolated success — and what it reveals

For **SAMN35361965** (ind11, true group T2D-P+ per Table 3), one field's raw
reasoning shows real self-correction:

> "...reviewing Table 1 more carefully shows subjects are numbered #1, #2,
> #3 within each group label, making the link between 'ID 11' from
> BioSample and the table subjects ambiguous... Re-examining: In Table 3,
> ID 11 is listed as 'T2D-P+', confirming this sample is from the
> T2D-negative, Periodontitis-positive group, indicating the subject HAS
> moderate-severe periodontitis. [Chosen: Table_3]"

This is the one case (out of ~98) where the model genuinely walked through
Table 1's ambiguity, caught it, and switched to Table 3 — proving the
underlying mechanism (worked example + forced enumeration) **can** work.
It's just rare.

## New bug #1: value/explanation mismatch within a single field

The same SAMN35361965 sample shows a second, different problem. Its
`condition_periodontitis` field:
- **value**: `"No moderate-severe periodontitis"` (wrong — Table 3 says
  P+, periodontitis present)
- **explanation** (same field, same JSON object): ends with *"...confirming
  this sample is from the T2D-negative, Periodontitis-positive group,
  indicating the subject **HAS** moderate-severe periodontitis."*

The model's own concluding sentence says the opposite of its own `value`
field. This is a new, distinct problem from anything in Bugs 0-4 — the
structured `value` isn't staying in sync with the model's own
chain-of-reasoning conclusion within the same response. Worth a closer look
separately; not something this session's fixes address.

## New bug #2: contradictory categorical fields within the same sample

**SAMN35361956** (ind2, true group T2D+P- per Table 3) emitted two
different categorical fields that flatly contradict each other:
- `disease`: `"No periodontitis, No type 2 diabetes"` (implies T2D-P-)
- `periodontal_status`: `"T2D+P+ (moderate-severe periodontitis with type
  2 diabetes)"` (implies T2D+P+)

Same sample, same underlying fact, two fields, two different answers —
neither fully correct (true answer is T2D+P-: diabetes present, periodontitis
absent). This wasn't visible before because earlier runs mostly emitted a
single `disease` field per sample; now that categorical information is
sometimes split into several more granular fields (`periodontal_status`,
`diabetes_status`, `condition_periodontitis`, etc. — field names vary run to
run since this is free-form Pass-2 extraction), nothing cross-checks that
they agree with each other.

## Full per-sample comparison

Ground truth (Table 3 ID→Type, verified exact match to `biosample_metadata.xlsx`
in an earlier turn): ind1=T2D+P+, ind2=T2D+P-, ind3=T2D+P+, ind4=T2D-P+,
ind5=T2D-P-, ind6=T2D+P-, ind7=T2D+P+, ind8=T2D-P-, ind9=T2D-P+, ind10=T2D-P-,
ind11=T2D-P+, ind12=T2D+P-.

| Accession | ind# | GT type | Match? | Categorical field(s) + value(s) | Chosen table (per field) |
|---|---|---|---|---|---|
| SAMN35361955 | ind1 | T2D+P+ | ✅ CORRECT | periodontal_status="moderate-severe periodontitis"; diabetes_status="type 2 diabetes, poorly controlled" | Table 1 |
| SAMN35361956 | ind2 | T2D+P- | ❌ WRONG (+ self-contradictory, see above) | disease="No periodontitis, No type 2 diabetes"; periodontal_status="T2D+P+ (...)" | Table 1 (both) |
| SAMN35361957 | ind3 | T2D+P+ | ✅ CORRECT | periodontitis_status="moderate to severe periodontitis (P+)"; diabetes_status="type 2 diabetes with poor glycemic control (T2D+)" | Table 1 |
| SAMN35361958 | ind4 | T2D-P+ | ❌ WRONG (T2D wrongly included) | disease="Type 2 Diabetes Mellitus with moderate-severe periodontitis"; type_2_diabetes_status="poorly controlled" | Table 1 |
| SAMN35361959 | ind5 | T2D-P- (control) | ❌ WRONG (both wrongly present) | disease="T2D+P+ (type 2 diabetes and periodontitis)" | Table 1 |
| SAMN35361960 | ind6 | T2D+P- | ❌ WRONG (periodontitis wrongly included) | disease="type 2 diabetes and periodontitis" | Table 1 |
| SAMN35361961 | ind7 | T2D+P+ | ✅ CORRECT | disease="Type 2 Diabetes Mellitus and moderate-severe periodontitis" | Table 1 |
| SAMN35361962 | ind8 | T2D-P- (control) | ❌ WRONG (both wrongly present) | disease="type 2 diabetes and periodontitis" | Table 1 |
| SAMN35361963 | ind9 | T2D-P+ | ❌ WRONG (T2D wrongly included) | disease="type 2 diabetes with moderate-severe periodontitis"; diabetes_status="poorly controlled type 2 diabetes" | Table 1 |
| SAMN35361964 | ind10 | T2D-P- (control) | ✅ CORRECT | diabetic_status="non-diabetic (T2D-)"; periodontal_status="healthy periodontium without periodontitis (P-)" | Table 1 |
| SAMN35361965 | ind11 | T2D-P+ | ❌ WRONG (periodontitis wrongly marked absent, despite the field's own explanation concluding it's present — see New bug #1) | condition_periodontitis="No moderate-severe periodontitis" (value/explanation mismatch); condition_type2_diabetes="No type 2 diabetes" (correct, genuinely cites Table 3) | Table 1 (periodontitis field) / **Table 3** (T2D field) |
| SAMN35361966 | ind12 | T2D+P- | ❌ WRONG (periodontitis wrongly included) | disease="type 2 diabetes and periodontitis" | Table 1 |

**Match rate: 4/12.**

## Files

- Full run data: `PRJNA976261_p2e.events.json` (scratchpad)
- Output Excel: `test-data/outputs/PRJNA976261_step1b_output.xlsx`

## Recommendation

The forcing mechanism (worked example + visible enumeration) is a real,
verifiable improvement in that it makes the model's comparison *inspectable*
— we can now see exactly what it considered and why it chose wrong, instead
of it silently committing to an answer. But it hasn't solved the accuracy
problem: the model still mislabels the ambiguous table as reliable in the
overwhelming majority of cases, and two new consistency bugs (value/explanation
mismatch, cross-field contradiction) surfaced as side effects of the more
detailed reasoning now being requested. Given three prompt-wording iterations
(structural rule → disambiguation clause → worked example + forced
enumeration) have each produced only marginal or no accuracy gain, I'd flag
this as approaching the point of diminishing returns for pure prompt
engineering on this specific failure mode — reporting rather than
attempting a fourth iteration unprompted.
