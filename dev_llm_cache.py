"""Dev-only disk cache for LLM calls -- entirely separate from the
production KnownCachedSamples cache in api.py (_cache_get/_cache_save),
which this module never touches.

Inactive unless DEV_LLM_CACHE=1 is set in the environment; with it unset,
`dev_cache_wrap` is a pure pass-through and behavior is unchanged.

Cache key is a sha256 of the exact prompt text -- any change to the prompt
(new instruction, different schema, different context) produces a
different hash and a fresh call, with no manual cache-clearing needed.

Set DEV_LLM_CACHE_BYPASS=1 (alongside DEV_LLM_CACHE=1) to force a fresh
call even when a cached entry exists, e.g. to test non-determinism itself.
"""
import contextlib
import functools
import hashlib
import io
import json
import os
import re
import sys
import time

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dev_llm_cache")

# Matches the existing model-routing log line printed by call_llm_api() on a
# successful Anthropic call, e.g. "[call_llm_api] used: model=claude-haiku-4-5-20251001 ...".
_USED_MODEL_RE = re.compile(r"\[call_llm_api\] used: model=(\S+)")


class _Tee(io.TextIOBase):
    """Writes to multiple streams so wrapped calls still print live while we
    also capture their output to look up which model answered."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, s):
        for stream in self._streams:
            stream.write(s)
        return len(s)

    def flush(self):
        for stream in self._streams:
            stream.flush()


def _prompt_hash(prompt):
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


def _cache_path(prompt_hash):
    return os.path.join(CACHE_DIR, f"{prompt_hash}.json")


def dev_cache_wrap(func):
    """Decorator for call_llm_api(prompt, model_name=None) -> (text, model_instance).

    No-op unless DEV_LLM_CACHE=1. When active, caches on disk keyed by an
    exact hash of the prompt text.
    """

    @functools.wraps(func)
    def wrapper(prompt, model_name=None, *args, **kwargs):
        if os.environ.get("DEV_LLM_CACHE") != "1":
            return func(prompt, model_name, *args, **kwargs)

        prompt_hash = _prompt_hash(prompt)
        short_hash = prompt_hash[:12]
        cache_file = _cache_path(prompt_hash)
        bypass = os.environ.get("DEV_LLM_CACHE_BYPASS") == "1"

        if not bypass and os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            print(f"[dev-cache] HIT {short_hash} -- skipped real API call")
            return cached["response_text"], None

        buf = io.StringIO()
        with contextlib.redirect_stdout(_Tee(sys.stdout, buf)):
            response_text, model_instance = func(prompt, model_name, *args, **kwargs)

        match = _USED_MODEL_RE.search(buf.getvalue())
        if match:
            model_used = match.group(1)
        else:
            model_used = getattr(model_instance, "model_name", None) or "unknown"

        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({
                "prompt_hash": prompt_hash,
                "model_used": model_used,
                "response_text": response_text,
                "cached_at": time.time(),
            }, f, indent=2)

        print(f"[dev-cache] MISS {short_hash} -- cached new response")
        return response_text, model_instance

    return wrapper
