"""
confidence_score.py — Generalized confidence scoring for any metadata field.

Two public entry points:
  calculate_confidence(field_name, predicted_value, sources)
      Text-search based. Use when you have raw source documents.
      Returns {'score', 'label', 'flags', 'explanation'}.

  compute_confidence_score_and_tier(signals, rules)
      Signals-based. Used by the main pipeline for structured NCBI metadata.
      Returns (score, tier, explanations).

Both are field-agnostic — they work for 'country', 'disease_status',
'collection_date', or any other metadata field.
"""

from typing import Dict, Any, Tuple, List, Optional
from openbiodata_logger import get_logger

log = get_logger(__name__)

# standardize_location is only needed for country normalization inside
# compute_confidence_score_and_tier. Import lazily to avoid pulling in the
# full model/faiss stack when this module is used standalone.
try:
    import standardize_location as _std_loc
    _HAS_STD_LOC = True
except Exception:
    _HAS_STD_LOC = False


def _smart_country_lookup(name: str) -> str:
    """Wrapper around standardize_location.smart_country_lookup with graceful fallback."""
    if _HAS_STD_LOC:
        try:
            return _std_loc.smart_country_lookup(name)
        except Exception as e:
            log.warning("standardize_location error: %s", e)
    return name


# ── Rule weights ──────────────────────────────────────────────────────────────

def set_rules() -> Dict[str, Any]:
    """Return scoring weights and thresholds."""
    return {
        "direct_evidence": {
            "explicit_geo_pubmed_text": 40,
            "geo_and_pubmed": 30,
            "geo_only": 20,
            "accession_in_text_only": 10,
        },
        "consistency": {
            "match": 20,
            "no_contradiction": 10,
            "contradiction": -30,
        },
        "evidence_density": {
            "two_or_more_pubs": 20,
            "one_pub": 10,
            "none": 0,
        },
        "risk_penalties": {
            "missing_key_fields": -10,
            "known_failure_pattern": -20,
        },
        "tiers": {
            "high_min": 70,
            "medium_min": 40,
        },
    }


# ── Generalized field scorer ───────────────────────────────────────────────────

def calculate_confidence(
    field_name: str,
    predicted_value: str,
    sources: Dict[str, str],
) -> Dict[str, Any]:
    """
    Generalized confidence scoring for any metadata field.

    Args:
        field_name:      e.g. 'country', 'disease_status', 'collection_date'
        predicted_value: the value the model predicted
        sources:         {source_name: source_text} dict of queried documents

    Returns:
        {'score': int (0-100), 'label': str, 'flags': list, 'explanation': str}

    Scoring:
        Source count    +10 per source containing predicted value, capped at +40
        Evidence        +20 peer-reviewed / +10 BioSample-confirmed
        Accession hit   +10 if 'ncbi_accession' source confirms value
        Conflict        -20 if '##' marker present in predicted_value
    """
    log.debug("calculate_confidence: field='%s' value='%s' sources=%s",
              field_name, predicted_value, list((sources or {}).keys()))

    predicted_str = str(predicted_value).strip() if predicted_value is not None else ""

    if not predicted_str:
        log.debug("calculate_confidence: %s: empty value -> score 0", field_name)
        return {
            "score": 0,
            "label": "not found",
            "flags": ["no_value"],
            "explanation": f"No value extracted for field '{field_name}'.",
        }

    score = 0
    flags: List[str] = []
    source_hits: List[str] = []
    predicted_lower = predicted_str.lower()

    # ── Source count bonus (+10 per hit, max +40) ─────────────────────────────
    for source_name, source_text in (sources or {}).items():
        if not source_text:
            continue
        if predicted_lower in str(source_text).lower():
            source_hits.append(source_name)
            score += 10

    score = min(score, 40)

    # ── Evidence strength bonus ───────────────────────────────────────────────
    strong_sources = {"ncbi_publication", "supplementary_table", "linked_paper"}
    medium_sources = {"ncbi_biosample", "ncbi_accession", "ncbi_experiment"}

    if any(s in source_hits for s in strong_sources):
        score += 20
        flags.append("publication_confirmed")
        log.debug("calculate_confidence: %s: publication confirmed (+20)", field_name)
    elif any(s in source_hits for s in medium_sources):
        score += 10
        flags.append("biosample_confirmed")
        log.debug("calculate_confidence: %s: biosample confirmed (+10)", field_name)

    if "ncbi_accession" in source_hits:
        score += 10
        flags.append("accession_keyword_found")

    # ── Conflict detection ────────────────────────────────────────────────────
    if "##" in predicted_str:
        score -= 20
        flags.append("conflict_detected")
        log.debug("calculate_confidence: %s: conflict marker ## detected (-20)", field_name)

    score = max(0, min(100, score))

    if score >= 70:
        label = "high"
    elif score >= 40:
        label = "medium"
    else:
        label = "low"

    n = len(source_hits)
    explanation = (
        f"Field '{field_name}': '{predicted_str}' confirmed in {n} source(s) "
        f"({', '.join(source_hits) if source_hits else 'none'}). "
        f"Flags: {flags or ['none']}."
    )

    log.debug("calculate_confidence: %s: score=%d label=%s hits=%s flags=%s",
              field_name, score, label, source_hits, flags)

    return {"score": score, "label": label, "flags": flags, "explanation": explanation}


# ── Signals-based scorer (pipeline entry point) ───────────────────────────────

def normalize_country(name: Optional[str]) -> Optional[str]:
    """Normalize common country name variants for equality checks."""
    if not name:
        return None
    name = name.strip().lower()
    mapping = {
        "usa": "united states",
        "u.s.a.": "united states",
        "u.s.": "united states",
        "us": "united states",
        "united states of america": "united states",
        "uk": "united kingdom",
        "u.k.": "united kingdom",
        "england": "united kingdom",
    }
    return mapping.get(name, name)


def compute_confidence_score_and_tier(
    signals: Dict[str, Any],
    rules: Optional[Dict[str, Any]] = None,
) -> Tuple[int, str, List[str]]:
    """
    Compute confidence score and tier from structured pipeline signals.

    Field-agnostic: reads 'predicted_field' / 'genbank_field' (generic) with
    fallback to legacy 'predicted_country' / 'genbank_country' keys.
    Which field is scored is set by signals.get('field_name', 'country').

    Expected signals keys:
        has_geo_loc_name:        bool
        has_pubmed:              bool
        accession_found_in_text: bool
        predicted_field:         str|None  (generic; overrides predicted_country)
        genbank_field:           str|None  (generic; overrides genbank_country)
        field_name:              str       (default 'country')
        predicted_country:       str|None  (legacy)
        genbank_country:         str|None  (legacy)
        num_publications:        int
        missing_key_fields:      bool
        known_failure_pattern:   bool

    Returns:
        score (0-100), tier ('high'/'medium'/'low'), explanations list
    """
    if rules is None:
        rules = set_rules()

    field_name = str(signals.get("field_name", "country"))
    log.debug("compute_confidence: scoring field='%s'", field_name)

    score = 0
    explanations: List[str] = []

    # ── Signal 1: Direct evidence ─────────────────────────────────────────────
    has_geo = bool(signals.get("has_geo_loc_name"))
    has_pubmed = bool(signals.get("has_pubmed"))
    accession_in_text = bool(signals.get("accession_found_in_text"))
    direct_cfg = rules["direct_evidence"]

    if has_geo and has_pubmed and accession_in_text:
        score += direct_cfg["explicit_geo_pubmed_text"]
        explanations.append(
            "Accession linked to a value in GenBank and associated publication text."
        )
    elif has_geo and has_pubmed:
        score += direct_cfg["geo_and_pubmed"]
        explanations.append("GenBank structured field and linked publication found.")
    elif has_geo:
        score += direct_cfg["geo_only"]
        explanations.append("GenBank structured field present.")
    elif accession_in_text:
        score += direct_cfg["accession_in_text_only"]
        explanations.append("Accession keyword found in extracted external text.")

    # ── Signal 2: Cross-source consistency (field-agnostic) ───────────────────
    pred_val = (
        signals.get("predicted_field")
        or signals.get("predicted_country")
    )
    ncbi_val = (
        signals.get("genbank_field")
        or signals.get("genbank_country")
    )

    if field_name == "country":
        if pred_val:
            pred_val = _smart_country_lookup(str(pred_val).lower())
        if ncbi_val:
            ncbi_val = _smart_country_lookup(str(ncbi_val).lower())

    log.debug("compute_confidence: predicted='%s' ncbi='%s'", pred_val, ncbi_val)

    cons_cfg = rules["consistency"]
    if ncbi_val and pred_val:
        result = calculate_confidence(
            field_name,
            str(pred_val),
            {"ncbi_structured_field": str(ncbi_val)},
        )
        if str(pred_val).lower() in str(ncbi_val).lower() or \
           str(ncbi_val).lower() in str(pred_val).lower():
            score += cons_cfg["match"]
            explanations.append(
                f"Predicted {field_name} matches NCBI structured metadata."
            )
        else:
            score += cons_cfg["contradiction"]
            explanations.append(
                f"Conflict between predicted {field_name} and NCBI metadata."
            )
    else:
        if has_geo or has_pubmed or accession_in_text:
            score += cons_cfg["no_contradiction"]
            explanations.append("No contradiction detected across available sources.")

    # ── Signal 3: Evidence density ────────────────────────────────────────────
    num_pubs = int(signals.get("num_publications", 0))
    dens_cfg = rules["evidence_density"]

    if num_pubs >= 2:
        score += dens_cfg["two_or_more_pubs"]
        explanations.append("Multiple linked publications available.")
    elif num_pubs == 1:
        score += dens_cfg["one_pub"]
        explanations.append("One linked publication available.")

    # ── Signal 4: Risk penalties ──────────────────────────────────────────────
    risk_cfg = rules["risk_penalties"]

    if signals.get("missing_key_fields"):
        score += risk_cfg["missing_key_fields"]
        explanations.append("Missing key metadata fields (higher uncertainty).")

    if signals.get("known_failure_pattern"):
        score += risk_cfg["known_failure_pattern"]
        explanations.append("Accession matches a known risky/failure pattern.")

    score = max(0, min(100, score))

    tiers = rules["tiers"]
    if score >= tiers["high_min"]:
        tier = "high"
    elif score >= tiers["medium_min"]:
        tier = "medium"
    else:
        tier = "low"

    explanations = explanations[:3]
    log.debug("compute_confidence: field='%s' score=%d tier=%s", field_name, score, tier)
    return score, tier, explanations
