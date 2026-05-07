"""
openbiodata_logger.py — Centralised logging for all OpenBioData modules.

Usage (in any module):
    from openbiodata_logger import get_logger
    log = get_logger(__name__)
    log.info("Resolving %s", accession_id)
    log.warning("No BioSample found for %s", acc)
    log.error("NCBI call failed: %s", exc)

Log file:  logs/openbiodata.log  (created next to this file, appended on every run)
Console:   all messages also printed to stdout so existing behaviour is preserved.

Format:
    [2024-01-15 10:30:45.123] [INFO    ] [module_name] message
"""

import logging
import os
import sys
from pathlib import Path

# ── File path ─────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent
_LOG_DIR   = _REPO_ROOT / "logs"
_LOG_FILE  = _LOG_DIR / "openbiodata.log"

_LOG_DIR.mkdir(exist_ok=True)

# ── Formatters ────────────────────────────────────────────────────────────────
_FMT = "[%(asctime)s.%(msecs)03d] [%(levelname)-8s] [%(name)s] %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# ── Root handler setup (runs once at import) ──────────────────────────────────
_root_logger = logging.getLogger("openbiodata")
_root_logger.setLevel(logging.DEBUG)

if not _root_logger.handlers:
    # File handler — append, UTF-8
    _fh = logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    _root_logger.addHandler(_fh)

    # Console handler — INFO and above
    _ch = logging.StreamHandler(sys.stdout)
    _ch.setLevel(logging.INFO)
    _ch.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    _root_logger.addHandler(_ch)


def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger under the 'openbiodata' hierarchy.

    Args:
        name: typically __name__ of the calling module
               e.g. 'ncbi_resolver', 'confidence_score'

    Returns:
        logging.Logger that writes to both logs/openbiodata.log and stdout.
    """
    # Use the leaf module name so log lines stay concise
    short = name.split(".")[-1]
    return _root_logger.getChild(short)
