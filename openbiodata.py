#!/usr/bin/env python3
"""CLI wrapper around the same extraction pipeline api.py's /analyze
endpoint uses, for a single accession, run one-shot from the terminal.

    python openbiodata.py <accession>

This does not reimplement any pipeline logic: accession parsing, NCBI
resolution, extraction/citation/confidence scoring, and row formatting
all call directly into mtdna_backend, input_handler, additional_pipeline,
and api -- the same functions api.py's /analyze route calls.
"""
import argparse
import asyncio
import sys

MAX_SAMPLES = 50


async def _run(accession: str) -> int:
    from mtdna_backend import extract_accessions_from_input
    from non_ncbi_resolver import is_non_ncbi_accession
    from input_handler import build_pipeline_input

    print("> Parsing accession input…")
    accessions, invalid, error = extract_accessions_from_input(file=None, raw_text=accession)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if not accessions:
        detail = f" (invalid: {', '.join(invalid)})" if invalid else ""
        print(f"Error: no valid accession found in {accession!r}{detail}", file=sys.stderr)
        return 1
    if len(accessions) > 1:
        print(
            f"Error: {len(accessions)} accessions detected ({', '.join(accessions)}). "
            "This CLI processes one accession per invocation.",
            file=sys.stderr,
        )
        return 1

    acc = accessions[0]

    if is_non_ncbi_accession(acc):
        print(
            f"Error: {acc} looks like a non-NCBI database accession. "
            "This CLI only supports NCBI accessions (BioProject, BioSample, SRA, "
            "GenBank, GEO) for now — use the web UI for non-NCBI databases.",
            file=sys.stderr,
        )
        return 1

    print("> Resolving accession via NCBI…")
    resolved_dict, skipped = await asyncio.to_thread(build_pipeline_input, acc, MAX_SAMPLES)
    if skipped:
        print(f"⚠ Could not resolve: {', '.join(skipped)}", file=sys.stderr)
    if not resolved_dict:
        print(
            f"Error: could not resolve {acc} via NCBI. It may be invalid, or NCBI "
            "may be temporarily rate-limiting requests — wait a minute and retry.",
            file=sys.stderr,
        )
        return 1

    # Imported here, not at module top, so an invalid/non-NCBI accession (caught
    # above) never pays for additional_pipeline's import-time Google Sheet cache
    # load — same lazy-import point api.py's /analyze route uses.
    from additional_pipeline import pipeline_with_gemini
    from api import _rows_from_new_pipeline

    async def progress_cb(msg) -> None:
        # pipeline_with_gemini's progress_cb receives plain human-readable
        # strings (the ones printed below) interleaved with structured dict
        # signals meant for the web frontend's incremental UI (api.py's
        # _emit_queue_item dispatches on these same keys): __links_update__
        # (live "papers found" panel), __partial_acc__/__partial_data__
        # (streamed per-sample rows, including raw NCBI XML), and
        # __auto_niche_cases__. None of those are terminal-appropriate, and
        # the final `result` below already carries everything they'd convey
        # for a one-shot single-accession run, except the paywall warning
        # inside __links_warning__, which is worth surfacing directly.
        if isinstance(msg, str):
            print(f"> {msg}")
        elif isinstance(msg, dict) and "__links_warning__" in msg:
            warning = (msg["__links_warning__"] or {}).get("message")
            if warning:
                print(f"> ⚠ {warning}")

    print(f"> Processing {len(resolved_dict)} sample(s)…")
    result = await pipeline_with_gemini(
        resolved_dict,
        niche_cases=None,
        progress_cb=progress_cb,
        # One-shot CLI run: no cancellation source, no uploaded context files,
        # no standardization schema, no multi-paper/session state -- every
        # other pipeline_with_gemini parameter is session/UI-specific and
        # already defaults to None/empty for exactly this case.
        cancel_event=None,
    )
    if result is None:
        print("Error: pipeline returned no result.", file=sys.stderr)
        return 1

    accs_output = result[0] if isinstance(result, tuple) else result
    auto_niche = accs_output.pop("__niche_cases__", None) or []
    rows = await _rows_from_new_pipeline(accs_output, auto_niche or None)

    if not rows:
        print("No metadata could be extracted.", file=sys.stderr)
        return 1

    print(f"✅ Extracted metadata for {len(rows)} sample(s)\n")
    for row in rows:
        _print_row(row)
    print(f"{len(rows)} sample(s) processed.")
    return 0


def _print_row(row: dict) -> None:
    header_parts = []
    if row.get("biosample_accession"):
        header_parts.append(row["biosample_accession"])
    if row.get("bioproject"):
        header_parts.append(f"BioProject: {row['bioproject']}")
    if row.get("sra_accession"):
        header_parts.append(f"SRA: {row['sra_accession']}")
    if row.get("genbank_accession"):
        header_parts.append(f"GenBank: {row['genbank_accession']}")
    header = "   ".join(header_parts) or "(unknown sample)"

    width = max(60, len(header) + 4)
    sep = "=" * width

    print(sep)
    print(header)
    print(sep)
    if row.get("confidence_score"):
        print(f"Confidence: {row['confidence_score']}")
    print()
    if row.get("explanation"):
        print(row["explanation"])
    if row.get("conflict"):
        print("\nConflicts:")
        print(row["conflict"])
    print()
    print("-" * width)
    if row.get("sources"):
        print(row["sources"])
    print()
    if row.get("time_cost"):
        print(f"Time cost: {row['time_cost']}")
    print(sep)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover NCBI metadata for a single accession by tracing it "
                     "back to its source publication."
    )
    parser.add_argument(
        "accession",
        help="NCBI accession: BioProject, BioSample, SRA (experiment/run), GenBank, or GEO.",
    )
    args = parser.parse_args()
    print("> Loading backend…")
    exit_code = asyncio.run(_run(args.accession))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
