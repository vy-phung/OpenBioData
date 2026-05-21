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
    """
    from ncbi_resolver import resolve_accessions, detect_accession_type

    tokens = parse_user_input(raw_input)
    if not tokens:
        log.warning("build_pipeline_input: empty input")
        return {}, ["No accession IDs found in input."]

    all_resolved = {}
    skipped = []

    for token in tokens:
        acc_type = detect_accession_type(token)
        log.info("build_pipeline_input: resolving '%s' (type=%s)", token, acc_type)

        try:
            resolved = resolve_accessions(token, max_samples=max_samples)
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
            bioproject  = str(entry.get("bioproject",  "") or "").strip()
            biosample   = str(entry.get("biosample",   "") or "").strip()
            experiment  = str(entry.get("experiment",  "") or "").strip()
            accession   = str(entry.get("accession",   "") or "").strip()

            is_genuinely_unresolved = (
                acc_type == "unknown"
                and not bioproject
                and not biosample
                and not experiment
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
      4. fallback argument (original user input)

    Args:
        entry:    dict with keys bioproject, biosample, accession, experiment
        fallback: original token string to return if nothing else is available

    Returns:
        Best non-empty accession string.
    """
    accession  = str(entry.get("accession",  "") or "").strip()
    experiment = str(entry.get("experiment", "") or "").strip()
    biosample  = str(entry.get("biosample",  "") or "").strip()

    if accession:
        return accession
    if experiment:
        return experiment
    if biosample:
        return biosample
    return fallback or str(entry.get("bioproject", "") or "").strip()
