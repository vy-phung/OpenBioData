import asyncio
import json
import os
import tempfile
import uuid
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
        _source_tag_re = _re.compile(r'\[Source:\s*([^\]]+)\]', _re.IGNORECASE)

        explanation_parts: list = []
        extra: dict = {}          # per-field explanation detail for Sheet 2
        method_text = ""          # initialized here so it's always defined even when niche_list is empty
        field = None

        for field in niche_list:
            field_data = data.get(field, {}) or {}
            if isinstance(field_data, dict) and field_data:
                answers  = [k for k in field_data if k]
                methods: list = []
                for ans_methods in field_data.values():
                    if isinstance(ans_methods, list):
                        methods.extend(ans_methods)
                value       = _tc("\n".join(answers) or "unknown")
                method_text = _tc("\n".join(methods))
            else:
                value       = "unknown"
                method_text = ""

            row[field] = value

            if value.lower() == "unknown":
                explanation_parts.append(
                    f"[{field}] not found in available sources"
                )
            elif method_text:
                # Strip method prefix (e.g. "rag_llm-") for cleaner display
                display_method = method_text
                if "-" in display_method[:20]:
                    display_method = display_method.split("-", 1)[-1].strip()
                # Trim to one sentence
                if ". " in display_method:
                    display_method = display_method.split(". ")[0] + "."
                explanation_parts.append(f"[{field}] {value} — {display_method}")
                extra[f"{field}_explanation"] = method_text
                # Extract [Source: source_name, specific_location] tag per field
                _src_match = _source_tag_re.search(method_text)
                if _src_match:
                    extra[f"{field}_source_location"] = _src_match.group(1).strip()
            else:
                explanation_parts.append(f"[{field}] {value}")

        # ── Source links appended at end of explanation ───────────────────────
        source_list = data.get("source", []) or []
        source_text = "\n".join(source_list) if source_list else "No external links"
        if explanation_parts:
            explanation_parts.append(f"\nSources: {source_text}")

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

        # ── Final columns ─────────────────────────────────────────────────────
        # Build per-field source location column: shows value + exactly where it came from
        per_field_source_lines = []
        for field in niche_list:
            field_val = str(row.get(field, ""))
            if field_val.lower() in ("unknown", ""):
                continue
            loc_key = f"{field}_source_location"
            if loc_key in extra:
                per_field_source_lines.append(f"{field} = {field_val!r}  ← [{extra[loc_key]}]")
            else:
                # No [Source:] tag parsed — fall back to listing all source keys
                per_field_source_lines.append(f"{field} = {field_val!r}  ← see sources")
        if not per_field_source_lines:
            per_field_source_lines_text = source_text
        else:
            # Append the full source list at the end so URLs are visible
            per_field_source_lines_text = "\n".join(per_field_source_lines) + "\n\nAll sources:\n" + source_text

        row["explanation"]      = _tc("\n".join(explanation_parts))
        row["confidence_score"] = _tc(confidence_display)
        row["sources"]          = _tc(per_field_source_lines_text)
        row["time_cost"]        = _tc(data.get("time_cost", ""))
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

        if not niche_list and not ontology_annots:
            # No user-specified fields: promote all Pass 2 extracted fields directly
            # into Sheet 1 columns so the user sees them immediately.
            # Sheet 2 will be identical to Sheet 1 (no separate extra columns needed).
            for k, v in pass2_fields.items():
                if k not in row:
                    row[k] = _tc(v)
            row["_additional_fields"] = {}
        elif not niche_list and ontology_annots:
            # Ontology mode: ontology columns already added above; extras go to Sheet 2
            row["_additional_fields"] = pass2_fields
        else:
            # User specified fields: niche fields are Sheet 1; Pass 2 extras go to Sheet 2
            row["_additional_fields"] = {**pass2_fields, **extra}
        rows.append(row)

    return rows


# ── request models ────────────────────────────────────────────────────────────

class NonNcbiInfo(BaseModel):
    database: Optional[str] = ""          # e.g. "MassIVE", "PRIDE", user-provided
    is_project: Optional[bool] = False
    dataset_files_url: Optional[str] = "" # URL to dataset files page for sub-sample scraping


class AnalyzeRequest(BaseModel):
    bioproject_id: str
    metadata_fields: Optional[List[str]] = None
    standardization_url: Optional[str] = None   # comma-separated URLs accepted
    context_file_id: Optional[str] = None        # temp path from /upload-context
    context_file_name: Optional[str] = None      # original filename of uploaded context file
    sample_limit: Optional[int] = None           # max samples to process this run
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

ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".csv", ".tsv", ".pdf", ".xlsx", ".xls", ".json", ".xml"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


def _extract_text_from_upload(file_bytes: bytes, filename: str) -> str:
    """Read uploaded file bytes and return plain text for the LLM context."""
    ext = os.path.splitext(filename.lower())[1]

    if ext in (".txt", ".csv", ".tsv", ".json", ".xml"):
        try:
            return file_bytes.decode("utf-8", errors="replace")
        except Exception:
            return file_bytes.decode("latin-1", errors="replace")

    if ext == ".pdf":
        try:
            import io
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            pages = [p.extract_text() or "" for p in reader.pages]
            return "\n".join(pages)
        except Exception as exc:
            return f"[PDF text extraction failed: {exc}]"

    if ext in (".xlsx", ".xls"):
        try:
            import io
            import pandas as pd
            df = pd.read_excel(io.BytesIO(file_bytes))
            return df.to_csv(index=False)
        except Exception as exc:
            return f"[Excel text extraction failed: {exc}]"

    return file_bytes.decode("utf-8", errors="replace")


@app.post("/upload-context")
async def upload_context(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}",
        )

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB).")

    text = _extract_text_from_upload(raw, file.filename or "upload")

    tmp_dir = tempfile.mkdtemp()
    ctx_path = os.path.join(tmp_dir, "user_context.txt")
    with open(ctx_path, "w", encoding="utf-8") as fh:
        fh.write(text)

    return {"context_file_id": ctx_path, "filename": file.filename, "chars": len(text)}


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

            # Load user-uploaded context file if provided
            user_context_text: Optional[str] = None
            if req.context_file_id:
                ctx_real = os.path.realpath(req.context_file_id)
                if ctx_real.startswith("/tmp") and os.path.isfile(ctx_real):
                    try:
                        with open(ctx_real, "r", encoding="utf-8") as _fh:
                            user_context_text = _fh.read()
                        yield _sse("progress", {"message": "User context file loaded."})
                    except Exception as _exc:
                        yield _sse("progress", {"message": f"Context file read warning: {_exc}"})

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

            if non_ncbi_entries:
                db_names = ", ".join(
                    e.get("_source_database", "unknown")
                    for e in non_ncbi_entries.values()
                )
                yield _sse("progress", {
                    "message": f"Added {len(non_ncbi_entries)} non-NCBI sample(s) ({db_names}) — will search web for metadata."
                })

            all_rows: list = []

            # Use the rich pipeline for all resolved entries (BioSample, SRA,
            # GenBank-only, or non-NCBI). GenBank accessions without a BioSample
            # link still benefit from rich pipeline's web-search fallback.
            use_rich = bool(resolved_dict and any(
                entry.get("biosample") or entry.get("experiment")
                or entry.get("_source_database") or entry.get("accession")
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
                            other_links=std_urls or None,
                            standardization_urls=std_urls or None,
                            user_context_text=user_context_text,
                            progress_cb=_pipe_progress,
                            cancel_event=cancel_event,
                            user_file_label=req.context_file_name or None,
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
                        elif isinstance(msg, dict) and "__partial_acc__" in msg:
                            partial_rows = _rows_from_new_pipeline(
                                msg["__partial_data__"], _effective_niche or None
                            )
                            _samples_done += len(partial_rows)
                            yield _sse("partial_result", {"rows": partial_rows})
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
                        all_rows = _rows_from_new_pipeline(accs_output, _effective_niche or None)
                        yield _sse("progress", {
                            "message": f"✅ Extracted metadata for {len(all_rows)} sample(s)"
                        })

                except Exception as exc:
                    yield _sse("progress", {
                        "message": f"Rich pipeline error ({exc}); falling back to legacy…"
                    })
                    use_rich = False

            if not use_rich:
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
                new_count = old_count + len(new_samples)
                new_samples_str = ", ".join(new_samples)
                combined = (old_samples + ", " + new_samples_str) if old_samples else new_samples_str
                ws.update_cell(i, 2, new_count)
                ws.update_cell(i, 4, combined)
                return

        # No existing row — create one
        ws.append_row([email.strip(), len(new_samples), paid_limit, ", ".join(new_samples)])
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
