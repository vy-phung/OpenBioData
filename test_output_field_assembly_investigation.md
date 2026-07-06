# Investigation: value/explanation contradiction in the `disease` field

## Question asked

For SAMN35361958, SAMN35361963, SAMN35361964, the model's free-text
reasoning correctly concludes the ground-truth group, but the structured
`disease` field it emits contradicts that same reasoning. Is the value
assembled from a separate parsing/regex step reading a different part of
the LLM's output than the explanation, or does the model generate both as
independent fields within the same response?

## Answer: same response, same JSON object, same field — no parsing step involved

`disease` is not extracted by any regex/positional-index parser. It comes
from `model.py: _extract_additional_fields()` ("Pass 2"), which asks the
LLM to return **one JSON object per sample, in one LLM call**, shaped
`{"field_name": {"value": "...", "explanation": "..."}}` for every field it
finds — `value` and `explanation` are **sibling keys of the same object,
written by the model in the same generation turn**, not sourced from
different parts of a longer text via separate logic.

The contradiction is a genuine LLM self-consistency failure inside that one
JSON object: it correctly narrates the true group in `explanation`, then
writes a different, contradictory string into that same object's `value`
key — nothing in this codebase re-derives `value` from `explanation` or
vice versa; they are just two strings the model chose to write, and the
code trusts them both as-is.

## How this was confirmed

### 1. Found where `disease` actually comes from

`disease` is not part of the fixed/default field set (`country_name`,
`modern/ancient/unknown`) that goes through `model.py: multi_prompts()` →
`parse_multi_sample_llm_output()` (the positional-index, one-line-per-field
parser used for Pass 1). That parser was the natural first suspect for a
value/explanation misalignment bug (it assigns explanations to fields by
line position when `**field:**` markers aren't found — see "Ruled out"
below) — but it's the wrong function. `disease` was not part of `niche_cases`
in this run (no schema/fields were specified), so it comes out of **Pass 2**
instead, confirmed directly from the live run's own debug log
(`uvicorn.log`):

```
[Pass 2] SAMN35361966: 16 additional fields -> ['host', 'isolation_source', ..., 'disease']
```

### 2. Traced Pass 2's code path line-by-line — nothing sits between the LLM call and the stored dict

`model.py: query_document_info()`, lines 1943–1964:
```python
pass2_context = prompts.get(acc, "") or context_for_llm or ""
all_additional = _extract_additional_fields(pass2_context, niche_cases or [])
additional_only = {
    k: v for k, v in all_additional.items()
    if k not in predefined_keys
}
outputs[acc]['_additional_fields'] = additional_only
```
`additional_only` is a pure key-exclusion filter (drops `country_name` /
`modern/ancient/unknown` / any niche_cases already handled by Pass 1) — it
does not touch the `value`/`explanation` content of any field. The very
next line (1967) prints `outputs[acc]` in full, confirming by direct
inspection of the log that this is the same dict, unmodified, at the point
it's captured.

### 3. Inside `_extract_additional_fields()` itself (model.py:1628-1802)

One `call_llm_api(generalized_prompt)` call (line 1765) returns
`response_text`. The code does:
```python
result = json.loads(raw.strip())
...
for k, v in result.items():
    ...
    v_str = str(v.get('value', '')).strip()
    expl  = str(v.get('explanation', '')).strip()
    ...
    cleaned[k_str] = {'value': v_str, 'explanation': expl}
```
`v` is one field's own JSON sub-object (e.g. `result["disease"]`), and
`value`/`explanation` are read from that exact same sub-object — there is
no cross-field indexing, no line-splitting, no `[Sources:]`-tag regex
reassignment (that regex logic belongs to a different function, see below).
This is a direct, faithful copy of whatever the model wrote for that one
field.

### 4. Ruled out: downstream merge step

`api.py: _rows_from_new_pipeline()` passes Pass 2's fields through
`metadata_merge.merge_metadata_into_table()` before they reach the final
row (since this run had no niche_cases, all Pass 2 fields get promoted to
Sheet 1 through this merge). Checked `merge_metadata_into_table()`
(`metadata_merge.py:89-161`): when a field has **only one contributing
source** (true here — nothing else in this run independently produces a
`disease` value to merge against), the function takes the
`existing_key is None` branch and does a **direct passthrough**:
```python
table[new_key] = {"value": value, "explanation": explanation,
                   "sources": [source_label], "is_llm": is_llm}
```
`value` and `explanation` here are unpacked from the *same* incoming dict
via `_value_and_explanation(new_val)` (metadata_merge.py:69-76), which also
just reads `v.get("value")` / `v.get("explanation")` from one object. No
conflict-merge logic (which *would* rewrite `value` while leaving
`explanation` referring to the old value — see `_extend_conflict_marker`)
was triggered, because there was only one source. Confirmed this branch is
inert here, not a contributor to the bug in this run.

### 5. Ruled out: the positional-index parser (`parse_multi_sample_llm_output`)

This function *is* a genuine misalignment risk in general (Strategy B
falls back to `ordered_lines[o]` — assigning the o-th raw explanation line
to the o-th field purely by position, with no name-based check, if the
model didn't emit a `**field:**` marker). But it only ever runs on Pass 1's
two fixed fields (`country_name`, `modern/ancient/unknown`), confirmed by
`parsed metadata_list keys: ['country_name', 'modern/ancient/unknown']` in
the log for every sample checked. `disease` never passes through this
function in the real pipeline, so this mechanism — although independently
fragile — is not the cause of the 3 flagged mismatches.

## Literal evidence from this run (unmodified log output, printed before any merge step)

**SAMN35361958** (GT: T2D-P+, i.e. periodontitis without diabetes):
```python
'disease': {
  'value': 'type 2 diabetes and periodontitis',
  'explanation': "[Candidates: RELIABLE Table 1 ...]. [Chosen: Table 1]. "
    "This sample belongs to patient ind4 (T2D−P+ group: patients affected "
    "by moderate to severe periodontitis but not type 2 diabetes), as "
    "confirmed by study group classification in the paper. "
    "[Sources: user_uploaded_file (Table 1, patient ind4 assigned to T2D−P+ group)]"
}
```
`explanation` says "**but not** type 2 diabetes" and cites group "T2D−P+"
explicitly; `value` says "type 2 diabetes **and** periodontitis" — directly
contradicts its own citation, in the same object.

**SAMN35361963** (GT: T2D-P+):
```python
'disease': {
  'value': 'Type 2 Diabetes Mellitus and moderate-severe periodontitis',
  'explanation': "[Candidates: Table 1=reliable ...] [Chosen: Table 1] "
    "Subject 'ind9' maps to study group T2D−P+ (patients with periodontitis "
    "but without type 2 diabetes), indicating the subject has "
    "moderate-severe periodontitis but not poorly controlled type 2 diabetes. "
    "[Sources: user_uploaded_file (Table 1, Subject ind9 belongs to T2D-P+ "
    "group with PD≥5mm=19-39 sites)]"
}
```
Same pattern: `explanation` explicitly says "**without** type 2 diabetes" /
"**not** poorly controlled type 2 diabetes"; `value` asserts both
conditions present.

**SAMN35361964** (GT: T2D-P- / control):
```python
'disease': {
  'value': 'periodontitis',
  'explanation': "[Candidates: Table 1=RELIABLE ...] [Chosen: Table 1]. "
    "Sample ind10 maps to T2D−P− group (Table 1, row with patient #3 "
    "'ind10' in metadata), but per-sample-level information in BioProject "
    "indicates this subject belongs to the control group without "
    "periodontitis or diabetes ... the user-uploaded paper Methods and "
    "Results show ind10 is actually from group T2D-P- (no periodontitis, "
    "no diabetes). Therefore this sample is from the control/healthy group."
}
```
`explanation` concludes "control/healthy group" (neither condition);
`value` asserts periodontitis alone (one condition) — a third distinct
value/explanation disagreement, again inside one JSON object.

All three excerpts above are taken verbatim from `uvicorn.log`'s
`total output of <acc>: {...}` line — printed immediately after Pass 2
returns and before any other code (merge, standardization, row-building)
runs, so they show the model's own output exactly as generated.

## Note: related, pre-existing test scripts found in the repo

`test_context_swap.py` and `test_context_swap_3samples.py` (both present in
the repo root, untouched by this investigation) already independently
document the same call path (`model.query_document_info()` → Pass 2 →
`model._extract_additional_fields()`) from an earlier round of
investigation into this same sample set. Their own comments corroborate
the finding here ("the disease/group field comes out of
query_document_info's Pass 2, model._extract_additional_fields(), not a
hand-reconstructed prompt"). Not re-run for this investigation since the
current live pipeline run already produced the needed evidence directly.

## Summary

| Question | Answer |
|---|---|
| Separate JSON field generated independently in the same response? | **Yes** — `value` and `explanation` are sibling keys of one JSON object for the `disease` field, both produced in the single `_extract_additional_fields()` LLM call. |
| Separate parsing/regex step reading from a different part of the output? | **No** — ruled out both candidate mechanisms: the positional-index parser (`parse_multi_sample_llm_output`) never touches `disease` in this run; the merge step (`merge_metadata_into_table`) does a verified no-op passthrough since `disease` has only one source here. |
| Where does the contradiction actually originate? | Inside the model's own single JSON generation — a token-level self-consistency failure between two string fields it was asked to keep consistent, not a code-level extraction/assembly bug. |

No fix proposed per instructions — this is a model-behavior problem (the
`explanation` field's own correct reasoning is not being used to constrain
the `value` field within the same generation), not a pipeline-code defect.
