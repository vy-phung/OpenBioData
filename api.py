import asyncio
import json
import os
import re
import tempfile
import uuid

import field_aliases
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import uvicorn

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="BioMetadataAudit API")

MAX_SAMPLES = 50

# Per-run cancellation: run_id -> asyncio.Event (set = cancelled)
_ACTIVE_RUNS: Dict[str, asyncio.Event] = {}


# ── helpers ───────────────────────────────────────────────────────────────────

def _sse(event_type: str, payload: dict) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


async def _thread_with_heartbeat(fn, *args, heartbeat_message: str = "Still working…",
                                  interval: float = 10.0):
    """Run a blocking call via asyncio.to_thread, yielding an SSE progress
    event every `interval` seconds while it's in flight.

    Some blocking calls (e.g. resolving a publisher paper page through
    several fallback fetches) can legitimately take 30-90+ seconds with no
    natural place to report intermediate progress. Without any bytes sent
    on the SSE stream during that gap, Railway's edge proxy (this app's
    deploy target, per Procfile) treats the connection as idle and closes
    it -- the browser then sees the literal "Cannot reach the server"
    network error even though the backend is still running fine. Periodic
    heartbeats keep the stream alive until the real result is ready.
    """
    task = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    waited = 0.0
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except asyncio.TimeoutError:
            waited += interval
            yield _sse("progress", {"message": f"{heartbeat_message} ({int(waited)}s)"})
    yield {"__result__": task.result()}


def _serialize_rows(rows: list) -> list:
    """Convert raw row dicts to JSON-serializable form."""
    out = []
    for r in rows:
        row_out: dict = {}
        for k, v in r.items():
            if k == "_additional_fields":
                row_out["_additional_fields"] = v if isinstance(v, dict) else {}
            else:
                row_out[k] = str(v) if v is not None else ""
        out.append(row_out)
    return out


def _log_to_gsheet(email: str, accession: str, message: str) -> None:
    from datetime import datetime, timezone
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open("Report").sheet1
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sheet.append_row([accession.strip(), message.strip(), email.strip(), ts])


def _open_or_create_worksheet(wb, title: str, headers: list):
    import gspread
    try:
        ws = wb.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = wb.add_worksheet(title=title, rows=2000, cols=len(headers))
        ws.append_row(headers)
    return ws


# ── Known-sample result cache (Google Sheet "KnownCachedSamples") ─────────────
_CACHE_SHEET_NAME = "KnownCachedSamples"
_CACHE_HEADERS    = ["sample_id", "bioproject", "timestamp", "fields_json"]
_CACHE_TTL_SECS   = 300   # reload from sheet at most every 5 minutes

_cache_mem: dict  = {}          # (sample_id, bioproject) → {field: value}
_cache_load_time: list = [0.0]  # mutable container so we can update in nested fn

def _cache_open_sheet():
    """Return the KnownCachedSamples gspread worksheet, creating it if needed."""
    creds_dict = json.loads(os.environ.get("GCP_CREDS_JSON", "{}"))
    if not creds_dict:
        return None
    from oauth2client.service_account import ServiceAccountCredentials
    import gspread
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    wb = client.open("Report")
    return _open_or_create_worksheet(wb, _CACHE_SHEET_NAME, _CACHE_HEADERS)


def _cache_reload() -> None:
    """Load all rows from the cache sheet into _cache_mem."""
    import time
    try:
        ws = _cache_open_sheet()
        if ws is None:
            return
        rows = ws.get_all_records(expected_headers=_CACHE_HEADERS)
        new_mem: dict = {}
        for r in rows:
            sid = (r.get("sample_id") or "").strip()
            bp  = (r.get("bioproject") or "").strip()
            fj  = r.get("fields_json") or "{}"
            if not sid:
                continue
            try:
                fields = json.loads(fj)
            except Exception:
                fields = {}
            new_mem[(sid, bp)] = fields
        _cache_mem.clear()
        _cache_mem.update(new_mem)
        _cache_load_time[0] = time.time()
        print(f"[cache] Loaded {len(_cache_mem)} cached samples.")
    except Exception as e:
        print(f"[cache] Reload failed: {e}")


def _cache_ensure_fresh() -> None:
    import time
    if time.time() - _cache_load_time[0] > _CACHE_TTL_SECS:
        _cache_reload()


def _cache_get(sample_id: str, bioproject: str, requested_fields: list):
    """Return cached field dict if all requested fields have non-unknown values; else None."""
    _cache_ensure_fresh()
    key = (sample_id.strip(), (bioproject or "").strip())
    cached = _cache_mem.get(key)
    if cached is None:
        return None
    _unknown = {"unknown", "Unknown", "UNKNOWN", ""}
    if all(str(cached.get(f, "unknown")).strip() not in _unknown for f in requested_fields):
        return cached
    return None  # some fields still unknown → rerun


def _cache_save(sample_id: str, bioproject: str, fields: dict) -> None:
    """Save/update a sample result in the cache sheet and in-memory dict."""
    try:
        _cache_ensure_fresh()
        from datetime import datetime, timezone
        ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        key = (sample_id.strip(), (bioproject or "").strip())
        # Merge with existing cached values so we don't overwrite known fields with unknown
        existing = _cache_mem.get(key, {})
        merged = {**existing}
        for f, v in fields.items():
            if f in ("biosample_accession", "bioproject", "sra_accession", "genbank_accession",
                     "explanation", "confidence_score", "time_cost", "_additional_fields"):
                continue  # skip non-field columns
            if str(v).strip().lower() not in ("unknown", ""):
                merged[f] = v
            elif f not in merged:
                merged[f] = v
        _cache_mem[key] = merged
        fj = json.dumps(merged, ensure_ascii=False)

        ws = _cache_open_sheet()
        if ws is None:
            return
        # Find existing row (search column A for sample_id, then check column B)
        col_a = ws.col_values(1)   # sample_id column
        col_b = ws.col_values(2)   # bioproject column
        row_idx = None
        for i, (sid, bp) in enumerate(zip(col_a, col_b), start=1):
            if sid == sample_id and bp == (bioproject or ""):
                row_idx = i
                break
        if row_idx:
            ws.update(f"C{row_idx}:D{row_idx}", [[ts, fj]])
        else:
            ws.append_row([sample_id, bioproject or "", ts, fj])
        print(f"[cache] Saved {sample_id} / {bioproject}.")
    except Exception as e:
        print(f"[cache] Save failed for {sample_id}: {e}")


def _log_analytics(event: str, session_id: str, email: str, properties: dict, user_agent: str) -> None:
    from datetime import datetime, timezone
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    wb = client.open("Report")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── Events sheet: every event from every visitor ──────────────────────────
    events_ws = _open_or_create_worksheet(
        wb, "Events",
        ["timestamp", "session_id", "event", "user_email", "properties", "user_agent"]
    )
    events_ws.append_row([
        ts,
        session_id[:36],
        event[:60],
        (email or "")[:120],
        json.dumps(properties or {}),
        (user_agent or "")[:200],
    ])

    # ── UserLog sheet: all users (anonymous identified by session_id) ─────────
    user_log_ws = _open_or_create_worksheet(
        wb, "UserLog",
        [
            "timestamp", "email", "event",
            "accession_input", "metadata_fields",
            "sample_count", "duration_sec", "feature_clicked",
            "has_std_url", "has_context_file",
            "accession_types", "session_id",
        ]
    )
    p = properties or {}
    duration_sec = round(p.get("duration_ms", 0) / 1000, 1) if p.get("duration_ms") else ""
    log_email = email[:120] if email else f"anon:{session_id[:16]}"
    user_log_ws.append_row([
        ts,
        log_email,
        event[:60],
        (p.get("accession_input") or "")[:500],
        (p.get("metadata_fields") or "")[:300],
        p.get("sample_count", ""),
        duration_sec,
        (p.get("feature") or "")[:80],
        "yes" if p.get("has_std_url") else ("no" if "has_std_url" in p else ""),
        "yes" if p.get("has_context_file") else ("no" if "has_context_file" in p else ""),
        json.dumps(p.get("accession_types") or {}),
        session_id[:36],
    ])

    # ── Users sheet: one row per signup ───────────────────────────────────────
    if event == "signup" and email:
        users_ws = _open_or_create_worksheet(
            wb, "Users",
            ["signup_date", "email", "name", "source", "session_id"]
        )
        users_ws.append_row([
            ts,
            email[:120],
            (properties.get("name") or "")[:120],
            (properties.get("source") or "")[:60],
            session_id[:36],
        ])


# ── helpers ────────────────────────────────────────────────────────────────────

def _rows_from_new_pipeline(accs_output: dict, niche_cases, use_direct_names: bool = True) -> list:
    """Convert additional_pipeline.pipeline_with_gemini output to row dicts.

    Each row includes:
      - IDENTIFIER columns: biosample_accession, bioproject, sra_accession
      - One column per metadata field (value only)
      - "explanation" column: all field explanations + source links combined
      - "confidence_score" column: per-field confidence combined into one paragraph
      - "time_cost" column

    Sheet 2 ("Full Raw Attributes") additionally carries per-field explanation
    columns inside _additional_fields for more granular inspection.
    """
    def _tc(v, max_len=49000):
        if v is None:
            return ""
        s = str(v) if not isinstance(v, str) else v
        if s.strip().lower() in ("none", "nan", "nat", "null"):
            return ""
        return s[:max_len] + ("… [TRUNCATED]" if len(s) > max_len else "")

    rows = []
    niche_list = list(niche_cases or [])

    for sample_id, data in accs_output.items():
        if not isinstance(data, dict):
            continue

        # ── IDENTIFIER columns ────────────────────────────────────────────────
        acc_info = data.get("_accession_info") or {}
        biosample_acc  = _tc(acc_info.get("biosample")   or sample_id)
        bioproject_val = _tc(acc_info.get("bioproject")  or "")
        sra_accession  = _tc(acc_info.get("experiment")  or "")
        genbank_acc    = _tc(acc_info.get("accession")   or "")

        row: dict = {
            "biosample_accession": biosample_acc,
            "bioproject":          bioproject_val,
            "sra_accession":       sra_accession,
        }
        if genbank_acc:
            row["genbank_accession"] = genbank_acc

        # ── Per-field values + collect explanation parts ──────────────────────
        import re as _re
        _source_tag_re   = _re.compile(r'\[Sources?:\s*([^\]]+)\]', _re.IGNORECASE)
        _conflict_tag_re = _re.compile(r'\[Conflict:\s*([^\]]+)\]', _re.IGNORECASE)

        explanation_parts: list = []
        conflict_parts:    list = []
        extra: dict = {}          # per-field explanation detail for Sheet 2
        fields_emitted: list = [] # fields that got a row[] value, in order (niche + promoted Pass 2)

        def _emit_field(field: str, value: str, raw_explanation: str, strip_method_prefix: bool = False) -> str:
            """Parse [Sources:]/[Conflict:] tags out of raw_explanation, append a clean
            one-line narrative to explanation_parts, and record per-field citation
            detail in `extra` for Sheet 2. Shared by Pass 1 (niche) and Pass 2
            (generalized) fields so both get identical explanation/source treatment.
            Returns the (possibly ##CONFLICT-stripped) display value.
            """
            value = _tc(value) or "unknown"
            if "##CONFLICT:" in value:
                value, inline_conflict = value.split("##CONFLICT:", 1)
                value = value.strip()
                conflict_parts.append(f"• {field}: {inline_conflict.strip()}")

            if value.lower() == "unknown":
                explanation_parts.append(f"[{field}] not found in available sources")
                fields_emitted.append(field)
                return value

            clean_method = raw_explanation or ""
            if strip_method_prefix and "-" in clean_method[:20]:
                clean_method = clean_method.split("-", 1)[-1].strip()

            if clean_method:
                _src_match  = _source_tag_re.search(clean_method)
                _conf_match = _conflict_tag_re.search(clean_method)
                if _src_match:
                    extra[f"{field}_source_location"] = _src_match.group(1).strip()
                if _conf_match:
                    conflict_text = _conf_match.group(1).strip()
                    if conflict_text.lower() not in ("none", "no conflict", "n/a"):
                        extra[f"{field}_conflict"] = conflict_text
                        conflict_parts.append(f"• {field}: {conflict_text}")

                narrative = _source_tag_re.sub("", clean_method)
                narrative = _conflict_tag_re.sub("", narrative).strip()
            else:
                narrative = ""

            if narrative:
                display_narrative = narrative
                if ". " in display_narrative and '[' not in display_narrative:
                    display_narrative = display_narrative.split(". ")[0] + "."
                explanation_parts.append(f"• {field}: {display_narrative}")
                extra[f"{field}_explanation"] = clean_method
                extra[f"{field}_narrative"]   = narrative
            else:
                explanation_parts.append(f"• {field}: {value}")

            fields_emitted.append(field)
            return value

        for field in niche_list:
            field_data = data.get(field, {}) or {}
            if isinstance(field_data, dict) and field_data:
                answers  = [k for k in field_data if k]
                methods: list = []
                for ans_methods in field_data.values():
                    if isinstance(ans_methods, list):
                        methods.extend(ans_methods)
                value       = "\n".join(answers) or "unknown"
                method_text = "\n".join(methods)
            else:
                value       = "unknown"
                method_text = ""

            row[field] = _emit_field(field, value, method_text, strip_method_prefix=True)

        # ── Source links appended at end of explanation ───────────────────────
        source_list = data.get("source", []) or []
        source_text = "\n".join(source_list) if source_list else "No external links"

        # ── Confidence score: numeric + tier + short reason ───────────────────
        signals     = data.get("signals", {}) or {}
        in_ncbi     = signals.get("in_NCBI", False)
        has_pubmed  = signals.get("has_pubmed", False)
        num_pubs    = signals.get("num_publications", 0)
        acc_in_text = signals.get("accession_found_in_text", False)
        missing_kf  = any(
            str(row.get(f, "")).lower() in ("unknown", "")
            for f in niche_list
        )
        known_fail  = signals.get("known_failure_pattern", False)

        try:
            from confidence_score import compute_confidence_score_and_tier
            conf_signals = {
                "has_geo_loc_name":        in_ncbi,
                "has_pubmed":              has_pubmed,
                "accession_found_in_text": acc_in_text,
                "num_publications":        num_pubs,
                "missing_key_fields":      missing_kf,
                "known_failure_pattern":   known_fail,
            }
            conf_score, conf_tier, conf_reasons = compute_confidence_score_and_tier(conf_signals)
        except Exception:
            # Fallback: simple rule-based score
            conf_score = 0
            if in_ncbi:   conf_score += 20
            if has_pubmed: conf_score += 30 if acc_in_text else 10
            if num_pubs >= 2: conf_score += 20
            elif num_pubs == 1: conf_score += 10
            if missing_kf:  conf_score -= 10
            conf_score = max(0, min(100, conf_score))
            conf_tier = "high" if conf_score >= 70 else ("medium" if conf_score >= 40 else "low")
            conf_reasons = ["Signal-based score"]

        tier_icon = {"high": "🟢 High", "medium": "🟡 Medium", "low": "🔴 Low"}.get(
            conf_tier.lower(), conf_tier.capitalize()
        )
        reason_str = "; ".join(conf_reasons[:2]) if conf_reasons else ""
        confidence_display = f"{conf_score} ({tier_icon})" + (f" — {reason_str}" if reason_str else "")

        # ── Pass 2 fields (now {field: {"value", "explanation"}}) ──────────────
        pass2_fields = dict(data.get("_additional_fields") or {})

        # ── Ontology annotations → add as extra columns ───────────────────────
        ontology_annots = data.get("_ontology_annotations") or {}
        _ONTO_LABELS = {
            "taxonomy":               "Taxonomy (NCBITaxon ID | label)",
            "organism_part":          "Organism Part/Body Site (UBERON ID | label)",
            "host_characteristics":   "Host/Subject Characteristics (PATO·SO·DOID IDs | labels)",
            "experimental_conditions":"Collection/Experimental Conditions (OBI·CHMO·MS IDs | labels)",
            "contextual_study":       "Contextual/Study Design (GO·DOID IDs | labels)",
        }
        for cat, label in _ONTO_LABELS.items():
            if cat in ontology_annots and ontology_annots[cat]:
                items = ontology_annots[cat]
                val = "\n".join(items) if isinstance(items, list) else str(items)
                # Ontology columns always go to Sheet 1 (they're the requested output)
                row[label] = _tc(val)
                pass2_fields.pop(f"ontology_{cat}", None)  # avoid duplication in Sheet 2

        def _pass2_value_explanation(v):
            if isinstance(v, dict):
                return v.get("value", ""), v.get("explanation", "")
            return str(v) if v is not None else "", ""

        if not niche_list and not ontology_annots:
            # No user-specified fields: promote Pass 2 fields directly into Sheet 1,
            # through the same _emit_field path as niche fields so they get a
            # real explanation/source citation instead of a bare value dump.
            # Alias-canonicalize each key against columns already on this row so
            # synonyms (geo_loc_name vs geographic_location_country_and_or_sea)
            # merge into one column instead of creating a duplicate.
            for k, v in pass2_fields.items():
                value, explanation = _pass2_value_explanation(v)
                merged_key = field_aliases.canonicalize_field_name(k, row.keys())
                if merged_key not in row:
                    row[merged_key] = _emit_field(merged_key, value, explanation)
            row["_additional_fields"] = {}
        else:
            # Ontology mode or user-specified niche fields: Pass 2 stays in Sheet 2
            # only, flattened to <field>/<field>_explanation pairs (consistent with
            # the niche-field `extra` shape) instead of nested dicts.
            pass2_flat: dict = {}
            for k, v in pass2_fields.items():
                value, explanation = _pass2_value_explanation(v)
                pass2_flat[k] = value
                if explanation:
                    pass2_flat[f"{k}_explanation"] = explanation
            row["_additional_fields"] = {**pass2_flat, **extra}

        # ── Final columns ─────────────────────────────────────────────────────
        # Build per-field source column: narrative + indented per-source citations
        # for every field that actually landed on this row (niche + promoted Pass 2).
        per_field_source_lines = []
        for field in fields_emitted:
            field_val = str(row.get(field, ""))
            if field_val.lower() in ("unknown", ""):
                continue
            narrative_key  = f"{field}_narrative"
            loc_key        = f"{field}_source_location"
            narrative_text = extra.get(narrative_key, "")

            if loc_key in extra:
                # Split "key1 (loc, 'text'); key2 (...)" into individual citations
                raw_cites = extra[loc_key]
                cite_entries = [c.strip() for c in raw_cites.split(";") if c.strip()]
                cite_lines   = "\n  ".join(f"→ {c}" for c in cite_entries)
                if narrative_text:
                    per_field_source_lines.append(
                        f"• {field}: {narrative_text}\n  {cite_lines}"
                    )
                else:
                    per_field_source_lines.append(f"• {field}:\n  {cite_lines}")
            elif narrative_text:
                per_field_source_lines.append(f"• {field}: {narrative_text}\n  → see linked sources")
            else:
                per_field_source_lines.append(f"• {field}: see linked sources")

        if not per_field_source_lines:
            per_field_source_lines_text = source_text
        else:
            per_field_source_lines_text = (
                "\n\n".join(per_field_source_lines)
                + "\n\nAll linked sources:\n" + source_text
            )

        # Conflict column: collected per-field conflicts from [Conflict:] tags
        conflict_display = "\n".join(conflict_parts) if conflict_parts else ""

        row["explanation"]      = _tc("\n".join(explanation_parts))
        row["sources"]          = _tc(per_field_source_lines_text)
        row["confidence_score"] = _tc(confidence_display)
        row["conflict"]         = _tc(conflict_display)
        row["time_cost"]        = _tc(data.get("time_cost", ""))
        rows.append(row)

    return rows


# ── request models ────────────────────────────────────────────────────────────

class NonNcbiInfo(BaseModel):
    database: Optional[str] = ""          # e.g. "MassIVE", "PRIDE", user-provided
    is_project: Optional[bool] = False
    dataset_files_url: Optional[str] = "" # URL to dataset files page for sub-sample scraping


class PaperEntry(BaseModel):
    doi_or_link: str
    context_file_ids: Optional[List[str]] = None  # PDF/supplementary files attached to THIS paper only
    link_context_id: Optional[str] = None          # text already extracted from doi_or_link via /process-context-urls
                                                    # (so the backend need not re-fetch the link)


class AnalyzeRequest(BaseModel):
    bioproject_id: str
    metadata_fields: Optional[List[str]] = None
    standardization_url: Optional[str] = None   # comma-separated URLs accepted
    context_file_id: Optional[str] = None        # legacy single-file path from /upload-context
    context_file_ids: Optional[List[str]] = None # multi-file paths from /upload-context or /process-context-urls
    context_file_name: Optional[str] = None      # display name(s) for UI/logging
    sample_limit: Optional[int] = None           # max samples to process this run
    papers: Optional[List[PaperEntry]] = None    # DOIs / paper links to resolve to NCBI accessions
    # Files attached to one specific manually-entered accession (e.g. uploaded
    # in response to a per-accession "paper inaccessible" warning from a prior
    # run), so they aren't broadcast into every other accession's context too.
    accession_context_file_ids: Optional[Dict[str, List[str]]] = None
    non_ncbi_info: Optional[Dict[str, NonNcbiInfo]] = None  # {acc_id: info}
    run_id: Optional[str] = None                 # client-provided run UUID for cancellation
    email: Optional[str] = ""                    # signed-in user email for usage tracking


class ChatMessageRequest(BaseModel):
    message: str
    state: Optional[Dict[str, Any]] = None


class ReportRequest(BaseModel):
    accession: str
    message: str
    email: Optional[str] = ""


class TrackRequest(BaseModel):
    event: str
    session_id: str
    properties: Optional[Dict[str, Any]] = None
    email: Optional[str] = ""
    user_agent: Optional[str] = ""


class FeedbackRequest(BaseModel):
    impact: int = 0
    next_steps: Optional[list] = None
    priority: Optional[str] = ""
    freetext: Optional[str] = ""
    sample_count: Optional[int] = 0
    email: Optional[str] = ""


class GenerateExcelRequest(BaseModel):
    rows: List[Dict[str, Any]]


# ── routes ────────────────────────────────────────────────────────────────────

ALLOWED_UPLOAD_EXTENSIONS = {
    ".txt", ".csv", ".tsv", ".pdf", ".xlsx", ".xls",
    ".json", ".xml", ".docx", ".zip",
}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

# Uploaded-context files are written here by /upload-context and
# /process-context-urls, then read back by realpath during /analyze. Using one
# stable, app-owned directory -- instead of a fresh tempfile.mkdtemp() per
# upload -- keeps paths predictable and lets the read guard trust exactly this
# prefix regardless of the host's TMPDIR (which may not be under /tmp; the old
# `startswith("/tmp")` guard silently dropped uploads whenever it wasn't). The
# reader still realpath-checks each id against this prefix to reject traversal.
# NOTE: this does not survive across separate server instances -- if uploads and
# /analyze land on different replicas the file won't be found; that case is now
# reported loudly to the user instead of failing silently.
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "obd_context_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
_UPLOAD_DIR_REAL = os.path.realpath(UPLOAD_DIR)


def _extract_text_from_upload(file_bytes: bytes, filename: str) -> str:
    """Read uploaded file bytes and return plain text for the LLM context."""
    ext = os.path.splitext(filename.lower())[1]

    if ext in (".txt", ".csv", ".tsv", ".json", ".xml"):
        try:
            return file_bytes.decode("utf-8", errors="replace")
        except Exception:
            return file_bytes.decode("latin-1", errors="replace")

    if ext == ".pdf":
        text = ""
        try:
            import fitz  # PyMuPDF — much better than PyPDF2
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            pages = [page.get_text("text") for page in doc]
            doc.close()
            text = "\n\n".join(pages)
        except Exception:
            try:
                import io
                import PyPDF2
                reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
                pages = [p.extract_text() or "" for p in reader.pages]
                text = "\n".join(pages)
            except Exception as exc:
                return f"[PDF text extraction failed: {exc}]"

        # Plain page text loses which table cell belongs to which row/column
        # (the same issue fixed for fetched papers in data_preprocess.py) --
        # append structured table rows so an uploaded closed-access PDF gets
        # the same treatment as one fetched live.
        try:
            from NER.PDF import pdf as _pdf_mod
            from data_preprocess import clean_tables_format, _serialize_tables_as_text
            import tempfile as _tempfile, pathlib as _pathlib
            tmp_pdf = _pathlib.Path(_tempfile.mktemp(suffix=".pdf"))
            tmp_pdf.write_bytes(file_bytes)
            tables = clean_tables_format(_pdf_mod.PDF(str(tmp_pdf), str(tmp_pdf.parent)).extractTable())
            tables_text = _serialize_tables_as_text(tables)
            if tables_text:
                text += "\n" + tables_text
            tmp_pdf.unlink(missing_ok=True)
        except Exception as exc:
            print(f"[upload-context] PDF table extraction failed for {filename}: {exc}")
        return text

    if ext == ".zip":
        try:
            from data_preprocess import _extract_zip_text
            import tempfile as _tempfile, pathlib as _pathlib
            tmp_zip = _pathlib.Path(_tempfile.mktemp(suffix=".zip"))
            tmp_zip.write_bytes(file_bytes)
            text = _extract_zip_text(str(tmp_zip))
            tmp_zip.unlink(missing_ok=True)
            return text
        except Exception as exc:
            return f"[ZIP text extraction failed: {exc}]"

    if ext in (".xlsx", ".xls"):
        try:
            from data_preprocess import _extract_excel_text
            import tempfile, pathlib
            tmp = pathlib.Path(tempfile.mktemp(suffix=ext))
            tmp.write_bytes(file_bytes)
            text = _extract_excel_text(tmp)
            tmp.unlink(missing_ok=True)
            return text
        except Exception as exc:
            return f"[Excel text extraction failed: {exc}]"

    if ext == ".docx":
        try:
            from data_preprocess import _extract_docx_text
            import tempfile, pathlib
            tmp = pathlib.Path(tempfile.mktemp(suffix=".docx"))
            tmp.write_bytes(file_bytes)
            text = _extract_docx_text(tmp)
            tmp.unlink(missing_ok=True)
            return text
        except Exception as exc:
            return f"[DOCX text extraction failed: {exc}]"

    return file_bytes.decode("utf-8", errors="replace")


def _process_one_upload(filename: str, raw: bytes) -> dict:
    """Blocking work for a single uploaded file: text + table extraction,
    plus (for PDFs) auto-fetching any Data Availability / Supplementary
    Material links found in the text. Run via asyncio.to_thread -- this can
    take from seconds to a couple minutes (PDF table parsing, network
    fetches for supplementary files), and doing it inline on the event loop
    would freeze every other request the server is handling (including any
    in-flight /analyze SSE stream) for the same duration.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return {
            "filename": filename,
            "status": "failed",
            "error": f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}",
        }

    if len(raw) > MAX_UPLOAD_BYTES:
        return {"filename": filename, "status": "failed", "error": "File too large (max 20 MB)."}

    text = _extract_text_from_upload(raw, filename or "upload")

    # An uploaded closed-access PDF's text often still names a Data
    # Availability / Supplementary Material URL even though the paper
    # itself was paywalled -- that linked file is usually on the open
    # web, so fetch it automatically instead of asking the user to find
    # and upload it themselves (mirrors getSupMaterial()'s auto-follow
    # for papers fetched live, which only works for HTML pages).
    if ext == ".pdf":
        try:
            import paper_resolver
            from data_preprocess import extract_url_text as _extract_url_text
            sup_links = paper_resolver.discover_supplementary_links_in_text(text)[:5]
            if sup_links:
                sup_dir = tempfile.mkdtemp()
                for sup_url in sup_links:
                    try:
                        sup_result = _extract_url_text(sup_url, sup_dir)
                        if sup_result.get("status") == "ok" and sup_result.get("text"):
                            text += (
                                f"\n\n-- Auto-fetched supplementary link from {filename}: "
                                f"{sup_result['name']} ({sup_url}) --\n{sup_result['text']}"
                            )
                    except Exception as _sup_exc:
                        print(f"[upload-context] supplementary auto-fetch failed for {sup_url}: {_sup_exc}")
        except Exception as _sup_scan_exc:
            print(f"[upload-context] supplementary link scan failed for {filename}: {_sup_scan_exc}")

    tmp_dir = tempfile.mkdtemp(dir=UPLOAD_DIR)
    ctx_path = os.path.join(tmp_dir, "user_context.txt")
    with open(ctx_path, "w", encoding="utf-8") as fh:
        fh.write(text)

    return {"filename": filename, "status": "ok", "context_file_id": ctx_path, "chars": len(text)}


@app.post("/upload-context")
async def upload_context(files: List[UploadFile] = File(...)):
    """Accept one or more files; return a list of per-file results."""
    results = []
    for file in files:
        raw = await file.read()
        result = await asyncio.to_thread(_process_one_upload, file.filename or "upload", raw)
        results.append(result)

    return {"files": results}


class ProcessUrlsRequest(BaseModel):
    urls: List[str]
    save_name: Optional[str] = None   # if provided, saves data/<save_name>.docx


@app.post("/process-context-urls")
async def process_context_urls(req: ProcessUrlsRequest):
    """
    Fetch and extract text from a list of URLs.
    Returns per-URL status; failed URLs are flagged so the user can download manually.
    Combined extracted text is saved to a temp file for LLM context.
    Optionally saves a formatted DOCX to data/<save_name>.docx.
    """
    def _process():
        from pathlib import Path as _Path
        from data_preprocess import extract_url_text, process_sources_to_docx

        data_dir = _Path("data")
        data_dir.mkdir(exist_ok=True)

        urls = [u.strip() for u in req.urls if u.strip()]

        # Single pass — keep full results (including text) in memory
        raw_results = [extract_url_text(url, data_dir) for url in urls]

        # JSON-safe summary (no full text)
        url_results = [
            {
                "url": r["url"],
                "name": r["name"],
                "kind": r["kind"],
                "status": r["status"],
                "chars": len(r["text"]),
                "error": r["error"],
                "supplementary_count": len(r.get("supplementary") or []),
            }
            for r in raw_results
        ]

        # Build combined context text. Each block is tagged with its source
        # URL (===SOURCE_URL:: ...===) so /analyze can later pull it back out
        # into a per-URL map -- letting the pipeline recognize a link it
        # rediscovers via web search as one the user already supplied,
        # instead of re-extracting it from scratch.
        combined_parts = [
            f"===SOURCE_URL:: {r['url']}===\nThe source - {r['name']}\n\n{r['text']}"
            for r in raw_results
            if r["status"] == "ok" and r["text"]
        ]
        combined_text = ("\n\n" + "─" * 80 + "\n\n").join(combined_parts)

        tmp_dir = tempfile.mkdtemp(dir=UPLOAD_DIR)
        ctx_path = os.path.join(tmp_dir, "url_context.txt")
        with open(ctx_path, "w", encoding="utf-8") as fh:
            fh.write(combined_text)

        saved_docx = None
        if req.save_name:
            out = data_dir / f"{req.save_name}.docx"
            process_sources_to_docx(urls, out, dataset_label=req.save_name)
            saved_docx = str(out)

        return {
            "results": url_results,
            "context_file_id": ctx_path,
            "chars": len(combined_text),
            "saved_docx": saved_docx,
        }

    return await asyncio.to_thread(_process)


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/cancel/{run_id}")
async def cancel_run(run_id: str):
    """Signal a running analysis to stop after the current sample."""
    ev = _ACTIVE_RUNS.get(run_id)
    if ev:
        ev.set()
        return {"status": "cancellation_requested", "run_id": run_id}
    return {"status": "not_found", "run_id": run_id}


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    run_id = req.run_id or str(uuid.uuid4())
    cancel_event = asyncio.Event()
    _ACTIVE_RUNS[run_id] = cancel_event
    _pipeline_task_ref: list = []  # mutable container so finally can cancel the task

    async def event_stream():
        try:
            yield _sse("run_id", {"run_id": run_id})
            yield _sse("progress", {"message": "Loading backend…"})
            await asyncio.sleep(0)

            try:
                from mtdna_backend import (
                    extract_accessions_from_input,
                    save_to_excel,
                    summarize_results,
                )
            except Exception as exc:
                yield _sse("error", {"message": f"Backend failed to load: {exc}"})
                return

            niche_cases = req.metadata_fields or None
            raw_text = req.bioproject_id.strip()
            effective_limit = req.sample_limit or MAX_SAMPLES

            # Load user-uploaded context files if provided (single or multiple).
            # Files from /process-context-urls are tagged with a
            # ===SOURCE_URL:: <url>=== marker per block (see that route) --
            # pull those back out into user_url_sources so the pipeline can
            # register each URL under its own key and skip re-extracting it
            # if web search rediscovers the same link. Plain file uploads
            # (/upload-context) have no markers and stay in the flat blob.
            user_context_text: Optional[str] = None
            user_url_sources: Dict[str, str] = {}
            _src_marker_re = re.compile(r'===SOURCE_URL:: (.*?)===\n(.*?)(?=\n===SOURCE_URL::|\Z)', re.S)
            _all_ctx_ids = []
            if req.context_file_id:
                _all_ctx_ids.append(req.context_file_id)
            if req.context_file_ids:
                _all_ctx_ids.extend(req.context_file_ids)
            if _all_ctx_ids:
                _ctx_parts = []
                for _cid in _all_ctx_ids:
                    _ctx_real = os.path.realpath(_cid)
                    if _ctx_real.startswith(_UPLOAD_DIR_REAL) and os.path.isfile(_ctx_real):
                        try:
                            with open(_ctx_real, "r", encoding="utf-8") as _fh:
                                _ctx_text = _fh.read()
                            for _u, _t in _src_marker_re.findall(_ctx_text):
                                _u, _t = _u.strip(), _t.strip()
                                if _u and _t:
                                    user_url_sources[_u] = _t
                            # Strip marked URL blocks out of the blob so their
                            # text isn't duplicated (once per-URL, once in the
                            # flat blob) -- leftover is plain file-upload text.
                            _ctx_parts.append(_src_marker_re.sub('', _ctx_text))
                        except Exception as _exc:
                            yield _sse("progress", {"message": f"Context file read warning: {_exc}"})
                if user_url_sources:
                    yield _sse("progress", {"message": f"{len(user_url_sources)} user-pasted link source(s) registered."})
                _ctx_parts = [p for p in _ctx_parts if p.strip()]
                if _ctx_parts:
                    user_context_text = "\n\n".join(_ctx_parts)
                    yield _sse("progress", {"message": f"User context loaded ({len(_all_ctx_ids)} source(s))."})
                elif not user_url_sources:
                    # IDs were sent but nothing came back -- temp file(s) missing.
                    yield _sse("progress", {"message": (
                        f"⚠ {len(_all_ctx_ids)} uploaded context source(s) could not be read and will NOT "
                        "be used — the upload may have expired or was stored on another server instance. "
                        "Re-upload and rerun."
                    )})


            # Context text scoped to a specific *discovered token* (a BioProject/GEO
            # series/etc. as found in the paper, or a manually-entered accession) --
            # not yet the final per-sample key. A BioProject token fans out into many
            # biosample-level sample IDs during NCBI resolution below, so this can't
            # be keyed by sample ID until after that fan-out happens; see the remap
            # into per_accession_context right after resolved_dict is built.
            _token_context: Dict[str, str] = {}

            def _read_context_text(file_ids: Optional[List[str]]) -> str:
                parts = []
                for _cid in (file_ids or []):
                    _real = os.path.realpath(_cid)
                    if _real.startswith(_UPLOAD_DIR_REAL) and os.path.isfile(_real):
                        try:
                            with open(_real, "r", encoding="utf-8") as _fh:
                                parts.append(_fh.read())
                        except Exception:
                            pass
                return "\n\n".join(p for p in parts if p.strip())

            # Paper links the user supplied, registered as metadata sources so the
            # pipeline reads each paper for its own discovered samples (not just the
            # NCBI records). Populated by extract_samples_from_paper below.
            _paper_other_links: list = []

            # ── Mode A: discovery ──────────────────────────────────────────────
            # Triggered when the user pasted paper links / uploaded files but typed
            # NO accession (frontend leaves the accession box empty and sends the
            # rows as `papers`). For each paper source it uses the already-extracted
            # text (paper files + any pre-fetched link text) or fetches the link,
            # discovers every NCBI accession referenced, and feeds those tokens into
            # the normal resolution path -- which already fans a BioProject/GEO
            # series out to all of its samples. Each paper's own files are scoped
            # only to the accessions discovered from that one paper, and the paper
            # link is registered as a source. Mutates raw_text / _token_context /
            # _paper_other_links; yields SSE progress. Returns nothing.
            async def extract_samples_from_paper():
                nonlocal raw_text
                import paper_resolver
                _paper_data_dir = tempfile.mkdtemp()
                _discovered_tokens: list = []
                for _paper_entry in req.papers:
                    _paper_input = (_paper_entry.doi_or_link or "").strip()
                    # Paper's own uploaded files + any text already extracted from
                    # its link both serve as discovery text and scoped context.
                    _file_text = _read_context_text(_paper_entry.context_file_ids)
                    _link_text = _read_context_text(
                        [_paper_entry.link_context_id] if _paper_entry.link_context_id else None)
                    _paper_text = "\n\n".join(p for p in (_file_text, _link_text) if p)
                    if not _paper_input and not _paper_text:
                        continue
                    if _paper_input:
                        _paper_other_links.append(_paper_input)   # register link as a source

                    # File-only row: nothing to fetch, just scan the uploaded text.
                    if not _paper_input:
                        found = sorted(paper_resolver.discover_accessions_in_text(_paper_text))
                        if found:
                            _discovered_tokens.extend(found)
                            for _acc_tok in found:
                                _token_context[_acc_tok.upper()] = _paper_text
                            yield _sse("progress", {
                                "message": f"Uploaded file(s): found {len(found)} NCBI accession(s) — {', '.join(found)}"
                            })
                        else:
                            yield _sse("progress", {"message": "No NCBI accessions found in uploaded file(s)."})
                        continue

                    yield _sse("progress", {"message": f"Resolving paper: {_paper_input}…"})
                    await asyncio.sleep(0)
                    try:
                        _paper_result = None
                        async for _hb in _thread_with_heartbeat(
                            paper_resolver.resolve_paper, _paper_input, _paper_data_dir,
                            None, _paper_text or None,
                            heartbeat_message=f"Still resolving {_paper_input}…",
                        ):
                            if isinstance(_hb, dict):
                                _paper_result = _hb["__result__"]
                            else:
                                yield _hb
                    except Exception as _pr_exc:
                        yield _sse("progress", {"message": f"⚠ Failed to resolve {_paper_input}: {_pr_exc}"})
                        continue

                    if _paper_result["status"] == "needs_pdf":
                        yield _sse("paper_needs_pdf", {
                            "paper": _paper_input,
                            "message": (
                                f"{_paper_input} could not be read (closed-access or unreachable). "
                                "Download it and upload the PDF using the '+ files' button next to this "
                                "paper, then rerun — accessions found in it are scoped to this paper only."
                            ),
                        })
                    elif _paper_result["status"] == "no_accessions_found":
                        yield _sse("progress", {"message": f"No NCBI accessions found for {_paper_input}."})
                    elif _paper_result["status"] == "ok":
                        found = sorted(_paper_result["discovered_accessions"])
                        _discovered_tokens.extend(found)
                        if _paper_text:
                            for _acc_tok in found:
                                _token_context[_acc_tok.upper()] = _paper_text
                        yield _sse("progress", {
                            "message": f"{_paper_input}: found {len(found)} NCBI accession(s) — {', '.join(found)}"
                        })
                    else:
                        yield _sse("progress", {"message": f"Could not fetch text for {_paper_input}."})

                if _discovered_tokens:
                    raw_text = (raw_text + "\n" + "\n".join(_discovered_tokens)).strip()

            # ── Mode B: augmentation ───────────────────────────────────────────
            # Triggered when the user typed accession IDs and attached files/links
            # to them (frontend sends `accession_context_file_ids` keyed by each
            # entered accession; also reused by the run-time "paper inaccessible"
            # warning flow). Each accession's files/links become scoped context for
            # that accession only -- a BioProject/GEO series later fans this context
            # out to all its samples during the resolved_dict remap below. Unlike
            # Mode A this does NOT append anything to raw_text, so a file that
            # happens to mention other accessions won't pull them into the run.
            # Mutates _token_context; yields SSE progress. Returns nothing.
            async def attach_files_to_accessions():
                import paper_resolver as _paper_resolver_mod
                for _acc_id, _file_ids in req.accession_context_file_ids.items():
                    _acc_text = _read_context_text(_file_ids)
                    if _acc_text:
                        _token_context[_acc_id.strip().upper()] = _acc_text
                        # Informational only -- these are accessions merely *cited* in
                        # the attached file, NOT samples we run (Mode B never adds to
                        # raw_text). Drop RefSeq genome IDs (NC_######), which a paper's
                        # reference list can mention by the hundreds, and cap the rest so
                        # the progress line doesn't look like a giant run exploded.
                        _acc_ctx_tokens = sorted(
                            t for t in _paper_resolver_mod.discover_accessions_in_text(_acc_text)
                            if not t.startswith("NC_")
                        )
                        _MENTION_CAP = 10
                        if _acc_ctx_tokens:
                            _shown = ", ".join(_acc_ctx_tokens[:_MENTION_CAP])
                            _extra = len(_acc_ctx_tokens) - _MENTION_CAP
                            _mentions = f" (context only, not run — also mentions: {_shown}" + (
                                f", … and {_extra} more)" if _extra > 0 else ")")
                        else:
                            _mentions = ""
                        yield _sse("progress", {
                            "message": f"Loaded {len(_file_ids)} context source(s) scoped to {_acc_id}" + _mentions
                        })
                    elif _file_ids:
                        # Files were attached but none could be read back -- the
                        # upload's temp file is gone (expired, or written on a
                        # different server instance). Say so instead of silently
                        # running without the context the user attached.
                        yield _sse("progress", {
                            "message": (
                                f"⚠ {len(_file_ids)} context file(s)/link(s) attached to {_acc_id} "
                                "could not be read and will NOT be used — the upload may have expired "
                                "or was stored on another server instance. Re-attach the file(s)/link(s) "
                                "and rerun so they're used as context."
                            )
                        })

            if req.papers:
                async for _ev in extract_samples_from_paper():
                    yield _ev

            if req.accession_context_file_ids:
                async for _ev in attach_files_to_accessions():
                    yield _ev

            # Scan any *unscoped* user-uploaded context text (the general
            # "Context files & links" area, not tied to one paper/accession)
            # for NCBI accessions too -- but only when the user didn't already
            # type an accession themselves. If they did, the file is supporting
            # context for that accession (e.g. a supplementary table listing the
            # whole BioProject's samples), not a request to also process every
            # other accession the table happens to mention.
            if user_context_text and not raw_text:
                try:
                    import paper_resolver
                    _ctx_tokens = sorted(paper_resolver.discover_accessions_in_text(user_context_text))
                    if _ctx_tokens:
                        raw_text = (raw_text + "\n" + "\n".join(_ctx_tokens)).strip()
                        yield _sse("progress", {
                            "message": f"Found {len(_ctx_tokens)} NCBI accession(s) in uploaded context — {', '.join(_ctx_tokens)}"
                        })
                except Exception as _ctx_scan_exc:
                    yield _sse("progress", {"message": f"⚠ Context accession scan failed: {_ctx_scan_exc}"})

            yield _sse("progress", {"message": "Parsing accession input…"})
            await asyncio.sleep(0)

            accessions, invalid, error = extract_accessions_from_input(
                file=None, raw_text=raw_text
            )

            if error:
                yield _sse("error", {"message": str(error)})
                return

            if not accessions:
                yield _sse("error", {"message": "No valid accessions found."})
                return

            # ── Non-NCBI accession handling ───────────────────────────────────
            # Separate tokens that belong to non-NCBI databases from NCBI ones.
            # non_ncbi_info from request overrides auto-detection.
            from non_ncbi_resolver import (
                detect_non_ncbi_database, build_non_ncbi_entry, is_non_ncbi_accession,
                scrape_project_samples,
            )
            non_ncbi_entries: dict = {}
            ncbi_accessions: list = []
            req_non_ncbi_info = req.non_ncbi_info or {}

            for acc_tok in accessions:
                if acc_tok in req_non_ncbi_info:
                    info = req_non_ncbi_info[acc_tok]
                    db   = info.database or detect_non_ncbi_database(acc_tok) or "unknown"
                    files_url = (info.dataset_files_url or "").strip()

                    # Project with dataset files URL → try to expand into sub-samples
                    if info.is_project and files_url:
                        yield _sse("progress", {
                            "message": f"Scraping sub-samples for {acc_tok} from {files_url}…"
                        })
                        try:
                            sub_samples = await asyncio.to_thread(
                                scrape_project_samples, files_url, db, acc_tok, 20
                            )
                        except Exception as _scrape_exc:
                            sub_samples = []
                            yield _sse("progress", {
                                "message": f"Sub-sample scrape failed ({_scrape_exc}); treating {acc_tok} as single sample."
                            })

                        if sub_samples:
                            yield _sse("progress", {
                                "message": f"Found {len(sub_samples)} sub-sample(s) in {acc_tok}."
                            })
                            for s in sub_samples:
                                sub_acc = f"{acc_tok} | {s['name']}"
                                sub_entry = build_non_ncbi_entry(sub_acc, db, False)
                                # Attach parent project URL as extra context
                                sub_entry[sub_acc]['_parent_project'] = acc_tok
                                sub_entry[sub_acc]['_dataset_files_url'] = files_url
                                non_ncbi_entries.update(sub_entry)
                        else:
                            # Fall back: treat project as single entity
                            yield _sse("progress", {
                                "message": (
                                    f"Could not enumerate sub-samples automatically. "
                                    f"Treating {acc_tok} as a single search query. "
                                    f"Tip: paste individual sample IDs in the accession box."
                                )
                            })
                            entry = build_non_ncbi_entry(acc_tok, db, True)
                            non_ncbi_entries.update(entry)
                    else:
                        entry = build_non_ncbi_entry(acc_tok, db, bool(info.is_project))
                        non_ncbi_entries.update(entry)
                elif is_non_ncbi_accession(acc_tok):
                    entry = build_non_ncbi_entry(acc_tok)
                    non_ncbi_entries.update(entry)
                else:
                    ncbi_accessions.append(acc_tok)

            # ── NCBI resolution (blocking — run in thread) ────────────────────
            resolved_dict: dict = {}
            if ncbi_accessions:
                yield _sse("progress", {"message": "Resolving accessions via NCBI…"})
                await asyncio.sleep(0)
                try:
                    from input_handler import build_pipeline_input, get_pipeline_accession

                    resolved_dict, skipped = await asyncio.to_thread(
                        build_pipeline_input, ", ".join(ncbi_accessions), effective_limit
                    )
                    if skipped:
                        invalid = list(invalid or []) + skipped
                        if not resolved_dict and not non_ncbi_entries:
                            # All NCBI accessions failed to resolve — likely rate-limited
                            yield _sse("error", {
                                "message": (
                                    f"Could not retrieve BioSamples for: {', '.join(skipped)}.\n"
                                    "NCBI may be temporarily rate-limiting requests (HTTP 429) or "
                                    "experiencing a server error (HTTP 500). "
                                    "Please wait a minute and try again."
                                )
                            })
                            return
                except Exception as exc:
                    yield _sse("progress", {"message": f"NCBI resolution warning: {exc}"})

            # Merge non-NCBI entries into resolved_dict
            resolved_dict.update(non_ncbi_entries)

            # Remap _token_context (keyed by the BioProject/GEO/etc. token as
            # discovered/entered) onto the final per-sample keys now that NCBI
            # resolution above has fanned a BioProject/GEO series out into its
            # individual biosample-level sample IDs -- a paper's context must
            # follow every sample that came from it, not just the literal token.
            per_accession_context: Dict[str, str] = {}
            if _token_context:
                for _sample_key, _entry in resolved_dict.items():
                    _candidates = {
                        str(_sample_key).upper(),
                        str(_entry.get("bioproject", "")).upper(),
                        str(_entry.get("biosample", "")).upper(),
                        str(_entry.get("accession", "")).upper(),
                        str(_entry.get("experiment", "")).upper(),
                        str(_entry.get("geo_series", "")).upper(),
                        str(_entry.get("geo_sample", "")).upper(),
                    }
                    for _cand in _candidates:
                        if _cand and _cand in _token_context:
                            per_accession_context[_sample_key] = _token_context[_cand]
                            break

            if non_ncbi_entries:
                db_names = ", ".join(
                    e.get("_source_database", "unknown")
                    for e in non_ncbi_entries.values()
                )
                yield _sse("progress", {
                    "message": f"Added {len(non_ncbi_entries)} non-NCBI sample(s) ({db_names}) — will search web for metadata."
                })

            all_rows: list = []

            # ── Cache lookup: skip pipeline for already-known samples ─────────
            if niche_cases and os.environ.get("GCP_CREDS_JSON"):
                _cached_accs: list = []
                for _ca, _ce in list(resolved_dict.items()):
                    _ca_id = (_ce.get("biosample") or _ce.get("accession")
                              or _ce.get("experiment") or _ca)
                    _ca_bp = _ce.get("bioproject") or (req.accession_id or "")
                    _hit = await asyncio.to_thread(_cache_get, _ca_id, _ca_bp, list(niche_cases))
                    if _hit is not None:
                        # Build a minimal row from cache
                        _crow: dict = {
                            "biosample_accession": _ce.get("biosample") or _ca_id,
                            "bioproject":          _ca_bp,
                            "sra_accession":       _ce.get("experiment") or "",
                        }
                        if _ce.get("accession"):
                            _crow["genbank_accession"] = _ce["accession"]
                        _crow.update(_hit)
                        _crow["explanation"] = "[Loaded from cache]"
                        _crow["confidence_score"] = ""
                        all_rows.append(_crow)
                        _cached_accs.append(_ca)
                        yield _sse("progress", {"message": f"[cache] {_ca_id} — returned from known-sample cache."})
                for _ca in _cached_accs:
                    del resolved_dict[_ca]

            # Use the rich pipeline for all resolved entries (BioSample, SRA,
            # GenBank-only, or non-NCBI). GenBank accessions without a BioSample
            # link still benefit from rich pipeline's web-search fallback.
            # If resolved_dict is now empty (all hits served from cache), skip the pipeline.
            if not resolved_dict and all_rows:
                yield _sse("progress", {"message": f"All {len(all_rows)} sample(s) served from cache — skipping pipeline."})

            use_rich = bool(resolved_dict and any(
                entry.get("biosample") or entry.get("experiment")
                or entry.get("_source_database") or entry.get("accession")
                or entry.get("geo_sample") or entry.get("_lazy_kind")
                for entry in resolved_dict.values()
            ))

            if use_rich:
                if len(resolved_dict) > effective_limit:
                    resolved_dict = dict(list(resolved_dict.items())[:effective_limit])
                    yield _sse("progress", {
                        "message": f"Capped at {effective_limit} sample(s) for this run."
                    })

                total = len(resolved_dict)
                yield _sse("progress", {
                    "message": f"Processing {total} sample(s)…"
                })
                await asyncio.sleep(0)

                try:
                    from additional_pipeline import (
                        pipeline_with_gemini as _rich_pipeline,
                    )

                    # Parse comma-separated standardization URLs into a list
                    std_urls: list = []
                    if req.standardization_url:
                        std_urls = [
                            u.strip()
                            for u in req.standardization_url.split(",")
                            if u.strip()
                        ]

                    # Queue lets the pipeline push progress without blocking
                    _progress_q: asyncio.Queue = asyncio.Queue()
                    _samples_done = 0

                    async def _pipe_progress(msg: str):
                        await _progress_q.put(msg)

                    pipeline_task = asyncio.ensure_future(
                        _rich_pipeline(
                            resolved_dict,
                            niche_cases=niche_cases,
                            # Discovery-mode paper links: read each as a source for
                            # its own discovered samples (schema URLs stay separate).
                            other_links=_paper_other_links or None,
                            standardization_urls=std_urls or None,
                            user_context_text=user_context_text,
                            user_url_sources=user_url_sources or None,
                            progress_cb=_pipe_progress,
                            cancel_event=cancel_event,
                            user_file_label=req.context_file_name or None,
                            per_accession_context=per_accession_context or None,
                        )
                    )
                    _pipeline_task_ref.append(pipeline_task)

                    # effective_niche starts as user-specified niche_cases; will be
                    # updated to auto-detected OHE fields once pipeline returns.
                    _effective_niche: list = list(niche_cases or [])

                    async def _emit_queue_item(msg):
                        """Emit a single queue item as the right SSE type."""
                        nonlocal _samples_done
                        if isinstance(msg, dict) and "__auto_niche_cases__" in msg:
                            # Pipeline resolved auto-niche fields — update effective list
                            # so subsequent partial rows use the proper field list.
                            if not niche_cases:
                                _effective_niche[:] = msg["__auto_niche_cases__"] or []
                        elif isinstance(msg, dict) and "__links_update__" in msg:
                            yield _sse("links_update", msg["__links_update__"])
                        elif isinstance(msg, dict) and "__links_warning__" in msg:
                            yield _sse("links_warning", msg["__links_warning__"])
                        elif isinstance(msg, dict) and "__partial_acc__" in msg:
                            partial_rows = _rows_from_new_pipeline(
                                msg["__partial_data__"], _effective_niche or None
                            )
                            _samples_done += len(partial_rows)
                            yield _sse("partial_result", {"rows": partial_rows})
                            # Save each completed sample to the known-sample cache (fire-and-forget)
                            for _cr in partial_rows:
                                _cid = (_cr.get("biosample_accession")
                                        or _cr.get("sra_accession")
                                        or _cr.get("genbank_accession") or "")
                                _cbp = _cr.get("bioproject") or ""
                                if _cid and os.environ.get("GCP_CREDS_JSON"):
                                    asyncio.ensure_future(
                                        asyncio.to_thread(_cache_save, _cid, _cbp, dict(_cr))
                                    )
                        elif isinstance(msg, str):
                            yield _sse("progress", {"message": msg})

                    # Stream progress while pipeline runs; check cancel each tick
                    while not pipeline_task.done():
                        if cancel_event.is_set():
                            pipeline_task.cancel()
                            yield _sse("progress", {"message": "⏹ Stop requested — cancelling…"})
                            break
                        if _samples_done >= effective_limit:
                            pipeline_task.cancel()
                            yield _sse("progress", {
                                "message": f"⚠ Sample limit reached ({effective_limit}). Stopping."
                            })
                            yield _sse("limit_reached", {"limit": effective_limit})
                            break
                        try:
                            msg = await asyncio.wait_for(_progress_q.get(), timeout=0.3)
                            async for evt in _emit_queue_item(msg):
                                yield evt
                            await asyncio.sleep(0)
                        except asyncio.TimeoutError:
                            await asyncio.sleep(0)

                    # Drain any remaining messages
                    while not _progress_q.empty():
                        msg = _progress_q.get_nowait()
                        async for evt in _emit_queue_item(msg):
                            yield evt

                    try:
                        pipeline_result = await asyncio.wait_for(
                            asyncio.shield(pipeline_task), timeout=2
                        )
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pipeline_result = None

                    if pipeline_result is not None:
                        accs_output = (
                            pipeline_result[0]
                            if isinstance(pipeline_result, tuple)
                            else pipeline_result
                        )
                        # Prefer user-specified niche_cases; fall back to auto-detected
                        # OHE fields from the pipeline (stored under __niche_cases__).
                        _auto_niche = accs_output.pop("__niche_cases__", None) or []
                        _effective_niche = list(niche_cases or _auto_niche)
                        _pipeline_rows = _rows_from_new_pipeline(accs_output, _effective_niche or None)
                        all_rows.extend(_pipeline_rows)   # preserve any cached rows already in all_rows
                        yield _sse("progress", {
                            "message": f"✅ Extracted metadata for {len(all_rows)} sample(s)"
                        })

                except Exception as exc:
                    yield _sse("progress", {
                        "message": f"Rich pipeline error ({exc}); falling back to legacy…"
                    })
                    use_rich = False

            if not use_rich and resolved_dict:
                # Fallback: resolve to best single accession string and use old pipeline
                if resolved_dict:
                    from input_handler import get_pipeline_accession
                    fallback_accs: list = []
                    for samn_key, entry in resolved_dict.items():
                        pa = get_pipeline_accession(entry, samn_key)
                        if pa and pa not in fallback_accs:
                            fallback_accs.append(pa)
                    if fallback_accs:
                        accessions = fallback_accs

                if len(accessions) > effective_limit:
                    accessions = accessions[:effective_limit]
                    yield _sse("progress", {
                        "message": f"Capped at {effective_limit} samples for this run."
                    })

                total = len(accessions)
                yield _sse("progress", {
                    "message": f"Running pipeline on {total} sample(s)…"
                })
                await asyncio.sleep(0)

                for i, acc in enumerate(accessions):
                    if cancel_event.is_set():
                        yield _sse("progress", {"message": "⏹ Stopped by user."})
                        break
                    if len(all_rows) >= effective_limit:
                        yield _sse("progress", {
                            "message": f"⚠ Sample limit reached ({effective_limit}). Stopping."
                        })
                        yield _sse("limit_reached", {"limit": effective_limit})
                        break
                    yield _sse("progress", {
                        "message": f"[{i + 1}/{total}] Processing {acc}…"
                    })
                    await asyncio.sleep(0)
                    try:
                        rows = await summarize_results(acc, niche_cases=niche_cases)
                        if rows:
                            all_rows.extend(rows)
                            yield _sse("partial_result", {"rows": _serialize_rows(rows)})
                            yield _sse("progress", {
                                "message": f"[{i + 1}/{total}] ✅ {acc}"
                            })
                        else:
                            yield _sse("progress", {
                                "message": f"[{i + 1}/{total}] ⚠ No result for {acc}"
                            })
                    except Exception as exc:
                        yield _sse("progress", {
                            "message": f"[{i + 1}/{total}] ❌ {acc}: {exc}"
                        })
                    await asyncio.sleep(0)

            # Build Excel in temp dir
            excel_path = ""
            if all_rows:
                try:
                    tmp = tempfile.mkdtemp()
                    excel_path = os.path.join(tmp, "biometadata_results.xlsx")
                    await asyncio.to_thread(
                        save_to_excel, all_rows, "", "", excel_path, False
                    )
                    if not os.path.isfile(excel_path):
                        excel_path = ""
                except Exception as exc:
                    excel_path = ""
                    yield _sse("progress", {"message": f"Excel generation failed: {exc}"})

            out_rows = _serialize_rows(all_rows)

            yield _sse(
                "result",
                {
                    "rows": out_rows,
                    "total": len(out_rows),
                    "excel_path": excel_path,
                },
            )

            # Track per-user usage for signed-in users (fire-and-forget)
            if req.email and all_rows:
                sample_ids = [
                    r.get("biosample_accession") or r.get("genbank_accession") or ""
                    for r in all_rows
                ]
                sample_ids = [s for s in sample_ids if s]
                if sample_ids:
                    asyncio.ensure_future(
                        asyncio.to_thread(_update_user_usage_in_gsheet, req.email, sample_ids)
                    )

        except Exception as exc:
            import traceback, logging
            logging.error("Unhandled pipeline error: %s", traceback.format_exc())
            yield _sse("error", {"message": str(exc)})
        finally:
            # Always signal cancellation on exit (covers client disconnect + explicit stop)
            # so any still-running pipeline_with_gemini loop stops at its next sample boundary.
            cancel_event.set()
            if _pipeline_task_ref and not _pipeline_task_ref[0].done():
                _pipeline_task_ref[0].cancel()
            _ACTIVE_RUNS.pop(run_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/download-excel")
async def download_excel(path: str):
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    # Restrict to /tmp to prevent path traversal
    real = os.path.realpath(path)
    if not real.startswith("/tmp"):
        raise HTTPException(status_code=403, detail="Forbidden path")
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        real,
        filename="biometadata_results.xlsx",
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )


@app.post("/generate-excel")
async def generate_excel_endpoint(req: GenerateExcelRequest):
    """Generate an Excel file on-demand from a list of row dicts (used when
    the run was stopped before the server built the file, or if inline
    generation failed)."""
    if not req.rows:
        raise HTTPException(status_code=400, detail="No rows provided")
    try:
        from mtdna_backend import save_to_excel
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backend not loaded: {exc}")
    tmp = tempfile.mkdtemp()
    excel_path = os.path.join(tmp, "biometadata_results.xlsx")
    try:
        await asyncio.to_thread(save_to_excel, req.rows, "", "", excel_path, False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Excel generation failed: {exc}")
    if not os.path.isfile(excel_path):
        raise HTTPException(status_code=500, detail="Excel file was not created")
    return {"path": excel_path}


@app.post("/chat-message")
async def chat_message(req: ChatMessageRequest):
    """Stateless chat turn: accepts a user message + prior state, returns reply + new state."""
    try:
        from chat_input_parser import process_chat_message, get_initial_message, fresh_state
        msg = (req.message or "").strip()
        if msg == "__init__":
            return {"reply": get_initial_message(), "state": fresh_state()}
        reply, new_state = process_chat_message(msg, req.state)
        return {"reply": reply, "state": new_state}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/report")
async def report(req: ReportRequest):
    try:
        await asyncio.to_thread(
            _log_to_gsheet, req.email or "", req.accession, req.message
        )
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/feedback")
async def feedback(req: FeedbackRequest):
    try:
        msg = (
            f"[FEEDBACK] impact={req.impact}/5 | priority={req.priority} | "
            f"next_steps={req.next_steps} | samples={req.sample_count} | "
            f"email={req.email} | freetext={req.freetext}"
        )
        await asyncio.to_thread(
            _log_to_gsheet, req.email or "", "FEEDBACK", msg
        )
    except Exception:
        pass
    return {"status": "ok"}


@app.post("/track")
async def track(req: TrackRequest, request: Request):
    # Fire-and-forget: never fail the client on analytics errors
    try:
        ua = req.user_agent or request.headers.get("user-agent", "")
        await asyncio.to_thread(
            _log_analytics,
            req.event, req.session_id, req.email or "",
            req.properties or {}, ua,
        )
    except Exception:
        pass
    return {"status": "ok"}


def _get_user_config_from_gsheet(email: str) -> dict:
    """Return {permitted_samples, usage_count, samples} for a signed-in user.

    If the user has no row in UserUsage yet, one is created with the global
    paid_limit as the default permitted_samples.
    """
    fallback = {"permitted_samples": 30, "usage_count": 0, "samples": []}
    if not email:
        return fallback
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        raw = os.environ.get("GCP_CREDS_JSON", "")
        if not raw:
            return fallback
        creds_dict = json.loads(raw)
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        wb = client.open("Report")

        # Read global paid_limit to use as default permitted_samples for new users
        paid_limit = 30
        try:
            cfg_ws = wb.worksheet("Config")
            for row in cfg_ws.get_all_records():
                if str(row.get("key", "")).strip() == "paid_limit":
                    try:
                        paid_limit = int(row.get("value", 30))
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        ws = _open_or_create_worksheet(
            wb, "UserUsage",
            ["email", "usage_count", "permitted_samples", "samples"]
        )
        rows = ws.get_all_records()
        email_lower = email.strip().lower()

        for row in rows:
            if str(row.get("email", "")).strip().lower() == email_lower:
                usage_count = int(row.get("usage_count") or 0)
                permitted = int(row.get("permitted_samples") or paid_limit)
                samples_str = str(row.get("samples") or "")
                samples_list = [s.strip() for s in samples_str.split(",") if s.strip()]
                return {"permitted_samples": permitted, "usage_count": usage_count, "samples": samples_list}

        # New user: create their row with defaults
        ws.append_row([email.strip(), 0, paid_limit, ""])
        return {"permitted_samples": paid_limit, "usage_count": 0, "samples": []}
    except Exception:
        return fallback


def _update_user_usage_in_gsheet(email: str, new_samples: list) -> None:
    """Increment usage_count and append accession IDs for this user in UserUsage sheet."""
    if not email or not new_samples:
        return
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        raw = os.environ.get("GCP_CREDS_JSON", "")
        if not raw:
            return
        creds_dict = json.loads(raw)
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        wb = client.open("Report")

        paid_limit = 30
        try:
            cfg_ws = wb.worksheet("Config")
            for cfg_row in cfg_ws.get_all_records():
                if str(cfg_row.get("key", "")).strip() == "paid_limit":
                    try:
                        paid_limit = int(cfg_row.get("value", 30))
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        ws = _open_or_create_worksheet(
            wb, "UserUsage",
            ["email", "usage_count", "permitted_samples", "samples"]
        )
        rows = ws.get_all_records()
        email_lower = email.strip().lower()

        for i, row in enumerate(rows, start=2):  # row 1 is the header
            if str(row.get("email", "")).strip().lower() == email_lower:
                old_count = int(row.get("usage_count") or 0)
                old_samples = str(row.get("samples") or "").strip()
                old_samples_set = {s.strip().lower() for s in old_samples.split(",") if s.strip()}
                # Only bill samples not already recorded for this user -- reruns
                # of the same DOI/paper resolve to the same accessions, and
                # those must not be counted (and charged) a second time.
                seen_in_batch = set()
                net_new = []
                for s in new_samples:
                    key = s.strip().lower()
                    if key and key not in old_samples_set and key not in seen_in_batch:
                        net_new.append(s)
                        seen_in_batch.add(key)
                if not net_new:
                    return
                new_count = old_count + len(net_new)
                new_samples_str = ", ".join(net_new)
                combined = (old_samples + ", " + new_samples_str) if old_samples else new_samples_str
                ws.update_cell(i, 2, new_count)
                ws.update_cell(i, 4, combined)
                return

        # No existing row — create one
        deduped_new = list(dict.fromkeys(s.strip() for s in new_samples if s.strip()))
        ws.append_row([email.strip(), len(deduped_new), paid_limit, ", ".join(deduped_new)])
    except Exception:
        pass  # never crash the pipeline for tracking


def _get_config_from_gsheet() -> dict:
    """Read free_limit and paid_limit from the Config sheet in the Report workbook."""
    defaults = {"free_limit": 10, "paid_limit": 30}
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        raw = os.environ.get("GCP_CREDS_JSON", "")
        if not raw:
            return defaults
        creds_dict = json.loads(raw)
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        wb = client.open("Report")

        config_ws = _open_or_create_worksheet(
            wb, "Config",
            ["key", "value", "description"]
        )
        rows = config_ws.get_all_records()

        if not rows:
            config_ws.append_row(["free_limit", "10", "Max samples for guest (not signed-in) users"])
            config_ws.append_row(["paid_limit", "30", "Max samples for signed-in users"])
            return defaults

        result = dict(defaults)
        for row in rows:
            key = str(row.get("key", "")).strip()
            val = row.get("value", "")
            if key in ("free_limit", "paid_limit"):
                try:
                    result[key] = int(val)
                except (ValueError, TypeError):
                    pass
        return result
    except Exception:
        return defaults


@app.get("/config")
async def get_config():
    """Return sample limits. Admin can edit these in the Config sheet of the Report workbook."""
    cfg = await asyncio.to_thread(_get_config_from_gsheet)
    return cfg


@app.get("/user-config")
async def get_user_config(email: str = ""):
    """Return per-user permitted_samples, usage_count, and samples list.

    Signed-in users call this on login. Admin controls permitted_samples
    by editing the UserUsage sheet in the Report workbook.
    """
    if not email:
        return {"permitted_samples": 30, "usage_count": 0, "samples": []}
    data = await asyncio.to_thread(_get_user_config_from_gsheet, email)
    return data


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n"


@app.get("/sitemap.xml", response_class=PlainTextResponse)
def sitemap_xml(request: Request):
    base = str(request.base_url).rstrip("/")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'  <url><loc>{base}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>\n'
        "</urlset>\n"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
