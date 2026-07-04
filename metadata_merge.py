"""Shared merge function so metadata gathered from multiple sources
accumulates into one table instead of either dropping the new value on a
match (api.py's old bug) or overwriting the existing value (additional_
pipeline.py's old bug).

Do NOT use model.merge_metadata_outputs() -- it's dead code (zero callers)
and does the wrong thing here (string-joins differing values with " or ",
no source-list tracking). This module is the replacement.
"""

import re

from field_aliases import field_name_matches

_WHITESPACE_RE = re.compile(r'\s+')
_SKIP_VALUES = {"", "unknown", "none", "null", "n/a", "na", "missing", "not applicable"}


def _normalize_for_compare(value: str) -> str:
    return _WHITESPACE_RE.sub(' ', (value or "").strip().lower())


def _value_and_explanation(v):
    """Normalize one new_fields entry to (value, explanation) strings.
    Accepts either a plain value or a {"value":..., "explanation":...} dict
    (the shape both existing call sites already produce).
    """
    if isinstance(v, dict):
        return str(v.get("value", "") or "").strip(), str(v.get("explanation", "") or "").strip()
    return (str(v).strip() if v is not None else ""), ""


def _extend_conflict_marker(existing_value: str, existing_label: str, new_label: str, new_value: str) -> str:
    """Extend (or start) a "##CONFLICT: a=x, b=y" marker on a field's value,
    reusing the exact convention model.py's _extract_additional_fields prompt
    already produces and api.py's _emit_field already parses.
    """
    if "##CONFLICT:" in existing_value:
        return f"{existing_value}, {new_label}={new_value}"
    return f"{existing_value} ##CONFLICT: {existing_label}={existing_value}, {new_label}={new_value}"


def merge_metadata_into_table(table: dict, new_fields: dict, source_label: str, is_llm: bool = False) -> dict:
    """Merge `new_fields` into `table`, corroborating matches instead of
    dropping or overwriting them. Safe to call repeatedly, across many
    sources over time, accumulating into the same table -- each call only
    ever adds to or corroborates what's already there.

    table: {field_name: {"value": str, "explanation": str, "sources": [str],
            "is_llm": bool}}, mutated in place and returned.
    new_fields: {field_name: value} or {field_name: {"value":..., "explanation":...}}.
    source_label: identifies this batch's contribution (e.g. a raw Pass-2
        field name, a document name, an NCBI record type) for the field's
        source list and "confirmed by" narrative.
    is_llm: whether this batch's values are LLM-derived; OR'd into each
        touched field's is_llm flag for future (not-yet-built) confidence work.
    """
    if not new_fields:
        return table

    for new_key, new_val in new_fields.items():
        value, explanation = _value_and_explanation(new_val)
        if value.lower() in _SKIP_VALUES:
            continue

        existing_key = None
        for candidate_key in table.keys():
            if field_name_matches(candidate_key, new_key):
                existing_key = candidate_key
                break

        if existing_key is None:
            table[new_key] = {
                "value": value,
                "explanation": explanation,
                "sources": [source_label],
                "is_llm": is_llm,
            }
            continue

        entry = table[existing_key]
        entry.setdefault("sources", []).append(source_label)
        entry["is_llm"] = entry.get("is_llm", False) or is_llm

        if _normalize_for_compare(value) == _normalize_for_compare(entry.get("value", "")):
            confirm_line = f"Confirmed by {source_label}."
            entry["explanation"] = f"{entry['explanation']}\n{confirm_line}" if entry.get("explanation") else confirm_line
        else:
            entry["value"] = _extend_conflict_marker(entry.get("value", ""), existing_key, source_label, value)
            conflict_line = f"Conflicting value from {source_label}: '{value}'."
            if explanation:
                conflict_line += f" {explanation}"
            entry["explanation"] = f"{entry['explanation']}\n{conflict_line}" if entry.get("explanation") else conflict_line

    return table
