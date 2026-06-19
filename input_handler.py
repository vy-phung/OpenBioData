"""
input_handler.py — Task 4: Web Interface Input Handling

Parses raw user text into NCBI accession tokens, resolves each one via
ncbi_resolver, and returns a pipeline-ready dict.

Public API:
    parse_user_input(raw_input)   -> list[str]
    build_pipeline_input(raw_input) -> (dict, list[str])
        dict  = {SAMN_id: {bioproject, biosample, accession, experiment}}
        list  = skipped IDs with reason strings for UI display
    get_pipeline_accession(entry, fallback) -> str
        picks the best accession to feed to the pipeline for one resolved entry
"""

import re
from openbiodata_logger import get_logger

log = get_logger(__name__)


def parse_user_input(raw_input: str) -> list:
    """
    Parse raw text input into a deduplicated list of accession strings.

    Splits on: commas, newlines, semicolons, and whitespace.
    Empty tokens and duplicates are removed.

    Args:
        raw_input: free-form user text, e.g. "PRJNA976261, OL757400\\nSRR17084312"

    Returns:
        Ordered list of unique non-empty token strings.
    """
    if not raw_input or not raw_input.strip():
        return []

    tokens = re.split(r'[,\n;\s]+', raw_input.strip())

    seen = set()
    result = []
    for token in tokens:
        t = token.strip()
        if t and t not in seen:
            seen.add(t)
            result.append(t)

    log.info("parse_user_input: %d unique tokens from input", len(result))
    return result


def build_pipeline_input(raw_input: str, max_samples: int = 50) -> tuple:
    """
    Full entry point: parse input, resolve each accession via NCBI, return
    a pipeline-ready dict and a list of skipped IDs with reasons.

    Args:
        raw_input: free-form user text

    Returns:
        (resolved_dict, skipped_list) where:
            resolved_dict = {SAMN_id: {bioproject, biosample, accession, experiment}}
            skipped_list  = ["Could not resolve FOO — skipping", ...]

    Note: BioProject and GEO series tokens are expanded via the cheap
    enumerate_project_samples() lazy path instead of fully resolving every
    sample up front. Entries with a '_lazy_kind' key still need
    ncbi_resolver.resolve_lazy_entry() called on them before use — the
    pipeline does this one sample at a time, immediately before processing
    it, so the first result isn't held up by the whole project resolving
    and a cancellation can take effect between samples.
    """
    from ncbi_resolver import resolve_accessions, detect_accession_type, enumerate_project_samples

    tokens = parse_user_input(raw_input)
    if not tokens:
        log.warning("build_pipeline_input: empty input")
        return {}, ["No accession IDs found in input."]

    all_resolved = {}
    skipped = []

    for token in tokens:
        # Stop early once we've hit the cap — avoids slow NCBI expansion
        # when the user's remaining quota is small.
        if len(all_resolved) >= max_samples:
            log.info("build_pipeline_input: cap=%d reached, skipping remaining tokens", max_samples)
            break

        acc_type = detect_accession_type(token)
        log.info("build_pipeline_input: resolving '%s' (type=%s)", token, acc_type)

        # Pass remaining slots so BioProject expansion doesn't over-fetch
        remaining_slots = max_samples - len(all_resolved)
        try:
            if acc_type in ("bioproject", "geo_series"):
                resolved = enumerate_project_samples(token, acc_type, max_samples=remaining_slots)
            else:
                resolved = resolve_accessions(token, max_samples=remaining_slots)
        except Exception as e:
            log.warning("build_pipeline_input: resolve_accessions failed for '%s': %s",
                        token, e)
            resolved = {}

        if not resolved:
            msg = f"Could not resolve {token} — skipping"
            log.warning("build_pipeline_input: %s", msg)
            skipped.append(msg)
            continue

        # Validate each resolved entry.
        # An entry is "unresolved" if:
        #   - the original token was of unknown type, AND
        #   - the only non-empty field is 'accession' echoing the original input
        # (GenBank accessions with no BioSample are valid and should be kept.)
        for samn_key, entry in resolved.items():
            if len(all_resolved) >= max_samples:
                break
            bioproject  = str(entry.get("bioproject",  "") or "").strip()
            biosample   = str(entry.get("biosample",   "") or "").strip()
            experiment  = str(entry.get("experiment",  "") or "").strip()
            accession   = str(entry.get("accession",   "") or "").strip()

            geo_sample  = str(entry.get("geo_sample",  "") or "").strip()
            is_genuinely_unresolved = (
                acc_type == "unknown"
                and not bioproject
                and not biosample
                and not experiment
                and not geo_sample
            )
            if is_genuinely_unresolved:
                msg = f"Could not resolve {token} — skipping"
                log.warning("build_pipeline_input: %s", msg)
                skipped.append(msg)
            else:
                all_resolved[samn_key] = entry
                log.info("build_pipeline_input: resolved %s -> %s", token, samn_key)

    log.info("build_pipeline_input: %d resolved, %d skipped",
             len(all_resolved), len(skipped))
    return all_resolved, skipped


def get_pipeline_accession(entry: dict, fallback: str = "") -> str:
    """
    Given one resolved entry dict, return the best accession string to feed
    to the pipeline.

    Priority order:
      1. GenBank accession (e.g. OL757400) — has full document text
      2. SRR run (e.g. SRR17084312) — SRA metadata available
      3. BioSample ID (e.g. SAMN23469632) — fallback, minimal metadata
      4. GSM sample ID (for GEO-only entries)
      5. fallback argument (original user input)

    Args:
        entry:    dict with keys bioproject, biosample, accession, experiment
                  (and optional geo_sample, geo_series for GEO entries)
        fallback: original token string to return if nothing else is available

    Returns:
        Best non-empty accession string.
    """
    accession  = str(entry.get("accession",  "") or "").strip()
    experiment = str(entry.get("experiment", "") or "").strip()
    biosample  = str(entry.get("biosample",  "") or "").strip()
    geo_sample = str(entry.get("geo_sample", "") or "").strip()

    if accession:
        return accession
    # ERR/ERX are ENA-specific run IDs that NCBI cannot look up.
    # For those entries, prefer the SAMEA biosample (EBI can fetch it);
    # for NCBI SRR/SRX runs, experiment is still preferred (more metadata).
    _is_ena_run = experiment.upper().startswith(("ERR", "ERX", "ERS"))
    if experiment and not _is_ena_run:
        return experiment
    if biosample:
        return biosample
    if experiment:
        return experiment
    if geo_sample:
        return geo_sample
    return fallback or str(entry.get("bioproject", "") or "").strip()
