# Legacy-file usage check: pipeline.py and mtdna_backend.py

Investigation only — no code changed.

## Correction to prior report

`test_output_architecture_gap_map.md` (written earlier this session) flagged
`pipeline.py`/`mtdna_backend.py` as possibly-dead "legacy" code and left it
as an open uncertainty. This investigation found that's **not quite right**:
`mtdna_backend.py` is actively imported by `api.py` (the live entry point)
and is wired in as a **real fallback path**, not dead code — see
"pipeline_with_gemini callers" below for the exact trigger conditions.
`pipeline.py` itself has no direct callers outside `mtdna_backend.py`, but
it's reachable at runtime through that fallback chain.

---

## Names imported from `mtdna_backend.py` by another active file

Only `api.py` imports from `mtdna_backend.py`. No other file in the active
set (`data_preprocess.py`, `smart_fallback.py`, `NCBI.py`, `model.py`)
references `mtdna_backend` at all.

| Name | Imported at | Defined at | One-line description |
|---|---|---|---|
| `extract_accessions_from_input` | `api.py:904` (also used `api.py:1178`) | `mtdna_backend.py:91` | Parses a raw-text/file input (CSV, Excel, or pasted text) into a deduped list of valid accession strings plus a list of invalid ones. |
| `save_to_excel` | `api.py:905` (also `api.py:1645`) | `mtdna_backend.py:461` | Writes result rows to a two-sheet Excel file ("cMD Metadata" + "Full Raw Attributes"), with resume/merge-into-existing-file support keyed on Sample ID. |
| `summarize_results` | `api.py:906` (called `api.py:1538`) | `mtdna_backend.py:194` | Runs the **legacy** `pipeline.pipeline_with_gemini` for one accession (via `pipeline_classify_sample_location_cached`), checking a known-output cache first, and formats the result into row dicts. |

## Names imported from `pipeline.py` by another active file

| Name | Imported/used in | Defined at | One-line description |
|---|---|---|---|
| `find_drive_file` | `data_preprocess.py` (module-level `import pipeline`, used lines 518, 617, 675) | `pipeline.py:74` | Looks up a file by name inside a specific Google Drive folder, returns its file ID or `None`. |
| `upload_file_to_drive` | `data_preprocess.py` (used via `pipeline.upload_file_to_drive` lines 536, 630, 687; **also** imported directly by name at `data_preprocess.py:1180`, `from pipeline import upload_file_to_drive` — that direct-imported name is never actually called, only the `pipeline.upload_file_to_drive` form is used, so the line-1180 import is dead/redundant) | `pipeline.py:99` | Uploads a local file to a Google Drive folder, deleting any existing file of the same name first. |
| `download_file_from_drive` | `data_preprocess.py` (used lines 520, 620, 678) | `pipeline.py:129` | Downloads a named file from a Google Drive folder to a local path. |
| `run_with_timeout` | `data_preprocess.py` (used line 851; one other call at line 865 is commented out) | `pipeline.py:144` | Runs a function in a separate process with a timeout, to guard a call (e.g. table extraction) against hanging. |
| `unique_preserve_order` | `model.py` (used line 1478, via module-level `import pipeline`) | `pipeline.py:184` | Dedupes a list of items while preserving original order. |
| `process_link_allOutput` | `model.py` (used line 1504, `await pipeline.process_link_allOutput(...)`) | `pipeline.py:231` | Fetches/extracts text for one link and appends it into a running combined-output string, capped at a size limit. |

**`smart_fallback.py`** does `import pipeline` in a try/except (line 141) but never accesses any `pipeline.*` attribute anywhere in the file — this import is unused.

**`NCBI.py`** does `import pipeline` in a try/except (line 4) but the only reference to `pipeline.` anywhere in the file is inside a commented-out line (`# markdown_content = pipeline.fetch_text_from_url(url)`, line 671) — this import is also unused.

---

## Does `pipeline.py`'s `pipeline_with_gemini()` have callers besides `mtdna_backend.py:36`?

**Direct callers: no.** Repo-wide grep for `pipeline_with_gemini` confirms exactly one direct call to `pipeline.pipeline_with_gemini` in the whole codebase: `mtdna_backend.py:36`, inside `pipeline_classify_sample_location_cached()`. (`additional_pipeline.py` defines its own separate function of the same name — unrelated, not a call to `pipeline.py`'s version.)

**Indirect reachability: yes, from `api.py`, as a live fallback.** The call chain is:

```
api.py /analyze endpoint
  → summarize_results()          (mtdna_backend.py:194, imported at api.py:906, called api.py:1538)
    → pipeline_classify_sample_location_cached()   (mtdna_backend.py:31)
      → pipeline.pipeline_with_gemini()            (pipeline.py:390)
```

This path is not dead: `api.py`'s `/analyze` handler decides `use_rich` (api.py:1349-1354) based on whether resolved accessions have recognizable fields (`biosample`, `experiment`, `_source_database`, `accession`, `geo_sample`, `_lazy_kind`). If `use_rich` is `True`, it uses the active/rich pipeline (`additional_pipeline.pipeline_with_gemini`, api.py:1370-1391) instead. But it falls back to the `summarize_results`/legacy-`pipeline.py` path in two cases, both real and reachable at runtime:
1. `use_rich` evaluates `False` from the start (api.py:1349) — no resolved entry has any of those recognizable fields.
2. The rich-pipeline block raises an exception (api.py:1493-1497, `except Exception as exc: ... use_rich = False`), which explicitly falls through to the `summarize_results` loop starting at api.py:1499.

So: no *other function* calls `pipeline_with_gemini()` directly, but it is not orphaned — it's a genuine, currently-reachable fallback path invoked from the production endpoint under specific conditions, not something safe to assume is unused.
