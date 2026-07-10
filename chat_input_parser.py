"""
chat_input_parser.py — NCBI E-utilities helpers shared with paper_resolver.py.

The chat-assistant input mode that used to live here (and its /chat-message
endpoint) was removed since it was unused; these two functions survive
because paper_resolver.py's URL-based accession lookup depends on them.
"""

import json
import urllib.parse
import urllib.request

MAX_EUTILS_RESULTS = 10


def _eutils_search(db: str, term: str) -> list:
    """Call NCBI esearch, return up to MAX_EUTILS_RESULTS UID strings."""
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db={db}&term={urllib.parse.quote(term)}&retmode=json&retmax={MAX_EUTILS_RESULTS}"
    )
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        return data.get("esearchresult", {}).get("idlist", [])
    except Exception:
        return []


def _eutils_summary_accs(db: str, uids: list) -> list:
    """Convert UID list → accession strings via esummary."""
    if not uids:
        return []
    uid_str = ",".join(uids)
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        f"?db={db}&id={uid_str}&retmode=json"
    )
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        result = data.get("result", {})
        accs = []
        for uid in uids:
            entry = result.get(uid, {})
            if db == "bioproject":
                acc = entry.get("project_acc", "")
            elif db == "biosample":
                acc = entry.get("accession", "")
            elif db == "sra":
                # SRA esummary: accession is in the experiment or run field
                acc = entry.get("experiment_acc") or entry.get("uid", "")
            elif db == "nuccore":
                acc = entry.get("accessionversion") or entry.get("accession", "")
            elif db == "gds":
                acc = entry.get("accession", "")
            else:
                acc = ""
            if acc:
                accs.append(acc.upper())
        return accs
    except Exception:
        return []
