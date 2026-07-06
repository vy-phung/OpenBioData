# Cache investigation — why reruns don't seem to hit `KnownCachedSamples`

Investigation only, per instruction — nothing below has been fixed yet.

## Where the cache lives

`_cache_get()` / `_cache_save()` / `_cache_reload()` are defined in `api.py:103-210`, backed by a Google Sheet named `KnownCachedSamples` (opened inside the `Report` spreadsheet, `api.py:111-122`). There is exactly one caller site for each, both inside the single `/analyze` SSE generator (`api.py:916`):

- **Read**: `api.py:1344-1367`, inside `if niche_cases and os.environ.get("GCP_CREDS_JSON"):`
- **Write**: `api.py:1457-1466`, inside the per-sample `partial_result` handler, gated only on `if _cid and os.environ.get("GCP_CREDS_JSON"):` (no `niche_cases` check on the write side)

## 1. What is the cache key built from?

`(sample_id, bioproject)` — nothing else. No schema URL, no session ID, no user ID, no timestamp. `requested_fields` is **not** part of the key tuple, but it does gate validity (see below), so a changed field list between "identical-looking" reruns still defeats the cache functionally, just not via the key itself.

**Read side** (`api.py:1346-1349`):
```python
_ca_id = (_ce.get("biosample") or _ce.get("accession")
          or _ce.get("experiment") or _ca)
_ca_bp = _ce.get("bioproject") or (req.accession_id or "")
```

**Write side** (`api.py:1459-1462`):
```python
_cid = (_cr.get("biosample_accession")
        or _cr.get("sra_accession")
        or _cr.get("genbank_accession") or "")
_cbp = _cr.get("bioproject") or ""
```

### Bug found: bioproject fallback asymmetry (confirmed, active)

When the resolved entry has **no bioproject** (realistic for a standalone SRA/GenBank accession entered directly, with no discoverable project link — `ncbi_resolver.py:869` shows `resolve_from_sra` defaults `'bioproject'` to whatever `known_bioproject` was passed in, which is `''` when none was supplied):

- Read side falls back to **`req.accession_id`** — the raw text the user typed into the input box.
- Write side falls back to a **hardcoded `""`**.

So such a sample is always *saved* under `(id, "")` but always *looked up* under `(id, "<raw input text>")`. Unless the user's input string happens to be empty (never, in practice), these two keys never coincide — the cache is written every run but can never be read back for this whole class of accessions (anything that resolves without a bioproject). This reproduces on the very next rerun of the exact same input, which matches the symptom described.

### Identifier-priority note (fragile, not currently proven to misfire)

The read-side OR-chain priority (`biosample` → `accession`/genbank → `experiment`/SRA → dict key) differs in order from the write-side OR-chain (`biosample_accession` → `sra_accession` → `genbank_accession`). In isolation this looks like a second bug, but `_rows_from_new_pipeline()` (`api.py:319`) sets `biosample_accession = acc_info.get("biosample") or sample_id`, and `sample_id` is the same dict key (`_ca`) the read side falls back to last. In every case actually observed in this codebase's `ncbi_resolver.py` output, the dict key already equals whichever identifier is "best" for that entry, so the two orderings coincide by construction rather than by guarantee. Flagging as a latent fragility (two independently-written fallback chains that happen to agree, not a designed invariant) rather than a confirmed active bug — no test data exhibited a mismatch here.

### All-or-nothing field-list check (functional, not literal, key dependency)

`_cache_get()` (`api.py:159-169`):
```python
if all(str(cached.get(f, "unknown")).strip() not in _unknown for f in requested_fields):
    return cached
return None  # some fields still unknown → rerun
```
If even one currently-requested field isn't already in the cached record (e.g. going from an 8-field niche-case list to a 10-field one, exactly what this session's own SAMN35361964→SAMN35361965/66 tests did), the whole lookup is treated as a **miss** and the sample re-runs the full pipeline from scratch — it does not reuse the 8 fields it already had and only fetch the 2 new ones. Not a bug in key matching, but it does mean "the same sample, slightly different field list" behaves exactly like a cache-key change from the user's point of view.

No case-normalization either (`.strip()` only, no `.upper()`/`.lower()`) on either side — a casing difference in `sample_id`/`bioproject` between two runs would also silently miss, though NCBI-resolved accessions are consistently uppercase in practice so this is a smaller risk than the bioproject-fallback bug above.

## 2. Does the cache require login?

**No — confirmed by design, not just by inspection.** `PRIVACY.md:31,34-36` explicitly documents `KnownCachedSamples` as a **shared, global** cache "keyed only by sample ID, not by who ran it." The actual gate is:

```python
if niche_cases and os.environ.get("GCP_CREDS_JSON"):
```

i.e. (a) the user must have specified at least one predefined/niche metadata field, and (b) the server process must have Google service-account credentials configured — nothing about authentication. In this environment, `GCP_CREDS_JSON` **is** set and is valid JSON (verified without printing its contents), so that gate is open here.

## 3. Is there a bug in `_cache_save`/`_cache_get`, or does the lookup correctly match a prior save?

Two separate answers:

**a) The real, decisive finding for *this session's* testing:** none of it went through the cache at all, because none of it went through `api.py`'s `/analyze` endpoint. Every test script used this session (`run_normalize_table_test.py`, `run_3samples_test.py`, `run_new_context_SAMN35361964.py`, `trace_context_for_llm_SAMN35361964.py`, `rerun_selfcheck_3samples.py`, `run_SAMN35361964_single_pdf.py`) only imports `_extract_text_from_upload` and/or `_rows_from_new_pipeline` from `api.py` and calls `additional_pipeline.pipeline_with_gemini()` directly — `_cache_get`/`_cache_save` live only inside the `/analyze` generator and are never reached by any of these scripts, regardless of `GCP_CREDS_JSON` or field-list stability. This alone fully explains "reruns behave like a fresh run every time" for every rerun done in this conversation — it isn't a cache bug, it's a code path that was never exercised.

**b) For the real, logged-through-the-browser `/analyze` flow:** the bioproject-fallback asymmetry above (§1) is a genuine, confirmed bug that would prevent cache hits specifically for accessions that resolve without a bioproject (bare SRA/GenBank entries). For accessions that *do* resolve with a bioproject (which covers every sample tested this session, e.g. the full PRJNA976261 fan-out — all 12 entries carried `'bioproject': 'PRJNA976261'`), the key-matching logic itself is consistent and a genuine identical rerun (same fields, same GCP creds) should hit the cache correctly — modulo the all-or-nothing field-list behavior in §1.

## Summary

| Question | Finding |
|---|---|
| What's the key? | `(sample_id, bioproject)` only — no fields/user/session/timestamp in the key itself, but field-list changes still defeat a lookup functionally (all-or-nothing validity check) |
| Login required? | No — shared/global cache by design (`PRIVACY.md`); gated only on `niche_cases` being non-empty and `GCP_CREDS_JSON` being set (both true in this environment) |
| Real bug? | Yes — bioproject-fallback asymmetry (`req.accession_id` on read vs. hardcoded `""` on write) permanently breaks caching for any bioproject-less accession. Separately, and more directly relevant to what's actually been observed this session: the ad hoc test scripts never call the cache functions at all, since they bypass the `/analyze` endpoint entirely. |

No fix has been applied. Candidates for a future fix (not implemented): make the write-side bioproject fallback mirror the read side's (or vice versa — pick one convention and use it in both places), and decide whether `_cache_get` should support partial-field reuse instead of all-or-nothing.
