# Extraction Accuracy Fixes — Investigation Report (Bugs 1–4)

Investigation only — no code was written or edited during this pass. This
report traces the actual live code paths for Bugs 1–4 from
`extraction_accuracy_fixes.md`, per the guardrail at the top of that
document (generalize, don't special-case PRJNA976261).

---

## ⚠️ Pre-existing contamination: this exact test case is already hardcoded into "general" logic

The guardrail in the doc warns against writing fixes that reference
"T2D+P+," "Table 3," etc. by name. That's already happened, in code that
predates this doc:

- **`model.py:1216-1220`** — the generic few-shot example baked into *every*
  extraction prompt (regardless of accession/fields) literally shows
  `"T2D+P+"` as the correct output for a "disease" field, with a fabricated
  citation to "Table 3 row 5 lists the subject group as T2D+P+." This
  example runs on **every single `/analyze` call**, not just this test
  case.
- **`model.py:1154-1164`** (`_disease_hint`) — its illustrative example is
  `"e.g. healthy control, T2D, periodontitis, T2D+periodontitis"`.
- **`additional_pipeline.py:289-311`** — when a standardization schema is
  supplied but the user typed no fields, the code auto-picks niche_cases
  from the schema and sorts them using a hardcoded "priority" set that
  includes `'disease_t2d', 'disease_periodontitis', 'hypoglycemic_medication',
  'metabolic_control'` (`additional_pipeline.py:305-306`) — these are
  clinical fields specific to *this* T2D/periodontitis study, sitting
  inside what's supposed to be a generic "well-known bio-metadata fields"
  list.

This is very likely a major contributor to Bug 1 — it explains why **both**
samples defaulted to the exact same wrong group (T2D+P+): the model's own
few-shot example primes it toward that literal string. Any fix to Bug 1
that only adds an ID-lookup instruction, without also genericizing this
example, is likely to only partially fix the symptom. This affects scope
for Bug 1 and Bug 2 below.

---

## Investigation findings, Bugs 1–4

### Bug 1 — Wrong disease-group assignment (prose over table-ID lookup)

**1. Where the logic lives:**
- Prompt construction: **`model.py:1096-1225`, `multi_prompts()`** —
  specifically the `_disease_hint` block (`1148-1164`) and the hardcoded
  output-format example (`1196-1221`).
- BioSample raw XML fetch (source of the numeric `id` attribute):
  **`NCBI.py:523-532`, `fetch_biosample_raw_metadata()`** → called by
  **`NCBI.py:556-561`, `fetch_biosample()`** → called by
  **`NCBI.py:736-754`, `extract_NCBI_directly()`**.
- Where that raw XML gets folded into the LLM's context:
  **`additional_pipeline.py:435-438`**
  (`acc_score["source_texts"]["NCBI_biosample"] = ncbi_texts`) →
  **`additional_pipeline.py:1021-1049`** (labels and concatenates every
  source into one `text` blob) → **`additional_pipeline.py:1113`**
  (`acc_prompts = {acc: text}`) → **`model.py:1692`**
  (`multi_prompts(prompts, ...)`, where `prompts` *is* `acc_prompts`).

**2. Why it produces the wrong result (traced, not assumed):**
The numeric `id` attribute is **not lost** — `fetch_biosample_raw_metadata()`
returns the *entire raw XML as a string* (`handle.read()`), so any
`<Attribute attribute_name="id">12</Attribute>` in the real NCBI record is
present verbatim in the text sent to the LLM (confirmed via the call chain
above; it would only be at risk of being mangled if the combined context
exceeded 800K chars and got run through
`data_preprocess.normalize_for_overlap()`, which strips XML tags — not
applicable here since a single BioSample XML is a few KB).

So the evidence is there. The actual failure is in the *instruction*, not
the *evidence*: `_disease_hint` (`model.py:1154-1164`) tells the model to
"check NCBI BioSample attributes (e.g. 'disease', 'health_state',
'clinical_diagnosis', 'group', 'treatment', 'subject_group')" — it never
mentions a generic numeric `id`/index attribute, and never says a
table-row-by-ID match should outrank prose. Combined with the hardcoded
`"T2D+P+"` example described above sitting in the same prompt, the model
has both (a) no explicit priority rule and (b) a literal anchor toward the
wrong answer.

**3. Overlap with other bugs' fixes:** **Yes — significant.** Bug 2's fix
also needs to modify prompt construction in `multi_prompts()` (specifically
the same disease-hint region), since both are about how the model reasons
over multi-group study designs. Any change to `_disease_hint` for Bug 1
will directly interact with whatever Bug 2 adds there — these two should
be designed together, not sequentially, or the second change is likely to
re-break the first.

**4. Proposed home + blast radius:** The fix belongs in `multi_prompts()`
(`model.py`), which is **shared/reused** — it's the single prompt-builder
for every extraction call in the live pipeline (`additional_pipeline.py`),
regardless of accession type or requested fields. Any change here affects
100% of extractions, not just disease/group fields (though the
`_disease_hint` sub-block itself only activates when disease/control/
group-type fields are requested, so a change scoped to that sub-block has
a narrower blast radius than editing the function's shared preamble). The
genuinely general version of "check a numeric ID against a table" (per the
doc's own Bug 1 framing) should be phrased field-agnostically — it's
useful for any table-indexed lookup, not just disease status, so this
might actually belong as a new always-on instruction rather than inside
the disease-specific hint.

---

### Bug 2 — `control` field collapses a multi-axis design

**1. Where the logic lives:** Same prompt-construction function,
**`model.py:1096-1225`, `multi_prompts()`** — specifically the
boolean-field handling in the schema branch (`1124-1132`) and
`_disease_hint` (`1148-1164`). Also relevant: **`model.py:1227-1307`,
`standardize_with_llm()`**, which is where a `control` field gets mapped
to TRUE/FALSE *if* a standardization schema is active (`1260-1263,
1281-1282`) — this only runs when `standardization_schema` is supplied
(gated in `query_document_info()` at `model.py:1770`).

**2. Why it's wrong:** Two mechanisms exist depending on whether a schema
is active, and neither captures multi-axis structure:
- With a schema whose `control` field has boolean-like allowed values, the
  model is told to output TRUE/FALSE based on "is this sample a
  control/reference" vs. "case/disease/treatment" — a strictly binary
  framing.
- Without a schema (or with non-boolean allowed values), `control` is just
  a free-text niche_case field with no special handling beyond the generic
  disease hint.

Neither path tells the model to *also* emit `periodontitis_status`/
`t2d_status`-style per-axis fields — there's no mechanism for that at all
currently.

**3. Overlap:** Overlaps with Bug 1 in `multi_prompts()` as noted above.
Does **not** overlap with Bug 3/4's primary functions.

**4. Proposed home + blast radius — needs explicit scope decision before
implementation, because it's architecturally bigger than the doc's
phrasing suggests:**

`multi_prompts()` builds `output_format_str` from a **fixed, pre-computed
field list** (set in `query_document_info()`, `model.py:1670-1689`,
*before* any source text is read), and `parse_multi_sample_llm_output()`
(`model.py:937-1013`) parses the model's answer **positionally** against
that exact fixed list (`Line 1: exactly {field_count} values ... separated
by ' | '`). There is currently no mechanism for the model to organically
add new fields mid-extraction — the output contract is rigid by field
count and order.

So "extract separate fields per axis" can't be done as a pure prompt
tweak to Pass 1 if the user only typed `control` as a requested field. Two
real options:
- **(a) Lean on Pass 2** (`model.py:1536-1649`,
  `_extract_additional_fields()`) — this pass already returns an
  open-ended JSON dict with no fixed field count, and already asks
  generically for "disease, treatment" attributes. It's the
  architecturally natural place for dynamically-discovered per-axis
  fields, but it currently *excludes* anything already in `niche_cases`
  (`model.py:1556`), and its output lands in the separate "Full Raw
  Attributes" sheet, not the main output row — so a user who only
  requested `control` would still need to go find `periodontitis_status`
  in a different sheet, not the primary table.
- **(b) Pre-scan for multi-axis language and dynamically expand
  `niche_cases`** before `output_format_str` is built in
  `query_document_info()` — this makes new fields show up in the main
  output table as the doc seems to intend, but touches the field-count
  contract that `multi_prompts()`, `parse_multi_sample_llm_output()`, and
  `_rows_from_new_pipeline()` (`api.py:393-407`) all currently assume is
  static, i.e., a materially larger change with more surface area.

Open decision: scope Bug 2 to option (a), (b), or the narrower "redefine
`control`'s meaning precisely" half of the fix only (skip auto-adding new
axis fields for now)?

---

### Bug 3 — Duplicate/redundant columns (`study_name`/`sample_id` echoing accession)

**1. Where the logic lives:** **`api.py:289-451`,
`_rows_from_new_pipeline()`** — specifically the identifier-column
assembly (`317-330`) and the niche-field loop that writes `row[field]`
(`393-407`, via the `_emit_field()` helper at `342-391`). Secondarily, the
LLM's actual answer for `study_name` originates in `multi_prompts()`'s
per-field extraction (same as Bugs 1/2) and is parsed by
`parse_multi_sample_llm_output()`.

**2. Why it's wrong:** `_rows_from_new_pipeline()` builds
`biosample_accession`, `bioproject`, `sra_accession`, `genbank_accession`
(lines 317-330) **before** looping over `niche_list` and writing each
requested field's value straight through via `_emit_field()` (line 407)
with **no comparison at all** against the identifier columns already in
the same row. So whatever string the LLM returns for `study_name` (even
if it's literally `"prjna976261"`) is accepted unconditionally. This
confirms the doc's root cause — the LLM has no instruction against
restating an accession, and nothing downstream catches it either.

**3. Overlap:** **Yes, with Bug 4.** `_rows_from_new_pipeline()` is also
exactly where the `conf_signals` dict gets assembled (lines 425-434) and
passed to `compute_confidence_score_and_tier()` — so Bug 4's new signal
has to be wired through this same function. Any refactor of the
niche-field loop for Bug 3 (e.g., adding a validation step) needs to be
written with Bug 4's signal-injection point in mind, or one fix risks
clobbering the other's insertion point.

**4. Proposed home + blast radius:** `_rows_from_new_pipeline()` is the
right place for the validation step exactly as the doc describes
(post-processing, case-insensitive compare against
`biosample_acc`/`bioproject_val`/`sra_accession`/`genbank_acc`, all of
which are already local variables by the time the niche loop runs). This
function is used only for this purpose (row construction for `/analyze`'s
output) — it's not shared elsewhere in the pipeline, so blast radius is
low: the change is additive (one more check inside `_emit_field()` or the
loop), doesn't touch model.py or additional_pipeline.py, and can't affect
any other endpoint. The `study_name`-specific instruction (asking for
author+year style identifiers) does need a `multi_prompts()` change too,
per the doc's item 2 — that part shares Bug 1/2's blast radius.

---

### Bug 4 — Confidence score doesn't reflect ID-linkage vs. general topical relevance

**1. Where the logic lives:** **`confidence_score.py:44-71`,
`set_rules()`** and **`confidence_score.py:192-324`,
`compute_confidence_score_and_tier()`** — exactly as the doc says.
Feeding it: **`api.py:413-435`** (`_rows_from_new_pipeline()`, where the
`conf_signals` dict is assembled from `signals = data.get("signals", {})`),
and further upstream, **`additional_pipeline.py`** wherever
`acc_score["signals"][...]` gets set (e.g. `355, 468-494, 1158-1208,
1286`) — this is where any *new* signal would need to originate, since
that's the only place with access to both the raw source text and the
sample's identifiers at the same processing stage.

**2. Why it's wrong:** **The confidence score is computed once per
row/sample, not per field.** `_rows_from_new_pipeline()` builds a single
`conf_signals` dict per accession (lines 425-434) — `missing_key_fields`
is a single boolean aggregated with `any(...)` across *all* requested
fields (line 419-422), and the resulting single `conf_score`/`conf_tier`
is what's shown as one "Confidence score" column for the whole row. So
today's signals (`has_geo_loc_name`, `has_pubmed`,
`accession_found_in_text`, `num_publications`) really do only measure
"does evidence exist somewhere for this sample," never "does the evidence
for *this specific claimed value* actually name this sample" — confirming
the doc's root cause exactly.

**3. Overlap:** With Bug 3, in `_rows_from_new_pipeline()` (both touch the
`conf_signals`-assembly region).

**4. Proposed home + blast radius — also needs a decision before
implementation:**

This is the one where the doc's fix requirement is written as if
confidence is per-field ("this signal should be strongly positive [for
this field]... weak/absent [for that field]... cap the score below High")
but the actual architecture computes one score per row. Implementing it
*exactly* as described would mean turning the single row-level
`confidence_score` column into multiple per-field scores — a materially
bigger change touching the Sheet 1 output schema, the Excel export, and
(per the README) the frontend's confidence display. Two narrower
alternatives that stay within the current one-score-per-row architecture:

- **(a) Row-level signal, informed by per-field detection:** compute the
  co-occurrence check per requested field (in `additional_pipeline.py`,
  where raw text is still available), but roll it into the *existing*
  single `conf_signals` dict as one new boolean, e.g. "at least one key
  categorical/group field lacked direct ID-linkage" → caps the row's
  score. Smaller change, reuses existing plumbing (per-field
  `extra[f"{field}_source_location"]` in `api.py`'s `_emit_field()`
  already parses the `[Sources: ...]` location text, which could feed
  this).
- **(b) True per-field confidence:** extend the schema to carry a score
  per requested field. Bigger, touches more files, but matches the doc's
  literal wording and would make Sheet 2 ("Full Raw Attributes," per-field
  granularity) meaningfully more honest too.

Separately — implementation mechanism for the co-occurrence check itself
is also open: **(i)** deterministic text-proximity search (regex/string
search for the sample's ID near the claimed value in the raw source blob
— cheap, but fragile against varied table formats) vs. **(ii)** have the
LLM self-report it as part of its existing structured output (extend the
`[Sources: key (location, 'excerpt')]` tag format the model already
produces, e.g. add an explicit ID-match flag) — more robust but requires a
prompt + parser change layered on top of Bug 1/2's changes to the same
prompt.

Open decisions: scope (a) vs (b), and mechanism (i) vs (ii).

---

## Summary of overlaps

| Function | Touched by |
|---|---|
| `model.py` `multi_prompts()` | Bug 1, Bug 2 (same disease-hint region — design together) |
| `api.py` `_rows_from_new_pipeline()` | Bug 3, Bug 4 (same signal-assembly region — design together) |
| `confidence_score.py` `set_rules()` / `compute_confidence_score_and_tier()` | Bug 4 only |
| `additional_pipeline.py` (schema-priority list, signal computation) | Pre-existing contamination cleanup (Bug 0), and potentially Bug 4's new signal origin |

No function is touched by all four bugs, but the two pairs above (1+2,
3+4) each land in the same function and should be implemented as a single
coordinated change per pair, not two independent patches.

---

## Housekeeping note (unrelated to the bugs themselves)

Confirmed `additional_pipeline.py` is the *only* pipeline module actually
wired into the live `/analyze` endpoint (`api.py:1370`,
`from additional_pipeline import pipeline_with_gemini`). `pipeline.py` and
`mtdna_backend.py`'s `pipeline_classify_sample_location_cached()` (which
calls into `pipeline.py`) are not imported anywhere in the live request
path — meaning the README's Transparency table entry "Source text
gathering | `pipeline.py` | `extractSources()` line 290" points at
inactive code. Not part of this bug investigation, but flagging since it
means none of Bugs 1-4's fixes should touch `pipeline.py` — that module
isn't in the request path this test case exercised.

---

## Open decisions before implementation

1. Bug 2 scope: (a) lean on Pass 2 dynamic extraction, (b) pre-scan and
   dynamically expand `niche_cases`/`output_format_str`, or (c) narrower
   "redefine `control`'s meaning precisely" only, skipping auto-added axis
   fields for now.
2. Bug 4 scope: (a) row-level signal informed by per-field detection
   (smaller change, current architecture), or (b) true per-field
   confidence scores (bigger change, more accurate to the doc's wording).
3. Bug 4 mechanism: (i) deterministic text-proximity search, or (ii)
   LLM self-reported ID-match flag via an extended output tag.
4. Whether to fold the Bug 0 contamination cleanup (hardcoded
   T2D/periodontitis references in `model.py` and
   `additional_pipeline.py`) into this pass.
