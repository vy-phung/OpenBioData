# Extraction Accuracy Fixes — Derived from PRJNA976261 Test Case

## ⚠️ READ THIS BEFORE IMPLEMENTING ANYTHING BELOW

The bugs in this document were found using **one test case (PRJNA976261)** as
a concrete example, but every fix must be implemented as a **general rule**
that applies to any BioProject, BioSample, SRR/SRA, GenBank, or GEO
accession — not specific to this paper, this table, or these sample IDs.

**Do NOT write logic that references "Table 3," "Farina 2019," "ind12,"
"T2D+P+," or "PRJNA976261" by name anywhere in the fix.** If you find
yourself writing a condition that only matches this specific paper's
structure, stop — that means you've implemented the instance instead of the
pattern. Generalize it. For example:

- Bug 1 → "any source table with a numeric ID/index column, cross-checked
  against any numeric identifier field on the sample record" — not "Table 3's
  ID column."
- Bug 2 → "any paper describing multiple independent grouping/study-design
  variables" — not "T2D and periodontitis specifically."
- Bug 3 → "any extracted field value that duplicates another populated
  column in the same row" — not "study_name specifically."
- Bug 4 → a new, general confidence signal usable on every extraction, not a
  special case for factorial-design studies.

After implementing a fix, re-test against PRJNA976261 to confirm the
original bugs are resolved (see Testing Requirement at the end of this
document), **and also test against at least one accession from a different
paper/study design** (not a multi-group factorial design) to confirm the fix
generalized rather than special-cased the test example. Report full output
tables for both runs, not summaries.

---

## Test case reference
- BioProject: PRJNA976261 (Farina/Favale oral microbiome, T2D × periodontitis, 4 groups of 3)
- Samples tested: SAMN35361966 (ind12), SAMN35361965 (ind11)
- Ground truth: `biosample_metadata.xlsx` (manually curated)
- Tool output: `biometadata_results_testOnToolUI_for_PRJNA976261.xlsx`
- Source docs the tool had: Farina 2019 (Archives of Oral Biology) + Favale 2023
  (Molecular Oral Microbiology), both full text, plus NCBI BioProject/BioSample/
  Experiment XML/text.

This document is the full list of confirmed bugs from comparing tool output to
ground truth, root causes (verified against the actual source text, not assumed),
and the fix required for each. Treat this as a checklist — do not skip items as
"probably fine," each was confirmed against the real files.

---

## BUG 1 (Critical): Wrong disease-group assignment despite correct data being present in sources

**What happened:** Both test samples were assigned to group T2D+P+ in the tool's
output (`target_condition` field and explanation text). Ground truth shows:
- SAMN35361966 (ind12) → **T2D+P−** (diabetic, no periodontitis)
- SAMN35361965 (ind11) → **T2D−P+** (periodontitis, no diabetes)

Neither sample is actually T2D+P+. The tool got both wrong, and both wrong in
the same direction (defaulted to the same group).

**Root cause (verified, not assumed):** The correct mapping IS present in the
source text the tool had access to — Farina 2019's **Table 3** ("Statistics of
whole metagenome shotgun sequencing of plaque samples") has an `ID` column
(values 1–12) directly mapped to a `Type` column (T2D+P+/T2D+P−/T2D−P+/T2D−P−).
Table 3 confirms: ID 12 → T2D+P−, ID 11 → T2D−P+.

Separately, the BioSample XML record has a **numeric `id` attribute** distinct
from the text submitter ID:
```
<Attribute attribute_name="id">12</Attribute>
```
This numeric `12` is the literal key that matches Table 3's `ID` column — NOT
the `ind12` string (which appears in SRA submitter IDs and filenames, and is
a different kind of identifier).

**What went wrong in extraction:** the model connected the sample to the more
visually/textually prominent group mentions in prose (T2D+P+ is listed first
in the group enumeration in both papers' Methods sections) rather than doing
the numeric ID lookup: BioSample `id` attribute → Table 3 `ID` column → `Type`.
This is a **reasoning/prompt gap**, not a missing-evidence problem — the correct
answer was retrievable, the model just didn't look in the right place.

**Fix required:**
1. In the extraction prompt (`model.py`, `multi_prompts()`), add an explicit
   instruction: when a BioSample record has a numeric `id` attribute, and any
   source document contains a table with a generic `ID` or numbered index
   column, check for a matching row **before** relying on prose statements
   about which group/condition is discussed most.
2. This check should take priority over prose-based group inference. Prose
   mentions of a group (e.g. "T2D+P+ group: ...") describe the study design in
   general, not this specific sample — a table row keyed by the sample's own
   ID number is direct evidence; general prose is not.
3. Apply this generally, not just for this one table: any time a source
   contains a table indexed by a sample identifier (numeric ID, subject number,
   patient number), that table should be checked against every numeric/ID-like
   attribute on the BioSample record, not just the most prominent one.

---

## BUG 2: `control` field collapses a multi-axis design into a near-meaningless binary

**What happened:** Both samples got `control = case`. True, but useless —
it doesn't capture the accurate 4-group design (2 independent conditions:
periodontitis yes/no, T2D yes/no).

**Fix required:**
- When a study has more than one binary/categorical disease axis (detectable
  from the paper describing multiple inclusion-criteria groups, e.g. "four
  groups based on presence/absence of X and Y"), extract **separate fields per
  axis** (e.g. `periodontitis_status`, `t2d_status`) rather than forcing
  everything into a single `case`/`control` value.
- Keep `control` as a field, but define it precisely: `control` should mean
  "this sample is the fully healthy/unaffected group" (i.e., T2D−P− here) —
  not "affected by at least one condition." Right now `case` is being used as
  a catch-all for "affected by something," which loses the group structure
  entirely.

---

## BUG 3: Duplicate/redundant columns — extraction not distinguishing accession from identity

**What happened:**
- `study_name` returned `'prjna976261'` — that's the BioProject accession,
  which is already a separate column (`bioproject`). Not a study name.
- `sample_id` returned `'samn35361966'` — identical to `biosample_accession`,
  just lowercased.
- Ground truth uses `'FarinaR_2019'` as the actual study identifier (author +
  year), which never appears anywhere in the tool's `study_name` field.

**Root cause:** When the extraction can't find a human-readable study title/
identifier close to the requested field name, it appears to fall back to
whatever accession-like string is nearby, rather than either finding the real
value or returning `unknown`.

**Fix required:**
1. Add a validation/post-processing step: if a requested field's extracted
   value is identical (case-insensitive) to the value already present in
   another column in the same row (e.g. `bioproject`, `biosample_accession`,
   `sra_accession`), do NOT accept it as valid for the new field — treat it as
   a failed extraction and either retry with a more targeted prompt or return
   `unknown`.
2. For `study_name` specifically: instruct the model to look for
   author-surname + year style identifiers (common in supplementary/metadata
   conventions, e.g. "FarinaR_2019") or the actual paper title, not the
   accession number. If genuinely absent from sources, return `unknown` rather
   than substituting the accession.

---

## BUG 4: Confidence score doesn't reflect the actual risk in Bug 1's failure mode

**What happened:** Both wrong group assignments got a 70/100 "High" confidence
score, with the explanation "Accession linked to a value in GenBank and
associated publication text; No contradiction detected."

**Root cause:** The current confidence signals (`has_geo_loc_name`, `has_pubmed`,
`accession_found_in_text`, `num_publications`) measure whether evidence *exists
somewhere*, not whether the evidence *specifically names this sample* for the
claimed value. A wrong inference drawn from real, topically-relevant sources
scores identically to a correct inference — there's no signal that
distinguishes "this fact was found attached to this sample's ID" from "this
fact was found in the general vicinity of this sample's sources."

**Fix required:**
1. Add a new confidence signal: **sample-ID co-occurrence**. Check whether the
   sample's own identifier (BioSample accession, numeric `id` attribute, or
   submitter ID) appears in direct proximity to the specific claimed value in
   the source text (e.g. in the same table row, same sentence, or same
   paragraph) — not just whether the value appears somewhere in a source
   linked to this sample's paper.
2. When a field is derived from a table lookup keyed by the sample's numeric
   ID (per Bug 1's fix), this signal should be strongly positive. When a field
   is inferred only from general prose about the study design with no direct
   ID linkage, this signal should be weak/absent, and should cap the score
   below "High" regardless of how many sources exist.
3. Update `set_rules()` in `confidence_score.py` to add this as a named signal
   with its own weight, and update `compute_confidence_score_and_tier()` to
   compute it. Do not just adjust existing weights — this is a genuinely new
   signal type not currently captured by direct_evidence/consistency/density/
   risk_penalties.

---

## BUG 5: Field coverage gap vs. ground truth (lower priority — expected, but worth documenting)

Ground truth's `cMD Metadata` sheet has 49 columns across clearly organized
sections (Identifiers, Technical/Study, Disease/Phenotype, Demographics,
Periodontal Clinical, T2D Clinical, Sequencing Stats, Confidence, Explanation,
Cost). Tool output has 16. Some of this gap is expected since only 8 fields
were explicitly requested — but confirm:
- Full Raw Attributes sheet (meant to catch anything not explicitly requested)
  should be checked for whether it captured the missing concepts (age, sex,
  ethnicity, smoking status, HbA1c, teeth count, probing depth, etc.) even
  though they weren't in the main output. If Full Raw Attributes is also
  missing them despite being present in the source text, that's a real gap in
  the second extraction pass, not just a "field wasn't requested" issue.

**Fix required:** Not urgent for this weekend. Note as a known limitation, or
investigate the Full Raw Attributes pass separately if time allows.

---

## BUG 6: Full Raw Attributes sheet has more columns than ground truth but may be inflated by repetition

Tool output: 86 columns × 2 data rows. Ground truth: 41 columns × 12 rows
(full project). The column *count* being higher isn't necessarily better —
needs a direct column-name diff to check whether the 86 columns are genuinely
richer or padded with near-duplicate variations of the same field (similar to
Bug 3's pattern).

**Fix required:** Run a column-name comparison between the two Full Raw
Attributes sheets. If duplication is confirmed, apply the same
identical-value-across-columns check from Bug 3's fix to this sheet as well.

---

## Testing requirement before considering this fixed

After implementing fixes for Bugs 1–4 (minimum bar — Bugs 5–6 optional this
weekend):

1. Re-run the tool on **PRJNA976261** with the same inputs used in this test
   (accession + both PDFs uploaded as context).
2. Confirm SAMN35361966 → T2D+P− and SAMN35361965 → T2D−P+ (matching ground
   truth), with the explanation citing Table 3's numeric ID match, not prose.
3. Confirm confidence score for the group assignment reflects the ID-match
   signal specifically, not just "sources exist."
4. Confirm `study_name` no longer duplicates `bioproject`/`biosample_accession`
   values.
5. If any of these don't pass, do not mark this as done — report back what
   still fails rather than partially fixing and moving on.
