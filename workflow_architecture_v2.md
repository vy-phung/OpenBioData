# OpenBioData Extraction Workflow — Modular Architecture

## Purpose of this document
This describes a staged, multi-source extraction pipeline that replaces the
current flat "combine everything, one LLM call" approach. It is a redesign
of the core pipeline, but it is written to be buildable at MVP quality now —
not perfect, but each function has one clear job, so it's contributable and
fixable incrementally. This workflow, done reasonably, resolves the sample-ID
linkage problem (Bug 1), the field-duplication problem (Bug 3), and the
control-field flattening problem (Bug 2) as natural side effects of correct
structure — they are not separate patches layered on top.

**Guardrail carried over from prior work in this project:** every function
below must be generic — usable on any accession type, any paper, any study
design. Do not hardcode field names, disease names, or paper-specific logic
into any function signature or body.

**Design decision (confirmed):** Explanation and Source are each a single
column, but each cell contains one line per metadata field, prefixed with
the field name (e.g. `country: stated directly in BioSample geo_loc_name`)
rather than relying on line position to match up with metadata columns —
position-based matching would silently break if a field is missing for one
sample, reordered, or newly added. Confidence follows the same per-field,
prefixed-line pattern (see Output Construction section) — this workflow
tracks confidence per field, not just per row.

---

## Top-level orchestrator

```
run_extraction(accession, uploaded_files=None, requested_fields=None,
               schema_url=None) -> output_row
```
Coordinates Stages 1, 2, and 3 below, in order, and produces the final
output row. This function should be thin — it calls the stage functions,
it does not itself contain extraction logic.

---

## Shared core function — used by every stage, must not be duplicated

```
merge_metadata_into_table(table, new_fields, source_label, is_llm) -> table
```
This is the single "check-and-add()" function referenced repeatedly in the
workflow notes. Every stage below calls THIS function — do not let each
stage grow its own copy of this logic, or Bug 3's duplication problem comes
back in a new form.

Behavior:
- For each field in `new_fields`: check if a field with the same meaning
  already exists in `table` (see `field_name_matches` below).
  - If yes: do NOT create a duplicate column. Append `source_label` to that
    field's existing source list and append a brief explanation
    ("confirmed by <source_label>") to that field's explanation.
  - If no: add a new column for this field, with its value, initial
    explanation, and `source_label` as its first source.
- Track and return updated time-cost alongside the table (or as a separate
  running counter passed through — implementer's choice, but must be
  consistent).

```
field_name_matches(existing_field_name, new_field_name) -> bool
```
Deterministic normalization first (lowercase, strip whitespace/punctuation,
common synonym table e.g. "geo_loc_name" ~ "country" ~ "location") before
ever falling back to an LLM call for ambiguous cases. Keep an explicit,
editable synonym list rather than hardcoding matches inline, so contributors
can extend it without touching pipeline logic.

---

## Stage 1 — NCBI records (mostly deterministic, no LLM)

```
identify_accession_type(accession) -> type
```
BioProject / BioSample / SRA / SRR / GenBank / GEO. Pure string/pattern
matching on accession format — no LLM needed.

```
fetch_ncbi_record(accession, type) -> record
```
Calls the appropriate NCBI API for the given type. Returns structured data
(not yet an output row).

```
init_output_table_from_record(record, accession) -> table
```
Builds the first columns directly from the primary record's own structured
fields. Deterministic — this is just reading NCBI's own metadata, no LLM
involved at this step.

```
find_related_accessions(record) -> list[accession]
```
Extracts linked BioProject/BioSample/SRA/GEO/GenBank accessions from the
record's cross-references.

```
process_related_accessions(table, related_accessions) -> table
```
For each related accession: `fetch_ncbi_record()`, then
`merge_metadata_into_table(table, record_fields, source_label=type, is_llm=False)`.

```
accumulate_big_context(big_context, record_text) -> big_context
```
Appends raw record text to the running context string, for the eventual
catch-all LLM pass at the end of Stage 2. Simple concatenation is fine here,
but see the truncation/ordering note in the Known Limitations section below.

**Important:** immediately after Stage 1 finishes (all NCBI/related-accession
records processed), take a snapshot of `big_context` at that point — this
is `ncbi_only_context`, used later by `run_llm_catchall_pass()` to detect
whether anything beyond NCBI records was ever added, so the LLM catchall
pass can be skipped entirely when there's nothing new to extract from.

---

## Stage 2 — Original paper and supplementary materials (sealed primary context)

```
classify_uploaded_file(file, accession) -> "original_paper" | "supplementary_material" | "unrelated"
```
When the user has uploaded files, determine which role each file plays for
THIS accession, rather than assuming. Can start as a simple heuristic
(filename patterns, first-page content check for title/abstract structure
vs. table-heavy structure) with an LLM fallback for ambiguous cases.

```
resolve_original_paper(accession, ncbi_record, uploaded_files=None) -> paper_source
```
If `uploaded_files` provided, use `classify_uploaded_file()` results.
Otherwise, prioritize the DOI/PMID **already linked in the NCBI record
itself** as the authoritative source for the original paper — this is more
reliable than any general web search, since it's the depositor's own stated
reference. Only fall back to broader search/resolution if the record has no
such link.

```
canonicalize_paper_reference(link) -> {doi, pmid, pmcid, access_type}
```
Extracts whatever canonical identifiers a link actually resolves to (DOI,
PMID, PMCID) regardless of URL form (DOI resolver, PubMed URL, PMC URL,
publisher URL). `access_type` is a routing heuristic based on URL pattern
(e.g. a bare PubMed URL is likely abstract-only; a DOI/publisher/PMC link
is likely full-text-capable) — this is a starting guess, confirmed only
once actually fetched.

```
deduplicate_paper_links(links, already_resolved_paper=None) -> list[paper]
```
Groups all links (NCBI record's own reference, any citing-paper search
results) by shared DOI/PMID/PMCID via `canonicalize_paper_reference()` —
NOT by raw URL string, since the same paper commonly appears under multiple
different-looking URLs (e.g. a PubMed abstract link and a DOI/publisher
link for the same article). Within each duplicate group, attempt to fetch
the variant most likely to yield full text first (reusing the tool's
existing DOI-first/PubMed-abstract-fallback logic — do not duplicate that
logic here, call it), falling back to a lower-priority variant only if the
preferred one fails or is paywalled. If `already_resolved_paper` is
provided (the paper already identified via `resolve_original_paper()`),
also dedupe against it, so the original paper is never double-counted as
one of the citing-paper search results.

```
find_supplementary_materials(paper_source) -> list[material]
```
If not user-provided, attempt to auto-discover supplementary files (journal
site, DOI-linked repository, Zenodo, etc. — reuse existing resolver logic
where possible rather than rebuilding NCBI/DOI-fetching from scratch).

```
check_accession_presence(text, accession_identifiers) -> "direct_match" | "table_candidate" | "no_match"
```
THE ID-presence triage function discussed earlier in this project. Returns
one of three states rather than a plain bool, since materials with a
plausible per-subject table need different handling than a clean miss (see
`process_source_for_accession` below):
- `"direct_match"`: the full accession string, or a bare numeric ID
  confirmed inside tabular/structured context, was found.
- `"table_candidate"`: no confirmed match to THIS sample's ID specifically,
  but the material contains some table-like structure with a numeric
  index/ID column — plausibly the same kind of per-subject table this
  sample's data could appear in, just not yet confirmed. Route these to
  full extraction rather than skip/summarize, since a real match here is
  common enough (per the reviewer-identified case) to be worth the extra
  extraction cost.
- `"no_match"`: no accession match and no ID-indexed table structure of any
  kind found.
Start deterministic for all three checks: string matching for direct_match,
and a structural heuristic (e.g. detecting numeric sequences in
column/row-like text patterns) for table_candidate. Use an LLM fallback
when these deterministic checks are inconclusive — and note this will
happen often in practice, since real PDF-extracted text is frequently messy
(merged cells, reflowed columns, OCR artifacts) in ways that can defeat
simple pattern matching even when a real per-subject table is present.

**Bias the fallback toward inclusion, not exclusion.** The cost of these two
mistakes is not symmetric: missing real per-subject data because a shaky
deterministic check said "no_match" (a false negative) reproduces the
original Bug 1 failure — a wrong or missing answer presented with no
warning. Running one extra LLM call on a material that turns out irrelevant
(a false positive) just costs a little money. So when the deterministic
check is uncertain — not just cleanly ambiguous, but genuinely uncertain due
to likely-messy extraction — default to `table_candidate` (route to full
LLM extraction) rather than `no_match`. Reserve `no_match` for materials
where the deterministic check is confidently negative (e.g. clearly
well-formed prose with no numeric/tabular content anywhere), not merely
"didn't find a clean pattern match."

```
process_source_for_accession(table, big_context, source_text, source_label, accession_identifiers) -> table, big_context
```
The repeated per-source loop used for supplementary materials, the main
paper text, and each citing paper (same logic every time — implement once,
call it from all three places rather than copy-pasting):
- `check_accession_presence(source_text, accession_identifiers)` →
  `"direct_match"` / `"table_candidate"` / `"no_match"`
- If `"direct_match"`: extract fields (LLM), then
  `merge_metadata_into_table(table, extracted_fields, source_label, is_llm=True)`
- If `"table_candidate"` (no confirmed match to THIS sample's ID by
  accession-string, but the material contains **some table-like structure
  with a numeric index/ID column**): still route to full extraction, not
  skip/summarize. This matters because a sample's numeric ID and its
  per-subject data (e.g. disease status, age, sex) commonly appear together
  in one table row, and the accession-string check alone can miss this — do
  not let a material with exactly this kind of table fall through to the
  branch below.
- If `"no_match"` (no accession-string match, no ID-indexed table structure
  found at all): **skip the material — do not append a summary of it to
  `big_context`.** Summarization is deliberately excluded as an option here,
  not just a cost-saving shortcut: compressing a material's content toward
  its general themes is exactly what would discard a specific
  numeric-ID-to-value row like the one this pipeline is designed to find
  (this is the same failure pattern as the original Bug 1 — prose/
  generalities crowding out one specific table row). If a material has no
  accession match and no per-subject table structure at all, there is no
  per-row data in it worth preserving, so skipping it costs nothing. Cost
  control should come from this include/exclude decision, not from lossy
  compression of included content.
  Log the skip reason (source label + "no accession match, no ID-indexed
  table structure found") so exclusions remain auditable.

```
run_llm_catchall_pass(table, big_context, ncbi_only_context) -> table
```
**Only runs if `big_context` contains more than the NCBI-records content
added in Stage 1** — i.e., only if original paper text and/or supplementary
material content was actually appended during Stage 2. (Citing-paper content
never enters `big_context` — see Stage 3 below for why it's kept separate.)
If nothing beyond NCBI records was ever added to `big_context` (no paper
found, no supplementary materials), skip this pass entirely — Stage 1
already deterministically extracted everything available, and re-running an
LLM over the same NCBI content a second time is pure wasted cost with no new
information to find.

Implementation: track `ncbi_only_context` as a snapshot of `big_context`
right after Stage 1 completes (before any Stage 2 additions). Before
invoking the LLM, compare current `big_context` against this snapshot —
if nothing was added, return `table` unchanged and skip the LLM call.

If it does run: send whatever's accumulated in `big_context` (the
Stage-2-added portion, not just the NCBI baseline) to the LLM, and merge its
output through `merge_metadata_into_table()` — same function as every other
stage, so catchall extractions are deduplicated against everything already
found, not just appended blindly.

This completes the primary, sealed pass (Stage 1 + Stage 2). The table
produced here is treated as the trusted baseline before Stage 3 below runs.

---

## Stage 3 — Citing papers (separate, secondary corroboration pass)

Runs only after Stage 1 + Stage 2 (including the catchall pass above) are
complete. Deliberately kept out of `big_context` — see rationale below.

```
find_citing_papers(accession, max_results=5) -> list[paper]
```
Capped search (reuse existing smart_search infrastructure) for papers
citing this accession by name. The cap exists for cost control — keep it
configurable, not hardcoded, so it can be tuned later without a code
change. Results MUST be passed through `deduplicate_paper_links()` (against
each other, and against the already-resolved original paper) before further
processing — otherwise the same paper reachable via two different URLs
could occupy two of the 5 slots, or be double-counted as if it
independently corroborated itself.

**Why citing papers get a separate context instead of joining `big_context`:**
The Stage 1+2 context (NCBI records + original paper + supplementary
materials) is the trusted, primary source for this sample and should stay
sealed once Stage 2 completes — do not add citing-paper content into it.
This isn't just caution: long-context extraction spanning multiple
subject-level datasets from different papers is a known place where models
misattribute details from one document to the wrong entity, especially as
context grows. Keeping the primary context focused (fewer but more directly
relevant documents) produces a more reliable first-pass answer, and citing
papers are better treated as a secondary, separately-scoped corroboration
step layered on afterward through the same merge/conflict logic as
everything else — not blended into the pass that produces the primary
answer.

```
build_cited_context(citing_papers, accession_identifiers) -> cited_context
```
For each deduplicated citing paper: apply the same three-state
`check_accession_presence()` triage used elsewhere (`direct_match` /
`table_candidate` / `no_match`) to decide whether to include its full text.
Include full text for `direct_match` and `table_candidate` results; skip
`no_match` results entirely (same reasoning as `process_source_for_accession`
— no summarization, since summarizing risks losing exactly the specific
per-subject linkage this pipeline depends on). Accumulate included texts
into one combined `cited_context`, kept separate from `big_context`.

```
run_cited_context_extraction(cited_context, table, accession_identifiers) -> table
```
ONE targeted LLM extraction pass over the combined `cited_context`,
explicitly prompted with the sample's accession identifiers so the model is
anchored to extracting only THIS sample's metadata, not other subjects'
data that may appear in the same citing papers. Merge the result through
`merge_metadata_into_table()` exactly as every other stage: a field
matching an existing value adds this citing paper as a corroborating
source; a field with a genuinely different value for the same field name
gets flagged as a conflict, with both values kept visible in that column
rather than one silently overwriting the other.

---

## Output construction

```
build_output_row(table, time_cost) -> row
```
Column 1: accession type name, value = the input accession.
Following columns: one per discovered metadata field (value only).
Next: **Explanation** column — one cell, containing one line per metadata
field, each line prefixed with the field name it belongs to
(e.g. `target_condition: derived from Table 3, ID row match`).
Next: **Source** column — same structure, one field-name-prefixed line per
field (e.g. `target_condition: Farina 2019 (Table 3, ID column)`).
Next: **Confidence** column — same structure, one field-name-prefixed line
per field with that field's score
(e.g. `target_condition: 85 (direct ID-table match)`).
Last column: time_cost (single value for the whole row).

**Why field-name-prefixed lines, not positional alignment:** if Explanation/
Source/Confidence lines were matched to metadata columns purely by line
order, any sample missing a field, any newly-added field, or any reordering
would silently misattribute one field's explanation/source/confidence to a
different field. Prefixing each line with its field name makes the mapping
explicit and self-describing — order becomes irrelevant, a missing field is
just an absent line rather than a shift, and the format stays human-readable
and greppable for auditing a specific field's provenance.

`merge_metadata_into_table()` should therefore update three aligned things
per field whenever it adds or merges a value: the field's own column value,
its line in Explanation, its line in Source — plus, in this workflow (unlike
the current tool's row-level-only interim fix), its line in Confidence,
computed per field at merge time based on how that specific value was
obtained (direct ID/table match vs. supplementary material vs. general
prose vs. LLM catchall pass) rather than one blended score per row.

---

## Known limitations to accept at MVP quality (do not block on these)

- `field_name_matches()`'s synonym list will start incomplete — that's fine,
  it improves over time as gaps are found, same as any dictionary-based
  matching system. Don't try to make this exhaustive on the first pass.
- `big_context` truncation/ordering (from the earlier investigation) still
  needs its own fix — if the catchall pass's context grows past whatever
  size limit exists, apply a fair per-source budget rather than a blind
  cutoff. This can be a fast-follow, not a blocker for shipping this
  workflow.
- Confidence scoring: this workflow builds per-field confidence in from the
  start (see Output Construction), computed at merge time based on how each
  value was obtained. This is a cleaner, greenfield design decision — it
  does not conflict with the earlier, narrower row-level-only confidence
  patch already decided for the CURRENT tool's quick bug-fix pass; that
  decision was about not touching existing Excel/frontend code under this
  weekend's time pressure, and applies to the interim patch, not to this
  redesign.

---

## Suggested file organization (for contributor clarity)

- `accession_resolver.py` — `identify_accession_type`, `fetch_ncbi_record`,
  `find_related_accessions`
- `metadata_merge.py` — `merge_metadata_into_table`, `field_name_matches`
  (this is the most-reused, most-important file — keep it small and
  well-commented, since every stage depends on it)
- `paper_resolver.py` — `classify_uploaded_file`, `resolve_original_paper`,
  `find_supplementary_materials`, `find_citing_papers`,
  `canonicalize_paper_reference`, `deduplicate_paper_links` (much of the
  base fetching/paywall-fallback logic likely already exists in the current
  paper_resolver.py — extend, don't duplicate)
- `accession_presence.py` — `check_accession_presence`
- `extraction_pipeline.py` — `process_related_accessions`,
  `process_source_for_accession`, `run_llm_catchall_pass`,
  `build_cited_context`, `run_cited_context_extraction`, `run_extraction`
  (the orchestrator)
- `output_builder.py` — `build_output_row`

This split means a contributor fixing a synonym-matching bug only ever
needs to open `metadata_merge.py`, and someone improving paper discovery
only touches `paper_resolver.py` — neither risks breaking the other.
