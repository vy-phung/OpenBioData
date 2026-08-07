# Fixing "Download Excel" 502 report

## Question

Task 2 — Diagnose 502 error on "Download Excel" after partial UI run

Repro: ran the same input in the UI, stopped after the 5th sample completed,
logged in, clicked "Download Excel" → got a 502 from the server, no file.

Investigate and report back (don't just patch blindly):
1. Where does "Download Excel" pull data from — an in-memory job state, a
   temp file, a DB row, or does it require the job to have reached "complete"?
2. Does logging in mid-run change the session/user context in a way that
   orphans the in-progress job (e.g. job was tied to an anonymous session,
   and login creates a new session id)?
3. Is 502 coming from the app itself, or a reverse proxy/gateway timing out
   while the backend is still generating the excel (long-running request)?
4. Is there any persistence of partial results (the 5 completed samples)
   before generation of the full excel, or is it all-or-nothing?
5. Propose a fix: e.g. persist per-sample results as they complete, decouple
   "download" from "must be 100% done", and/or increase gateway timeout for
   the excel-generation endpoint. Show the plan before implementing.

## Answer

### 1. Where "Download Excel" pulls data from

Two different paths, depending on how the run ended — client-side JS decides which (`index.html:1599`, `handleDownload()`):

- **Completed run:** the server builds the `.xlsx` exactly once, at the very end of the `/analyze` SSE stream (`api.py:1625-1638`), into a tempfile under `/tmp`, and sends its path down the stream. The frontend stores it in `currentExcelPath`. Clicking Download just does `GET /download-excel?path=...` and streams that file back.
- **Stopped run (this repro):** on `AbortError`, the frontend explicitly clears `currentExcelPath = ''` and instead holds onto `currentRows` — the rows that arrived via SSE `partial_result` events, in browser memory only. Clicking Download then calls `_generateAndDownload()`, which `POST`s those in-memory rows to `/generate-excel` (`api.py:1707`), which calls the *same* `save_to_excel()` to build a fresh `.xlsx` from just those rows, then serves it via the same `/download-excel`.

So: no DB row, no per-run persistent job record — an ephemeral server tempfile either way, sourced from either a finished pipeline's output or the browser's in-memory partial rows. **Nothing requires the job to be 100% done** — the code already has the on-demand branch specifically for this case, so this isn't a "must complete first" bug.

### 2. Does logging in mid-run orphan the job?

No — and it can't, structurally. There is **no session-to-job binding anywhere in this codebase.** A run is tracked purely by a random `run_id` UUID, server-side in `_ACTIVE_RUNS: Dict[str, asyncio.Event]` and client-side in `currentRunId` — neither is derived from or touches email/session. Login hits a completely separate, unrelated endpoint (`/user-config?email=...`, itself properly `asyncio.to_thread`-wrapped, just a Google Sheets quota lookup). The auth popup is also just an in-page modal — no navigation/reload — so `currentRows`/`currentExcelPath` survive it untouched. Logging in before downloading is only there because Download is gated behind sign-up (a product gate, not a job-tracking artifact); it's incidental timing in this repro, not a cause.

### 3. Is 502 the app or a gateway?

**Gateway, not the app.** `api.py` never constructs a 502 anywhere (only 400/403/404/500 `HTTPException`s exist), and uvicorn doesn't emit 502 for a slow-but-alive response. 502 is the signature of a fronting reverse proxy (Railway's edge, sitting in front of the single `uvicorn api:app` process per `Procfile`/`railway.toml` — or a Codespaces port-forward proxy in dev) giving up on an unresponsive upstream. This is the **same symptom class already diagnosed once on this branch** for the live-progress SSE stream (`fixing UI report.md`) — except `/generate-excel` is a plain non-streaming POST, so unlike `/analyze` there's no heartbeat mechanism possible for it at all. If the backend takes too long building the file, there is currently no way to keep that connection alive.

### 4. Is there persistence of partial results, or all-or-nothing?

Partial results are preserved, but **client-side only** (browser JS memory, filled incrementally per SSE `partial_result` event) — nothing server-side per-sample (no DB, no file checkpoint; unlike the checkpointing built for the standalone PRJEB14215 batch-run script, this production path has no equivalent). Given whatever row-list it's handed, file generation itself is one-shot/all-or-nothing — `save_to_excel()` builds the whole file in a single call, no incremental writing.

### 5. Root cause and fix plan

**Primary cause, found by tracing `save_to_excel()` → `metadata_merge.normalize_output_table()`:** this does an **O(n²) pairwise comparison across every column name** in the table (`_union_find_clusters`, `metadata_merge.py:254`), and for any pair not both covered by the static 23-entry `FIELD_ALIASES` table, it makes a **live, synchronous, one-at-a-time, uncached-on-first-use LLM API call** (`asyncio.run(field_name_matches(a, b))` per pair, `metadata_merge.py:484`). A real run showed 56-72 free-form "Full Raw Attributes" columns per sample — comfortably outside that 23-entry catalog — so even 5 samples can produce dozens of unique column names, i.e. dozens of sequential blocking LLM calls with zero concurrency and no time budget. This is the *same bug family* the team already fixed once for the per-sample `merge_metadata_into_table` path (see `fixing UI report.md`) — that fix explicitly noted this `normalize_output_table` call site as "already safe" only in the sense of *not freezing the whole server*; nobody checked whether it makes **the calling request itself** unacceptably slow, which is exactly what's happening here.

**Compounding factors:**
- Stop can't (and structurally can't — Python threads aren't forcibly killable) interrupt an in-flight sample's blocking work; the abandoned background thread from the stopped run may still be chewing CPU/memory/API budget for 1-2+ minutes after Stop, right when the download is attempted.
- `/generate-excel` has no streaming option, so there's no way to out-wait a slow proxy timeout the way `/analyze`'s heartbeats do.

**Proposed fix (plan only — not implemented):**
1. **Bound the LLM cost in `normalize_output_table`** — the real fix: batch all ambiguous column-name pairs into one LLM call per normalize pass instead of one round-trip per pair; skip pairs already resolved by exact-string match before the O(n²) walk; add a hard wall-clock budget (~15-20s) with fallback to "save unmerged" (the function already tolerates failure non-fatally — just needs a timeout, not just an exception handler).
2. **Make `/generate-excel` non-blocking from the client's perspective** (return a job id immediately, poll or stream for the result) — the durable structural fix, since it removes the proxy-timeout dependency entirely regardless of how long normalization takes.
3. *(Nice-to-have, not required to fix this bug)* persist partial per-sample results server-side too, so a lost/refreshed tab isn't the only thing standing between "5 samples done" and a working download.
4. Treat "raise the gateway timeout" as a mitigation only, not the fix — an unbounded O(n²) LLM-calling loop will eventually outgrow any timeout as sample/column counts grow.

Recommended order: (1) first — likely gets ordinary 5-20 sample downloads back under any reasonable proxy timeout without touching the request/response shape — then (2) as the real structural fix.
