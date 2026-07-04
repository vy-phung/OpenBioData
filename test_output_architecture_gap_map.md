# Architecture Gap Map: workflow_architecture_v2.md vs. current codebase

Investigation only — no code was changed to produce this report.

## Important context that shapes every recommendation below

**There are two parallel pipelines in this repo, and only one is active.**

- **Active**: `api.py` (entry point — README says `python api.py`) calls
  `additional_pipeline.pipeline_with_gemini()` (imported at `api.py:1370-1371`
  as `_rich_pipeline`). Output rows are built by `api.py`'s
  `_rows_from_new_pipeline()` (line 289). This is the current, maintained,
  "rich" path and last touched 2026-07-02.
- **Legacy**: `mtdna_backend.py:36` calls the older `pipeline.pipeline_with_gemini()`
  (different signature, different module). This path was last touched
  2026-06-16, in a commit titled "Refactor pipeline, expand API, add test
  fixtures, **remove legacy files**" — the naming and the fact `api.py`/README
  never reference `mtdna_backend.py` strongly suggests this is stale, but the
  file still exists and wasn't actually deleted, so I'm not 100% certain it's
  dead. **Flagging as UNCERTAIN whether `mtdna_backend.py`/`pipeline.py` need
  any attention at all, or can be ignored/removed in a later pass** — worth
  confirming with the user before extending anything there.

All EXTEND recommendations below target the **active** path
(`additional_pipeline.py` / `api.py`) unless otherwise noted. Where the
legacy path has its own similar-but-separate logic, I've called it out
explicitly rather than treating it as the same thing — extending the active
path's function without knowing the legacy path has its own copy is exactly
the duplication risk this task was set up to avoid.

Two additional pieces of dead/unused code turned up during this
investigation that are directly relevant to gaps below, so I'm flagging them
here rather than only in their sections: `confidence_score.calculate_confidence()`
(per-field confidence scorer, zero callers) and `model.merge_metadata_outputs()`
(generic dict merge-by-"or", zero callers).

---

## Top-level orchestrator

### run_extraction

Status: NO MATCH (for the *shape* the doc wants; the *role* is filled by something structurally different)

Existing equivalent (if any): `additional_pipeline.py`, `pipeline_with_gemini()`, line 252

What it currently does: `pipeline_with_gemini` is the de facto orchestrator — it's the single function `api.py` calls to run the whole extraction for a batch of accessions — but it is not thin. It's a ~1000-line monolith (lines 252–1298) containing NCBI fetch logic, link-fetching/fallback cascades, LLM prompt construction, schema alignment, and output-field assembly all inline, rather than calling out to discrete stage functions.

Gap vs. what's needed (if EXTEND): Everything the doc wants factored into `accession_resolver.py` / `metadata_merge.py` / `paper_resolver.py` / `accession_presence.py` / `extraction_pipeline.py` currently lives inline in this one function. This isn't a small extension — it's the end state all the other sections below build toward. Not recommending any direct edit here yet per the user's stated plan to land `merge_metadata_into_table` and `check_accession_presence` first and prove those out before touching this.

Callers found: `api.py:1371` (`from additional_pipeline import pipeline_with_gemini as _rich_pipeline`) is the only caller of the active version; `mtdna_backend.py:36` calls the separate legacy `pipeline.pipeline_with_gemini`, different signature — see note above.

Recommendation: Do not build `run_extraction` yet. Treat it as the final step once Stage 1/2/3 helper functions actually exist as separate callables — trying to carve out a thin orchestrator before the logic it would call is modularized would just create a wrapper around the same monolith.

---

## Stage 1 — NCBI records

### identify_accession_type

Status: REUSE AS-IS

Existing equivalent (if any): `ncbi_resolver.py`, `detect_accession_type()`, line 51

What it currently does: Pure regex classification, no network/LLM calls. Returns `'bioproject' | 'biosample' | 'geo_series' | 'geo_sample' | 'sra_experiment' | 'sra_run' | 'genbank' | 'unknown'` from prefix patterns (PRJ, SAM, GSE, GSM, SRX, SRR, ERR, GenBank-style). This is a near-exact match to the doc's spec (types differ only in naming/granularity — doc lumps SRA/SRR together and doesn't mention GEO series vs. sample as a distinction, which this function already handles more precisely).

Gap vs. what's needed (if EXTEND): None functionally. Possible naming/output-value alignment only if downstream code expects the doc's exact type vocabulary.

Callers found (informational, not required since this is REUSE AS-IS):
- `ncbi_resolver.py:1138` (`resolve_accessions`) — dispatches to per-type resolver function.
- `input_handler.py:90` — decides between `enumerate_project_samples` vs `resolve_accessions`.
- `mtdna_backend.py:77` — legacy path, uses it only as a boolean gate (`!= 'unknown'`).

Recommendation: Reuse as-is. Do note a **pre-existing duplication** independent of this task: `NCBI.py:736-754` (`extract_NCBI_directly`) has its own cruder inline re-derivation of accession type via bare `.startswith("PRJ")/"SAM"/"SR"/"ER"` checks, instead of calling `detect_accession_type`. Not touching it now per the "don't rename/delete, just report" rule, but this is exactly the kind of duplicate-logic pattern the architecture doc is trying to prevent going forward — worth a follow-up cleanup once the new architecture is in place.

---

### fetch_ncbi_record

Status: EXTEND

Existing equivalent (if any): `ncbi_resolver.py`, `resolve_accessions()` (line 1128) dispatching to `resolve_from_bioproject`/`resolve_from_biosample`/`resolve_from_geo_series`/`resolve_from_geo_sample`/`resolve_from_genbank`/`resolve_from_sra`; separately, `NCBI.py`, `extract_NCBI_directly()`, line 736, dispatching to `fetch_bioproject`/`fetch_biosample`/`get_experiment_xml`/`mtdna_classifier.fetch_ncbi_metadata`.

What it currently does: Two independent dispatch-by-type implementations exist, each doing real Entrez/eutils/ENA calls, but returning **two different record shapes**: `ncbi_resolver`'s functions return a standardized `{accession_key: {bioproject, biosample, accession, experiment}}` (cross-reference IDs only); `NCBI.extract_NCBI_directly` returns a much richer `{accession: {...many fields incl. pubmed, pubmed_dois, umbrella_projects, external_links, country, etc.}}`. The active pipeline (`additional_pipeline.py`) uses `NCBI.extract_NCBI_directly`, not `ncbi_resolver`'s version, for the actual record-fetch step.

Gap vs. what's needed (if EXTEND): No single `fetch_ncbi_record(accession, type) -> record` exists with one uniform return shape. Building this means wrapping/normalizing `NCBI.extract_NCBI_directly` (the one the active pipeline actually uses) rather than `ncbi_resolver`'s resolvers, to avoid creating a third, competing record shape.

Callers found (if EXTEND):
- `additional_pipeline.py:397,411,414,423,437,441,452` — call `NCBI.extract_NCBI_directly(acc_or_related_id)` expecting a `{id: dict}` mapping, then read `.get("pubmed")`, `.get("umbrella_projects")`, `.get("external_links")` etc. off the nested dict.
- `pipeline.py:436` (legacy) — calls `mtdna_classifier.fetch_ncbi_metadata(acc)` expecting a flat dict (`country, specific_location, ethnicity, sample_type, collection_date, isolate, title, doi, pubmed_id, all_features`) — a *third* shape, legacy-path only.
- `ncbi_resolver.enumerate_project_samples` / `resolve_lazy_entry` (lines ~341-343) — consume the standardized 4-key dict from the `resolve_from_*` family.

Recommendation: Extend `NCBI.extract_NCBI_directly` (or wrap it) as the basis for `fetch_ncbi_record`, since it's what the active pipeline already depends on. Do not touch `ncbi_resolver.py`'s `resolve_from_*` family without separately confirming whether anything besides `input_handler.py`'s sample-enumeration flow still needs its narrower 4-key shape.

---

### init_output_table_from_record

Status: UNCERTAIN

Existing equivalent (if any): `pipeline.py:436-507` (legacy path only) — deterministically writes `acc_score["country"]`, `acc_score["sample_type"]`, and `signals["has_geo_loc_name"]`/`["genbank_country"]` straight from the NCBI record, source-labeled `"ncbi"`, before any LLM call.

What it currently does: In the **legacy** pipeline this exists in a real, if inline, form — record fields go straight into typed output columns, no LLM. In the **active** pipeline (`additional_pipeline.py`), I could not find an equivalent deterministic pre-LLM column-init step. Fields there seem to accumulate as raw text into `acc_score["source_texts"]` and get structured later via the LLM pass plus `field_aliases.canonicalize_field_name` (line 1235).

Gap vs. what's needed (if EXTEND): Genuinely unclear whether the active pipeline has no equivalent (a real gap — NO MATCH) or whether I simply didn't find it in ~1000 lines of `pipeline_with_gemini`. Recommend a targeted second look at `additional_pipeline.py`'s early section (roughly lines 340-400, before the source-text accumulation loop starts) before assuming this needs to be built from scratch.

Callers found (if EXTEND): N/A pending the uncertainty above — legacy `pipeline.py:436-507` has no separate callers, it's inline in `pipeline.pipeline_with_gemini` itself.

Recommendation: Do not guess. Confirm first whether `additional_pipeline.py` populates any output columns deterministically from the NCBI record before Stage 2/LLM involvement. If genuinely absent, build new in `accession_resolver.py` per the doc's file layout, modeled on the legacy `pipeline.py:436-507` logic (source-labeled `"ncbi"`, deterministic).

---

### find_related_accessions

Status: EXTEND

Existing equivalent (if any): Scattered across `NCBI.py` (`fetch_bioproject()` lines 86-277, `extract_biosample_links()` line 618, `parse_bioproject_markdown()` line 680) and `ncbi_resolver.py` (`get_bioproject_from_biosample()` line 177, `get_genbank_from_biosample()` line 244, `get_sra_from_biosample()` line 274, `_biosample_ids_from_sra()` line 407, `_find_bioproject_samples()` line 466, `_find_geo_series_samples()` line 1058).

What it currently does: Each cross-reference relationship (bioproject↔biosample, biosample↔SRA, biosample↔GenBank, GEO series↔GSM) has its own bespoke resolver function using elink/esearch/regex-over-XML-or-markdown. No single function takes a generic `record` and returns `list[accession]` across all relationship types — the relationships are resolved individually and assembled into `ncbi_resolver`'s standardized dict internally.

Gap vs. what's needed (if EXTEND): Needs a thin function that calls the right existing per-relationship resolver(s) based on the record's type and concatenates results into one flat list — the underlying fetch logic itself doesn't need to be rebuilt, just wrapped/unified.

Callers found (if EXTEND): Consumed transitively through `resolve_from_bioproject`/`resolve_accessions`/`enumerate_project_samples`, called by `input_handler.build_pipeline_input()` and `additional_pipeline.py:341-343` (`resolve_lazy_entry`). No caller currently expects a flat `list[accession]` return — all current callers consume the already-assembled standardized dict.

Recommendation: Extend by adding a thin wrapper (in `accession_resolver.py` per the doc's layout) that dispatches to the existing per-relationship resolvers named above and flattens their output — do not reimplement the elink/esearch calls themselves.

---

### process_related_accessions

Status: EXTEND

Existing equivalent (if any): `additional_pipeline.py:405-455`, inline loop inside `pipeline_with_gemini`.

What it currently does: `for ncbi_source in accessions[acc]: NCBI.extract_NCBI_directly(related_id) ...` then merges into `acc_score["source_texts"]` with a label like `"NCBI_bioproject"` / `"NCBI_biosample"` / `"NCBI_accession"` / `"NCBI_experiment"` — this label plays the role of `source_label=type` in the doc's spec.

Gap vs. what's needed (if EXTEND): (1) It's inline, not a standalone callable. (2) There's no `is_llm` flag concept anywhere in the codebase — everything in `source_texts` is implicitly non-LLM raw text, with LLM-derived values added separately later; introducing `is_llm` as an explicit parameter is new. (3) It writes into `source_texts` (raw text blobs), not into a structured `table` with typed columns — merging "into a table" per the doc's `merge_metadata_into_table` contract doesn't happen here today; it happens later, if at all (see `init_output_table_from_record` uncertainty above and the `merge_metadata_into_table` gap below).

Callers found (if EXTEND): This block has no separate callers to trace — it only exists inline inside `pipeline_with_gemini` (`additional_pipeline.py:252`), itself called only from `api.py:1370-1371`.

Recommendation: Extend by extracting this loop into a standalone `process_related_accessions(table, related_accessions)` in `extraction_pipeline.py`, but its correctness depends entirely on `merge_metadata_into_table` existing first (per the user's stated build order) — don't build this before that lands, since it's supposed to call it.

---

### accumulate_big_context

Status: EXTEND

Existing equivalent (if any): `additional_pipeline.py:1024-1049`, inline in `pipeline_with_gemini`.

What it currently does: `text = ""` then loops over `acc_score["source_texts"]` (ALL sources — NCBI, web search, PDFs, user-uploaded files, indiscriminately) concatenating `f'The source - {source}: {source_text}' + "-----END OF THIS SOURCE {source}----\n"`, with size-capping via `data_preprocess.build_context_for_llm`/`normalize_for_overlap` and an 800,000-char hard cap.

Gap vs. what's needed (if EXTEND): The doc's `accumulate_big_context` is meant to be called incrementally per-record during Stage 1 (NCBI only) and again per-source during Stage 2, so that a snapshot can be taken *between* those two additions (see `ncbi_only_context` below). Today it's a single one-shot loop executed once, after everything (NCBI + web + files) is already mixed into `source_texts` — there's no incremental accumulation and no ordering/provenance marker distinguishing what was added at which stage.

Callers found (if EXTEND): No separate callers — inline in `pipeline_with_gemini` only, consumed by the LLM extraction call immediately following it in the same function.

Recommendation: Extend/refactor into an incremental function called once per Stage-1 record and once per Stage-2 source, rather than the current single end-of-pipeline concatenation. This is a real behavior change (incremental vs. batch), not a pure rename.

---

### ncbi_only_context snapshot (supporting `run_llm_catchall_pass`'s skip logic)

Status: NO MATCH

Existing equivalent (if any): None found. Repo-wide grep for `ncbi_only`, `catchall`, `catch_all`, `is_llm`, `source_label` returned zero hits anywhere.

What it currently does: N/A — doesn't exist.

Gap vs. what's needed (if EXTEND): N/A.

Callers found (if EXTEND): N/A.

Recommendation: Build new. This is only meaningful once `accumulate_big_context` is refactored to be incremental (see above) — a snapshot needs an incremental accumulator to snapshot *between* stages. Sequence this after that refactor, in `extraction_pipeline.py`.

---

## Shared merge function

### merge_metadata_into_table

Status: EXTEND (from a gap, not from a near-complete function — see below)

Existing equivalent (if any):
- `api.py:480-490`, inline inside `_rows_from_new_pipeline` (the active output-row builder).
- `additional_pipeline.py:1219-1238`, inline schema-alignment block inside `pipeline_with_gemini`.
- (Distinct, NOT a match — see "ruled out" below): `model.py:1015 merge_metadata_outputs()`; `mtdna_backend.py:535 _merge()`.

What it currently does: Both real candidates already do the "does a field with this meaning already exist?" check via `field_aliases.canonicalize_field_name()`:
```python
# api.py:488-490
merged_key = field_aliases.canonicalize_field_name(k, row.keys())
if merged_key not in row:
    row[merged_key] = _emit_field(merged_key, value, explanation)
```
```python
# additional_pipeline.py:1235-1239
merged_key = field_aliases.canonicalize_field_name(canonical, acc_score["_additional_fields"].keys())
acc_score["_additional_fields"][merged_key] = {'value': ..., 'explanation': ...}
```

Gap vs. what's needed (if EXTEND): In **both** places, when `merged_key` already exists, the code does the wrong thing for this architecture: `api.py` silently **drops** the new value entirely (the `if merged_key not in row` guard means the branch where it already exists does nothing), and `additional_pipeline.py` silently **overwrites** the existing entry. Neither appends `source_label` to an existing source list, neither appends a "confirmed by `<source_label>`" line to the field's explanation, and there is no per-field `sources: [...]` list anywhere in the current row structure at all — `row["sources"]`/`Source` column is a single flattened string built later from `per_field_source_lines` (api.py:507-538), not a structured per-field list that a merge step appends to. There's also no `is_llm` parameter or time-cost-tracking-through-merge concept in either location. Both call sites only guard within *one* batch of fields being merged into an existing structure — neither is built to be called repeatedly across many sources/stages accumulating into the same table, which is the doc's core requirement.

Callers found (if EXTEND):
- `api.py:289` (`_rows_from_new_pipeline`, the enclosing function) — this is the only call site for the `api.py:480-490` logic; it expects the eventual merge behavior to still return usable values into `row[merged_key]` for downstream `_emit_field`/Explanation/Source column assembly (lines 504-538). Changing this in place must preserve that `row` ends up with one value per field, plus the explanation/source-building logic downstream still works off whatever shape the merge produces.
- `additional_pipeline.py`'s enclosing `pipeline_with_gemini` (line 252) — only call site for the `additional_pipeline.py:1219-1238` logic; expects `acc_score["_additional_fields"]` to end up as `{field: {'value':..., 'explanation':...}}` for later consumption by `api.py:455` (`pass2_fields = dict(data.get("_additional_fields") or {})`) and by the ontology-annotation block right after it (`additional_pipeline.py:1246-1269`, reads from the same dict).

Recommendation: Do not write a brand-new `merge_metadata_into_table` in a vacuum — the natural build path is to **replace the gap-y logic at both of these two call sites** with calls to one new shared function in `metadata_merge.py`, built to (a) accept repeated calls across many sources, (b) track a per-field source list and append to it on a match instead of silently overwriting/dropping, and (c) append a "confirmed by `<source_label>`" explanation line. This is consistent with the user's stated plan to land this function first — both call sites are small and well-isolated, making them a reasonable first testbed.

---

### field_name_matches

Status: EXTEND

Existing equivalent (if any): `field_aliases.py`, `canonicalize_field_name()`, line 45, backed by the `FIELD_ALIASES` synonym dict (line 10).

What it currently does: Deterministic lowercase/strip-based normalization plus a static synonym table (`geo_loc_name`/`country`/`location` → `geographic_location_country_and_or_sea`, etc.), checking both alias→canonical and the reverse direction. Returns a **canonical name string**, not a bool, and has **no LLM fallback** for ambiguous cases.

Gap vs. what's needed (if EXTEND): Signature mismatch (`(name, known_fields) -> str` vs. doc's `(existing_field_name, new_field_name) -> bool`) — easy to adapt. Bigger gap: no ambiguous-case LLM fallback exists in this file at all. The best existing template for that fallback is `model.align_to_schema()` (`model.py:1309`, only caller `additional_pipeline.py:1224`) — an LLM-based prompt that maps free-text field names onto a *fixed target schema's* canonical names ("find the best-matching schema field name... only include fields where you're confident of the match"). It's the right pattern (LLM asked to match a name against known candidates, told to abstain if unsure) but its scope is schema-mapping (many names → one schema), not pairwise comparison of two arbitrary field names, so it would need adaptation rather than direct reuse.

Callers found (if EXTEND) — full list, grep-verified, only two in the whole repo:
- `additional_pipeline.py:1235` — `field_aliases.canonicalize_field_name(canonical, acc_score["_additional_fields"].keys())`, expects a canonical name back (or the input unchanged if no match), used to fold a Pass-2 field into the aligned canonical schema field.
- `api.py:488` — `field_aliases.canonicalize_field_name(k, row.keys())`, same expectation, used to decide whether a Pass-2 field aliases an already-populated row column.

Recommendation: Extend `field_aliases.py` — keep `canonicalize_field_name` (both callers can keep using it, since it does useful normalization work `merge_metadata_into_table` will still want), and add a new `field_name_matches(a, b) -> bool` alongside it (likely implemented as "canonicalize both, compare" plus the new LLM-ambiguous-fallback modeled on `model.align_to_schema`'s prompt style, adapted for pairwise comparison). Don't discard the existing function — `merge_metadata_into_table` will likely want both: the bool check to decide "same field," and the canonicalization to decide what to *call* the merged field.

---

## Stage 2 — Original paper and supplementary materials

### classify_uploaded_file

Status: NO MATCH

Existing equivalent (if any): None. `api.py:728` (`_process_one_upload`) handles uploaded files but treats every uploaded file identically as undifferentiated context — there is no code path anywhere that decides "is this the main paper vs. a supplement vs. unrelated" for a given accession.

What it currently does: N/A — the closest thing, `data_preprocess.classify_url()` (data_preprocess.py:64), classifies by file *type* (pdf/xlsx/docx/zip/html), not by document *role*, which is a different axis entirely.

Gap vs. what's needed (if EXTEND): N/A.

Callers found (if EXTEND): N/A.

Recommendation: Build new, in `paper_resolver.py` per the doc's layout. The regex-first/LLM-fallback-for-ambiguous-cases *pattern* used elsewhere in the repo (e.g. `chat_input_parser.py`'s `_llm_call_cheap`/`_llm_extract_ncbi_ids`) is a reasonable style template, but there's no reusable code for the actual role-classification logic.

---

### resolve_original_paper

Status: EXTEND

Existing equivalent (if any): Inline in `additional_pipeline.py:401-509` (inside `pipeline_with_gemini`).

What it currently does: Already implements the doc's core prioritization rule — pulls `pubmeds = _bp_data_now.get("pubmed", [])` and a DOI map off the already-fetched NCBI record, guarded by `if not pubmeds:` / `if not doi:` before falling through to broader web search (`smart_fallback`/`mtdna_classifier.search_google_custom`). This is genuinely "prioritize the record's own DOI/PMID over general search," just not exposed as a standalone function.

Gap vs. what's needed (if EXTEND): (1) Not a standalone callable — it's ~100 lines deep inside `pipeline_with_gemini`, interleaved with `acc_score["source_texts"]`/`links` mutation, so extracting it needs care not to break that surrounding state. (2) No `uploaded_files` branch exists anywhere — ties directly to `classify_uploaded_file` being NO MATCH above; there's no code path where uploaded files are consulted to pick the original paper.

Callers found (if EXTEND): Only caller is `pipeline_with_gemini` itself (`additional_pipeline.py:252`) — the logic isn't separately invoked elsewhere, so factoring it out won't break other call sites, but it will require restructuring the surrounding inline state accumulation it's currently entangled with.

Recommendation: Extend by extracting the existing NCBI-record-first logic (additional_pipeline.py:401-509) into a standalone function, then add the missing `uploaded_files`/`classify_uploaded_file` branch on top — build the new branch, don't rebuild the working prioritization logic that already exists.

---

### canonicalize_paper_reference

Status: NO MATCH (as a unified function — the underlying pieces exist separately)

Existing equivalent (if any): DOI extraction — `paper_resolver.py:109 normalize_doi()`, `paper_resolver.py:126 _scrape_doi_from_page()` + `:153 resolve_real_doi()` (page-meta-tag fallback for publisher URLs that don't embed a DOI, e.g. nature.com/cell.com). PMID — `paper_resolver.py:165 resolve_doi_to_pmid()` (DOI→PMID via Entrez). PMCID — `NCBI.py:807 fetch_pmc_fulltext()`'s internal EuropePMC step is the only place in the repo a PMCID is derived at all.

What it currently does: Each identifier type has its own extraction path, developed independently and called from different places; nothing combines all three into one `{doi, pmid, pmcid, access_type}` struct for an arbitrary input link.

Gap vs. what's needed (if EXTEND): `access_type` (abstract-only vs. full-text-capable, as a **URL-pattern heuristic**) doesn't exist at all — the closest concept, `paper_resolver.py:217 check_accessible()`, does a live network fetch (Unpaywall/PMC) to determine actual open/closed access, which is a *reachability* check, not a cheap pre-fetch URL-shape guess. Building `access_type` as specified means new logic, not adapting `check_accessible`.

Callers found (if EXTEND): N/A — building new, not extending an existing function; but note `normalize_doi`/`resolve_real_doi`/`resolve_doi_to_pmid`/`fetch_pmc_fulltext` all have their own existing callers elsewhere (in `additional_pipeline.py`'s fetch cascade, see `deduplicate_paper_links` below) that must keep working — the new function should call these, not replace them.

Recommendation: Build new in `paper_resolver.py`, assembled from the three existing identifier-extraction functions named above, plus new URL-pattern-based `access_type` logic (this part is genuinely new).

---

### deduplicate_paper_links

Status: NO MATCH (dedup logic exists but is confirmed wrong-by-design for this purpose — raw-string dedup, not canonical-ID dedup)

Existing equivalent (if any): `mtdna_classifier.py:400`, `smart_fallback.py:241,501,506,511`, `additional_pipeline.py:500` — all do `if link not in links` / `if l not in links` style dedup, i.e. exact string matching on the URL.

What it currently does: Prevents the exact same URL string from appearing twice in a results list. Does **not** group `doi.org/X`, `pubmed.../PMID`, and `pmc.ncbi.../PMCXXX` as "the same paper" — confirmed these would currently be treated as three unrelated links. There is also no `already_resolved_paper` exclusion concept anywhere (grepped `citing`, `already_resolved`, `is_original_paper` — zero hits).

Gap vs. what's needed (if EXTEND): Everything — grouping by canonical ID (depends on `canonicalize_paper_reference` existing first), preferred-variant selection within a group, and the "don't double-count the original paper as a citing-paper result" exclusion are all absent.

Callers found (if EXTEND): N/A — building new. But the doc explicitly says to reuse, not duplicate, the "existing DOI-first/PubMed-abstract-fallback logic" for picking the best-fetchable variant within a duplicate group — that cascade is real and located at **`additional_pipeline.py:567-713`** (inside `pipeline_with_gemini`, "Step 2 — Fetch text from DOI/publication links"): DOI page fetch → CrossRef metadata → PubMed abstract fallback (Entrez esearch/efetch, lines 584-609) → PMC full text (`NCBI.fetch_pmc_fulltext`, lines 615-632) → headless-browser Playwright render → Unpaywall OA copy. This cascade is inline, tightly coupled to `acc_score["source_texts"]`/`jsonSM`/`all_links` mutation, and has no separate callers today besides `pipeline_with_gemini` itself — extracting it into a standalone callable (without changing its behavior) is prerequisite work before `deduplicate_paper_links` can "call it" per the doc's instruction rather than reimplementing it.

Recommendation: Build `deduplicate_paper_links` new in `paper_resolver.py`, but treat extracting the `additional_pipeline.py:567-713` fetch cascade into a standalone function as a **shared prerequisite** — both this and `canonicalize_paper_reference` need it, so do that extraction once, not twice.

---

### find_supplementary_materials

Status: EXTEND

Existing equivalent (if any): `NER/html/extractHTML.py:392 HTML.getSupMaterial()` (journal-site heading/extension scan); `NCBI.py:807 fetch_pmc_fulltext()`'s `sup_links` (EuropePMC bulk supplementary-files zip + PMC XML `<supplementary-material>` links); `paper_resolver.py:83 discover_supplementary_links_in_text()` (regex keyword scan over already-fetched text for `datadryad`/`dryad.org`/`figshare`/`zenodo`/`osf.io` plus file-extension hints).

What it currently does: Three separate, real discovery mechanisms already exist and are already stitched together ad hoc in `additional_pipeline.py:576-711` and `api.py:756-774`. None of them actively queries a Zenodo/Figshare/Dryad API for a DOI-linked deposit — the Zenodo/Dryad/Figshare/OSF awareness in `discover_supplementary_links_in_text` only recognizes such URLs *if already present in text that's already been fetched*, it doesn't go looking for them independently.

Gap vs. what's needed (if EXTEND): No single `find_supplementary_materials(paper_source)` entry point unifies the three mechanisms — callers currently stitch them together inline, differently in `additional_pipeline.py` vs. `api.py`. Building the unified function is mostly a wrapping exercise; actively querying Zenodo/Figshare/Dryad APIs (vs. only recognizing their URLs in already-fetched text) would be new behavior, not present today.

Callers found (if EXTEND):
- `HTML.getSupMaterial()` — called from multiple sites in `additional_pipeline.py` (e.g. line 577) as part of building `jsonSM`.
- `NCBI.fetch_pmc_fulltext()` — called from `paper_resolver.check_accessible()` (line 237) and `additional_pipeline.py` (lines 622, ~887, ~908-935) for PubMed-linked PMC fallback.
- `paper_resolver.discover_supplementary_links_in_text()` — called from `api.py:760` (`_process_one_upload`).

Recommendation: Extend by wrapping the three existing mechanisms behind one `find_supplementary_materials(paper_source)` function in `paper_resolver.py` (matching the doc's suggested file location) — do not duplicate any of the three underlying fetchers, since each already has its own working callers that must keep functioning.

---

### run_llm_catchall_pass

Status: NO MATCH (for the conditional-skip mechanism; a merge-step candidate exists but has problems — see Recommendation)

Existing equivalent (if any): `model.py:1789-1810`, "PASS 2" inside `multi_prompts` — an LLM extraction pass over accumulated context to catch fields beyond the predefined schema.

What it currently does: This pass runs **unconditionally**, every time, regardless of whether context grew beyond an NCBI-only baseline — there is no gating/skip logic at all. Repo-wide grep for `catchall`, `big_context`, `ncbi_only`, `baseline` returns zero hits outside this investigation's own vocabulary, confirming the "skip if nothing new" concept doesn't exist anywhere.

Gap vs. what's needed (if EXTEND): The entire conditional-skip mechanism is missing (depends on `accumulate_big_context` becoming incremental and `ncbi_only_context` existing — see Stage 1 above, both already flagged NO MATCH/build-new).

Callers found (if EXTEND): `multi_prompts`'s Pass 2 is called from within `model.py`'s own extraction flow, consumed by `getMoreInfoForAcc` and ultimately `additional_pipeline.py`'s `acc_score["_additional_fields"]`.

Recommendation: Build the skip-gating logic new, in `extraction_pipeline.py`, sequenced after the Stage 1 incremental-context work above. On the merge step: one research agent suggested `model.py:1015 merge_metadata_outputs()` as "the shared merge function" this pass should use — **do not use it**. A separate, more thorough investigation (see `merge_metadata_into_table` section above) confirmed `merge_metadata_outputs` has **zero callers anywhere in the repo** (dead code) and does the wrong thing for this architecture (joins differing values with `" or "` string concatenation, no source-list tracking, no per-field explanation). The catchall pass should merge through the new `merge_metadata_into_table` (once built), not this function — flagging this explicitly since it's exactly the kind of "two overlapping functions, pick the wrong one" mistake this task was set up to catch.

---

## Stage 3 — Citing papers

### find_citing_papers

Status: EXTEND

Existing equivalent (if any): `smart_fallback.py:487 smart_google_search()` (orchestrates `smart_google_queries` + capped per-source searches), `smart_fallback.py:69 search_ncbi_elink()`, `smart_fallback.py:96 search_europepmc_fulltext(accession_id, max_results=5)` — this last one already has a configurable `max_results` parameter matching the doc's requirement almost exactly. Broader orchestration happens in `model.getMoreInfoForAcc()` (model.py:1470), which calls `smart_google_search`, dedupes (string-only, see `deduplicate_paper_links` above), filters by relevance (`smart_fallback.async_filter_links_by_metadata`), then fetches text per link.

What it currently does: `getMoreInfoForAcc` is today's "gather everything" call — it does not distinguish citing papers from any other kind of source, has per-source caps (2-5, some hardcoded like the `5` in `smart_google_search`'s inner query loop) but no single overall cap across all sources combined, and doesn't dedupe against the already-resolved original paper.

Gap vs. what's needed (if EXTEND): No dedicated "citing papers, capped at N total, excluding the original paper" function — `getMoreInfoForAcc` conflates citing-paper discovery with general link discovery. Needs a configurable overall cap (doc explicitly wants this tunable, not hardcoded) and needs to route through `deduplicate_paper_links` once that exists (both against each other and against the already-resolved original paper).

Callers found (if EXTEND):
- `pipeline.py:383` (inside `extractSources`, legacy) — unpacks `(more_all_output, more_linksWithTexts, more_links)`, merges directly into `all_output`/`links`/`linksWithTexts`.
- `additional_pipeline.py:803` (active) — same tuple shape, stores into `acc_score["source_texts"]["web_search_<link>"]` and appends to `all_links`.

Recommendation: Extend by carving a dedicated `find_citing_papers(accession, max_results=5)` out of `getMoreInfoForAcc`'s search portion (reusing `smart_google_search`/`search_ncbi_elink`/`search_europepmc_fulltext` as the "smart_search infrastructure" the doc names), rather than rebuilding search logic — but both existing callers currently expect a 3-tuple merged straight into a combined context, so switching to a separate citing-papers-only path (kept out of `big_context` per Stage 3's design rationale) is a real behavior change both callers need to be updated for, not just a signature tweak.

---

### build_cited_context

Status: NO MATCH

Existing equivalent (if any): None that keeps citing-paper text separate from the main context. Confirmed: `pipeline.py:380-386` appends web-search results (which its own comment calls "citing papers, institutional repos, etc.") straight into `all_output`, the main context; `model.py:1497-1519` does `context_for_llm += new_all_output` unconditionally for every link; `additional_pipeline.py:806-810` folds web-search texts into `acc_score["source_texts"]` alongside every other source. There is no separation today between primary (Stage 1+2) context and citing-paper context anywhere.

Gap vs. what's needed (if EXTEND): The entire "keep it separate" architecture is absent — today everything found gets merged into one context and everything gets included (no skip-on-no_match, since `check_accession_presence` itself doesn't exist — see Stage 2/3 presence-check section below).

Callers found (if EXTEND): N/A — building new.

Recommendation: Build new in `extraction_pipeline.py`, but it's entirely dependent on `check_accession_presence` existing first (see below) and on `find_citing_papers`/`deduplicate_paper_links` being built — sequence this after those.

---

### run_cited_context_extraction

Status: EXTEND (the conflict-flagging mechanism it needs already exists and works; the targeted extraction pass itself doesn't)

Existing equivalent (if any): No standalone targeted-extraction-anchored-to-accession-identifiers pass exists (verified: no "anchor"/"other subjects"/"this sample only" guard language anywhere in `model.py` or `additional_pipeline.py`). However, the **conflict-preserving merge mechanism** this function is supposed to plug into already exists and is real:
- `model.py:1536 _extract_additional_fields()` — its own prompt already instructs the LLM to emit `##CONFLICT: source_A=<val_A>, source_B=<val_B>` when sources disagree (model.py:1581).
- `api.py:342-391` (`_emit_field` closure, inside `_rows_from_new_pipeline`) — already parses `##CONFLICT:` out of a value and an explicit `[Conflict: ...]` tag out of explanation text, keeping both values visible via `conflict_parts.append(...)` rather than silently overwriting.
- `confidence_score.py:76 calculate_confidence()` — already flags `"##"` in a value as `conflict_detected` with a score penalty, but **has zero callers anywhere in the repo** (dead code, confirmed via grep).

Gap vs. what's needed (if EXTEND): The actual "one targeted LLM pass over `cited_context`, explicitly anchored to accession identifiers, merged via the shared merge function" doesn't exist — this needs to be built. But it should be built to route through the existing `##CONFLICT:` convention and `_emit_field`'s parsing of it, not invent a new conflict-representation scheme.

Callers found (if EXTEND): `_extract_additional_fields` is called only from `model.py:1799` (feeding into `getMoreInfoForAcc`'s return path, ultimately `data["_additional_fields"]` consumed by `api.py:455`). `calculate_confidence` has no callers to list since it's unused — worth flagging to the user as available-but-unused rather than something to route around.

Recommendation: Build the targeted extraction pass new, in `extraction_pipeline.py`, but wire its output through the existing `##CONFLICT:`-marker convention (so `_emit_field`'s existing parsing keeps working) and route confidence through `calculate_confidence` (finally giving that dead function a caller) rather than inventing a second conflict-flagging or confidence-scoring scheme.

---

### check_accession_presence

Status: NO MATCH (for the 3-way triage as a whole; two of the three branches have no deterministic precedent at all)

Existing equivalent (if any): Two boolean (2-state) substring checks exist — `model.py:1222` (`if acc_cleaned.lower() in context_for_llm.lower(): accession_found_in_text = True`, inside `multi_prompts`) and `data_preprocess.py:1447` (`if accession.lower() in text_l:`, inside `build_context_for_llm`, used to prioritize "PRIMARY CONTEXT" vs. keyword-scored supplemental context). Also `data_preprocess.py:813`, a private nested function `is_table_relevant(table, keywords, accession_id)` inside `merge_text_and_tables` — checks if an accession literally appears inside a flattened table, the closest existing "table_candidate"-adjacent primitive, but it's binary (present/absent), unexported, and only used to decide whether to keep a table in merged LLM input, not to gate full-text inclusion vs. skip for a whole source.

What it currently does: Both substring checks are exactly 2-state (found/not found), used purely as confidence-scoring signals (`signals["accession_found_in_text"]`) or context-prioritization hints — neither is used to decide whether to extract, skip, or route a source at all. No code anywhere detects "numeric ID/index column" table structure independent of accession-string matching — confirmed via repo-wide search of `additional_pipeline.py`, `model.py`, `data_preprocess.py`, `NCBI.py`, `ncbi_resolver.py`, `non_ncbi_resolver.py`, `paper_resolver.py`, `chat_input_parser.py`. The only "table" code found (`NER/PDF/pdf.py:138 extractTable`, `NER/html/extractHTML.py:480/491`, `NER/WordDoc/wordDoc.py:72`) extracts table *content*, it doesn't classify whether a table plausibly indexes per-subject data.

Gap vs. what's needed (if EXTEND): All three states need to exist; today only a crude 2-state version of "direct_match" exists (as a scoring signal, not a routing decision), "table_candidate" has no structural-heuristic precedent at all, and there's no LLM fallback for uncertain cases anywhere.

Callers found (if EXTEND) — for the two existing boolean checks, since anything replacing/wrapping them needs these to keep working:
- `model.py:1222` → feeds `accession_found_in_text` into `prompts[acc] = [prompt_for_llm, accession_found_in_text]`, consumed downstream as a plain bool.
- `data_preprocess.py:1447` → used inline within `build_context_for_llm` to bucket text as primary vs. supplemental context, not returned to an external caller.
- `confidence_score.compute_confidence_score_and_tier()` (confidence_score.py:206,231,234,245) — consumes `signals["accession_found_in_text"]` as a plain bool across several scoring branches. **If `check_accession_presence` becomes 3-way and something upstream starts passing a 3-way result into this signal, this function needs an explicit bool-coercion or a new signal, since it currently only branches on true/false.**

Recommendation: Build new in `accession_presence.py` (per the doc's file layout) as a genuinely new 3-way function — the two existing boolean checks can seed the deterministic "direct_match" branch's string-matching logic, but "table_candidate"'s structural heuristic and the LLM fallback are new work, not extensions of anything existing. Flag `confidence_score.compute_confidence_score_and_tier` as a caller-of-a-downstream-consumer that will need a small update once this exists, even though it doesn't call `check_accession_presence` directly today.

---

### process_source_for_accession

Status: NO MATCH (as a callable function); EXTEND target identified for where its logic should replace existing behavior

Existing equivalent (if any): `additional_pipeline.py:1021-1049` ("Step 4: Build combined text from ALL sources," inside `pipeline_with_gemini`); structurally similar but simpler pattern in legacy `pipeline.py:600-612`.

What it currently does: A single **unconditional** loop over every source in `acc_score["source_texts"]` (supplementary files, main paper, citing papers, uploaded files, NCBI records — all mixed together), concatenating all of them into `text` regardless of whether the accession is present. There is no presence check, no skip path, and therefore — importantly — **no existing regression to fix here**: today everything is always included and always sent to extraction, so introducing the doc's "skip no_match, no summarization" behavior is a genuinely new cost-control behavior, not a bug fix.

Gap vs. what's needed (if EXTEND): The entire branching structure (direct_match → extract; table_candidate → extract; no_match → skip + log) needs to be added where today there is no branching at all. This is entirely downstream of `check_accession_presence` existing.

Callers found (if EXTEND): This code isn't a separate function today, so there's no external caller list to trace — its only consumer is the rest of `pipeline_with_gemini` itself, which passes the resulting `text` into the LLM extraction call and returns `(accs_output, acc_score["source_texts"], text)` at `additional_pipeline.py:1298`. The enclosing `pipeline_with_gemini`'s only external caller is `api.py:1371`, which expects that same 3-tuple shape — changing what this loop does (e.g., skipping some sources) doesn't change that outer shape, so `api.py` shouldn't be directly affected by this specific change, only by upstream changes to `pipeline_with_gemini`'s overall behavior.

Recommendation: Build the 3-way branching function new in `extraction_pipeline.py`, sequenced strictly after `check_accession_presence`, then replace the unconditional loop at `additional_pipeline.py:1021-1049` with calls to it. Note the legacy `pipeline.py:600-612` has the same "no branching" pattern — not touching it, but flagging that if the legacy path is ever revived, it has this same gap.

---

## Output construction

### build_output_row

Status: EXTEND

Existing equivalent (if any): `api.py:289-550`, `_rows_from_new_pipeline()` (the active output-row/DataFrame-row builder; also referenced directly in README's "Transparency" table).

What it currently does, section by section:
- **Accession + per-field value columns**: already matches the doc's spec — `biosample_accession`, `bioproject`, `sra_accession`, optional `genbank_accession` (api.py:324-330), then one column per discovered field, value only (api.py:407, 490).
- **Explanation column**: already structured almost exactly as the doc wants — one cell, one line per field: `explanation_parts.append(f"• {field}: {display_narrative}")` (api.py:384), joined at api.py:543 into `row["explanation"]`. Difference from spec is cosmetic (bullet `"• field: ..."` vs. doc's plain `"field: ..."`), not structural.
- **Source column**: same — already one cell, per-field-prefixed lines with nested citation detail (api.py:507-538, `row["sources"]`).
- **Confidence column**: **confirmed NOT per-field, exactly as the doc's "Known Limitations" section predicts.** `compute_confidence_score_and_tier()` computes one blended score from row-level aggregate signals (`has_geo_loc_name`, `has_pubmed`, `num_publications`, `missing_key_fields`, etc.), producing a single `confidence_display` string for the whole row (`row["confidence_score"]`, api.py:545). I independently verified this (api.py:413-452, 545) before the research agent confirmed it — this is a real, current row-level-only limitation, matching the doc's explicit note that this is a known interim decision, not yet fixed.
- **time_cost**: already last column (`row["time_cost"]`, api.py:547).
- **Extra column not in the doc's spec**: `row["conflict"]` (api.py:546) — an aggregate of all fields' `[Conflict: ...]` tags, kept as its own column. The doc doesn't mention a separate Conflict column (conflicts are meant to live inside the per-field Explanation/value per Stage 3's `run_cited_context_extraction` section). Worth a decision (not answered by this investigation): fold `conflict` into the Explanation column's per-field lines, or keep it as an additional column beyond the doc's five.

Gap vs. what's needed (if EXTEND): Only the Confidence column needs real structural change — from one row-level blended score to a per-field-prefixed-line score computed at merge time. `confidence_score.calculate_confidence(field_name, predicted_value, sources)` (confidence_score.py:76) already has almost exactly the right per-field signature and returns `{'score','label','flags','explanation'}` — but it has **zero callers anywhere in the repo**. Wiring per-field confidence in means giving this existing-but-unused function a real caller (at merge time, per the doc's "computed per field at merge time... rather than one blended score per row"), rather than writing a new per-field scorer from scratch.

Callers found (if EXTEND):
- `api.py:1425` — streaming/partial rows: `partial_rows = _rows_from_new_pipeline(msg["__partial_data__"], ...)`, SSE-emitted to the frontend as `{"rows": partial_rows}`.
- `api.py:1487` — final rows: `_pipeline_rows = _rows_from_new_pipeline(accs_output, ...)`, extended into `all_rows`.
- `all_rows` → `mtdna_backend.save_to_excel(all_rows, ...)` (api.py:1562) — this Excel writer builds its DataFrame straight from row dict keys, so it is **not hardcoded to specific column names** and won't break outright from a Confidence-column restructure, but any column rename/removal changes the Excel sheet headers users see directly. No frontend/JS code was found in this repo parsing these column names specifically (none exists locally to check), so the main real risk is the Excel output shape changing under users who may have existing spreadsheets/scripts built around today's single blended `confidence_score` column.

Recommendation: Extend `_rows_from_new_pipeline`'s Confidence-column construction specifically — leave Explanation/Source alone (already correct shape) — by routing per-field confidence through `confidence_score.calculate_confidence()` computed at merge time (inside `merge_metadata_into_table`, once built) rather than at row-assembly time as today. This is the one place in Output Construction with a real architectural gap, not just a naming difference; also worth a decision on where `conflict` (api.py:546) ends up in the new structure.

---

## Summary: what's genuinely new vs. what's reuse/extend

**Clean REUSE AS-IS**: `identify_accession_type` (`ncbi_resolver.detect_accession_type`).

**EXTEND (real logic exists, gap is bounded and specific)**: `fetch_ncbi_record`, `find_related_accessions`, `process_related_accessions`, `accumulate_big_context`, `field_name_matches`, `resolve_original_paper`, `find_supplementary_materials`, `find_citing_papers`, `run_cited_context_extraction`, `build_output_row`. Also `merge_metadata_into_table` — technically EXTEND since the check-and-add skeleton exists at two call sites, but both currently do the wrong thing on a match (drop/overwrite instead of corroborate), so most of the function's actual logic is new.

**NO MATCH (genuinely new)**: `classify_uploaded_file`, `canonicalize_paper_reference`, `deduplicate_paper_links`, `run_llm_catchall_pass`'s skip-gate, `ncbi_only_context` snapshot, `check_accession_presence`, `process_source_for_accession`, `build_cited_context`.

**UNCERTAIN — needs a follow-up look before deciding**: `init_output_table_from_record` (may or may not exist in the active pipeline — only confirmed in the legacy one); whether `mtdna_backend.py`/`pipeline.py` (the legacy pipeline) need any attention at all or can be set aside entirely.

**Sequencing note carried over from the user's own plan**: `merge_metadata_into_table` and `check_accession_presence` are correctly identified as the right starting points — nearly everything else in Stage 2/3 (`process_source_for_accession`, `build_cited_context`, `run_llm_catchall_pass`'s skip logic, `run_cited_context_extraction`) is explicitly blocked on one or both of them existing first.
