"""
Reusable single-call pipeline runner for the accession benchmark.

Mirrors the real /analyze code path (see api.py ~line 1220-1440) and the
existing run_3samples_test.py pattern: resolve accession(s) via
ncbi_resolver / non_ncbi_resolver, extract any local upload files once,
then call additional_pipeline.pipeline_with_gemini() -- the same function
the production API calls -- to get real extraction output. No extraction
logic is reimplemented here.

Accessions that share the same upload file set are resolved together and
passed to pipeline_with_gemini() in a single call so the (already-extracted)
file text is reused across them; pipeline_with_gemini still fetches each
accession's own NCBI record / web sources individually inside that call.
"""
import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import additional_pipeline
import ncbi_resolver
import non_ncbi_resolver
from api import _extract_text_from_upload

OUT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "by_accession")

_file_text_cache: dict = {}


def clean_accession(raw: str) -> str:
    """Strip parenthetical annotations, e.g. 'SAMN20503430 (bioproject: PRJNA514286)' -> 'SAMN20503430'."""
    return re.split(r"\s*\(", raw.strip(), maxsplit=1)[0].strip()


def split_metadata_fields(metadata_str: str) -> list:
    return [f.strip() for f in metadata_str.split(",") if f.strip()]


def split_upload_files(upload_str: str) -> list:
    upload_str = (upload_str or "").strip()
    if not upload_str or upload_str.lower() == "no user upload file":
        return []
    parts = re.split(r"[;,]", upload_str)
    return [p.strip() for p in parts if p.strip()]


def extract_file_text(path: str) -> str:
    """Extract text from a local PDF/XLSX upload, cached by path so files shared
    across multiple accessions/rows are only parsed once."""
    if path in _file_text_cache:
        return _file_text_cache[path]
    with open(path, "rb") as f:
        raw = f.read()
    filename = os.path.basename(path)
    text = _extract_text_from_upload(raw, filename)
    print(f"[extract_file_text] {filename}: {len(raw)} bytes -> {len(text)} chars")
    _file_text_cache[path] = text
    return text


def resolve_one(acc_clean: str) -> dict:
    """Return a {key: entry} dict for one accession, trying non-NCBI first."""
    if non_ncbi_resolver.is_non_ncbi_accession(acc_clean):
        entry = non_ncbi_resolver.build_non_ncbi_entry(acc_clean)
        print(f"[resolve_one] {acc_clean} -> non-NCBI entry ({list(entry.values())[0].get('_source_database')})")
        return entry
    entry = ncbi_resolver.resolve_accessions(acc_clean)
    print(f"[resolve_one] {acc_clean} -> NCBI resolved: {entry}")
    return entry


async def run_group(rows: list, group_label: str) -> dict:
    """
    rows: list of dicts each with keys: accession (raw, uncleaned), metadata (str),
          upload_files (list of local paths, already resolved to abs paths).
    All rows in a group share the same upload_files list (may be empty).
    Returns {raw_accession: extraction_result_dict}.
    """
    resolved_dict: dict = {}
    acc_key_by_raw: dict = {}
    for row in rows:
        acc_clean = clean_accession(row["accession"])
        entry = resolve_one(acc_clean)
        resolved_dict.update(entry)
        # remember which resolved key(s) belong to this raw row (usually exactly one)
        acc_key_by_raw[row["accession"]] = list(entry.keys())

    upload_files = rows[0]["upload_files"]
    file_texts = [extract_file_text(p) for p in upload_files]
    user_context_text = "\n\n".join(t for t in file_texts if t.strip()) or None
    user_file_label = ", ".join(os.path.basename(p) for p in upload_files) or None

    niche_cases = split_metadata_fields(rows[0]["metadata"])

    print(f"\n{'=' * 80}\n[run_group] {group_label}: {list(resolved_dict.keys())}")
    print(f"[run_group] niche_cases={niche_cases}")
    print(f"[run_group] user_context_text: {len(user_context_text or '')} chars from {user_file_label}")

    accs_output, source_texts, combined_all_text = await additional_pipeline.pipeline_with_gemini(
        accessions=resolved_dict,
        niche_cases=niche_cases,
        user_context_text=user_context_text,
        user_file_label=user_file_label,
        standardization_urls=None,
        save_df=None,
    )

    results = {}
    for row in rows:
        raw_acc = row["accession"]
        keys = acc_key_by_raw[raw_acc]
        merged = {"raw_accession": raw_acc, "resolved_keys": keys, "fields": {}, "additional_fields": {}, "signals": {}}
        for key in keys:
            acc_score = accs_output.get(key, {})
            for field in niche_cases:
                field_data = acc_score.get(field, {})
                if field_data:
                    answer = list(field_data.keys())[0]
                    explanations = field_data[answer]
                    merged["fields"].setdefault(field, {"answer": answer, "explanations": explanations, "resolved_key": key})
            merged["additional_fields"].update(acc_score.get("_additional_fields", {}))
            merged["signals"][key] = acc_score.get("signals", {})
            merged["resolved_entry"] = {k: v for k, v in resolved_dict.get(key, {}).items()}

        acc_dir = os.path.join(OUT_ROOT, clean_accession(raw_acc).replace("/", "_"))
        os.makedirs(acc_dir, exist_ok=True)
        out_path = os.path.join(acc_dir, "extraction_result.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, default=str)
        print(f"[run_group] saved {out_path}")
        results[raw_acc] = merged

    return results
