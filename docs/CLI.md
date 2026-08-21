# CLI reference

`python openbiodata.py <accession>` runs the same extraction pipeline the
hosted web UI uses (`api.py`'s `/analyze` route), for one accession at a
time, from the terminal. It resolves the accession via NCBI, traces it back
to its source publication, extracts/cross-checks metadata, and saves the
result to a file — Excel by default, so it's ready to open directly or feed
into a downstream workflow without any extra conversion step.

See the main [README](../README.md) for installation and API key setup.
This page covers every flag and output format in detail.

## Usage

```bash
python openbiodata.py <accession> [-o OUTPUT] [--format {xlsx,csv,json}] [-v] [--api-key KEY]
```

`<accession>` — a single NCBI accession: BioProject, BioSample, SRA
(experiment/run), GenBank, or GEO. Only one per invocation — if it resolves
to a BioProject with many samples (e.g. `PRJNA514286`), every sample under
it is still processed and included in the output file as one row each; the
"one accession" limit is about the CLI argument, not the resulting row
count.

## Flags

| Flag | Default | Description |
|---|---|---|
| `-o`, `--output PATH` | `<accession>.<format>` in the current directory | Where to save the result. See [Output location](#output-location) below. |
| `--format {xlsx,csv,json}` | `xlsx` | Output file format. See [Output formats](#output-formats) below. |
| `-v`, `--verbose` | off | Print full per-field detail (explanations, conflicts, source list) to the terminal, plus underlying library/debug output. Off by default — see [Terminal output](#terminal-output). |
| `--api-key KEY` | — | Anthropic API key for this run only. Overrides `ANTHROPIC_API_KEY` from the environment/`.env`. |

## Output formats

### `xlsx` (default)

Identical to what the web UI's "Download Excel" button produces — both
call the same `save_to_excel()` function, so there's exactly one code path
for Excel output across the whole project. Two sheets:

- **`cMD Metadata`** — one row per sample, predefined columns only
  (`biosample_accession`, `bioproject`, `sra_accession`, each requested
  metadata field, `confidence_score`, `explanation`, `sources`, `conflict`,
  `time_cost`, …).
- **`Full Raw Attributes`** — the same columns, plus one extra column per
  unique raw NCBI attribute key found across all processed samples (blank
  for samples that don't have that particular attribute). This is the
  fullest view of what was actually pulled from NCBI/the source paper,
  useful when the curated Sheet 1 columns don't cover a field you need.

### `csv`

A single flat table — the same columns as the `xlsx`'s `cMD Metadata`
sheet, written with `pandas.DataFrame.to_csv()`. Does not include the raw
per-sample attribute breakout (that's an `xlsx`-only sheet). Pick this if
your downstream tool reads CSV/TSV-style tabular input rather than Excel.

### `json`

Each sample as one JSON object in a list, fields exactly as returned by
the pipeline — including the nested `_additional_fields` dict (the same
raw-attribute data behind the `xlsx`'s second sheet, just nested instead of
flattened into extra columns). Pick this for scripting/programmatic
consumption.

## Output location

- **Default:** `<accession>.<format>` in the current working directory,
  e.g. `./SAMN20283122.xlsx`. **Overwritten on rerun** — deliberately
  deterministic, so a downstream script can point at a fixed path instead
  of globbing for the latest timestamped file.
- **`-o some/dir/`** (existing directory, or any path ending in `/`) — saves
  `<accession>.<format>` inside it, creating the directory if needed.
- **`-o some/exact_name.xlsx`** — used verbatim as the file path (parent
  directories created if needed).

A path that doesn't yet exist and doesn't end in `/` is treated as an exact
file path, not a directory to create — same convention as `cp`/`rsync`.

## Terminal output

By default the terminal shows a condensed line per sample (accession,
linked BioProject/SRA/GenBank IDs, confidence score, number of fields
recovered) followed by a `✅ Saved: <path>` line — not the full per-field
explanation/conflict/source dump. That full detail isn't lost, it's just
moved: it's what's in the saved file's `explanation`/`sources`/`conflict`
columns (or the `xlsx`'s `Full Raw Attributes` sheet).

Pass `-v`/`--verbose` to print that full detail to the terminal as well
(and to see underlying library/debug output such as NLTK downloads or HF
Hub warnings, which are silenced by default).

## Examples

```bash
# Default: Excel, saved as ./SAMN20283122.xlsx
python openbiodata.py SAMN20283122

# Machine-readable output for a script
python openbiodata.py SAMN20283122 --format json -o results/

# Full detail printed to the terminal too, not just the saved file
python openbiodata.py SAMN20283122 -v

# Explicit output path
python openbiodata.py PRJNA514286 --format csv -o out/prjna514286_samples.csv
```
