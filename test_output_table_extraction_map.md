# Table → LLM-context extraction: full function map

Investigation only. No code changed. All outputs below were produced by
actually running the functions against real files in this repo
(`test-data/PRJNA976261/FarinaR_2019.pdf`, `test-data/PRJNA976261/biosample_metadata.xlsx`).

## TL;DR

- For a **PDF upload** (the `/upload-context` endpoint), the real chain is:
  `NER/PDF/pdf.py: PDF.extractTable()` (tabula, raw DataFrames) →
  `data_preprocess.py: clean_tables_format()` → `data_preprocess.py:
  _serialize_tables_as_text()` → appended into `text`, which api.py writes
  to a temp file, which `additional_pipeline.py` later folds into
  `acc_score["source_texts"]["user_uploaded_file"]` and finally concatenates
  into the single `text` blob sent to the LLM (Step 4 in
  `additional_pipeline.py`, ~line 1051).
- **`clean_tables_format()` is used in this real path**, and it does exactly
  the thing its own docstring in `table_reliability.py` warns about: it
  **discards the DataFrame's true header** (`.columns`) and instead treats
  whatever tabula put in data-row 0 as the header, then **drops any blank
  cell from every row** (including legitimate merged/spanned cells), which
  **shifts all later cells left**. Running it on the paper's real Table 1
  (a merged-cell demographics table) produces silently mislabeled
  `Row: key=value` pairs — verified below, not just a theoretical bug.
- `table_reliability.py`'s `detect_candidate_id_tables()` reads the **raw**
  tables (correct DataFrame headers, before `clean_tables_format` mangles
  them) for a separate purpose: judging whether a table's ID column is
  reliable for per-sample lookup. It does **not** re-serialize row data, so
  it doesn't duplicate the garbled table text — **but its verdict block is
  appended into the same `text` string** that becomes the LLM context (via
  `api.py: _extract_text_from_upload`), so it's not fully "separate" either.
  It correctly flags the paper's real Table 1 as UNRELIABLE (repeating
  #1/#2/#3 patient IDs across groups) — a correct judgment about a table
  whose *data* the model is simultaneously being shown in garbled form by
  the other function.
- Word/.docx, HTML, and Excel/CSV each have their **own**, independently
  written table→text function, with three different serialization styles
  (`col=val` pairs vs. bare `" | "`-joined vs. Python-list `repr()` dump)
  depending on which code path a given source takes.

---

## 1. Full inventory of table→text functions

| # | Function | File:line | Format(s) | Reached from real serving path? |
|---|----------|-----------|-----------|----------------------------------|
| 1 | `PDF.extractTable()` | `NER/PDF/pdf.py:138` | PDF | Yes — raw table source for #2/#3/#7 |
| 2 | `clean_tables_format()` | `data_preprocess.py:748` | PDF/DOCX/XLSX/HTML (generic) | **Yes** — 3 real call sites (see §3) |
| 3 | `_serialize_tables_as_text()` | `data_preprocess.py:723` | PDF/DOCX (post-clean) | **Yes** — 3 real call sites |
| 4 | `extract_table()` | `data_preprocess.py:663` | PDF/DOCX/XLSX/HTML dispatcher | Yes, but only for *secondary* links (supplementary/extra), via `pipeline.process_link_allOutput` — not the direct upload path |
| 5 | `merge_text_and_tables()` | `data_preprocess.py:796` | PDF/DOCX (JSON-per-table) | **No — dead code**, only reachable via `preprocess_document()`, which nothing in `api.py`/`additional_pipeline.py` calls |
| 6 | `preprocess_document()` | `data_preprocess.py:840` | wraps #4 | **No — dead/unreachable** from the live app; also internally broken (see §5) |
| 7 | `detect_candidate_id_tables()` + `inject_table_reliability_context()` | `table_reliability.py:102,165` | PDF (raw DataFrames, correct headers) | **Yes**, for direct uploads only (`api.py:_extract_text_from_upload`) — separate judgment pass, not a row-data serializer |
| 8 | `WordDocFast.extractTablesAsList()` | `NER/WordDoc/wordDoc.py:72` | DOCX | Yes — feeds #2/#3 |
| 9 | `extractHTML.HTML.extractTable()` | `NER/html/extractHTML.py:480` | HTML | Yes — feeds #10 |
| 10 | `extractHTML.HTML.getTablesAsText()` | `NER/html/extractHTML.py:491` | HTML | **Yes** — called from `getListSection()`/`async_getListSection()`, which `additional_pipeline.py` calls directly ~6 times for DOI/Unpaywall/Playwright-rendered article pages |
| 11 | `_extract_excel_text()` | `data_preprocess.py:129` | XLSX/XLS | **Yes** — used for direct `.xlsx`/`.xls` uploads (`api.py:760-770`) and for fetched xlsx links (`extract_url_text`) |
| 12 | `extract_table()`'s xlsx branch (`pd.read_excel` + `.values.tolist()`) | `data_preprocess.py:701-710` | XLSX | Yes, but only via the secondary-link path (#4), and produces a *different* (header-dropping) shape than #11 for the same file type |
| 13 | `pipeline.process_link_allOutput()`'s table join | `pipeline.py:271` (`", ".join(str(t) for t in tables_link)`) | any (post `extract_table()`) | **Yes** — real path for supplementary/extra links found while resolving an accession |
| 14 | `paper_resolver.resolve_paper()`'s inline PDF table block | `paper_resolver.py:279-282` | PDF | **Yes** — separate PDF path for papers resolved by DOI/link that turn out to be a downloadable PDF (not a direct upload); duplicates #2+#3's logic, **without** the table-reliability check from #7 |
| 15 | `pipeline.extractSources()` / `pipeline.pipeline_with_gemini()` | `pipeline.py:290,390` | HTML (own copy) | **No — legacy/superseded.** `api.py` imports `additional_pipeline.pipeline_with_gemini`, not this one. Not called anywhere in the live app. |
| 16 | `model.read_docx_text()` + `parse_literal_python_list()` / `parse_population_code_to_country()` / `general_parse_population_code_to_country()` | `model.py:151,210,374,509` | DOCX-derived "table_strings" | **No — dead/legacy RAG subsystem** (`build_vector_index_and_data`, `load_rag_assets`), not called from `api.py` or `additional_pipeline.py` |
| 17 | `metadata_merge.merge_metadata_into_table()` | `metadata_merge.py:89` | n/a | **Out of scope** — merges the pipeline's *output* fields into the result table; consumes/produces structured metadata, not document text sent to the LLM |

---

## 2. Real output on FarinaR_2019.pdf's Table 1 (merged-cell table)

Table 1 of the paper (Group / Patient / Gender / Age / Year of diagnosis /
HbA1c / Teeth / Sites with PD≥5mm / BoP score) visually merges the `Group`
cell down 3 rows per group (T2D+P+, T2D+P-, T2D-P+, T2D-P-). Ran
`NER/PDF/pdf.py: PDF.extractTable()` (tabula) against the real file — this
table comes back as raw table index 3 of 9:

```
     Group Patient  Gender  Age (years)  Year of type 2 diabetes  Glycated haemoglobin  Teeth present  Sites with probing depth  Bleeding on probing
0      NaN     NaN     NaN          NaN                diagnosis                   (%)            (n)                  ≥5mm (n)            score (%)
1   T2D+P+      #1    Male         67.0                     1990                   7.3             24                        18                   30
2      NaN      #2  Female         60.0                     2000                   8.0             24                        29                   36
3      NaN      #3    Male         70.0                     2003                   7.4             20                        26                   44
4   T2D+P-      #1    Male         47.0                     2012                   8.0             26                         0                   18
...
```

tabula correctly recovers the **true header** as `df.columns` = `["Group",
"Patient", "Gender", "Age (years)", ...]`, and the merged `Group` cell shows
up as `NaN` for rows 2/3 within each group of 3 — exactly the expected raw
shape for a merged/spanned column.

### `clean_tables_format([t3])` — actual output

```python
[[
  ["diagnosis", "(%)", "(n)", "≥5mm (n)", "score (%)"],          # <- WRONG header
  ["T2D+P+", "#1", "Male", "67.0", "1990", "7.3", "24", "18", "30"],
  ["#2", "Female", "60.0", "2000", "8.0", "24", "29", "36"],      # <- one cell short
  ["#3", "Male", "70.0", "2003", "7.4", "20", "26", "44"],
  ["T2D+P-", "#1", "Male", "47.0", "2012", "8.0", "26", "0", "18"],
  ...
]]
```

Two independent bugs compound here:
1. `clean_tables_format()` converts the DataFrame with `.values.tolist()`
   and takes `table[0]` as "the header" — but `table[0]` is row-index-0 of
   the **data**, not `df.columns`. Row 0 here is itself a broken
   sub-header fragment (`diagnosis`, `(%)`, `(n)`, `≥5mm (n)`, `score (%)`)
   that tabula left behind because the real header wrapped across two
   lines in the PDF. The true 9-column header (`Group, Patient, Gender,
   Age (years), ...`) is discarded entirely.
2. Every row also drops any blank/falsy cell (`if str(cell).strip()`)
   rather than preserving position — so the merged `Group` cell being
   blank on rows 2/3 of each group **shifts every following cell one
   column to the left** relative to that row's neighbors.

### `_serialize_tables_as_text(cleaned)` — actual output (this is what reaches the LLM)

```
## Table 1
Row: diagnosis=T2D+P+, (%)=#1, (n)=Male, ≥5mm (n)=67.0, score (%)=1990, col5=7.3, col6=24, col7=18, col8=30
Row: diagnosis=#2, (%)=Female, (n)=60.0, ≥5mm (n)=2000, score (%)=8.0, col5=24, col6=29, col7=36
Row: diagnosis=#3, (%)=Male, (n)=70.0, ≥5mm (n)=2003, score (%)=7.4, col5=20, col6=26, col7=44
Row: diagnosis=T2D+P-, (%)=#1, (n)=Male, ≥5mm (n)=47.0, score (%)=2012, col5=8.0, col6=26, col7=0, col8=18
...
```

Read literally, this tells the LLM the patient's **gender is stored under
key `"(%)"`**, their **age under key `"(n)"`**, and their **year of T2D
diagnosis under key `"score (%)"`** — every field name is wrong, and it
gets *worse* for every row after a merged `Group` cell, since the column
count (and therefore the header pairing) silently shifts between the
first row of a group (9 fields) and the following two rows (8 fields, one
column short). This is a genuinely garbled, mislabeled table, not just a
missing-header cosmetic issue — **the model cannot recover the correct
Group/Patient/Gender/Age association from this text.**

### `detect_candidate_id_tables()` / `inject_table_reliability_context()` — actual output on the same raw table

This function is handed the **raw** (uncleaned) tables and uses the
DataFrame's real `.columns`, so it correctly identifies `Patient` as the ID
column and correctly reasons about the data:

```
- "Candidate table #4": UNRELIABLE (identifier value '#1' repeats 5 times --
  not a unique per-row mapping (e.g. an index that restarts across
  sub-groups/sections))
```

So the model *is* told "don't trust this table for per-sample lookup" —
but that verdict is about a table whose row *contents*, as actually shown
to the model via `_serialize_tables_as_text`, are already scrambled by
`clean_tables_format` before the model ever gets to apply that verdict.
The reliability check operates on better-quality data (the real header)
than the row-serialization the model actually reads.

---

## 3. Confirmed call path: PDF upload → LLM context

```
api.py: _extract_text_from_upload(file_bytes, filename)        [line 678]
  ├─ fitz (PyMuPDF): plain page text                            [688-704]
  ├─ NER/PDF/pdf.py: PDF(...).extractTable()  → raw_tables      [716]
  ├─ data_preprocess.clean_tables_format(raw_tables) → tables   [717]   <-- garbles header/alignment
  ├─ data_preprocess._serialize_tables_as_text(tables)          [718]   <-- serializes the garbled shape
  │    text += "\n" + tables_text                               [719-720]
  └─ table_reliability.detect_candidate_id_tables(raw_tables,   [732]   <-- uses RAW tables (correct headers)
       full_text=text)
     table_reliability.inject_table_reliability_context(...)    [733]
       text += "\n\n" + reliability_block                       [734-735]  <-- appended to the SAME text
  return text
        │
        ▼
api.py: _process_one_upload() writes `text` to ctx_path (user_context.txt)  [835-838]
        │
        ▼
api.py: _read_context_text(file_ids) reads it back as user_context_text /
        per_accession_context[sample_key]                        [1054-1064, 1347-1361]
        │
        ▼
additional_pipeline.py: pipeline_with_gemini(..., user_context_text=...,
        per_accession_context=...)                                [287]
  acc_score["source_texts"]["user_uploaded_file"] = _scoped_context or
        user_context_text                                         [1007-1011]
        │
        ▼
additional_pipeline.py Step 4 (~line 1051): concatenates every
  acc_score["source_texts"][...] entry into one `text` string —
  THIS is the actual combined context sent to the LLM.             [1051-1094]
```

So both `clean_tables_format`/`_serialize_tables_as_text`'s garbled table
text **and** `table_reliability`'s verdict block end up, unmodified,
inside the same final LLM input. `table_reliability.py` does *not*
duplicate table row content into the context (it only adds short
RELIABLE/UNRELIABLE lines), but it is **not fully separate/inert** either
— its output is real context text, appended right after the garbled
table dump it's implicitly commenting on.

### Other real paths that also reach the LLM context (not via direct upload)

- **Paper resolved by pasted DOI/link that turns out to be a PDF**
  (`paper_resolver.py: resolve_paper()`, lines 271-285): duplicates the
  exact same `clean_tables_format` + `_serialize_tables_as_text` call
  pair — **but does not call `table_reliability`** at all. So a
  PDF fetched this way gets the same garbled table text with *no*
  reliability annotation to offset it.
- **Supplementary/extra links found while resolving an accession**
  (`additional_pipeline.py` → `pipeline.process_link_allOutput()` →
  `data_preprocess.extract_table()`): this is a *third* serialization
  style — `extract_table()` already runs its own internal
  `clean_tables_format()` call (data_preprocess.py:714), then
  `process_link_allOutput` does `", ".join(str(t) for t in tables_link)`
  (pipeline.py:271), i.e. it dumps each table's Python `list` `repr()` as
  a bare string with no `col=val` pairing at all — worse than the direct
  PDF upload output, and without the tabula-header bug being visible as
  "col5=..." (it's just raw nested-list text), but still misaligned
  because the underlying `clean_tables_format()` call already stripped
  headers/shifted columns before `str()` is applied.
- **DOI-resolved HTML article pages** (`additional_pipeline.py` calling
  `extractHTML.HTML(...).async_getListSection()` ~6 times for the direct
  DOI page, Playwright-rendered fallback, and Unpaywall OA fallback):
  goes through `getTablesAsText()` (`NER/html/extractHTML.py:491`), which
  is structurally the "good" version of this pattern — it uses
  `pd.read_html()`'s DataFrame directly (`df.columns`, `df.fillna("")`)
  instead of routing through `clean_tables_format`, so it does **not**
  have the tabula sub-header bug. (Not independently verified against a
  real merged-cell HTML table in this pass — no HTML test fixture exists
  in `test-data/` — but the code path itself does not call
  `clean_tables_format`, so the specific header-discarding bug shown
  above for PDF cannot occur here; whether `pd.read_html` forward-fills
  `rowspan`/`colspan` cells or leaves them blank was not tested.)
- **Direct `.xlsx`/`.xls` upload** (`api.py:760-770` →
  `data_preprocess._extract_excel_text()`): a *fourth* style. See §4.

---

## 4. Excel/CSV path

`_extract_excel_text()` (`data_preprocess.py:129`) is the function actually
used for `.xlsx`/`.xls` uploads and fetched xlsx links. It reads each sheet
with `header=None` (no header row is ever extracted as such), fills NaN
with `""`, then per row does:

```python
cleaned = [c.strip() for c in row if c.strip()]
rows.append(" | ".join(cleaned))
```

i.e. **bare `" | "`-delimited cell dump, one row per line, no `col=val`
pairing, no header line prepended** — a plain row of whatever
non-empty cells remain, in order.

Ran against the real `test-data/PRJNA976261/biosample_metadata.xlsx`
("cMD Metadata" sheet, which has 8 merged cell ranges — e.g. `A1:E1` merged
as a "IDENTIFIERS" group heading spanning what are 5 separate columns
below it):

```
[Sheet: cMD Metadata]
IDENTIFIERS | TECHNICAL / STUDY | DISEASE / PHENOTYPE | DEMOGRAPHICS | ...
study_name | sample_id | subject_id | sra_accession | bioproject | ...
FarinaR_2019 | SAMN35361955 | ind1 | SRS17819534 | PRJNA976261 | ...
```

Here the merged-cell row (row 1, the group-title banner) collapses down to
just its unique group titles (since the other cells under the same merge
are blank and get filtered out) — which happens to look fine on its own
line since there's no header pairing to misalign, but it means row 1's 10
group titles no longer line up positionally with row 2's 49 real column
names or row 3's 49 data values. Because this function never attempts
`col=val` pairing in the first place, this specific case doesn't visibly
scramble field labels the way the PDF path does — but it also means the
LLM gets **zero explicit field-to-value association** for any Excel
upload; it has to infer which of the 49 pipe-joined values on a data row
corresponds to which of the 49 header names on the row above, purely from
prose-like ordering. This is the "bare one-value-per-line" (well, bare
pipe-joined) form the task asked about.

Separately, `extract_table()`'s own xlsx branch (`data_preprocess.py:
701-710`) reads the same kind of file completely differently: `pd.read_excel(xls,
sheet_name)` (default `header=0`, i.e. row 0 *is* consumed as the header
and excluded from the data), then `.values.tolist()` (which — like the PDF
case — discards `df.columns`), then it's fed to `clean_tables_format()`,
which will again pick data-row-0 as "the header" for
`_serialize_tables_as_text()` downstream. This is a **second, independent
Excel-reading implementation** producing a different (and similarly
header-mangled) shape than `_extract_excel_text()`'s output for the exact
same file, depending only on which link/upload code path a given Excel
file happens to arrive through.

---

## 5. Duplication and dead-code notes

- **`clean_tables_format()`** is called from 3 real call sites (`api.py`
  upload handler, `data_preprocess.extract_url_text` for fetched
  PDF/DOCX, `paper_resolver.resolve_paper`'s local-PDF branch) plus
  internally by `extract_table()` — 4 call sites total, all sharing the
  same header-discarding/column-shifting behavior documented in §2. It is
  **not** a one-off or already-replaced function; it is the common
  denominator underneath most of the PDF/DOCX table-to-text conversions
  in the live app, which is why the bug demonstrated above is not
  cosmetic — it's the default behavior for the large majority of table
  extractions in this codebase.
- **`merge_text_and_tables()`** (`data_preprocess.py:796`) and
  **`preprocess_document()`** (`data_preprocess.py:840`) are dead code
  from the app's perspective — nothing in `api.py` or
  `additional_pipeline.py` calls them (only `process_inputToken`, itself
  uncalled, references `preprocess_document`). `preprocess_document`'s own
  active line (873, since the `merge_text_and_tables` call is commented
  out) is also internally broken: `final_input = text + ", ".join(tables)`
  where `tables` is `List[List[List[str]]]` — `str.join` requires an
  iterable of strings, so this raises `TypeError` on any non-empty table
  list and falls into the bare `except: final_input = ""`, silently
  discarding both text and tables. Confirmed by direct inspection, not
  re-run (the function isn't reachable from the live app so there was no
  live path to exercise it against).
- **`pipeline.py`'s own `extractSources()` / `pipeline_with_gemini()`**
  (lines 290, 390) are legacy — `api.py` imports
  `additional_pipeline.pipeline_with_gemini`, not `pipeline.py`'s version.
  `pipeline.py`'s *helper* functions (`process_link_allOutput`,
  `run_with_timeout`, Drive helpers) are still imported and used by
  `additional_pipeline.py`, but `extractSources` itself, and therefore its
  call to `html.async_getListSection()`, is not reached in the live app —
  `additional_pipeline.py` calls `extractHTML.HTML(...).async_getListSection()`
  directly instead, bypassing `pipeline.extractSources` entirely.
- **`model.py`'s `read_docx_text` / `parse_literal_python_list` /
  `parse_population_code_to_country` / `general_parse_population_code_to_country`**
  plus the FAISS-based `build_vector_index_and_data`/`load_rag_assets` are
  an older RAG-chunking subsystem, not called from `api.py` or
  `additional_pipeline.py` (only referenced, commented-out, in
  `additional_pipeline.py:561`, and actively only from `pipeline.py:518`
  inside the legacy `pipeline_with_gemini`). Not in the real serving path.
