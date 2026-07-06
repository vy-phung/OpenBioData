"""Canonical-field-name registry so Pass 2's free-form attribute discovery
merges into existing schema/Pass-1 columns instead of creating duplicate
columns that hold the same information under a different name
(e.g. NCBI's raw 'geo_loc_name' vs cMD's 'geographic_location_country_and_or_sea').

Seeded with common cMD / NCBI BioSample / SRA synonyms. Extend as new
duplicate pairs are observed in practice.
"""

FIELD_ALIASES: dict[str, list[str]] = {
    "geographic_location_country_and_or_sea": [
        "geo_loc_name", "country", "geographic_location", "location", "geo_location",
    ],
    "geographic_location_latitude": ["latitude", "lat"],
    "geographic_location_longitude": ["longitude", "lon", "long"],
    "host_disease": ["disease", "host_disease_status", "clinical_diagnosis", "diagnosis"],
    "host_disease_status": ["disease_status", "health_state", "clinical_status"],
    "host_age": ["age", "host_age_at_collection"],
    "host_sex": ["sex", "gender", "host_gender"],
    "host_body_mass_index": ["bmi", "host_bmi"],
    "host": ["host_taxon", "host_organism", "host_species"],
    "tissue": ["body_site", "isolation_source_tissue", "anatomical_site"],
    "isolation_source": ["source_material_id", "material_source"],
    "collection_date": ["sample_collection_date", "date_collected", "date_of_collection"],
    "environment_biome": ["env_biome", "biome"],
    "environment_feature": ["env_feature"],
    "environment_material": ["env_material"],
    "sequencing_method": ["sequencing_platform", "platform", "instrument_model"],
    "library_strategy": ["lib_strategy"],
    "library_source": ["lib_source"],
    "library_selection": ["lib_selection"],
    "organism": ["scientific_name", "species", "organism_name"],
    "sample_type": ["sample_source_type", "modern/ancient/unknown"],
    "strain": ["host_strain", "isolate"],
    "bio_sample_accession": ["biosample_accession", "biosample", "external_id"],
}

# Reverse lookup: alias (lowercase) -> canonical name
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _aliases in FIELD_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias.lower()] = _canonical


def canonicalize_field_name(name: str, known_fields) -> str:
    """Map `name` onto an existing field in `known_fields` when it is a known
    synonym, so callers reuse the existing column instead of creating a new
    one. `known_fields` may be any iterable/container supporting `in`
    (case-insensitive match is attempted first).

    Returns `name` unchanged if no equivalence is found.
    """
    if not name:
        return name
    known_lower = {str(k).lower(): k for k in known_fields}

    name_lower = name.strip().lower()
    if name_lower in known_lower:
        return known_lower[name_lower]

    canonical = _ALIAS_TO_CANONICAL.get(name_lower)
    if canonical:
        if canonical.lower() in known_lower:
            return known_lower[canonical.lower()]
        return canonical

    # Reverse direction: name IS a canonical key, but known_fields holds one of its aliases
    for alias in FIELD_ALIASES.get(name_lower, []):
        if alias.lower() in known_lower:
            return known_lower[alias.lower()]

    return name


def _is_known_field(name: str) -> bool:
    """True if `name` (case-insensitive) is a canonical key or a listed alias
    in FIELD_ALIASES -- i.e. the static table has an actual opinion about it.
    """
    name_lower = (name or "").strip().lower()
    return name_lower in FIELD_ALIASES or name_lower in _ALIAS_TO_CANONICAL


def _canonical_of(name: str) -> str:
    """Resolve `name` to its FIELD_ALIASES canonical key if known, else return
    `name` unchanged. Only meaningful when `_is_known_field(name)` is True.
    """
    return canonicalize_field_name(name, FIELD_ALIASES.keys())


_LLM_MATCH_CACHE: dict = {}  # {(a_lower, b_lower) sorted pair: bool} -- avoid repeat LLM calls for the same pair


def _llm_field_name_match(existing_field_name: str, new_field_name: str) -> bool:
    """Ask the LLM whether two field names refer to the same underlying
    metadata concept. Modeled on model.align_to_schema()'s prompt style:
    confidently match, or explicitly abstain (-> False) if unsure. Safe:
    always returns a bool, defaulting to False on any parse/call failure --
    a false "different" verdict is safe (worst case: two separate columns),
    a false "same" verdict risks silently merging unrelated data.
    """
    cache_key = tuple(sorted((existing_field_name.strip().lower(), new_field_name.strip().lower())))
    if cache_key in _LLM_MATCH_CACHE:
        return _LLM_MATCH_CACHE[cache_key]

    result = False
    try:
        from model import call_llm_api
        import json as _json

        prompt = (
            "You are comparing two metadata field names to decide if they represent the SAME "
            "underlying concept about a biological sample, just phrased or named differently.\n\n"
            f'Field A: "{existing_field_name}"\n'
            f'Field B: "{new_field_name}"\n\n'
            "Task: Decide whether Field A and Field B refer to the same underlying metadata "
            "concept (so a value recorded for one could be merged with a value recorded for the "
            "other), or whether they are different concepts.\n\n"
            "IMPORTANT DISTINCTION -- an IDENTIFIER field (a code, label, or number that names or "
            "indexes one specific instance -- e.g. a subject/participant ID, a sample ID, an "
            "accession, a barcode) is NEVER the same concept as a DESCRIPTIVE ATTRIBUTE field (a "
            "property or characteristic shared by many instances -- e.g. a species/organism name, "
            "a disease/condition, a body site, a measurement). Both may describe 'the same sample', "
            "but naming WHICH instance it is differs from describing WHAT that instance is like. "
            "For example: 'subject_id' (an identifier for a study participant) is NOT the same "
            "concept as 'host' or 'host_species' (the organism species that participant belongs "
            "to) -- one picks out an individual, the other names a biological category shared by "
            "every individual in the study. The same reasoning applies to any other ID-vs-"
            "attribute pair, not just this specific example. Answer false for pairs like this even "
            "though they both relate to the sample.\n"
            "Similarly, a field holding narrative/citation text about OTHER fields (e.g. a general "
            "'sources', 'notes', or 'explanation' column that exists to document where values came "
            "from) is NEVER the same concept as a substantive data field, even if a word like "
            "'source' appears in both names -- one is metadata ABOUT the record, the other is a "
            "fact IN the record. Answer false for pairs like this too.\n\n"
            "Only answer true if you are confident they mean the same thing. If you are at all "
            "unsure, answer false -- a false 'different' verdict is safe (worst case: two separate "
            "columns), but a false 'same' verdict risks silently merging unrelated data.\n\n"
            'Return ONLY a JSON object: {"same_field": true|false}\n'
            "No markdown fences, no explanation.\n"
        )
        response_text, _ = call_llm_api(prompt)
        raw = response_text.strip()
        if raw.startswith('```'):
            parts = raw.split('```')
            raw = parts[1] if len(parts) > 1 else raw
            if raw.lower().startswith('json'):
                raw = raw[4:]
        brace_start = raw.find('{')
        brace_end = raw.rfind('}')
        if brace_start != -1 and brace_end > brace_start:
            raw = raw[brace_start:brace_end + 1]
        parsed = _json.loads(raw.strip())
        result = bool(parsed.get("same_field", False))
    except Exception as e:
        print(f"[_llm_field_name_match] WARNING: {e}")
        result = False

    _LLM_MATCH_CACHE[cache_key] = result
    return result


def field_name_matches(existing_field_name: str, new_field_name: str) -> bool:
    """Decide whether `existing_field_name` and `new_field_name` refer to the
    same underlying metadata concept, so a caller (e.g.
    metadata_merge.merge_metadata_into_table) can merge a new value into an
    existing field instead of creating a duplicate column.

    Deterministic first: canonicalize both via the FIELD_ALIASES table. If
    both names are recognized by the table (as a canonical key or a listed
    alias), the comparison is confident and no LLM call is made. Otherwise
    (at least one name is unrecognized -- it could be an uncatalogued
    synonym, or a genuinely different concept) falls back to an LLM
    comparison for this specific ambiguous pair.
    """
    if not existing_field_name or not new_field_name:
        return False

    a = existing_field_name.strip().lower()
    b = new_field_name.strip().lower()
    if a == b:
        return True

    if _is_known_field(existing_field_name) and _is_known_field(new_field_name):
        return _canonical_of(existing_field_name).strip().lower() == _canonical_of(new_field_name).strip().lower()

    return _llm_field_name_match(existing_field_name, new_field_name)
