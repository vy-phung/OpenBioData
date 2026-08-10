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
import contextlib
import io
import logging
import os
import sys
import warnings

MAX_SAMPLES = 50


def _quiet_third_party_setup() -> None:
    """Silence third-party noise (HF/transformers progress bars + verbosity,
    google-generativeai's FutureWarning) for the rest of the process. Only
    called when --verbose is NOT passed. NLTK's downloader prints straight
    to stdout rather than through logging, so it isn't covered here --
    see _quiet_import() below, which wraps the actual import statements
    that trigger it.
    """
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    logging.disable(logging.WARNING)


@contextlib.contextmanager
def _quiet_import():
    """Swallow stdout/stderr while heavy backend modules are first
    imported -- this is where NLTK's downloader, the HF Hub warning, and
    the 'known_samples' Google Sheet cache warning all currently print,
    none of which are meant for CLI end users. No-op when --verbose is
    passed (callers just skip wrapping their import in this).
    """
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        yield


async def _run(accession: str, verbose: bool = False) -> int:
    # OPENBIODATA_VERBOSE is exported as an env var (not just a local
    # variable) so that other modules -- mtdna_backend, additional_pipeline,
    # input_handler, api -- can check os.environ.get("OPENBIODATA_VERBOSE")
    # and gate their own internal debug prints the same way, without a
    # circular import back into this file.
    os.environ["OPENBIODATA_VERBOSE"] = "1" if verbose else ""

    print("> Parsing accession input…")
    if verbose:
        from mtdna_backend import extract_accessions_from_input
        from non_ncbi_resolver import is_non_ncbi_accession
        from input_handler import build_pipeline_input
    else:
        _quiet_third_party_setup()
        with _quiet_import():
            from mtdna_backend import extract_accessions_from_input
            from non_ncbi_resolver import is_non_ncbi_accession
            from input_handler import build_pipeline_input

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
    # load — same lazy-import point api.py's /analyze route uses. This is also
    # where the "Could not load 'known_samples' Google Sheet cache" warning
    # currently fires; that's this project's hosted-infra detail, not
    # something an OSS CLI user should ever see, so it's suppressed here
    # unconditionally (not just under --verbose).
    if verbose:
        from additional_pipeline import pipeline_with_gemini
        from api import _rows_from_new_pipeline
    else:
        with _quiet_import():
            from additional_pipeline import pipeline_with_gemini
            from api import _rows_from_new_pipeline

    fatal_llm_error = False

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
        # inside __links_warning__ and the fatal __fatal_error__ signal,
        # which are worth surfacing directly.
        nonlocal fatal_llm_error
        if isinstance(msg, str):
            print(f"> {msg}")
        elif isinstance(msg, dict) and "__links_warning__" in msg:
            warning = (msg["__links_warning__"] or {}).get("message")
            if warning:
                print(f"> ⚠ {warning}")
        elif isinstance(msg, dict) and "__fatal_error__" in msg:
            fatal = msg["__fatal_error__"] or {}
            if fatal.get("type") == "llm_keys_exhausted":
                fatal_llm_error = True
                print("❌ All LLM API keys exhausted — no metadata could be extracted.", file=sys.stderr)
                print("Check your ANTHROPIC_API_KEY / GOOGLE_API_KEY and try again.", file=sys.stderr)

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
    if fatal_llm_error:
        return 1
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
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show underlying library/debug output (NLTK downloads, HF Hub "
             "warnings, internal pipeline logging) instead of just this "
             "CLI's own progress lines. Off by default.",
    )
    parser.add_argument(
        "--api-key",
        help="Anthropic API key for this run only. Overrides ANTHROPIC_API_KEY "
             "from the environment/.env, which remains the primary way to set it.",
    )
    args = parser.parse_args()

    # Applied before load_dotenv() so a value passed here always wins over
    # whatever .env would otherwise populate -- load_dotenv() defaults to
    # not overriding an already-set env var, so this ordering, not just the
    # later assignment, is what makes --api-key a genuine override rather
    # than something .env can silently reclaim.
    if args.api_key:
        os.environ["ANTHROPIC_API_KEY"] = args.api_key

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Anthropic is preferred (better extraction quality), but model.call_llm_api()
    # already falls through to Gemini when ANTHROPIC_API_KEY is unset -- so any
    # one of these keys is sufficient to actually run the pipeline. Gemini isn't
    # merely a mid-run fallback for Anthropic failures; it's a standalone path.
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("NEW_GOOGLE_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("NEW_GEMINI_API")):
        print("Error: no LLM API key found. Set ANTHROPIC_API_KEY (recommended) or", file=sys.stderr)
        print("a Gemini key (GOOGLE_API_KEY) in .env — see README.", file=sys.stderr)
        print("Don't want to set one up? Try the hosted version instead:", file=sys.stderr)
        print("https://app.openbiodata.it.com/", file=sys.stderr)
        sys.exit(1)

    print("> Loading backend…")
    exit_code = asyncio.run(_run(args.accession, verbose=args.verbose))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()