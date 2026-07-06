# `call_llm_api()` model routing — Haiku / Sonnet 5 / Gemini

## What changed

`model.py`'s `call_llm_api()` no longer always calls `claude-haiku-4-5-20251001`. It now:

1. Estimates input size with `_estimate_tokens(prompt) = len(prompt) // 4` (rough, routing-only — not a billing estimate, per instruction not to over-engineer this).
2. If `estimated_input_tokens + 4096 <= 170,000` (real margin below Haiku's 200K hard context limit) → **`claude-haiku-4-5-20251001`**.
3. Else if `estimated_input_tokens + 4096 <= 1,000,000` (Sonnet 5's context window) → **`claude-sonnet-5`**.
4. Else (bigger than even Sonnet 5's window) → skip Anthropic entirely, go to Gemini.
5. Gemini is now reached only when: no Anthropic key is set, the context is too large even for Sonnet 5, or the Anthropic call itself raises a non-rate-limit error. Exceeding Haiku's limit alone no longer triggers Gemini — Sonnet 5 absorbs that case, per the request.
6. Every routing decision and every model actually used is logged: `[call_llm_api] routing: model=<...> estimated_input_tokens=<...>` and `[call_llm_api] used: model=<...> estimated_input_tokens=<...>`.

**Model ID:** used `claude-sonnet-5` (not `claude-sonnet-4-6`), per your correction.

**Pre-flight check (before wiring this in):** searched every Anthropic call site in the codebase (`model.py:call_llm_api`, `chat_input_parser.py:_llm_call_cheap`) for `thinking`/`budget_tokens`/`temperature`/`top_p`/`top_k`. Found none — both call sites only ever passed `model`, `max_tokens`, `messages`. Confirmed clean before adding Sonnet 5, so no manual thinking budget or non-default sampling parameters needed removing.

## Bug found and fixed during testing

The **first** large-context test run exposed a real bug that would have silently defeated the whole feature: Sonnet 5 has **adaptive thinking on by default**, so its response's `content` array starts with a `ThinkingBlock`, not a text block. The old code (`msg.content[0].text`) assumed `content[0]` is always text — true for Haiku (no default thinking), false for Sonnet 5. This raised `'ThinkingBlock' object has no attribute 'text'`, which `call_llm_api`'s exception handler caught as a generic (non-rate-limit) error and silently fell through to Gemini — meaning every real Sonnet 5 call would have been served by Gemini instead, without any error surfacing to the caller.

**Fix:** find the first block whose `type == "text"` instead of assuming position 0:
```python
text_block = next((b for b in msg.content if getattr(b, "type", None) == "text"), None)
```
This is a strict generalization, not a behavior change for Haiku (which already returns text at `content[0]`).

## Test results

### Small-context call → Haiku

- Prompt: `"Reply with exactly the word: OK"` (32 chars, estimated 7 tokens)
- Log: `[call_llm_api] routing: model=claude-haiku-4-5-20251001 estimated_input_tokens=7` → `[call_llm_api] used: model=claude-haiku-4-5-20251001 estimated_input_tokens=7`
- Response: `"OK"`
- Files: `test-data/PRJNA976261/test_output_model_routing_small_prompt.txt`, `test_output_model_routing_small_answer.txt`

### Large-context call → Sonnet 5

- Prompt: `test-data/PRJNA976261/SAMN35361964_prompt.txt` — 1,282,712 chars, estimated 320,678 tokens (well over the 170K Haiku threshold, well under Sonnet 5's 1M window)
- **First attempt** (before the fix): routed correctly to `claude-sonnet-5`, but crashed extracting the response text (`ThinkingBlock` bug above) and silently fell through to Gemini.
- **After the fix**: `[call_llm_api] routing: model=claude-sonnet-5 estimated_input_tokens=320678` → `[call_llm_api] used: model=claude-sonnet-5 estimated_input_tokens=320678`, returned `model_instance = None` (confirms Anthropic/Sonnet 5 served it — Gemini success would return a truthy model instance instead), and a real, coherent response (starts `"# Response to Captured Prompts for SAMN35361964..."`).
- Files: `test-data/PRJNA976261/test_output_model_routing_large_prompt.txt`, `test_output_model_routing_large_answer.txt`

## Confirmed

- ✅ Small context → Haiku
- ✅ Large context (>170K, <1M estimated tokens) → Sonnet 5, with a real response actually served by Sonnet 5 (not silently by Gemini)
- ✅ No `thinking`/`budget_tokens`/non-default sampling parameters anywhere in the Anthropic call path — confirmed by direct code inspection before and after the change
- ✅ Every call logs which model was routed to and which model actually served the response
