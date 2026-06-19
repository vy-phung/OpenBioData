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
