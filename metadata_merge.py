"""Shared merge function so metadata gathered from multiple sources
accumulates into one table instead of either dropping the new value on a
match (api.py's old bug) or overwriting the existing value (additional_
pipeline.py's old bug).

Do NOT use model.merge_metadata_outputs() -- it's dead code (zero callers)
and does the wrong thing here (string-joins differing values with " or ",
no source-list tracking). This module is the replacement.
"""

import asyncio
import re

from field_aliases import field_name_matches, _is_known_field, _canonical_of

_WHITESPACE_RE = re.compile(r'\s+')
_SKIP_VALUES = {"", "unknown", "none", "null", "n/a", "na", "missing", "not applicable"}

# Local, cheap synonym check for the small fixed set of identifier columns --
# deliberately NOT routed through field_name_matches's LLM fallback, since
# every field extracted for a sample would otherwise trigger up to 4 extra
# LLM calls (one per identifier) just to check "is this field itself an
# identifier column". A false negative here (failing to recognize a field as
# its own identifier) just means we skip an optimization, not a correctness
# problem -- the value-equality check below still only rejects a field that
# duplicates a *different* identifier's value.
_IDENTIFIER_FIELD_ALIASES = {
    "biosample_accession": {"biosample_accession", "biosample", "bio_sample_accession", "external_id"},
    "bioproject":          {"bioproject", "bioproject_accession", "bioproject_id"},
    "sra_accession":       {"sra_accession", "sra_study_accession", "sra"},
    "genbank_accession":   {"genbank_accession", "accession"},
}


def _is_same_identifier_field(new_key: str, id_label: str) -> bool:
    key_l = (new_key or "").strip().lower()
    if key_l == id_label.lower():
        return True
    return key_l in _IDENTIFIER_FIELD_ALIASES.get(id_label.lower(), set())


def is_duplicate_identifier_value(field_name: str, value: str, identifier_values: dict) -> bool:
    """True if `value` (proposed for `field_name`) exactly duplicates a
    *different* identifier column's value (case-insensitive, whitespace-
    trimmed) -- e.g. a 'study_name' field whose value turned out to be the
    bioproject accession. A field legitimately reporting its own matching
    identifier (e.g. 'biosample_accession' correctly repeating the biosample
    accession) is exempted.

    Exposed standalone (not just used inside merge_metadata_into_table) so
    non-merge code paths -- e.g. additional_pipeline.py's niche-case answers,
    which are accepted directly and never pass through
    merge_metadata_into_table -- can apply the same value-level duplicate
    check before accepting a field's answer.
    """
    if not identifier_values:
        return False
    for id_label, id_value in identifier_values.items():
        if not id_value or _is_same_identifier_field(field_name, id_label):
            continue
        if _normalize_for_compare(value) == _normalize_for_compare(str(id_value)):
            return True
    return False


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


async def cross_check_fields(table: dict, new_fields: dict, source_label: str, is_llm: bool = False,
                              identifier_values: dict = None) -> dict:
    """Read-only w.r.t. `table`. Computes the same per-field decision
    merge_metadata_into_table() used to make inline, without mutating `table`.
    Returns {new_key: CrossCheckResult}, one entry per key in `new_fields`,
    in `new_fields` iteration order.

    CrossCheckResult:
      {"action": "confirm" | "conflict" | "new_field" |
                 "rejected_duplicate_identifier" | "skipped",
       "value": str, "explanation": str, "matched_existing_key": str | None,
       "source_label": str, "is_llm": bool}

    identifier_values: optional {"biosample_accession": ..., "bioproject": ...,
        "sra_accession": ..., "genbank_accession": ...} -- already-resolved
        identifier values for this same sample/row. This is a VALUE-level
        check, distinct from field_name_matches's NAME-level check: a new
        field whose extracted value exactly matches a *different* identifier
        column's value (case-insensitive, whitespace-trimmed) is rejected as
        "unknown" instead of accepted -- e.g. a 'study_name' field whose
        value turned out to be the bioproject accession is a mislabeled
        identifier, not a real study name. A field that legitimately reports
        its own matching identifier (e.g. a 'biosample_accession' field
        correctly repeating the biosample accession) is exempted.

    Order matters: apply_cross_check() MUST replay these in the same order
    it receives them (Python dicts preserve insertion order, so passing the
    dict straight through is enough). A later field in the same batch can be
    a synonym of an earlier field's brand-new column, exactly as today's
    single interleaved loop allows -- e.g. new_fields={"geo_loc_name": "Italy",
    "geographic_location": "Italy"} creates one column, not two, because by
    the time "geographic_location" is scanned, "geo_loc_name" already exists.
    To reproduce that without actually mutating `table`, this function keeps
    a local shadow_values map (new_key -> current comparison value) seeded
    from `table` and updated after every decision exactly the way
    apply_cross_check() will update the real table -- so a same-batch
    synonym's confirm/conflict decision compares against the same value it
    would if this were still one interleaved pass.
    """
    identifier_values = identifier_values or {}
    results: dict = {}
    if not new_fields:
        return results

    shadow_values = {k: v.get("value", "") for k, v in table.items()}

    for new_key, new_val in new_fields.items():
        value, explanation = _value_and_explanation(new_val)

        if is_duplicate_identifier_value(new_key, value, identifier_values):
            print(f"[cross_check_fields] Rejected {new_key}={value!r} from {source_label}: "
                  f"duplicates an identifier value, not a distinct fact")
            results[new_key] = {
                "action": "rejected_duplicate_identifier", "value": "unknown", "explanation": explanation,
                "matched_existing_key": None, "source_label": source_label, "is_llm": is_llm,
            }
            continue

        if value.lower() in _SKIP_VALUES:
            results[new_key] = {
                "action": "skipped", "value": value, "explanation": explanation,
                "matched_existing_key": None, "source_label": source_label, "is_llm": is_llm,
            }
            continue

        existing_key = None
        # Sequential and short-circuiting on purpose: takes the FIRST matching
        # key in (shadow) table-iteration order, so running these concurrently
        # (e.g. asyncio.gather over every candidate_key) would fire extra LLM
        # calls past the first match on every row -- wasted latency/cost for no
        # behavior change, since only the first match is ever kept anyway.
        for candidate_key in shadow_values.keys():
            if await field_name_matches(candidate_key, new_key):
                existing_key = candidate_key
                break

        if existing_key is None:
            results[new_key] = {
                "action": "new_field", "value": value, "explanation": explanation,
                "matched_existing_key": None, "source_label": source_label, "is_llm": is_llm,
            }
            shadow_values[new_key] = value
            continue

        if _normalize_for_compare(value) == _normalize_for_compare(shadow_values.get(existing_key, "")):
            results[new_key] = {
                "action": "confirm", "value": value, "explanation": explanation,
                "matched_existing_key": existing_key, "source_label": source_label, "is_llm": is_llm,
            }
            # value unchanged on confirm -- shadow stays as-is
        else:
            shadow_values[existing_key] = _extend_conflict_marker(
                shadow_values.get(existing_key, ""), existing_key, source_label, value)
            results[new_key] = {
                "action": "conflict", "value": value, "explanation": explanation,
                "matched_existing_key": existing_key, "source_label": source_label, "is_llm": is_llm,
            }

    return results


def apply_cross_check(table: dict, cross_check_result: dict) -> dict:
    """Mutates and returns `table`: applies each field's decision from
    cross_check_fields(), in the order the dict provides them (must match
    cross_check_fields()'s own new_fields order -- see its docstring). This
    is the only place a field becomes confirmed/flagged/new -- steps 5-6 of
    today's merge_metadata_into_table(), unchanged, just driven by a
    decision dict instead of recomputed inline.
    """
    for new_key, decision in cross_check_result.items():
        action = decision["action"]
        if action in ("skipped", "rejected_duplicate_identifier"):
            continue

        value = decision["value"]
        explanation = decision["explanation"]
        source_label = decision["source_label"]
        is_llm = decision["is_llm"]
        existing_key = decision["matched_existing_key"]

        if action == "new_field":
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

        if action == "confirm":
            confirm_line = f"Confirmed by {source_label}."
            entry["explanation"] = f"{entry['explanation']}\n{confirm_line}" if entry.get("explanation") else confirm_line
        elif action == "conflict":
            entry["value"] = _extend_conflict_marker(entry.get("value", ""), existing_key, source_label, value)
            conflict_line = f"Conflicting value from {source_label}: '{value}'."
            if explanation:
                conflict_line += f" {explanation}"
            entry["explanation"] = f"{entry['explanation']}\n{conflict_line}" if entry.get("explanation") else conflict_line

    return table


async def merge_metadata_into_table(table: dict, new_fields: dict, source_label: str, is_llm: bool = False,
                                     identifier_values: dict = None) -> dict:
    """Compatibility wrapper: apply_cross_check(table, cross_check_fields(...)).
    Existing call sites (additional_pipeline.py:1316, api.py:504) are
    unchanged by this refactor -- see cross_check_fields()/apply_cross_check()
    for the actual logic, split out so a caller can inspect the decision
    before it's applied.

    Safe to call repeatedly, across many sources over time, accumulating into
    the same table -- each call only ever adds to or corroborates what's
    already there.

    table: {field_name: {"value": str, "explanation": str, "sources": [str],
            "is_llm": bool}}, mutated in place and returned.
    new_fields: {field_name: value} or {field_name: {"value":..., "explanation":...}}.
    source_label: identifies this batch's contribution (e.g. a raw Pass-2
        field name, a document name, an NCBI record type) for the field's
        source list and "confirmed by" narrative.
    is_llm: whether this batch's values are LLM-derived; OR'd into each
        touched field's is_llm flag for future (not-yet-built) confidence work.
    identifier_values: see cross_check_fields()'s docstring.
    """
    if not new_fields:
        return table
    cross_check_result = await cross_check_fields(table, new_fields, source_label, is_llm, identifier_values)
    return apply_cross_check(table, cross_check_result)


# ── Cross-sample table normalization ────────────────────────────────────────
# merge_metadata_into_table() above only ever sees ONE sample's own extraction
# batch at a time (its Pass 1 answers + Pass 2 additional fields) -- it has no
# visibility into what column names or values OTHER samples' rows ended up
# using. Two samples processed independently can each name the same concept
# differently (sample A's Pass 2 discovers "disease", sample B's discovers
# "disease_status"), producing two mostly-empty columns in the final
# multi-sample table instead of one complete one. Separately, the same value
# can land under two differently-named columns without either being a
# recognized name-synonym (e.g. two identifier-shaped columns that just
# happen to hold identical values in this dataset). normalize_output_table()
# is the one-time, whole-table pass -- run once after every sample's row has
# been assembled, not per-sample -- that catches both.

# Structural per-field companion-column suffixes this codebase's own row
# builders already use (api.py's _emit_field: "<field>_explanation",
# "<field>_source_location", etc.) -- a naming CONVENTION, not a specific
# field name, so treating them specially here isn't hardcoding a field.
# A companion rides along with whatever its base field merges into, instead
# of being compared against unrelated columns on its own.
_COMPANION_SUFFIXES = (
    "_explanation", "_source_location", "_conflict", "_id_match",
    "_candidates", "_chosen", "_narrative",
)

# Step 2 (value-level duplicate detection) merges a column pair when at least
# this fraction of rows where BOTH are non-blank hold the identical
# (normalized) value, and at least this many rows overlap (avoids merging on
# a single coincidental match).
_VALUE_DUP_THRESHOLD = 0.9
_VALUE_DUP_MIN_OVERLAP = 2

# Fixed pipeline-INFRASTRUCTURE columns that must never be considered for
# synonym/duplicate merging -- NOT a domain-specific field exclusion (nothing
# here names a biological concept like a species or a disease). These are the
# columns api._rows_from_new_pipeline() sets unconditionally on every row it
# ever builds, for any accession/species/study whatsoever:
#   - row = {"biosample_accession": ..., "bioproject": ..., "sra_accession": ...}
#     (api.py:324, the row dict's own initial literal)
#   - row["explanation"], row["sources"], row["confidence_score"],
#     row["conflict"], row["time_cost"] (api.py:579-583, set unconditionally
#     at the end of every row)
# ("genbank_accession" is deliberately NOT included -- api.py:329-330 only
# sets it `if genbank_acc:`, so it is not unconditional the way the others
# are.) Re-check api._rows_from_new_pipeline() if that function changes.
_PIPELINE_INFRASTRUCTURE_COLUMNS = {
    "biosample_accession", "bioproject", "sra_accession",
    "explanation", "sources", "confidence_score", "conflict", "time_cost",
}


def _companion_base(col: str, known_columns: set):
    """If `col` is a structural companion of some OTHER column actually
    present in the table (e.g. 'target_condition_explanation' when
    'target_condition' is itself a column), return (base, suffix); else None.
    """
    for suf in _COMPANION_SUFFIXES:
        if col.endswith(suf) and len(col) > len(suf):
            base = col[: -len(suf)]
            if base in known_columns:
                return base, suf
    return None


def _union_find_clusters(items, are_same) -> list:
    """Generic pairwise clustering: group `items` into connected components
    under the symmetric relation `are_same(a, b)`, checking every pair once
    (skipping pairs already joined by transitivity). Returns a list of
    clusters (each a list of original items, first-seen order preserved).
    """
    items_list = list(items)
    parent = {it: it for it in items_list}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(items_list)):
        for j in range(i + 1, len(items_list)):
            a, b = items_list[i], items_list[j]
            if find(a) == find(b):
                continue
            if are_same(a, b):
                union(a, b)

    clusters: dict = {}
    for it in items_list:
        clusters.setdefault(find(it), []).append(it)
    return list(clusters.values())


def _pick_canonical(cluster: list) -> str:
    """Representative name for a merged column cluster: prefer a name already
    recognized by FIELD_ALIASES (as a canonical key or a listed alias --
    resolved to its canonical key), else the first-seen column in the
    cluster. Generic: makes no reference to any specific field name.
    """
    for name in cluster:
        if _is_known_field(name):
            return _canonical_of(name)
    return cluster[0]


def _row_has_value(row: dict, col: str) -> bool:
    return _normalize_for_compare(str(row.get(col, "") or "")) not in _SKIP_VALUES


def _merge_columns_in_rows(rows: list, cluster: list, canonical: str) -> int:
    """Mutate `rows` in place: collapse every column in `cluster` into
    `canonical` using the same agree/corroborate-or-##CONFLICT convention
    merge_metadata_into_table() already uses, then drop the other columns
    from every row. Returns how many rows got a new ##CONFLICT marker.
    """
    others = [c for c in cluster if c != canonical]
    if not others:
        return 0

    n_conflicts = 0
    for row in rows:
        has_canon = _row_has_value(row, canonical)
        # base_val is the stable agreement anchor (never conflict-decorated) so
        # later columns are always compared against the true first value, not
        # against a growing "##CONFLICT: ..." display string.
        base_val = str(row.get(canonical, "") or "") if has_canon else ""
        display_val = base_val
        row_conflicted = False
        for other in others:
            if other not in row:
                continue
            other_val = str(row.get(other, "") or "")
            if _normalize_for_compare(other_val) in _SKIP_VALUES:
                continue
            if not has_canon:
                base_val = other_val
                display_val = other_val
                has_canon = True
            elif _normalize_for_compare(other_val) != _normalize_for_compare(base_val):
                display_val = _extend_conflict_marker(display_val, canonical, other, other_val)
                row_conflicted = True
            # else: agrees with the anchor value -- nothing to add
        if has_canon:
            row[canonical] = display_val
        if row_conflicted:
            n_conflicts += 1
        for other in others:
            row.pop(other, None)
    return n_conflicts


def _merge_companions(full_table: list, canonical_map: dict, companions_by_base: dict, merge_log: list) -> None:
    """For every group of original base columns that ended up sharing a final
    canonical name, merge their companion columns (matched by suffix) into
    the canonical base's companion column too. Companions hold narrative/
    citation detail rather than a single fact to reconcile, so they're
    combined by concatenation, not agree/##CONFLICT logic.
    """
    groups: dict = {}
    for orig, canon in canonical_map.items():
        groups.setdefault(canon, []).append(orig)

    for canon, originals in groups.items():
        if len(originals) <= 1:
            continue
        suffixes = set()
        for orig in originals:
            suffixes.update(companions_by_base.get(orig, {}).keys())
        for suf in suffixes:
            canon_companion = canon + suf
            merged_from = []
            for orig in originals:
                orig_companion = companions_by_base.get(orig, {}).get(suf)
                if not orig_companion or orig_companion == canon_companion:
                    continue
                touched = False
                for row in full_table:
                    if orig_companion not in row:
                        continue
                    val = str(row.pop(orig_companion, "") or "").strip()
                    if not val:
                        continue
                    existing = str(row.get(canon_companion, "") or "").strip()
                    row[canon_companion] = f"{existing}\n{val}" if existing else val
                    touched = True
                if touched:
                    merged_from.append(orig_companion)
            if merged_from:
                merge_log.append({
                    "canonical": canon_companion, "merged_from": merged_from,
                    "reason": f"companion detail column of merged base field '{canon}'",
                    "conflicts": 0,
                })


_NUMERIC_RE = re.compile(r'^-?\d+(\.\d+)?$')


def _looks_numeric(value: str) -> bool:
    v = value.strip().replace(",", "")
    if not v:
        return False
    return bool(_NUMERIC_RE.match(v))


def _column_value_shape(full_table: list, col: str) -> str:
    """Classify a column's own non-blank values as "numeric" (at least one
    value parses as a plain number, and none don't), "text" (at least one
    value doesn't parse as a plain number, and none do), "mixed" (some of
    each -- inconclusive), or "unknown" (no non-blank values to judge from).
    """
    seen_numeric = False
    seen_text = False
    for row in full_table:
        if not _row_has_value(row, col):
            continue
        val = str(row.get(col, "") or "")
        if _looks_numeric(val):
            seen_numeric = True
        else:
            seen_text = True
    if seen_numeric and seen_text:
        return "mixed"
    if seen_numeric:
        return "numeric"
    if seen_text:
        return "text"
    return "unknown"


def _type_shape_conflict(full_table: list, col_a: str, col_b: str) -> bool:
    """True only when the two columns' value shapes are confidently
    opposite (one strictly numeric-only, the other strictly text/
    categorical-only) -- a structural signal that they hold different
    kinds of facts (e.g. a raw measurement vs. a categorical label) even
    when field_name_matches() or value-overlap judged them the same
    concept. Deliberately doesn't fire on "mixed"/"unknown" shapes, to
    avoid over-blocking legitimate merges just because a column happens to
    have few or ambiguous values.
    """
    shape_a = _column_value_shape(full_table, col_a)
    shape_b = _column_value_shape(full_table, col_b)
    return {shape_a, shape_b} == {"numeric", "text"}


def _normalize_output_table_impl(full_table: list):
    if not full_table:
        return full_table, []

    all_columns: list = []
    seen_cols = set()
    for row in full_table:
        for col in row.keys():
            if col not in seen_cols:
                seen_cols.add(col)
                all_columns.append(col)

    # Separate structural companions (ride along with their base field) from
    # independently-matchable base columns. Fixed pipeline-infrastructure
    # columns are excluded from matching entirely -- they pass through
    # untouched, never merged into or absorbing anything else.
    companions_by_base: dict = {}
    base_columns: list = []
    for col in all_columns:
        if col in _PIPELINE_INFRASTRUCTURE_COLUMNS:
            continue
        parsed = _companion_base(col, seen_cols)
        if parsed:
            base, suf = parsed
            companions_by_base.setdefault(base, {})[suf] = col
        else:
            base_columns.append(col)

    merge_log: list = []
    canonical_map: dict = {c: c for c in base_columns}  # original base col -> current canonical

    # ── Structural safety net: never merge two columns whose value shapes
    # confidently conflict (one numeric-only, the other text-only), even if
    # the underlying matcher (name-synonym or value-overlap) said "same".
    # Flags the pair for manual review instead of silently merging or
    # dropping it. Applies to both Step 1 and Step 2's matchers below.
    flagged_pairs: set = set()

    def _guarded_matcher(base_matcher):
        def matcher(a, b):
            if not base_matcher(a, b):
                return False
            if _type_shape_conflict(full_table, a, b):
                pair_key = frozenset((a, b))
                if pair_key not in flagged_pairs:
                    flagged_pairs.add(pair_key)
                    merge_log.append({
                        "canonical": None, "merged_from": [a, b],
                        "reason": "FLAGGED FOR MANUAL REVIEW -- matcher judged these the same "
                                  "concept, but one column's values are numeric-only and the "
                                  "other's are text/categorical-only; not merged automatically",
                        "conflicts": 0,
                    })
                return False
            return True
        return matcher

    # ── Step 1: column-name synonym merge (reuses field_name_matches) ──────
    # field_name_matches is async (its LLM fallback is offloaded via
    # asyncio.to_thread so it doesn't block api.py's request-handling event
    # loop -- see merge_metadata_into_table above). This function itself runs
    # synchronously on its own worker thread (via asyncio.to_thread(save_to_excel,
    # ...), with no event loop of its own), so bridge with asyncio.run() per call.
    def _sync_field_name_matches(a, b):
        return asyncio.run(field_name_matches(a, b))

    name_clusters = _union_find_clusters(base_columns, _guarded_matcher(_sync_field_name_matches))
    for cluster in name_clusters:
        if len(cluster) <= 1:
            continue
        canonical = _pick_canonical(cluster)
        n_conflicts = _merge_columns_in_rows(full_table, cluster, canonical)
        for c in cluster:
            canonical_map[c] = canonical
        merge_log.append({
            "canonical": canonical, "merged_from": [c for c in cluster if c != canonical],
            "reason": "name-synonym (field_name_matches)", "conflicts": n_conflicts,
        })

    # ── Step 2: value-level duplicate detection across remaining columns ───
    remaining_columns = list(dict.fromkeys(canonical_map.values()))

    def _values_mostly_equal(a: str, b: str) -> bool:
        both_present = 0
        agree = 0
        for row in full_table:
            if _row_has_value(row, a) and _row_has_value(row, b):
                both_present += 1
                if _normalize_for_compare(str(row.get(a, ""))) == _normalize_for_compare(str(row.get(b, ""))):
                    agree += 1
        if both_present < _VALUE_DUP_MIN_OVERLAP:
            return False
        return (agree / both_present) >= _VALUE_DUP_THRESHOLD

    value_clusters = _union_find_clusters(remaining_columns, _guarded_matcher(_values_mostly_equal))
    for cluster in value_clusters:
        if len(cluster) <= 1:
            continue
        canonical = _pick_canonical(cluster)
        n_conflicts = _merge_columns_in_rows(full_table, cluster, canonical)
        merged_from = [c for c in cluster if c != canonical]
        for orig, step1_canon in list(canonical_map.items()):
            if step1_canon in cluster:
                canonical_map[orig] = canonical
        merge_log.append({
            "canonical": canonical, "merged_from": merged_from,
            "reason": f"value-level duplicate (>={int(_VALUE_DUP_THRESHOLD * 100)}% row agreement)",
            "conflicts": n_conflicts,
        })

    # ── Propagate merges to structural companion columns ────────────────────
    _merge_companions(full_table, canonical_map, companions_by_base, merge_log)

    return full_table, merge_log


def normalize_output_table(full_table: list) -> list:
    """One-time, whole-table post-processing pass -- run once after every
    sample's row has been assembled into the final output table (all rows,
    all columns), immediately before the Excel/output file is written.
    Deliberately separate from merge_metadata_into_table(), which only ever
    sees one sample's extraction batch at a time and can't see other
    samples' column choices.

    Step 1: for every pair of columns, reuse field_name_matches() (the same
    function merge_metadata_into_table() already uses per-sample) to check
    if two differently-named columns represent the same concept table-wide;
    merge matches into one canonical column, corroborating agreeing values
    or marking genuine disagreements with the existing ##CONFLICT convention.

    Step 2: for every pair of columns remaining after Step 1, check what
    fraction of rows (where both are non-blank) hold the identical value;
    a high agreement rate merges them too, catching duplicate columns that
    aren't name-synonyms.

    Purely generic -- no field-specific logic; works on whatever columns/
    values happen to be present. Mutates and returns `full_table`.
    """
    full_table, _log = _normalize_output_table_impl(full_table)
    return full_table


def normalize_output_table_with_log(full_table: list):
    """Same as normalize_output_table(), but also returns the list of merge
    decisions made -- [{"canonical": str, "merged_from": [str, ...],
    "reason": str, "conflicts": int}, ...] -- for reporting/testing.
    """
    return _normalize_output_table_impl(full_table)
