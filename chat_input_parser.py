"""
chat_input_parser.py — Modular chat-based input parser for BioMetadataAudit.

Guides users through the three required pipeline inputs via a structured chat:
  1. Accession IDs  (BioProject / BioSample / SRR/SRX/SRA / GenBank)
  2. Metadata fields to extract
  3. Standardization schema URL(s) (optional)

Extraction strategy (hybrid, cheapest-first):
  • Regex is tried first — zero API cost, handles ~80% of inputs.
  • LLM fallback (Gemini 2.5 Flash Lite → Claude Haiku 4.5) is called only when
    regex finds nothing and the message is long enough to warrant it.
  • Gracefully degrades to regex-only if no API keys are present.

Public API
----------
extract_ncbi_ids(text)                     -> list[str]
get_initial_message()                      -> str
fresh_state()                              -> dict
process_chat_message(message, state=None)  -> (reply: str, new_state: dict)

State schema
------------
{
  "step": "accessions" | "metadata_fields" | "schema" | "confirm" | "done",
  "accessions": [],       # NCBI IDs collected so far (capped at MAX_CHAT_SAMPLES)
  "metadata_fields": [],  # field names; empty list = auto-extract
  "schema": "",           # comma-joined schema URL(s)
  "ready_to_run": False,
}
"""

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Optional

MAX_CHAT_SAMPLES = 10

# ── 1. Regex-based NCBI ID extraction ─────────────────────────────────────────

_NCBI_PATTERNS = [
    re.compile(r'\bPRJ[A-Z]{2}\d+\b'),                          # BioProject PRJNA/PRJEB
    re.compile(r'\bSAM[A-Z]{1,2}\d+\b'),                        # BioSample SAMN/SAMEA
    re.compile(r'\bSRX\d+\b'),                                   # SRA experiment
    re.compile(r'\bSRR\d+\b'),                                   # SRA run
    re.compile(r'\bERR\d+\b'),                                   # ENA run
    re.compile(r'\bERP\d+\b'),                                   # ENA project
    re.compile(r'\bERX\d+\b'),                                   # ENA experiment
    re.compile(r'\b(?:NC_|OL|MT|MW|MZ|PQ|OM|MN|MK|KY|KX|KU|JN|FJ)[A-Z0-9]+\d+(?:\.\d+)?\b'),
    re.compile(r'\b[A-Z]{1,2}\d{5,8}(?:\.\d+)?\b'),             # generic GenBank
]

# Non-NCBI database accession patterns (MassIVE, PRIDE, MetaboLights, etc.)
_NON_NCBI_PATTERNS = [
    re.compile(r'\bMSV\d+\b', re.IGNORECASE),    # MassIVE
    re.compile(r'\bPXD\d+\b', re.IGNORECASE),    # PRIDE
    re.compile(r'\bMTBLS\d+\b', re.IGNORECASE),  # MetaboLights
    re.compile(r'\bMGYS\d+\b', re.IGNORECASE),   # MGnify
    re.compile(r'\bEGAD\d+\b', re.IGNORECASE),   # EGA datasets
    re.compile(r'\bEGAS\d+\b', re.IGNORECASE),   # EGA studies
]

_SCHEMA_URL_RE = re.compile(
    r'https?://[^\s<>"\']+(?:\.csv|\.tsv|\.xlsx|github\.com[^\s<>"\']*)',
    re.IGNORECASE,
)

_SKIP_RE = re.compile(
    r'^\s*(?:skip|none|no|n/a|na|blank|empty|nothing|auto|automatic|not sure|unsure|idk)\s*[.!]?\s*$',
    re.IGNORECASE,
)

_OFF_TOPIC_RE = re.compile(
    r'\b(?:how (?:do|does)|what is|explain|tell me|help me with|'
    r'price|pricing|cost|subscription|contact|support|'
    r'login|sign[\s-]?up|register|forgot|password|account)\b',
    re.IGNORECASE,
)

_YES_RE = re.compile(r'\b(?:yes|run|go|ok|sure|confirm|start|yeah|yep)\b', re.IGNORECASE)
_NO_RE  = re.compile(r'\b(?:no|cancel|stop|restart|reset|change|edit|back)\b', re.IGNORECASE)


def extract_ncbi_ids(text: str) -> list:
    """Return deduplicated, ordered accession strings (NCBI and known non-NCBI) found in text.

    Operates on uppercased text so patterns work case-insensitively.
    Word-boundary anchors handle accessions embedded in URLs naturally
    (the '/' acts as a word boundary).
    """
    if not text:
        return []
    upper = text.upper()
    seen, result = set(), []
    for pat in _NCBI_PATTERNS + _NON_NCBI_PATTERNS:
        for m in pat.finditer(upper):
            tok = m.group(0)
            if tok not in seen:
                seen.add(tok)
                result.append(tok)
    return result


# ── 2. NCBI URL search → E-utilities fetch ────────────────────────────────────

_NCBI_SEARCH_URL_RE = re.compile(
    r'https?://(?:www\.)?ncbi\.nlm\.nih\.gov/([a-z]+)/?(?:\?([^\s<>"\']*))?' ,
    re.IGNORECASE,
)

_PUBMED_URL_RE = re.compile(
    r'https?://(?:www\.)?(?:pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov/pubmed)/(\d+)',
    re.IGNORECASE,
)

_NCBI_DB_MAP = {
    "bioproject": "bioproject",
    "biosample":  "biosample",
    "sra":        "sra",
    "nuccore":    "nuccore",
}


def _parse_ncbi_search_url(text: str):
    """Return (db, term) from the first NCBI search URL found in text, or None."""
    for m in _NCBI_SEARCH_URL_RE.finditer(text):
        db_raw = m.group(1).lower()
        db = _NCBI_DB_MAP.get(db_raw)
        if not db:
            continue
        query_str = m.group(2) or ""
        params = urllib.parse.parse_qs(query_str)
        terms = params.get("term", [])
        if terms:
            return db, urllib.parse.unquote(terms[0])
    return None


def _eutils_search(db: str, term: str) -> list:
    """Call NCBI esearch, return up to MAX_CHAT_SAMPLES UID strings."""
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db={db}&term={urllib.parse.quote(term)}&retmode=json&retmax={MAX_CHAT_SAMPLES}"
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


def _fetch_ncbi_search_ids(text: str) -> tuple:
    """If text contains an NCBI search URL, fetch matching accession IDs.

    Returns (accessions: list, term: str) so the caller can build a useful
    reply.  Returns ([], "") when no NCBI search URL is detected or the
    network call yields no results.
    """
    parsed = _parse_ncbi_search_url(text)
    if not parsed:
        return [], ""
    db, term = parsed
    uids = _eutils_search(db, term)
    if not uids:
        return [], term
    accs = _eutils_summary_accs(db, uids)
    return accs, term


def _fetch_pubmed_ids(text: str) -> tuple:
    """If text contains PubMed URLs, use eLink to find linked BioProject/SRA accessions.

    Returns (accessions: list, pmids: list) — pmids used for the reply label.
    """
    pmids = list(dict.fromkeys(_PUBMED_URL_RE.findall(text)))  # deduplicated, ordered
    if not pmids:
        return [], []

    # eLink: pubmed → bioproject
    pmid_str = ",".join(pmids[:5])
    elink_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
        f"?dbfrom=pubmed&db=bioproject&id={pmid_str}&retmode=json"
    )
    try:
        with urllib.request.urlopen(elink_url, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        uids = []
        for linkset in data.get("linksets", []):
            for lsdb in linkset.get("linksetdbs", []):
                if lsdb.get("dbto") == "bioproject":
                    uids.extend(lsdb.get("links", []))
        uids = list(dict.fromkeys(uids))[:MAX_CHAT_SAMPLES]
        if not uids:
            return [], pmids
        accs = _eutils_summary_accs("bioproject", uids)
        return accs, pmids
    except Exception:
        return [], pmids


# ── 3. Cheap LLM call (Gemini Flash Lite → Claude Haiku) ──────────────────────

def _llm_call_cheap(prompt: str) -> str:
    """Call the cheapest available LLM.

    Priority:
      1. Gemini 2.5 Flash Lite  (~$0.075 / 1M input tokens)
         Keys tried: NEW_GOOGLE_API_KEY, then GOOGLE_API_KEY
      2. Claude Haiku 4.5       (~$0.80  / 1M input tokens)
         Key: ANTHROPIC_API_KEY

    Returns the model's text response, or "" if no key is available or all
    calls fail.  The caller must handle the empty-string case gracefully.
    """
    # ── Gemini 2.5 Flash Lite ──────────────────────────────────────────────
    gemini_key = os.getenv("NEW_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            m = genai.GenerativeModel("gemini-2.5-flash-lite")
            r = m.generate_content(prompt)
            return r.text or ""
        except Exception:
            pass  # fall through to Haiku

    # ── Claude Haiku 4.5 (fallback) ────────────────────────────────────────
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text or ""
        except Exception:
            pass

    return ""


def _llm_extract_ncbi_ids(text: str) -> list:
    """Ask the LLM to pull NCBI accession IDs out of free-form text.

    Called only when regex found zero IDs and the message is long enough
    to be worth an LLM call (non-trivial prose, not just "skip").
    """
    prompt = (
        "Extract all NCBI accession IDs from the text below.\n"
        "Valid types:\n"
        "  BioProject  — PRJNA... or PRJEB...\n"
        "  BioSample   — SAMN... or SAMEA...\n"
        "  SRA run     — SRR... or ERR...\n"
        "  SRA exp     — SRX...\n"
        "  GenBank     — e.g. OL757400, KU131308, MN908947, AB123456\n\n"
        "Return ONLY a JSON array of uppercase strings, e.g. [\"PRJNA123\",\"SRR456\"]\n"
        "If none found, return exactly: []\n\n"
        f"Text:\n{text[:2000]}"
    )
    raw = _llm_call_cheap(prompt)
    if not raw:
        return []
    try:
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            ids = json.loads(match.group(0))
            return [str(i).strip().upper() for i in ids if str(i).strip()]
    except Exception:
        pass
    return []


def _llm_extract_fields(text: str) -> list:
    """Ask the LLM to extract metadata field names from natural language.

    Called only when the regex parser produced nothing from what looks like
    a genuine field-description sentence (not a "skip" command).
    """
    prompt = (
        "Extract the metadata field names the user wants to analyze from the text below.\n"
        "Normalize each to snake_case lowercase "
        "(e.g. 'Disease Status' → 'disease_status', 'host species' → 'host_species').\n"
        "Return ONLY a JSON array of strings, "
        "e.g. [\"disease_status\",\"country\",\"organism\"]\n"
        "If the user says skip/none/auto or no specific fields, return exactly: []\n\n"
        f"Text:\n{text[:1000]}"
    )
    raw = _llm_call_cheap(prompt)
    if not raw:
        return []
    try:
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            fields = json.loads(match.group(0))
            return [
                str(f).strip().lower()
                for f in fields
                if f and 2 <= len(str(f).strip()) <= 60
            ]
    except Exception:
        pass
    return []


# ── 3. Regex field parser ──────────────────────────────────────────────────────

def _parse_fields(text: str) -> list:
    """Split a comma/newline list of field names and normalise to snake_case.

    Strips common filler words so sentences like "I want country and organism"
    are handled correctly.  Falls back to LLM for complex prose.
    """
    cleaned = re.sub(
        r'\b(?:i want|please extract|give me|extract|fields?|metadata|columns?|'
        r'like|such as|including|for example|e\.g\.?)\b',
        ',', text, flags=re.IGNORECASE,
    )
    parts = re.split(r'[,;\n/|]|\band\b|\bor\b', cleaned, flags=re.IGNORECASE)
    seen, fields = set(), []
    for part in parts:
        raw = part.strip().strip('"\'').strip()
        raw = re.sub(r'\s+', '_', raw).lower()
        raw = re.sub(r'[^a-z0-9_]', '', raw)
        words = [w for w in raw.split('_') if w]
        if 2 <= len(raw) <= 60 and len(words) <= 4 and raw not in seen:
            seen.add(raw)
            fields.append(raw)
    return fields


def _extract_schema_urls(text: str) -> list:
    return _SCHEMA_URL_RE.findall(text)


# ── 4. Conversation helpers ────────────────────────────────────────────────────

def _is_off_topic(text: str, new_ids: list) -> bool:
    if not _OFF_TOPIC_RE.search(text):
        return False
    return not new_ids  # allow off-topic phrasing when it also contains real IDs


def _step_label(step: str) -> str:
    return {"accessions": "1", "metadata_fields": "2",
            "schema": "3", "confirm": "3", "done": "3"}.get(step, "?")


def _reprompt(step: str) -> str:
    return {
        "accessions":      "What NCBI accessions would you like to analyze?",
        "metadata_fields": "Which metadata fields should I extract? (or type **skip**)",
        "schema":          "Any standardization schema URL? (or type **skip**)",
        "confirm":         "Type **yes** to run or **no** to restart.",
    }.get(step, "")


def _confirm_message(state: dict) -> str:
    accs   = state.get("accessions") or []
    fields = state.get("metadata_fields") or []
    schema = state.get("schema") or ""

    acc_disp = ", ".join(f"`{a}`" for a in accs[:5]) + (
        f" +{len(accs) - 5} more" if len(accs) > 5 else ""
    )
    field_disp = ", ".join(f"`{f}`" for f in fields[:5]) + (
        f" +{len(fields) - 5} more" if len(fields) > 5 else ""
    ) if fields else "_auto-extract all_"
    schema_disp = f"`{schema[:80]}`" if schema else "_none_"

    return (
        "**Ready to run!** Here's your configuration:\n\n"
        f"• **Accessions** ({len(accs)}): {acc_disp}\n"
        f"• **Metadata fields**: {field_disp}\n"
        f"• **Schema**: {schema_disp}\n\n"
        "Type **yes** to start the analysis, or **no** to restart."
    )


# ── 5. Public API ──────────────────────────────────────────────────────────────

def get_initial_message() -> str:
    return (
        "Hi! I'll help you set up a metadata analysis.\n\n"
        "**Step 1 of 3 — Accession IDs**\n"
        "Share your accessions. You can:\n"
        "• NCBI IDs: `PRJNA976261`, `SRR17084312`, `SAMN23469632`, `OL757400`\n"
        "• Non-NCBI IDs: `MSV000080918` (MassIVE), `PXD000001` (PRIDE), `MTBLS1` (MetaboLights)\n"
        "• Paste an NCBI or PubMed URL — I'll extract the IDs\n"
        "• Describe your dataset in plain text\n\n"
        "_For non-NCBI samples the tool skips NCBI fetch and searches the web for metadata._\n\n"
        f"_Up to {MAX_CHAT_SAMPLES} samples per run._"
    )


def fresh_state() -> dict:
    return {
        "step": "accessions",
        "accessions": [],
        "metadata_fields": [],
        "schema": "",
        "ready_to_run": False,
    }


def process_chat_message(message: str, state: Optional[dict] = None) -> tuple:
    """Process one user message and return (reply_text, updated_state).

    The server is stateless — callers pass the full state from the previous
    turn and receive an updated copy.  LLM calls happen only as a last resort
    when regex extraction fails, keeping per-request cost near zero.
    """
    if state is None:
        state = fresh_state()

    step = state.get("step", "accessions")

    # Scan every message for accessions and schema URLs (regex, zero cost)
    new_ids  = extract_ncbi_ids(message)
    new_urls = _extract_schema_urls(message)

    # Merge newly found accessions (deduplicate, preserve order)
    existing = list(state.get("accessions") or [])
    seen_set = set(existing)
    for acc in new_ids:
        if acc not in seen_set:
            seen_set.add(acc)
            existing.append(acc)
    capped     = existing[:MAX_CHAT_SAMPLES]
    was_capped = len(existing) > MAX_CHAT_SAMPLES
    state      = {**state, "accessions": capped}

    # ── Off-topic guard (steps 1-3 only) ──────────────────────────────────
    if step not in ("confirm", "done") and _is_off_topic(message, new_ids):
        reply = (
            "I can only help you configure a metadata analysis. I collect:\n"
            "1. **Accession IDs** — BioProject, BioSample, SRR/SRX/SRA, or GenBank\n"
            "2. **Metadata fields** — what to extract (e.g. country, disease_status)\n"
            "3. **Standardization schema** — an optional CSV/GitHub URL\n\n"
            f"We're on **step {_step_label(step)} of 3**. {_reprompt(step)}"
        )
        return reply, state

    # ── Step 1: Accessions ─────────────────────────────────────────────────
    if step == "accessions":
        url_source_note = ""
        ncbi_search_term = ""

        # NCBI search URL fetch (e.g. /bioproject/?term=genometrakr)
        if not capped:
            fetched_ids, ncbi_search_term = _fetch_ncbi_search_ids(message)
            if fetched_ids:
                url_source_note = f" (from NCBI search: _{ncbi_search_term}_)"
            for acc in fetched_ids:
                if acc not in seen_set:
                    seen_set.add(acc)
                    existing.append(acc)
            capped     = existing[:MAX_CHAT_SAMPLES]
            was_capped = len(existing) > MAX_CHAT_SAMPLES
            state      = {**state, "accessions": capped}

        # PubMed URL fetch (e.g. pubmed.ncbi.nlm.nih.gov/24629344/)
        if not capped:
            pubmed_ids, pubmed_pmids = _fetch_pubmed_ids(message)
            if pubmed_ids:
                url_source_note = (
                    f" (from PubMed paper{'s' if len(pubmed_pmids) > 1 else ''}: "
                    + ", ".join(f"PMID {p}" for p in pubmed_pmids[:3]) + ")"
                )
            for acc in pubmed_ids:
                if acc not in seen_set:
                    seen_set.add(acc)
                    existing.append(acc)
            capped     = existing[:MAX_CHAT_SAMPLES]
            was_capped = len(existing) > MAX_CHAT_SAMPLES
            state      = {**state, "accessions": capped}

        # LLM fallback: prose message with no recognisable IDs
        if not capped and len(message.strip()) > 20 and not _SKIP_RE.match(message.strip()):
            llm_ids = _llm_extract_ncbi_ids(message)
            for acc in llm_ids:
                if acc not in seen_set:
                    seen_set.add(acc)
                    existing.append(acc)
            capped     = existing[:MAX_CHAT_SAMPLES]
            was_capped = len(existing) > MAX_CHAT_SAMPLES
            state      = {**state, "accessions": capped}

        if capped:
            cap_note    = f"\n_Capped at {MAX_CHAT_SAMPLES} samples._" if was_capped else ""
            acc_preview = "  ".join(f"`{a}`" for a in capped[:5]) + (
                f" … +{len(capped) - 5} more" if len(capped) > 5 else ""
            )
            state["step"] = "metadata_fields"
            reply = (
                f"Got **{len(capped)} accession{'s' if len(capped) > 1 else ''}**"
                f"{url_source_note}: "
                f"{acc_preview}{cap_note}\n\n"
                "**Step 2 of 3 — Metadata fields**\n"
                "Which fields should I extract? Examples:\n"
                "`disease_status, country, organism, body_site, sequencing_platform`\n\n"
                "Or type **skip** to auto-extract all available fields."
            )
        elif ncbi_search_term:
            reply = (
                f"I searched NCBI for **'{ncbi_search_term}'** but found no matching accessions.\n\n"
                "Try pasting the IDs directly:\n"
                "• BioProject: `PRJNA976261`\n"
                "• BioSample: `SAMN23469632`\n"
                "• SRA run: `SRR17084312`\n"
                "• GenBank: `OL757400`, `KU131308`"
            )
        else:
            has_pdf  = bool(re.search(r'\bpdf\b|\bpaper\b|\barticle\b|\bpublication\b|\bmanuscript\b', message, re.IGNORECASE))
            has_doi  = bool(re.search(r'\bdoi\.org\b|\bdoi:\s*10\.\b', message, re.IGNORECASE))
            if has_pdf:
                reply = (
                    "It looks like you're describing a paper or PDF dataset.\n\n"
                    "If the data is already deposited in NCBI, paste the accession IDs "
                    "or a BioProject link from the paper's **Data Availability** section:\n"
                    "• BioProject: `PRJNA976261`\n"
                    "• NCBI search URL: `https://www.ncbi.nlm.nih.gov/bioproject/?term=...`\n"
                    "• PubMed URL: `https://pubmed.ncbi.nlm.nih.gov/24629344/`\n\n"
                    "Or use the **Upload context document** button above to attach the PDF."
                )
            elif has_doi:
                reply = (
                    "I see a DOI link — I can't access journal pages directly, but if "
                    "the paper has NCBI data, paste the **BioProject or PubMed URL** "
                    "from its Data Availability section:\n"
                    "• `https://www.ncbi.nlm.nih.gov/bioproject/PRJNA976261`\n"
                    "• `https://pubmed.ncbi.nlm.nih.gov/24629344/`\n\n"
                    "Or paste the accession IDs directly: `PRJNA...`, `SRR...`, `SAMN...`"
                )
            else:
                reply = (
                    "I couldn't find any accession IDs in your message.\n\n"
                    "**NCBI IDs:**\n"
                    "• BioProject: `PRJNA976261`\n"
                    "• BioSample: `SAMN23469632`\n"
                    "• SRA run: `SRR17084312`\n"
                    "• GenBank: `OL757400`, `KU131308`\n"
                    "• ENA project: `ERP115334`\n\n"
                    "**Non-NCBI IDs** (web search used instead of NCBI fetch):\n"
                    "• MassIVE: `MSV000080918`\n"
                    "• PRIDE: `PXD000001`\n"
                    "• MetaboLights: `MTBLS1`\n\n"
                    "You can paste them, share an NCBI or PubMed URL, or describe the paper."
                )
        return reply, state

    # ── Step 2: Metadata fields ────────────────────────────────────────────
    if step == "metadata_fields":
        # If user typed only more accessions here, merge and re-ask for fields
        stripped = re.sub(
            r'\b(?:PRJ[A-Z]{2}|SAM[A-Z]{1,2}|SRX|SRR|ERR)\d+\b|'
            r'\b[A-Z]{1,2}\d{5,8}(?:\.\d+)?\b',
            '', message, flags=re.IGNORECASE,
        ).strip()
        if new_ids and (not stripped or _SKIP_RE.match(stripped)):
            reply = (
                f"Updated to **{len(capped)} accession{'s' if len(capped) > 1 else ''}**.\n\n"
                "**Step 2 of 3 — Metadata fields**\n"
                "Which fields should I extract? (or type **skip**)"
            )
            return reply, state

        if _SKIP_RE.match(message.strip()):
            state["metadata_fields"] = []
            state["step"] = "schema"
            reply = (
                "OK — I'll auto-extract all available fields.\n\n"
                "**Step 3 of 3 — Standardization schema (optional)**\n"
                "Paste a GitHub or CSV URL for a data dictionary, or type **skip**."
            )
            return reply, state

        # Regex parse first (zero cost)
        fields = _parse_fields(message)

        # LLM fallback for natural-language field descriptions
        if not fields and len(message.strip()) > 10:
            fields = _llm_extract_fields(message)

        if fields:
            state["metadata_fields"] = fields
            state["step"] = "schema"
            field_disp = ", ".join(f"`{f}`" for f in fields[:8]) + (
                f" … +{len(fields) - 8} more" if len(fields) > 8 else ""
            )
            reply = (
                f"Fields recorded: {field_disp}\n\n"
                "**Step 3 of 3 — Standardization schema (optional)**\n"
                "Paste a GitHub or CSV URL for a data dictionary, or type **skip**."
            )
        else:
            reply = (
                "I couldn't parse any field names. Please list them comma-separated:\n"
                "`disease_status, country, organism, body_site`\n\n"
                "Or type **skip** to auto-extract all fields."
            )
        return reply, state

    # ── Step 3: Schema ─────────────────────────────────────────────────────
    if step == "schema":
        if new_urls:
            state["schema"] = ", ".join(new_urls)
            state["step"] = "confirm"
            reply = _confirm_message(state)
        elif _SKIP_RE.match(message.strip()):
            state["schema"] = ""
            state["step"] = "confirm"
            reply = _confirm_message(state)
        else:
            reply = (
                "I couldn't find a URL. Paste a GitHub or CSV link, or type **skip**:\n"
                "_e.g. https://github.com/.../cMD_data_dictionary.csv_"
            )
        return reply, state

    # ── Step 4: Confirm ────────────────────────────────────────────────────
    if step == "confirm":
        if _YES_RE.search(message):
            state["step"] = "done"
            state["ready_to_run"] = True
            reply = "Starting analysis now…"
        elif _NO_RE.search(message):
            state = fresh_state()
            reply = "No problem — let's start over.\n\n" + get_initial_message()
        else:
            reply = _confirm_message(state)
        return reply, state

    # ── Done / unexpected → restart ────────────────────────────────────────
    state = fresh_state()
    reply = "Analysis submitted! To start another, let's begin fresh.\n\n" + get_initial_message()
    return reply, state
