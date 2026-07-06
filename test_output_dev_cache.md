# Dev LLM cache — implementation & test report

## What was built

- **New file `dev_llm_cache.py`** — a standalone dev-only disk cache, completely
  separate from the production `_cache_get`/`_cache_save`/`_cache_reload` cache
  in `api.py` (backed by the `KnownCachedSamples` Google Sheet). Nothing in
  `api.py` was touched.
- **One-line wiring in `model.py`**: `call_llm_api` is decorated with
  `@dev_cache_wrap` (`model.py:129-131`, right after the existing
  `_estimate_tokens` helper). No other line of `call_llm_api`'s body changed.

## Behavior

- **Inactive by default.** `dev_cache_wrap` checks `os.environ.get("DEV_LLM_CACHE") != "1"`
  first and, if so, calls straight through to the real `call_llm_api` with no
  side effects — default (unset) behavior is exactly as before.
- **When `DEV_LLM_CACHE=1`:**
  - Hashes the exact prompt text with sha256.
  - Looks for `.dev_llm_cache/<hash>.json`.
  - **Hit**: prints `[dev-cache] HIT <hash prefix> -- skipped real API call` and
    returns the cached response text immediately (model_instance is returned as
    `None`, same as the existing Anthropic path already does — callers that
    check `if model_instance:` before doing token-cost accounting simply skip
    that on a cache hit, which is correct since no real call was made).
  - **Miss**: calls the real `call_llm_api` (output still prints live, via a
    tee'd stdout), then parses the existing model-routing log line
    (`[call_llm_api] used: model=...`) to determine which model actually
    answered (falls back to `model_instance.model_name` for the Gemini path,
    where that print doesn't fire). Saves `{prompt_hash, model_used,
    response_text, cached_at}` to the hash-named file and prints
    `[dev-cache] MISS <hash prefix> -- cached new response`.
- **`DEV_LLM_CACHE_BYPASS=1`** (alongside `DEV_LLM_CACHE=1`): skips the cache
  lookup entirely, always makes a fresh call, and overwrites the cache file
  with the new response — verified below.
- Any change to the prompt text (new instruction, different schema, different
  context) changes the hash automatically — no manual cache-clearing needed.
  Only a byte-for-byte identical prompt hits the cache.

## `.gitignore`

Added:
```
# ── Dev-only LLM call cache (local scratch, never committed) ──────────────────
.dev_llm_cache/
```

## Test performed

Ran the same prompt (`"Reply with exactly the single word: PONG"`) twice in
one process with `DEV_LLM_CACHE=1`, against the real Anthropic API (Haiku,
routed automatically by the existing model-routing logic):

```
=== RUN 1 ===
[call_llm_api] routing: model=claude-haiku-4-5-20251001 estimated_input_tokens=10
[call_llm_api] used: model=claude-haiku-4-5-20251001 estimated_input_tokens=10
[dev-cache] MISS 1f8539d2ba9c -- cached new response
RESPONSE 1: 'PONG'
=== RUN 2 ===
[dev-cache] HIT 1f8539d2ba9c -- skipped real API call
RESPONSE 2: 'PONG'
OK: identical responses
```

**Confirmed:**
- Run 1 logs `MISS` and shows the real model-routing lines
  (`routing: model=...` and `used: model=...`), i.e. the real API was called.
- Run 2 logs `HIT` and — critically — **no new `[call_llm_api] used: model=...`
  line appears** on the second run, confirming the real API was *not* called
  a second time.
- Both runs return the identical response text.

Cached file written to `.dev_llm_cache/1f8539d2ba9c...json`:
```json
{
  "prompt_hash": "1f8539d2ba9c04f2cb423b09c52b002f18b61ffc88b1b455969c8157fe8e2f96",
  "model_used": "claude-haiku-4-5-20251001",
  "response_text": "PONG",
  "cached_at": 1783315396.4694622
}
```

### Bypass escape hatch

With `DEV_LLM_CACHE=1` and `DEV_LLM_CACHE_BYPASS=1`, re-running the same
prompt (with the cache file from above already present) still shows the real
routing/used log lines and a fresh `MISS`-style cache write — confirming the
cache lookup was skipped and a live call was made despite an identical prompt
already being cached:

```
[call_llm_api] routing: model=claude-haiku-4-5-20251001 estimated_input_tokens=10
[call_llm_api] used: model=claude-haiku-4-5-20251001 estimated_input_tokens=10
[dev-cache] MISS 1f8539d2ba9c -- cached new response
RESPONSE (bypass): 'PONG'
```

### Default (unset) sanity check

With `DEV_LLM_CACHE` unset, `call_llm_api` still resolves and runs normally
(decorator is a transparent pass-through) — confirmed no import/wiring errors
and normal execution path.
