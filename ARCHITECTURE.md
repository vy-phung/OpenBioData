# ARCHITECTURE.md — Current-state map of the four conceptual modules

## Start here

OpenBioData (BioMetadataAudit) works in four conceptual stages. Given a
public accession (BioProject, BioSample, SRA, GEO, or GenBank), it:

1. **Traces back** — resolves the accession to its official database record
   and finds the paper that deposited it.
2. **Mines the literature** — starting from that paper, searches for every
   other publication that cites or reuses the same accession/sample, and
   fetches full text or supplementary materials where possible.
3. **Cross-checks** — compares field values across every source found (the
   original paper, citing papers, supplementary tables) to catch agreement,
   conflicts, or gaps.
4. **Recovers, verifies, and expands** — using all that cross-checked
   evidence, fills in missing fields, confirms or flags existing ones, and
   surfaces new metadata fields beyond what was originally asked for — each
   one with a citation and a confidence score.

That's the product. Everything below this point is a **detailed, line-level
audit of how those four stages are actually implemented in the code today**
— written for contributors who want to trace a specific function, verify a
claim before relying on it, or pick up a `good first issue`. It is not
required reading to understand or use the tool.

A few terms that come up repeatedly below, defined once here so they're not
re-explained inline every time:

- **"Live" vs. "legacy"** — this repo contains two historical implementations
  of parts of the pipeline. "Live" means the code path the deployed server
  (`api.py`) actually calls today. "Legacy" means code that still exists in
  the repo, still runs if called directly, but isn't reachable from the live
  server anymore. Legacy code isn't automatically wrong or slated for
  deletion — some of it is reused as a utility by the live path — but don't
  assume a function is in use just because it exists.
- **`elink`** — an NCBI Entrez API endpoint that returns records linked to a
  given accession (e.g. "which PubMed articles are linked to this
  BioProject"). Used throughout Module 1 and Module 2 for cross-referencing.
- **PMID / DOI / PMCID** — three different identifiers for the same
  underlying paper (PubMed ID, Digital Object Identifier, PubMed Central ID
  respectively). A recurring theme below is that the same paper reached via
  different identifier types currently gets treated as separate sources
  rather than deduplicated — see Issue 2 in the launch kit.
- **`acc_score` / `niche_cases`** — internal variable names in the live
  pipeline code (`additional_pipeline.py`). `acc_score` is the per-accession
  working dictionary that accumulates source text, links, and confidence
  signals as the pipeline runs. `niche_cases` is the list of user-requested
  field names for a given run. Neither is public API — they're named here
  only because the audit below traces data through them directly.

Three other internal docs are referenced below by name —
`workflow_architecture_v2.md` (an aspirational redesign proposal, not the
current implementation), `HOW_IT_WORKS.md`, and
`test_output_architecture_gap_map.md` (a past internal gap analysis, now
partly out of date — see Module 3/4 notes on what's changed since). If
you're reading this on GitHub and any of these don't resolve as links,
they exist in the project's working tree but may not yet be committed to
this branch — treat their contents as background context rather than
something you need to open to follow the audit below.

---

## Detailed module-by-module audit

Read-only investigation, current as of 2026-08-02 (HEAD `b3c9ccf`). No code
was changed to produce this document. It describes what the code **actually
does today**, not the target design in `workflow_architecture_v2.md`.

### How to read this

There are **two pipelines in the repo; only one is live**:

- **Live**: `python api.py` (per README) starts a FastAPI/uvicorn server.
  `api.py` imports `additional_pipeline.pipeline_with_gemini` as `_rich_pipeline`
  (`api.py:1418-1419`) and calls it from the `/analyze` SSE endpoint. Output
  rows are assembled by `api.py`'s `_rows_from_new_pipeline()` (~`api.py:289`)
  and written to Excel by `mtdna_backend.save_to_excel()` (`api.py:1656,1745`).
  `api.py` also imports three narrow utilities from `mtdna_backend.py`
  (`extract_accessions_from_input`, `save_to_excel`, `summarize_results`) but
  never imports `mtdna_classifier.py` or `pipeline.py` directly.
- **Legacy**: `pipeline.py`'s own `pipeline_with_gemini()` (a different function,
  same name, different signature, `pipeline.py:390`) is reachable only through
  `mtdna_backend.pipeline_classify_sample_location_cached()`
  (`mtdna_backend.py:31-37`, called at `mtdna_backend.py:246`), which `api.py`
  never calls. This whole chain (`pipeline.py`, and `mtdna_classifier.py`'s
  role as *the* NCBI-fetch entry point, as `HOW_IT_WORKS.md:191` still claims)
  is dead from the live server's perspective. Treat any reference to
  `mtdna_classifier.fetch_ncbi_metadata` as "documented but not the live entry
  point" — see Module 1 below for its real (subordinate) role.

**Two function name collisions worth flagging up front**: `pipeline_with_gemini`
exists in both `pipeline.py` (dead) and `additional_pipeline.py` (live) with
incompatible signatures. `fetch_ncbi_metadata` in `mtdna_classifier.py` is
documented in `HOW_IT_WORKS.md` as the NCBI fetch entry point, but the live
entry point is actually `NCBI.extract_NCBI_directly()`, which only calls
`fetch_ncbi_metadata` as one of four branches (see Module 1).

**Fixed since this audit was first written, noted here for the record**:
`ncbi_resolver.py` briefly had one debug line,
`print(resolve_from_biosample("SAMN38287679"))`, inserted at **module scope**
(column 0, not inside a function) right after `resolve_from_biosample`'s
definition. Because it sat at the top level, it fired a live NCBI network
round-trip and a stdout dump on the **first import** of `ncbi_resolver.py`
anywhere in the process — not gated by `if __name__ == "__main__":`. This has
since been removed and committed; flagging it here only so the fix has a
paper trail and nobody re-introduces the same pattern.

**Also fixed since this audit was first written**:

- **Canonical-ID dedup.** Module 2's "Dead / duplicate code" section below
  documents that every link-collection site deduped by raw string equality
  rather than canonical DOI/PMID/PMCID, inflating `num_publications` and
  causing the same paper's text to be fetched more than once. This has been
  fixed and committed — links are now deduped by canonical identifier at
  every site listed there.
- **Record-derived vs. search-derived citation origin.** Module 2's
  "Overlaps" section below documents that the `web_search_` key prefix was
  the only trace of this distinction, and that it was write-only — nothing
  downstream read it. This has been fixed and committed: a parallel
  `acc_score["source_texts_origin"]` dict, keyed identically to
  `source_texts`, now tags every collected source `"record"` or `"search"`
  at the point it's first gathered.
- **Per-field confidence scoring.** Module 4's "Overlaps" section below
  documents that confidence was per-row (blended), not per-field, despite
  `calculate_confidence()` already existing with the right signature. This
  has been fixed, committed, and validated against real data (PRJNA976261,
  12 samples) — confidence now varies genuinely per field within a row
  (e.g. a weak field scoring 0 while others in the same row score 40-60),
  with row-level confidence correctly taking the minimum across fields
  rather than a blended average. As part of the same change, extraction was
  also switched from one LLM call per sample to one call per paper (batched,
  with a reactive split-and-retry fallback for batches that exceed the
  output token ceiling) — this is what actually made per-field consistency
  possible, since independent per-sample calls had no mechanism to agree
  with each other on field naming or values.

The three sections below still describe these as open problems in their
original line-level detail — that detail (which files, which functions, why
it mattered) remains accurate as a record of what was fixed and why. Read
those sections as "this is what was true, and here's the fix" rather than
"this is still broken."

---

## Module 1 — Trace back

*Resolve a given accession to its official database record and find the paper
that deposited it.*

### What exists

**Accession-type detection** — `ncbi_resolver.detect_accession_type()`
(`ncbi_resolver.py:51`): pure regex, `accession_id -> 'bioproject' |
'biosample' | 'genbank' | 'sra_experiment' | 'sra_run' | 'geo_series' |
'geo_sample' | 'unknown'`. Called by `resolve_accessions()`
(`ncbi_resolver.py:1129`) and `input_handler.build_pipeline_input()`
(`input_handler.py:90`).

**Accession cross-referencing (NCBI)** — `ncbi_resolver.py` is pure
BioProject↔BioSample↔GenBank↔SRA↔GEO graph traversal; it never touches
DOI/PMID. Its output shape is always `{key: {bioproject, biosample,
accession, experiment}}` (4 string fields, never `None`), built by
`resolve_from_bioproject/_biosample/_genbank/_sra/_geo_series/_geo_sample`
(lines 790, 387, 307, 823, 1105, 959). For BioProject/GEO-series inputs (many
samples), `enumerate_project_samples()` (line 1171) returns cheap placeholder
entries tagged `_lazy_kind`, resolved one at a time on demand by
`resolve_lazy_entry()` (line 1246) — called live from
`additional_pipeline.py:387`. `input_handler.build_pipeline_input()`
(`input_handler.py:52`) is the actual live orchestrator: text →
`(resolved_dict, skipped_list)`, called from `api.py:1295-1299` inside the
`/analyze` endpoint.

**Record fetch + paper-linking (the part that finds DOI/PMID)** —
`NCBI.extract_NCBI_directly(accession) -> {accession: payload}`
(`NCBI.py:736`) is the actual live entry point, called from
`additional_pipeline.py:444,458,461,470,484,488,499` once per populated key
in the resolved record. It dispatches on its own bare `.startswith("PRJ"/
"SAM"/"SR"/"ER")` checks (a cruder, duplicate accession-type detector — see
Overlaps) to:
- BioProject → `NCBI.fetch_bioproject()` (`NCBI.py:86`), which is where the
  depositing paper is actually found: `<ProjectDescr/Publication>` XML PMID
  first, then EuropePMC full-text search by accession phrase, then NCBI
  `elink bioproject→pubmed`, then DOI resolution per PMID via
  `NCBI.get_doi_via_europepmc()` (`NCBI.py:758`). Output keys: `bioproject_id,
  title, description, publications, pubmed, pubmed_dois, biosamples,
  umbrella_projects, external_links`.
- BioSample → `NCBI.fetch_biosample()` (record text only, no PMID/DOI).
- SRA experiment/run → `NCBI.get_experiment_xml()` (record text only).
- Bare GenBank accession → `mtdna_classifier.fetch_ncbi_metadata()`
  (`mtdna_classifier.py:40`) — GenBank XML → `{country, specific_location,
  ethnicity, sample_type, collection_date, isolate, title, doi, pubmed_id,
  all_features}`. This is `fetch_ncbi_metadata`'s real, current role: a
  **fallback branch of `extract_NCBI_directly`**, used only when the
  BioProject-level lookup found no PMID.

**Non-NCBI accessions** — `non_ncbi_resolver.py` has its own third
accession-family detector, `detect_non_ncbi_database()` (line 59, for
MassIVE/PRIDE/MetaboLights/MGnify/BioStudies/EGA/PDB patterns), and
`build_non_ncbi_entry()` (line 73) which returns an all-empty NCBI-field
record so the NCBI-fetch step is skipped. Depositing-paper discovery for
non-NCBI accessions has no record-level equivalent to BioProject's
`<Publication>` XML — it falls straight through to keyword search (Module 2)
via `get_search_keywords()`/`fetch_dataset_metadata()` (lines 97, 148), except
for MassIVE, whose dataset API happens to expose a "Publications" section
(`fetch_dataset_metadata`, lines 200-216).

**Live wiring for the DOI/PMID-priority decision itself** —
`additional_pipeline.py:538-556`, inside `pipeline_with_gemini`: only runs
`if pubmeds:` (i.e. only after Step 1's record fetch found something).
Priority is (1) BioProject `<Publication>` PMID → BioProject's own cached DOI
map or `NCBI.get_doi_via_europepmc()`, else (2) GenBank record's own PMID/DOI
(via `fetch_ncbi_metadata`, only reached if BioProject yielded nothing). Both
are genuinely "read off the deposited record," not search — this is the real
implementation of the "prioritize the record's own DOI/PMID" idea named in
`workflow_architecture_v2.md`, just not extracted into a standalone function.

### Overlaps / blurry boundaries

- **Trace-back → Mine-the-literature boundary dissolves immediately after the
  record-derived DOI is found.** `additional_pipeline.py:787-862` ("Step 3")
  runs **unconditionally**, even on total DB-fetch failure, calling
  `model.getMoreInfoForAcc()` (keyword web search — Module 2). Its results,
  and any PubMed URLs it surfaces (Step 3b, lines 864-886, resolved to DOIs
  and fetched through the identical extraction path as the record-derived
  DOI), land in the **same** `acc_score["source_texts"]`/`links` structures as
  the record-derived paper, with no field distinguishing "the record's own
  deposited paper" from "something search happened to return for this
  accession." The `web_search_` key prefix set in Step 3
  (`additional_pipeline.py:856`) is the only trace of this distinction, and
  it's write-only — nothing downstream reads it (confirmed by grep).
- `paper_resolver.py` implements the **reverse** relationship (paper → its
  accessions), used when the user pastes a DOI/link instead of an accession
  (`api.py:1096` `extract_samples_from_paper`). It's a legitimate, separate
  feature reachable from the same `/analyze` endpoint, but it duplicates
  trace-back's DOI/PMID logic independently (its own `resolve_doi_to_pmid`,
  `normalize_doi`, `_scrape_doi_from_page`) rather than sharing code with
  `NCBI.get_doi_via_europepmc`.

### Dead / duplicate code

- **Accession-type detection implemented three times**, no shared dispatcher:
  `ncbi_resolver.detect_accession_type()` (regex, 8 types, most rigorous),
  `NCBI.extract_NCBI_directly()`'s bare `.startswith()` checks (4 crude
  buckets, conflates SRA run/experiment and doesn't recognize GEO at all),
  `non_ncbi_resolver.detect_non_ncbi_database()` (separate non-NCBI table —
  non-overlapping domain, same category of problem solved a third way).
- **BioProject→BioSample enumeration implemented twice**, both fire on the
  same request: `ncbi_resolver._find_bioproject_samples()` (5-strategy
  fallback chain) via `input_handler.build_pipeline_input`, and
  `NCBI.get_biosamples_from_bioproject()` (`NCBI.py:565`, single elink call,
  no fallbacks) via `extract_NCBI_directly`'s BioProject branch. A BioProject
  request does the "find linked BioSamples" NCBI round-trip twice.
- **User-input tokenization implemented twice, sequentially, on the same
  string**: `mtdna_backend.extract_accessions_from_input()`
  (`mtdna_backend.py:92`, splits on `[\n,;\t]`, validates format) runs first
  (`api.py:1214`); `input_handler.parse_user_input()` (`input_handler.py:22`,
  splits on `[,\n;\s]+`, no validation) runs second (`api.py:1298`) on the
  already-tokenized-then-rejoined output of the first pass.
- **`pipeline.py::pipeline_with_gemini`** (`pipeline.py:390`) — dead from
  `api.py`'s perspective (see "How to read this" above), superseded by
  `additional_pipeline.pipeline_with_gemini`. Other functions in the *same
  file* (`pipeline.process_link_allOutput`, `pipeline.unique_preserve_order`,
  `pipeline.sanitize_filename`) are still live utilities called from
  `additional_pipeline.py` — so `pipeline.py` is not uniformly dead, only its
  own orchestrator function is.
- **PMID→DOI / DOI→PMID resolution implemented independently at least three
  times** with no shared helper: `NCBI.fetch_bioproject`'s EuropePMC layer,
  `NCBI.get_doi_via_europepmc()` (PMID→DOI), and `paper_resolver.
  resolve_doi_to_pmid()` (DOI→PMID, opposite direction, separate
  implementation). See Module 2 for two more instances of the same pattern
  inside `additional_pipeline.py` itself.
- `accessions.csv`/`accessions.xlsx` (repo-root smoke-test fixtures) contain
  only bare GenBank accessions — they exercise just one of the seven
  accession-type branches, not BioProject/BioSample/SRA/GEO.

---

## Module 2 — Mine the literature

*Starting from the depositing paper, find every other publication that cites
or reuses the same accession/sample, and fetch full text or supplementary
materials where possible.*

### What exists

**The actual accession → citing-paper search** lives in `smart_fallback.py`,
orchestrated by `model.getMoreInfoForAcc(iso, acc, saveLinkFolder, niche_cases,
limit_context, extra_metadata) -> (context_for_llm: str, linksWithTexts: dict,
links: list)` (`model.py:1830-1894`). Internally: `smart_fallback.fetch_ncbi()`
(GenBank metadata for query-building) → `smart_fallback.smart_google_queries()`
(builds up to ~10 query strings from organism/author/journal/title/etc.) →
`smart_fallback.smart_google_search()` (`smart_fallback.py:487`, runs each
query through `mtdna_classifier.search_google_custom` in practice — see Dead
code below — then appends `search_ncbi_elink()` and
`search_europepmc_fulltext()` results) → `smart_fallback.
async_filter_links_by_metadata()` (keyword-relevance filter + text fetch per
link, via `data_preprocess.async_extract_text`). `search_europepmc_fulltext()`
(`smart_fallback.py:96`) is the one function that searches full-text-indexed
paper *bodies* (not just abstracts/metadata) for the accession string —
closest thing in the repo to genuine citation discovery. This whole chain is
called live from exactly one place: `additional_pipeline.py:850-852` ("Step
3 — Web search — runs ALWAYS", per its own comment, even if the NCBI record
fetch in Module 1 fully succeeded).

**Content fetching (full text / supplementary materials)** — three real,
independent discovery mechanisms, all live but stitched together ad hoc
rather than through one entry point:
- `NER/html/extractHTML.py`'s `HTML` class: `getSupMaterial()` (line 392,
  two-pass heading-keyword + file-extension scan of a fetched page's links),
  `async_getListSection()`/`getListSection()` (async/sync twins, lines 225 and
  296, `<h2>`-bucketed paragraph extraction merged with `getTablesAsText()`),
  `fetch_crossref_metadata()` (line 57).
- `NCBI.fetch_pmc_fulltext(pmid) -> {"text", "pmc_id", "sup_links"}`
  (`NCBI.py:807`): EuropePMC PMID→PMCID, then `efetch` PMC XML full text plus
  `<supplementary-material>` links. Genuinely shared (not duplicated) — called
  from both `paper_resolver.check_accessible()` and **both**
  `additional_pipeline.py`'s Step 2 (line 669) and Step 3b (line 925).
- `paper_resolver.discover_supplementary_links_in_text()` (line 83): regex
  keyword scan for Dryad/Zenodo/Figshare/OSF URLs, but only over text already
  fetched — never actively queries those platforms' APIs for a DOI-linked
  deposit.
- PDF/DOCX table+text extraction: `NER/PDF/pdf.py` has two parallel classes
  (`PDF`, old/tabula-based, and `PDFFast`, new/PyMuPDF-based) — see Dead code,
  the live path mixes them (uses `PDFFast.extract_text()` for text but the
  *old* `PDF.extractTable()` for tables, e.g. `paper_resolver.py:278-279`).
  `NER/WordDoc/wordDoc.py`'s `WordDocFast` is the sole, fully-live DOCX
  extractor.

**The DOI-fetch cascade** (the actual multi-rung fallback for "get this
paper's text"), implemented in `additional_pipeline.py`'s **Step 2**
(lines 616-786, for the depositing paper's DOI) as: supplementary links +
list-section text → CrossRef metadata → PubMed abstract (Entrez esearch/
efetch) → PMC full text (`NCBI.fetch_pmc_fulltext`) → Playwright headless
render (`extractHTML.async_fetch_html_playwright`) → Unpaywall OA URL. **Step
3b** (lines 864-1017, for DOIs resolved from web-search-discovered PubMed
URLs found in Module-2's own search step) re-implements this **same 5-rung
cascade nearly verbatim**, as a ~150-line copy with `_pm`-suffixed variable
names, rather than calling a shared function.

### Overlaps / blurry boundaries

- **Depositing-paper and citing-paper fetch cascades are structurally
  identical but literally duplicated, not shared**, and both write into the
  same `acc_score["source_texts"]` dict keyed only by DOI-URL string — nothing
  downstream can tell a Step-2 entry (depositing paper) from a Step-3b entry
  (search-discovered paper) apart. The one weak signal (`web_search_` prefix,
  set only in Step 3, not Step 3b) is never read by anything.
- **This module's output feeds directly, undifferentiated, into Module 4's
  extraction context.** Step 4 of `pipeline_with_gemini`
  (`additional_pipeline.py:1072` onward) concatenates every `source_texts`
  value — NCBI record text, depositing-paper text, citing-paper text, user
  uploads — into one blob for the LLM, with no structural marker for "this
  came from mining the literature" vs. "this came from the record itself."
  This is the same blur `workflow_architecture_v2.md` explicitly designed
  against (its Stage 3 "keep citing-paper context separate" proposal), and
  it does not exist in the live code — there's no `big_context`/
  `cited_context` split at all today.
- `paper_resolver.py` (Module 1's reverse-direction sibling) and
  `smart_fallback.search_ncbi_elink`/EuropePMC search both traverse the same
  NCBI accession↔PMID elink relationship from opposite ends, independently,
  with no reciprocal check that a paper discovered via one direction
  round-trips back to the same accession via the other.
- `model.getMoreInfoForAcc`'s fallback (`model.py:1855`): if the keyword
  relevance filter finds zero hits, it silently falls back to processing
  **every unfiltered search-result link**, including off-topic ones — no
  distinction between "filter legitimately found nothing" and "filter step
  degraded."

### Dead / duplicate code

- `smart_fallback.google_accession_search()` (line 218) — zero callers.
- `smart_fallback.filter_links_by_metadata()` (sync twin, line 404) — zero
  callers; the only place `TRUSTED_DOMAINS`/`is_trusted_link` is used, and
  it's unreachable.
- `smart_fallback.py`'s own `search_serper`/`search_pubmed_free`/
  `search_europepmc_free`/`_search_any` (lines 8-67) — reachable only if
  `import mtdna_classifier` fails inside `smart_fallback.py`; since
  `mtdna_classifier` imports successfully throughout the live app,
  `mtdna_classifier.search_google_custom` shadows these at runtime — a
  dormant, near-duplicate parallel search implementation.
- `NER/PDF/pdf.py`'s old `PDF` class: `extractText`, `extract_text_excluding_
  tables`, `extractTextWithPDFReader`, `mergeTextinJson`, `openPDFFile` — zero
  callers (only `PDF.extractTable()` from this class is live).
- `NER/PDF/pdf.py`'s new `PDFFast` class: `extract_text_excluding_tables`,
  `extract_tables` — zero callers; live code still uses the *old* class's
  tabula-based `extractTable()` for tables even in `PDFFast`-based flows, so
  the newer table extractor is the dead half of a duplicated pair.
- `NER/WordDoc/wordDoc.py`'s `WordDocFast.extractTablesAsExcel` — zero
  callers.
- `NER/html/extractHTML.py`'s `HTML.getReference` (line 382) — zero callers,
  and also broken against the current codebase: it does `json["References"]`
  as if `getListSection()` still returned a dict, but that method now returns
  a plain `str` — would raise `TypeError` if ever invoked.
- `NER/html/extractHTML.py`'s `HTML.bulk_fetch` (classmethod, line 194) —
  zero callers.
- `pipeline.py::extractSources()` (line 290) — zero callers; contains its own
  older, fourth variant of the DOI→PMC-abstract cascade, fully superseded by
  `additional_pipeline.py`'s Step 2/3/3b.
- **Dedup by raw string equality, never canonical DOI/PMID/PMCID, at every
  link-collection site**: `smart_fallback.smart_google_search()` (lines 501,
  506, 511), `smart_fallback.async_filter_links_by_metadata()` (lines
  392-398, dict keyed by raw link string), `mtdna_classifier.
  search_google_custom()` (lines 412-435), `additional_pipeline.py` (Step
  2/3/3b, lines 607, 675, 725, 765, 859, 883, 932, 977, 997), `pipeline.
  unique_preserve_order()` (`pipeline.py:184-186`, used for the final
  `acc_score["source"]` list at `additional_pipeline.py:1376` and inside
  `getMoreInfoForAcc` at `model.py:1838`). Net effect: `doi.org/X`,
  `pubmed.ncbi.nlm.nih.gov/PMID`, and `pmc.ncbi.nlm.nih.gov/PMCID` for the
  *same paper* are treated as separate sources, inflating
  `signals["num_publications"]` (an input to Module 4's confidence score —
  see below) and causing the same paper's text to be fetched/concatenated
  more than once.
- Two divergent sync/async implementations of the same `<h2>`-bucketed
  section-extraction algorithm (`HTML.getListSection` vs. `HTML.
  async_getListSection`) have drifted: their ScienceDirect-API branches use
  different env-var access (`os.environ[...]` vs. `.get(...)`) and different
  trigger conditions — a fix made in one was not ported to the other.

---

## Module 3 — Cross-check

*Compare field values across all sources found (original paper, citing
papers, supplementary tables) to detect agreement, conflict, or gaps.*

### What exists

Cross-checking happens at three layers of increasing granularity, all live:

**3a. Inside the LLM prompt itself.** `model.multi_prompts()` (Pass 1,
`model.py:1440`) and `model._extract_additional_fields()` (Pass 2,
`model.py:1956`) both instruct the LLM to compare sources *while extracting*:
"If different sources give DIFFERENT values for the same field, keep the most
specific/reliable value AND append `##CONFLICT: source_A=<val_A>,
source_B=<val_B>`" (`model.py:1520-1521`, `model.py:2046-2048`). Pass 2 also
appends a `##SELF-CONTRADICTION:` marker when its own explanation negates its
own value (`_find_negation_contradiction`, `model.py:1918`). One LLM call does
extraction and cross-check together — there is no separate compare-only pass.

**3b. Per-sample, code-level merge.** `metadata_merge.
merge_metadata_into_table(table, new_fields, source_label, is_llm,
identifier_values) -> table` (`metadata_merge.py:90`) is the real cross-check
engine: for each incoming field it (1) rejects a value that exactly
duplicates a *different* identifier column's value via
`is_duplicate_identifier_value()` (line 42), (2) finds a matching existing
column via `field_name_matches()` (`field_aliases.py:172` — deterministic
`FIELD_ALIASES` synonym table first, LLM fallback `_llm_field_name_match()`
only for unrecognized names, both cached), (3) on agreement, appends
`"Confirmed by <source_label>."` to the field's explanation; on disagreement,
extends a `##CONFLICT: a=x, b=y` marker via `_extend_conflict_marker()` (line
80) and appends a `"Conflicting value from <source>"` line. Table shape:
`{field_name: {"value", "explanation", "sources": [...], "is_llm": bool}}`.
Called live from **both** `additional_pipeline.py:1298` (folding Pass-2/
schema-aligned fields) and `api.py:523` (merging raw Pass-2 fields into
`pass2_table`) — each call is per-accession; this function never sees more
than one sample's data at a time.

**3c. Whole-table, cross-sample pass, after every row is built.**
`metadata_merge.normalize_output_table()` /
`normalize_output_table_with_log()` (lines 536, 562) — run once, over the
entire assembled Excel table (all rows × all columns), right before writing
the file. Step 1: name-synonym column clustering via `field_name_matches()`
(union-find, line 486). Step 2: value-overlap clustering for columns that
aren't name-synonyms but agree ≥90% of the time on ≥2 overlapping rows. A
structural numeric-vs-text shape guard (`_type_shape_conflict`, line 405)
blocks merges where one column's values are confidently numeric-only and the
other's text-only, flagging the pair for manual review instead. Called live
from `mtdna_backend.save_to_excel()` (`mtdna_backend.py:530`), which is
called from `api.py:1656,1745`.

This is a substantive functional area that has evolved significantly since
the last time anyone documented it (the repo's own
`test_output_architecture_gap_map.md`, dated 2026-07-07, describes
`merge_metadata_into_table` as not yet existing / both call sites doing "the
wrong thing on a match" — that has since been fixed; `metadata_merge.py`
now correctly corroborates or conflict-flags rather than dropping/
overwriting).

### Overlaps / blurry boundaries

**There is no clean boundary between cross-check and recover/verify/expand —
they are interleaved in the same function calls, not sequential phases.** See
Module 4 below for the concrete evidence; the short version: the LLM prompts
that do "recovery/expansion" are the same prompts that do "cross-check" (3a);
`additional_pipeline.py:1283` (schema-alignment/recovery) and
`additional_pipeline.py:1298` (cross-check merge) are three lines apart in
the same code block operating on the same data structure; `api.py`'s
`_emit_field()` closure parses both the `[Conflict:]` tag (cross-check
output) and builds the citation/explanation narrative (recover/expand output)
in one function. The **only** genuinely separable stage is temporal:
`normalize_output_table()` (3c) runs strictly after all per-sample work is
done, as the last step before Excel export.

### Dead / duplicate code

- `model.merge_metadata_outputs()` (`model.py:1137`) — zero callers anywhere
  in the repo. Duplicates `merge_metadata_into_table`'s job but does it
  wrong (string-joins conflicting values with `" or "`, no source tracking,
  no conflict marker). `metadata_merge.py:6` itself carries a comment
  explicitly warning future contributors not to use it.
- `confidence_score.calculate_confidence()` (`confidence_score.py:77`) —
  **called but functionally unused**: it now has exactly one caller
  (`confidence_score.py:272`, inside `compute_confidence_score_and_tier`),
  but that call's return value is never read again — the surrounding
  match/mismatch decision (lines 277-287) is made independently via a plain
  substring check. So it is not literally dead code anymore (a change since
  the 2026-07-07 internal gap analysis, which called it zero-callers), but
  its output has no live effect — worth treating the same as dead code in
  practice.
- `confidence_score.set_rules()` is called redundantly by the legacy
  `mtdna_backend.py:304-306` path even though `compute_confidence_score_and_
  tier()` already defaults to calling it internally when `rules=None` — not a
  correctness bug, just a harmless redundant call site that only exists on
  the non-live legacy pipeline.

---

## Module 4 — Recover, verify & expand

*Using the cross-checked evidence, fill in missing metadata fields, confirm
or flag existing fields, and add new metadata fields beyond what was
originally requested — each with a citation and a confidence score.*

### What exists

**Recovery/expansion (new fields beyond what was requested)** —
`model._extract_additional_fields()` (Pass 2, `model.py:1956`) *is* this
step: it explicitly excludes fields already requested in Pass 1
(`niche_cases`, lines 1979-1980) and extracts every other attribute it can
find across all accumulated source text (geo_loc_name, host, tissue,
collection_date, sex, age, disease, "any other custom sample attribute" —
lines 2049-2054). Output: `{field_name: {"value", "explanation"}}`.
`model.align_to_schema(extracted_dict, schema, acc)` (`model.py:1669`) then
maps Pass 2's free-text field names onto a canonical schema vocabulary (e.g.
a user-supplied standardization schema), only for high-confidence LLM-judged
matches — called live from `additional_pipeline.py:1283`.
`model.annotate_with_ontologies()` (`model.py:1758`) adds ontology-ID
annotation (GO/OBO), but only in "ontology mode" (`additional_pipeline.
py:1325`).

**Verify/confirm/flag** — there is no separate "verify" function; this is a
side effect of Module 3's merge machinery. A field is "confirmed" when
`merge_metadata_into_table()` appends `"Confirmed by <source>."`
(`metadata_merge.py:158`), and "flagged" when it appends the `##CONFLICT:`
marker. Per-field citations live inside the `explanation` string itself,
produced by the Pass 1/Pass 2 prompts as `[Sources: ...]`/`[Conflict:
...]`/`[ID-match: ...]` tags (`model.py:1556-1567`, `model.py:2059-2069`),
then parsed back out downstream by `api.py`'s `_emit_field()` closure
(`api.py:348-352`) to populate the row's Explanation/Source/Conflict columns.

**Confidence scoring** — `confidence_score.compute_confidence_score_and_tier
(signals, rules=None) -> (score: int 0-100, tier: str, explanations: list)`
(`confidence_score.py:193`) is the live entry point, called once per **row**
(`api.py:470`), reading row-level aggregate `signals` (`has_geo_loc_name,
has_pubmed, accession_found_in_text, num_publications, missing_key_fields,
known_failure_pattern, any_key_field_lacked_id_linkage`, the last computed by
scanning *every* categorical field for missing ID linkage,
`additional_pipeline.py:1339-1360`). This matches the weights/tiers described
in `HOW_IT_WORKS.md` (`set_rules()`, `confidence_score.py:44`). A hard tier
cap applies: if any key field lacked ID linkage, the row can never reach
"high" tier (`confidence_score.py:332-334`).

### Overlaps / blurry boundaries — direct answers to the two open questions

**1. Is confidence per-field or per-row?** **Fixed since this audit was
first written.** As documented below, this used to be per-row (blended),
not per-field, despite `calculate_confidence(field_name, predicted_value,
sources)` (`confidence_score.py:77`) having almost exactly the right
per-field signature, and despite `compute_confidence_score_and_tier()`
being called once per sample and writing one shared `row["confidence_score"]`
string used for every field on that row. That gap is now closed:
`calculate_confidence()`'s output is wired into the actual stored per-field
result, and this has been validated against real data (PRJNA976261, 12
samples) — confidence genuinely varies field-to-field within a row (e.g. one
weak field at 0 while others in the same row sit at 40-60), with the
row-level `confidence_score` correctly taking the minimum across fields
(the weakest field caps the row) rather than a blended average.

**2. Where does cross-check end and recover/verify/expand begin?**
**Nowhere cleanly — they are interleaved at every layer**, not sequential
phases:
- Every LLM extraction call (`multi_prompts`, `_extract_additional_fields`)
  simultaneously extracts (recover), cross-checks (module 3a), and flags
  conflicts, in one prompt/response.
- `additional_pipeline.py:1283` (`align_to_schema`, recovery/standardization)
  and `additional_pipeline.py:1298` (`merge_metadata_into_table`,
  cross-check) run three lines apart, in the same `try` block, sharing the
  same `aligned_batch` data structure — the cross-check step directly
  consumes the recovery step's output with no gap between them.
- The ID-linkage confidence signal (module 4's own input) is computed from
  both Pass-1 and Pass-2 data in the same loop immediately after the
  module-3 merge call.
- `api.py`'s `_emit_field()` closure does both jobs in one function: parses
  module 3's `[Conflict:]` tags into `row["conflict"]`, and builds module 4's
  citation/explanation narrative into `row["explanation"]`, from the same
  string.

The one place with a genuine seam is temporal, not architectural:
`normalize_output_table()` (module 3c) runs strictly after all per-sample
recover/verify/expand work finishes, as the last step before Excel export —
that is the only stage boundary that actually exists in the code today.

### Dead / duplicate code

Already covered under Module 3 (`merge_metadata_outputs`,
`calculate_confidence`) since the dead code there is the same dead code that
would back this module's per-field confidence, if it were ever wired up for
real.

---

## Summary — what's genuinely missing vs. what's real but tangled

| | Exists and live | Notably absent |
|---|---|---|
| **Module 1: Trace back** | Full record fetch + record-derived DOI/PMID priority (`additional_pipeline.py:538-556`), backed by real NCBI cross-referencing | A single unified `fetch_ncbi_record()`-shaped function — logic is split across `ncbi_resolver.py`/`NCBI.py`/`non_ncbi_resolver.py` with 3x duplicated accession-type detection |
| **Module 2: Mine literature** | Real web/EuropePMC/elink search (`smart_fallback.py`), real multi-rung fetch cascade (`additional_pipeline.py` Step 2/3b), canonical-ID dedup (fixed since first audit), record-vs-search citation origin tagging via `source_texts_origin` (fixed since first audit) | A `find_citing_papers`-shaped function distinct from general search; separation between "citing paper" context and "depositing paper" context in what actually reaches the LLM (origin is now tagged, but Step 4 still concatenates everything into one undifferentiated blob) |
| **Module 3: Cross-check** | Real, three-layer, and — notably — more complete than the repo's own most recent internal gap analysis suggests (`merge_metadata_into_table`/`normalize_output_table` are now genuinely wired in, not stubs) | A pass that runs *before* recovery/expansion rather than interleaved with it |
| **Module 4: Recover/verify/expand** | Real Pass-2 expansion, real schema alignment, real per-field confidence scoring (fixed since first audit — `calculate_confidence()` is now wired into the stored result, validated on real data), per-paper batched extraction with reactive split-and-retry on oversized output (also fixed since first audit) | A pass that runs before recovery/expansion rather than interleaved with it |

No module is "not implemented at all" — all four have real, live logic. The
common thread across all four is that boundaries between them are blurry by
construction: a handful of large functions (`additional_pipeline.
pipeline_with_gemini`, `model.multi_prompts`/`_extract_additional_fields`,
`api._rows_from_new_pipeline`) each do work that spans two or three modules
in a single call, rather than the modules being separable stages with typed
interfaces between them.
