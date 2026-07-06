# Fixing UI report

## Question

why on the UI app: i input accession: PRJEB14215; user upload: /workspaces/OpenBioData/test-data/PRJEB14215/41591_2018_61_MOESM3_ESM.xlsx, and /workspaces/OpenBioData/test-data/PRJEB14215/s41591-018-0061-3.pdf. Predefined metadata: study_name, subject_id, sample_id, target_condition, control, body_site, sequencing_plaAtform, host_species. No schema input and then hit button submit & classify and on the UI this what i saw:

```
Live Progress
124s elapsed
Loading backend…
Loaded 2 context source(s) scoped to PRJEB14215 (context only, not run — also mentions: ERP015847, ERP27, ERP29, ERP44, PRJEB14215, PRJEB24891, SAMEA4019772, SAMEA4019774, SAMEA4019775, SAMEA4019776, … and 52 more)
Parsing accession input…
Resolving accessions via NCBI…
Processing 10 sample(s)…
[1/10] Resolving SAMEA4019843…
[1/10] Fetching NCBI data for SAMEA4019843…
[1/10] Fetching NCBI data for SAMEA4019843…
❌ Cannot reach the server — the backend may not be running.
   Please start the server with: python api.py
   Then refresh this page and try again.

Sources found
6 links
SAMEA4019843
[FILE] 41591_2018_61_MOESM3_ESM.xlsx, s41591-018-0061-3.pdf
[NCBI] https://doi.org/10.1186/s12866-025-04549-5
[NCBI] https://doi.org/10.1038/s41467-026-71981-0
[NCBI] https://doi.org/10.1002/ctm2.70053
[NCBI] https://doi.org/10.1186/s12866-026-04892-1
[NCBI] https://doi.org/10.1002/advs.202517658
```

But on the terminal, the server kept printing a lot of activity long after the UI gave up: fetching a Zenodo record page (which returned an HTML page instead of a file, so the download failed with "Publisher returned an HTML page instead of the file"), then successfully downloading and extracting text/tables from several Springer static-content supplementary PDFs/xlsx files (`41467_2026_71981_MOESM1_ESM.pdf`, `MOESM2_ESM.pdf`, `MOESM3_ESM.xlsx`), each producing PDFBox ICC-profile warnings, and finally attempting to extract tables from `s41467-026-71981-0.pdf` via tabula-java, which crashed with:

```
Exception in thread "main" java.lang.IllegalArgumentException: lines must be orthogonal, vertical and horizontal
	at technology.tabula.Ruling.intersectionPoint(Ruling.java:214)
	...
No tables found in PDF file
```

So all of this just kept running server-side and never showed up on the UI — the UI had already stopped with the "Cannot reach the server" error. The user asked: **what happens here, and how can I really fix it so it won't happen again, not just for this accession but for any input?**

(Follow-up: user asked for an explanation and a written plan only — no code changes yet.)

## Answer

### Why this happens

The backend (`api.py`) uses a single-threaded `asyncio` event loop (via `uvicorn`). The `/analyze` endpoint is a Server-Sent-Events (SSE) stream: the browser holds one HTTP connection open and expects a steady trickle of `data: ...` lines (progress messages) to keep it alive.

The per-sample pipeline (`pipeline_with_gemini` in `additional_pipeline.py`) is scheduled directly onto that **same event loop**:

```python
pipeline_task = asyncio.ensure_future(_rich_pipeline(...))   # api.py:1423
```

Inside that pipeline, most of the actual work is done with plain **blocking** calls, not `await`-ed thread-offloaded calls:

- `NCBI.extract_NCBI_directly(acc)` — synchronous HTTP requests to NCBI (`additional_pipeline.py:444` etc.)
- downloading supplementary files (Zenodo, Springer static-content, nature.com PDFs) — synchronous `requests.get`
- `subprocess.run(...)` calling `tabula-java` to pull tables out of PDFs (`data_preprocess.py:183`) — this spawns a JVM and blocks until it exits, which from the log took many seconds per PDF, including failures/retries

Because these are ordinary blocking calls, not `await asyncio.to_thread(...)`, they don't yield control back to the event loop. **The entire process — including the code responsible for flushing SSE progress bytes to the browser — freezes for the whole duration of each blocking call.**

While the loop is frozen, zero bytes go out over the SSE connection. After roughly 120s of dead air, the browser (or an intervening proxy) decides the connection is dead and kills it. The frontend's `fetch` then throws a `TypeError`, which `index.html`'s catch block (line ~1620) reports as:

```
❌ Cannot reach the server — the backend may not be running.
```

...even though the Python process is completely alive and still grinding away in the terminal (which is exactly what was observed: the UI gave up while the terminal kept printing tabula/Zenodo/Springer output for a while longer).

**Confirmation this is a known failure mode, not a guess:** there's already a helper in `api.py` (`_thread_with_heartbeat`, lines 37-59) whose docstring literally describes this exact bug and fixes it — by running one specific blocking step (paper resolution) via `asyncio.to_thread` and emitting a heartbeat every 10s. That fix was applied to *one* code path but not to the main per-sample pipeline (NCBI fetch + supplementary-file download + tabula extraction), which is the one actually doing the multi-minute blocking work in this case.

A secondary effect: because the main loop is frozen too, the "Stop" button/cancel check (`cancel_event.is_set()`) also can't be evaluated until a blocking call finishes — so today, Stop doesn't actually interrupt a long blocking step either.

### Why it's not accession-specific

Nothing above is tied to PRJEB14215. Any input that triggers a slow blocking segment — a big NCBI query, a slow-responding publisher/Zenodo/Springer host, a PDF that makes tabula-java struggle, a supplementary file that takes a while to download — will reproduce the same "Cannot reach the server" once that segment runs longer than the browser/proxy's idle tolerance (~2 min). The 10-sample PRJEB14215 run just happened to hit several of these back-to-back (multiple linked papers, several PDFs, a large xlsx table).

### Plan to fix it generally

Run the actual pipeline execution off the main event loop, on a worker thread with its own event loop, so blocking NCBI/download/tabula calls no longer freeze the SSE stream:

1. Swap the `asyncio.Queue` used for progress messages (`_progress_q`, `api.py:1417`) for a plain thread-safe `queue.Queue` — an `asyncio.Queue` isn't safe to `put`/`get` across threads.
2. Instead of `asyncio.ensure_future(_rich_pipeline(...))`, run it as `asyncio.to_thread(lambda: asyncio.run(_rich_pipeline(...)))` — giving the pipeline its own event loop on a separate OS thread.
3. Update the drain loop that currently does `await asyncio.wait_for(_progress_q.get(), timeout=0.3)` to poll the thread-safe queue via `asyncio.to_thread(_progress_q.get, True, 0.3)` / catch `queue.Empty`, so the main loop keeps flushing SSE bytes continuously regardless of what the worker thread is blocked on.
4. Leave `cancel_event` as-is (an `asyncio.Event`) — only `.is_set()` is read cross-thread, which is already safe; `.set()` stays on the main thread.

This is a localized, contained change (`api.py` only) rather than hunting down and converting every blocking call scattered across `NCBI.py`, `data_preprocess.py`, `paper_resolver.py`, etc. — same pattern already proven by `_thread_with_heartbeat`, just applied to the main pipeline instead of one sub-step.

One caveat: cancelling the wrapping asyncio Task won't kill an in-progress blocking call inside the worker thread (Python threads can't be force-stopped) — an already-running tabula/download call will finish on its own in the background even after Stop/timeout. That's a pre-existing limitation, not something this fix makes worse; the fix's real win is that the UI stream itself stays alive and responsive instead of appearing to die.

## Follow-up: re-test after the thread-isolation fix (2026-07-06)

### Question

Re-tested the SSE fix on `fix/sse-blocking-freeze` by running `python api.py` fresh and submitting PRJEB14215 through the browser UI. The backend terminal showed the pipeline completed successfully for SAMEA4019843 (full extraction result, all fields populated). But the browser still showed "Cannot reach the server" at ~116s elapsed — same failure as before the fix. Asked for a diagnosis (no code changes) covering: whether the running server was actually on the current code; whether this is a backend problem or a Codespaces port-forwarding/proxy problem (via direct `curl -N` against `/analyze`); if backend, which call is still blocking; and a report before any further changes.

### Answer

**1. Fix is present in the working tree.** `git diff api.py` confirmed the thread-isolation changes were sitting uncommitted (worker-thread pipeline via `asyncio.run(_rich_pipeline(...))` inside `asyncio.to_thread`, `queue.Queue` instead of `asyncio.Queue`), matching the plan above. No `python api.py` process was actually running at the time of the check, so a fresh instance was started from this exact working tree to test against.

**2. Not a Codespaces/proxy problem — curl sees the same silence the browser does.** Running `curl -N` against `/analyze` for PRJEB14215 with timestamps on every line showed the full run completing successfully in 5m27s (final `partial_result` had all fields populated, matching the backend terminal). But the byte stream itself has long dead patches with **zero bytes sent**, e.g. ~170 seconds of total silence between the first `links_update` and the "supplementary" `links_update` (40.8s → 210.6s elapsed). The reported browser failure at ~116s elapsed falls right in the middle of that gap. curl has no idle-read timeout by default so it just waits it out; a browser's `fetch`/`EventSource`, or the Codespaces port-forwarding proxy in front of it, almost certainly does have an idle timeout in the 60–120s range — so it kills the connection during that silence while the backend, unaware anyone hung up, keeps working and finishes fine.

**3. Root cause: the thread-isolation fix solved blocking, but never added heartbeats to the main pipeline stream.** The event loop is no longer frozen (that part works), but the SSE loop at `api.py:1497-1517` only yields bytes when the pipeline calls `progress_cb` — during literature search / supplementary-link scraping / LLM inference, no progress message fires for 1–3 minutes, so the generator just spins `queue.Empty → asyncio.sleep(0)` silently, emitting nothing. There's already a `_thread_with_heartbeat` helper (`api.py:38-60`) whose docstring literally describes this exact "Cannot reach the server" failure mode — but it's wired up only for one sub-step (resolving a paper link, line 1087), not for the main pipeline task, which is the one now going silent for minutes at a time.

The blocking NCBI calls in `additional_pipeline.py` (`NCBI.extract_NCBI_directly(acc)` at line 444 and `NCBI.extract_NCBI_directly(bioproject_id)` at line 458) are unwrapped, but that's no longer a problem — the whole pipeline already runs on its own worker thread, so they don't block the main event loop.

**Aside, not the bug:** "Fetching NCBI data for SAMEA4019843" printing twice isn't a retry — `additional_pipeline.py` emits that exact progress string once unconditionally at line 391, then again at line 437 in the non-`_is_non_ncbi` branch. Harmless duplicate, unrelated to the freeze.

**Bottom line:** same underlying bug family as before ("no bytes flow during a long silent stage"), not a new proxy issue — but a different manifestation than what was already fixed. The remaining fix needs to extend heartbeat coverage from just the paper-resolving step to the entire pipeline-running loop (`api.py:1497`), using the same `_thread_with_heartbeat` pattern already proven elsewhere.

## Follow-up: heartbeat fix implemented + verified, second blocking call found (2026-07-06)

### Question

Implement the heartbeat fix: extend heartbeat coverage from just the paper-resolution sub-step to the entire pipeline-running loop at `api.py:1497-1517`, using the same `_thread_with_heartbeat` pattern already proven at line 1087. Specifics requested: (1) emit a heartbeat at a fixed 10s interval while the pipeline runs with no real `progress_cb` message; (2) make heartbeats clearly distinguishable from real progress — either a proper SSE comment/keep-alive line rather than a fake `data:` progress message, or a clearly-prefixed data line; (3) don't touch the already-working thread-isolation change, purely additive. Verification requested before declaring done: re-run `curl -N` with timestamps and confirm no gap >~15s anywhere in the stream (including the previously-silent 40.8s–210.6s window); then re-test through the actual browser UI and confirm no "Cannot reach the server"; then test a fast/simple accession to confirm heartbeats don't spam when real progress is already frequent. Report back before merging.

### Answer

**Implemented.** Added `import time` to `api.py`, and in the main streaming loop (`api.py:1497-1517`):
- `_last_activity = time.monotonic()`, reset every time a real SSE event is yielded.
- On each `queue.Empty` poll (the 0.3s `_progress_q.get` timeout), if `time.monotonic() - _last_activity >= 10.0`, yield a raw SSE comment line `": heartbeat\n\n"` and reset `_last_activity`.
- Used a bare SSE comment (not a `data:` line) specifically so the frontend's `line.startsWith('data: ')` filter in `index.html` silently ignores it — no frontend changes needed, and heartbeats will never appear in the Live Progress log by construction.
- Did not touch the thread-isolation code (worker-thread pipeline, `queue.Queue`) from the prior fix — purely additive.

**Verification, part 1 — curl, target gap closed.** Re-ran `curl -N` against `/analyze` for PRJEB14215 with per-line timestamps. The previously-silent 40.8s→210.6s window (~170s of zero bytes) is now fully closed: heartbeats land every ~10.0–10.3s throughout that stretch. Max gap in that specific window dropped from ~170s to ~10.3s.

**Verification, part 2 — a second, separate blocking call was found, still open.** Across three repeated curl runs, a different gap persisted every time: between the `"✓ ... done (Ns)"` progress message and the following `partial_result` event — 79s, 172s, and 73s respectively — with **zero heartbeats** during it (proof the event loop itself was frozen, not just "no progress to report," since a frozen loop can't run the heartbeat check either). Confirmed the cause with a live `py-spy dump` of the running server process mid-gap (had to install `py-spy` and use `sudo` for ptrace access — read-only diagnostic, no state changed). The **main thread** (not the pipeline's worker thread) was blocked in:

```
event_stream (api.py:1525)          ← the SSE generator loop just patched
_emit_queue_item (api.py:1477)
_rows_from_new_pipeline (api.py:511)
merge_metadata_into_table (metadata_merge.py:134)
field_name_matches (field_aliases.py:186)
_llm_field_name_match (field_aliases.py:141)
call_llm_api (model.py:148)
→ anthropic SDK → recv() on the SSL socket
```

`_rows_from_new_pipeline` is called synchronously, directly on the main event loop, at the moment a `partial_result` row is built. It calls `merge_metadata_into_table`, which loops over every existing table key and calls `field_name_matches()` for each one; when names don't match by string rules, it falls through to a real, synchronous, **unwrapped** Claude API call (`model.py:148`) asking an LLM whether two field names mean the same thing. That call (or several in sequence) blocks the whole process for up to 172s. Same bug family as the original diagnosis (blocking I/O not wrapped in `asyncio.to_thread`), just in a different, previously-unaudited code path — the row-building step on the API side, not the pipeline itself.

**Net effect / not merged yet:** the heartbeat fix is correct and complete for the scope requested, but PRJEB14215 would still very likely show "Cannot reach the server" in the actual browser test, just later (~190–380s instead of ~116s), because of this second stall. Did not fix this without review, per instructions not to expand scope silently. Stopped here to report back; browser UI re-test and the fast/simple-accession heartbeat-spam check were not yet run since they'd be moot until this second call is addressed. Proposed next step: wrap `_rows_from_new_pipeline` (or specifically just the LLM field-matching call inside it) in `asyncio.to_thread`, then re-verify all three items in the original verification checklist.

## Follow-up: narrow async fix for the field-matching LLM call + codebase audit (2026-07-06)

### Question

Approved, scoped exactly as: (1) wrap ONLY the LLM call inside `field_name_matches`/`_llm_field_name_match` (`model.py:148`, the `call_llm_api` invocation) in `asyncio.to_thread` — not the whole `_rows_from_new_pipeline` function; (2) before implementing, check `merge_metadata_into_table`'s per-key loop for whether the LLM calls could safely run concurrently via `asyncio.gather`, and only do that if there's no ordering/side-effect risk — otherwise sequential `to_thread` calls, noted as a follow-up; (3) audit the codebase for any other synchronous `call_llm_api`/anthropic SDK call sites on request-handling paths, so a third landmine isn't discovered later. Verification requested: re-run the curl gap check for PRJEB14215 (capped at 3 samples to limit LLM cost), an actual browser UI re-test, and a fast/simple-accession re-test for heartbeat-spam/latency regression — with before/after max-gap numbers and the audit results reported back here. Still not to be merged without review.

### Answer

**Concurrency check (item 2): not safe, kept sequential.** `merge_metadata_into_table`'s loop (`metadata_merge.py:134`, now line ~138) is `for candidate_key in table.keys(): if field_name_matches(candidate_key, new_key): existing_key = candidate_key; break` — it short-circuits on the **first** match in table-iteration order. Running all candidates concurrently via `asyncio.gather` would fire extra LLM calls past the first match on every single row (since you can't know a candidate is unneeded until you've already checked it) — pure added cost and latency with no behavior change, since only the first match is ever kept. Kept it sequential, `await`-ing each `field_name_matches(...)` call in turn, with a comment in the code explaining why. Noted as a possible future optimization, not done now.

**Implementation (item 1).** Changed only the minimum needed to move the actual `call_llm_api` invocation off the main event loop, without touching its two other call sites that were already safe:
- `field_aliases.py`: `_llm_field_name_match` and `field_name_matches` are now `async def`; the blocking line became `response_text, _ = await asyncio.to_thread(call_llm_api, prompt)`.
- `metadata_merge.py`: `merge_metadata_into_table` is now `async def`; its loop now does `await field_name_matches(...)`.
- Because `field_name_matches` is also called directly (not through `merge_metadata_into_table`) by `_normalize_output_table_impl` (metadata_merge.py, used by `mtdna_backend.save_to_excel`, which already runs on its own worker thread via `asyncio.to_thread(save_to_excel, ...)` with no event loop of its own) — added a tiny local sync bridge there (`asyncio.run(field_name_matches(a, b))`) so that already-safe, already-isolated path keeps working unchanged.
- `additional_pipeline.py:1298`'s call to `merge_metadata_into_table` (inside the already-thread-isolated `pipeline_with_gemini`) became `await metadata_merge.merge_metadata_into_table(...)` — trivial, since that function was already `async def` running in its own dedicated event loop.
- `api.py`: `_rows_from_new_pipeline` is now `async def`; its one internal call to `merge_metadata_into_table` (line ~511) is now awaited; both its callers (`_emit_queue_item` at line ~1477, and the fallback aggregation branch at line ~1562) now `await` it — both were already inside `async def`/async-generator scopes, so this was a straightforward call-site change, not a restructure.
- `run_normalize_table_test.py` (standalone dev script) needed one `await` added at its `_rows_from_new_pipeline(...)` call, since its `main()` was already `async def` run via `asyncio.run(main())`.

**Audit (item 3): two more request-path blocking LLM calls found, not fixed.**
1. **Legacy/fallback pipeline.** `api.py`'s `/analyze` fallback branch (only reached if the "rich" pipeline throws and `use_rich` flips to `False`) does `rows = await summarize_results(acc, niche_cases=niche_cases)` directly on the main event loop. `summarize_results` (`mtdna_backend.py:195`) calls `pipeline.pipeline_with_gemini(...)` (`mtdna_backend.py:37`) — a completely different, older pipeline module (`pipeline.py`, not `additional_pipeline.py`) that is **not** thread-isolated and makes its own direct `call_llm_api`/network calls throughout (e.g. via `model.query_document_info`, called from `pipeline.py:614`). Not exercised in the PRJEB14215 tests (the rich pipeline succeeded every time), but it's a live landmine if the rich pipeline ever errors out for some other accession.
2. **`/chat-message` endpoint.** `api.py`'s `chat_message` handler calls `process_chat_message(msg, req.state)` synchronously, no `await`/`to_thread`. That eventually reaches `chat_input_parser.py:244`'s `_llm_call_cheap`, which makes a direct, synchronous `anthropic.Anthropic().messages.create()` call. Since this isn't an SSE endpoint, it won't produce "Cannot reach the server" — but because it's not offloaded, it blocks FastAPI's **entire** single-threaded event loop for the duration of that call, meaning every other concurrent request (including any other user's in-flight `/analyze` SSE stream) stalls too. Worth fixing at some point for multi-user correctness, separate from this ticket's scope.

No other direct `call_llm_api`/anthropic/genai call sites were found on request-handling code paths outside these two and the one just fixed.

**Verification.**

1. **curl, PRJEB14215, capped at 3 samples.** Restarted the server fresh (killed the old process, confirmed the new one started clean from the current working tree) and ran `curl -N` with per-line timestamps. Gaps over 12s across the whole run:

   | Gap | Location |
   |---|---|
   | 37.5s | "Loading backend…" → "Parsing accession input…" (pre-existing startup gap, before the pipeline-running loop even starts — out of scope for both fixes so far, flagging for awareness) |
   | 32.6s | sample 1: "done" → `partial_result` (row-building/field-matching stage) |
   | 63.6s | sample 2: "done" → `partial_result` (same stage) |
   | 28.0s | final aggregation → `result` event |

   This is a real, large improvement — the same stage was **up to 172s** of total silence before this fix, now caps at 63.6s across two samples in the same run. But it does **not** fully meet the "<15s everywhere" bar. Reason: `asyncio.to_thread` correctly stops the LLM call from freezing the *whole process* (confirmed — the heartbeat loop kept firing normally during the earlier "literature search" stage in the very same run, proving the event loop itself stayed responsive), but the heartbeat mechanism added in the previous fix only lives in the main polling loop's `except queue.Empty` branch (`api.py` main `while` loop). `_emit_queue_item`'s handling of a `__partial_acc__` message now does `await _rows_from_new_pipeline(...)` — and while that await is in flight (now safely threaded, but still sequential across however many novel field-name pairs need an LLM call), the outer loop is sitting inside that single `async for evt in _emit_queue_item(msg): yield evt` step, never reaching the heartbeat-check branch. So this specific sub-stage can still go quiet for as long as its sequential LLM calls take — no longer freezing the *server*, but still producing a silent stretch on *this request's own SSE bytes*. Also worth noting: some of the improvement here is likely inflated by the in-process `_LLM_MATCH_CACHE` warming up across repeated field names in this same test session (a live caveat, not a clean isolated A/B).

2. **Fast/simple accession (KJ442651, single GenBank record, sample_limit=1).** Completed in 1m43s wall time. Heartbeats fired only during the ~63s "Searching literature" stage (6 heartbeats, ~10s apart) — none anywhere else, confirming no spam when real progress messages are already frequent. Same residual pattern showed up here too, smaller: a 21s silent gap between "done" and `partial_result`, consistent with the mechanism above. No sign of added latency from the async conversion itself (the time is dominated by the LLM/network calls that were always happening; they're just non-blocking to the rest of the server now).

3. **Browser UI re-test: not performed by me.** I don't have a browser-automation tool available in this environment (checked; only a non-interactive `WebFetch` exists, which can't drive a live SSE page or reproduce a real browser's connection-idle behavior). The server is left running (current working tree, both fixes applied, port 8000) for you to test directly. Given the max remaining gap (63.6s) is well under the ~116s that triggered the original failure, I'd expect PRJEB14215 to complete in-browser now, but I haven't confirmed it and don't want to claim it.

**Bottom line:** the narrow fix works exactly as scoped — the specific LLM call is off the main thread, `merge_metadata_into_table`'s ordering/cost behavior is unchanged, and the two other already-safe call sites (`additional_pipeline.py`, `_normalize_output_table_impl`) keep working without modification. Two more not-yet-fixed blocking call sites were found and are documented above (legacy fallback pipeline, `/chat-message`). One residual gap remains in the row-building stage (up to 63.6s, down from 172s) because the existing heartbeat only covers the outer polling loop, not this specific inner await — worth a small follow-up (e.g. wrapping the `_rows_from_new_pipeline` await with the same `_thread_with_heartbeat` pattern) if you want the byte-stream itself gap-free rather than just server-freeze-free. Still not merged — diff is ready for your review.
