import asyncio
import json
import os
import tempfile
from typing import Any, Dict, List, Optional

import uvicorn

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="BioMetadataAudit API")

MAX_SAMPLES = 50


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

    # ── UserLog sheet: detailed log for signed-in users only ──────────────────
    if email:
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
        user_log_ws.append_row([
            ts,
            email[:120],
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
        explanation_parts: list = []
        extra: dict = {}          # per-field explanation detail for Sheet 2

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
        row["explanation"]      = _tc("\n".join(explanation_parts))
        row["confidence_score"] = _tc(confidence_display)
        row["sources"]          = _tc(source_text)
        row["time_cost"]        = _tc(data.get("time_cost", ""))
        # Merge Pass 2 additional fields with per-field explanations for Sheet 2
        pass2_fields = data.get("_additional_fields") or {}
        row["_additional_fields"] = {**pass2_fields, **extra}
        rows.append(row)

    return rows


# ── request models ────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    bioproject_id: str
    metadata_fields: Optional[List[str]] = None
    standardization_url: Optional[str] = None  # comma-separated URLs accepted
    context_file_id: Optional[str] = None       # temp path from /upload-context


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


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    async def event_stream():
        try:
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

            # NCBI resolution (blocking — run in thread)
            yield _sse("progress", {"message": "Resolving accessions via NCBI…"})
            await asyncio.sleep(0)

            resolved_dict: dict = {}
            try:
                from input_handler import build_pipeline_input, get_pipeline_accession

                resolved_dict, skipped = await asyncio.to_thread(
                    build_pipeline_input, ", ".join(accessions), MAX_SAMPLES
                )
                if skipped:
                    invalid = list(invalid or []) + skipped
            except Exception as exc:
                yield _sse("progress", {"message": f"NCBI resolution warning: {exc}"})

            all_rows: list = []

            # Use the rich BioSample/SRA pipeline when we have resolved entries
            use_rich = bool(resolved_dict and any(
                entry.get("biosample") or entry.get("experiment")
                for entry in resolved_dict.values()
            ))

            if use_rich:
                if len(resolved_dict) > MAX_SAMPLES:
                    resolved_dict = dict(list(resolved_dict.items())[:MAX_SAMPLES])
                    yield _sse("progress", {
                        "message": f"Capped at {MAX_SAMPLES} samples for this run."
                    })

                total = len(resolved_dict)
                yield _sse("progress", {
                    "message": f"Fetching BioSample/BioProject/SRA metadata for {total} sample(s)…"
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

                    pipeline_result = await _rich_pipeline(
                        resolved_dict,
                        niche_cases=niche_cases,
                        other_links=std_urls or None,
                        standardization_urls=std_urls or None,
                        user_context_text=user_context_text,
                    )
                    # additional_pipeline returns (accs_output, source_texts, text)
                    accs_output = (
                        pipeline_result[0]
                        if isinstance(pipeline_result, tuple)
                        else pipeline_result
                    )
                    all_rows = _rows_from_new_pipeline(accs_output, niche_cases)
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

                if len(accessions) > MAX_SAMPLES:
                    accessions = accessions[:MAX_SAMPLES]
                    yield _sse("progress", {
                        "message": f"Capped at {MAX_SAMPLES} samples for this run."
                    })

                total = len(accessions)
                yield _sse("progress", {
                    "message": f"Running pipeline on {total} sample(s)…"
                })
                await asyncio.sleep(0)

                for i, acc in enumerate(accessions):
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
                except Exception as exc:
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

        except Exception as exc:
            import traceback, logging
            logging.error("Unhandled pipeline error: %s", traceback.format_exc())
            yield _sse("error", {"message": str(exc)})

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


@app.post("/report")
async def report(req: ReportRequest):
    try:
        await asyncio.to_thread(
            _log_to_gsheet, req.email or "", req.accession, req.message
        )
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
