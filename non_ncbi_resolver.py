"""
non_ncbi_resolver.py — Handler for non-NCBI database accession IDs.

Detects and wraps accession IDs from databases outside NCBI:
  MassIVE (MSV...), PRIDE (PXD...), MetaboLights (MTBLS...),
  MGnify (MGYS...), BioStudies (S-BSST/S-BIAD...), EGA (EGA[DS]...),
  and user-described "other" databases.

For non-NCBI samples:
  - NCBI fetch is skipped entirely
  - Smart search uses database-specific keywords
  - Metadata extraction pipeline runs normally

Public API
----------
detect_non_ncbi_database(acc_id)        -> str  (db name or '')
is_non_ncbi_accession(acc_id)           -> bool
build_non_ncbi_entry(acc_id, database, is_project) -> {acc_id: entry_dict}
get_search_keywords(acc_id, database)   -> list[str]
"""

import re
import json

try:
    import requests as _req
except ImportError:
    _req = None

# ── Known non-NCBI database patterns ─────────────────────────────────────────

_NON_NCBI_PATTERNS = [
    (re.compile(r'^MSV\d+$',   re.IGNORECASE), 'MassIVE'),
    (re.compile(r'^PXD\d+$',   re.IGNORECASE), 'PRIDE'),
    (re.compile(r'^MTBLS\d+$', re.IGNORECASE), 'MetaboLights'),
    (re.compile(r'^MGYS\d+$',  re.IGNORECASE), 'MGnify'),
    (re.compile(r'^S-BS[A-Z]+\d+$',  re.IGNORECASE), 'BioStudies'),
    (re.compile(r'^S-BIAD\d+$',      re.IGNORECASE), 'BioStudies'),
    (re.compile(r'^EGAD\d+$',  re.IGNORECASE), 'EGA'),
    (re.compile(r'^EGAS\d+$',  re.IGNORECASE), 'EGA'),
    (re.compile(r'^EGAN\d+$',  re.IGNORECASE), 'EGA'),
    (re.compile(r'^EGAX\d+$',  re.IGNORECASE), 'EGA'),
    # PDB: 4-char alphanumeric starting with a digit, optional _chain suffix
    (re.compile(r'^\d[A-Z0-9]{3}(_[A-Z])?$', re.IGNORECASE), 'PDB'),
]

# Search keywords associated with each database for Google/web searches
_DB_SEARCH_TERMS = {
    'MassIVE':     ['MassIVE', 'massive.ucsd.edu'],
    'PRIDE':       ['PRIDE', 'ProteomeXchange'],
    'MetaboLights':['MetaboLights', 'EBI metabolomics'],
    'MGnify':      ['MGnify', 'EBI metagenomics'],
    'BioStudies':  ['BioStudies', 'EBI'],
    'EGA':         ['EGA', 'European Genome-phenome Archive'],
    'PDB':         ['PDB', 'RCSB Protein Data Bank', 'rcsb.org'],
}


def detect_non_ncbi_database(acc_id: str) -> str:
    """Return the database name for a known non-NCBI accession, or ''."""
    acc = (acc_id or '').strip().upper()
    for pattern, db_name in _NON_NCBI_PATTERNS:
        if pattern.match(acc):
            return db_name
    return ''


def is_non_ncbi_accession(acc_id: str) -> bool:
    """Return True if the accession matches a known non-NCBI database pattern."""
    return bool(detect_non_ncbi_database(acc_id))


def build_non_ncbi_entry(acc_id: str, database: str = '', is_project: bool = False) -> dict:
    """
    Build a pipeline-compatible entry for a non-NCBI sample.

    Sets NCBI identifier fields to '' so the pipeline skips NCBI fetching,
    and adds _source_database / _is_project for downstream logic.

    Returns:
        {acc_id: {bioproject, biosample, accession, experiment,
                  _source_database, _is_project}}
    """
    db = database or detect_non_ncbi_database(acc_id) or 'unknown'
    return {
        acc_id: {
            'bioproject':       '',
            'biosample':        '',
            'accession':        '',
            'experiment':       '',
            '_source_database': db,
            '_is_project':      is_project,
        }
    }


def get_search_keywords(acc_id: str, database: str = '') -> list:
    """
    Return ordered search keyword strings for finding literature about
    this non-NCBI sample (most specific first).
    """
    db = database or detect_non_ncbi_database(acc_id) or ''
    keywords = [acc_id]
    for term in _DB_SEARCH_TERMS.get(db, [])[:2]:
        keywords.append(f"{acc_id} {term}")
    if db and db not in _DB_SEARCH_TERMS:
        keywords.append(f"{acc_id} {db}")
    return keywords


# ── Guidance text shown in the UI when a project is selected ─────────────────

_PROJECT_GUIDANCE = {
    'MassIVE': (
        "For MassIVE datasets: go to massive.ucsd.edu → search your ID → "
        "open the dataset page → click 'Browse Dataset' or 'Data Files' tab "
        "→ copy the URL from your browser. It looks like: "
        "https://massive.ucsd.edu/ProteoSAFe/dataset_files.jsp?task=…"
    ),
    'PRIDE': (
        "For PRIDE datasets: go to pride.ebi.ac.uk → search your ID → "
        "on the dataset page find the 'Files' tab → copy the page URL."
    ),
    'MetaboLights': (
        "For MetaboLights datasets: go to ebi.ac.uk/metabolights → open your study → "
        "navigate to the 'Files' section → copy the URL."
    ),
    'MGnify': (
        "For MGnify studies: go to ebi.ac.uk/metagenomics → open your study → "
        "go to the 'Samples' tab → copy the URL."
    ),
}

_DEFAULT_PROJECT_GUIDANCE = (
    "Navigate to your database's project/dataset page and find the section that "
    "lists individual samples or data files. Copy that page URL here, or paste a "
    "list of sub-sample IDs (one per line) in the text box below."
)


def get_project_guidance(database: str) -> str:
    """Return guidance text for finding sub-sample URLs in the given database."""
    return _PROJECT_GUIDANCE.get(database, _DEFAULT_PROJECT_GUIDANCE)


# ── Dataset-level metadata fetch ─────────────────────────────────────────────

def fetch_dataset_metadata(project_id: str, database: str = '') -> str:
    """
    Fetch rich dataset-level metadata from the database API and return it as
    plain text suitable for insertion into the LLM context.

    Currently supports MassIVE via the GNPS ProXI v0.1 endpoint.
    Returns '' when the fetch fails or the database is unsupported.
    """
    if _req is None:
        return ''
    db = (database or detect_non_ncbi_database(project_id) or '').upper()
    if 'MASSIVE' not in db and not project_id.upper().startswith('MSV'):
        return ''

    msv_id = project_id.upper().split(' | ')[0].strip()
    url = f"https://gnps.ucsd.edu/ProteoSAFe/proxi/v0.1/datasets/{msv_id}"
    try:
        resp = _req.get(url, timeout=15, headers={'User-Agent': 'BioMetadataAudit/1.0',
                                                   'Accept': 'application/json'})
        if resp.status_code != 200:
            return ''
        data = resp.json()
    except Exception as exc:
        print(f"[fetch_dataset_metadata] {url}: {exc}")
        return ''

    lines = [f"MassIVE Dataset Metadata for {msv_id}:"]
    if data.get('title'):
        lines.append(f"  Title: {data['title']}")
    if data.get('summary'):
        lines.append(f"  Summary: {data['summary']}")

    species_list = []
    for sp_group in (data.get('species') or []):
        name_entry = next((e for e in sp_group if e.get('accession') == 'MS:1001469'), None)
        taxid_entry = next((e for e in sp_group if e.get('accession') == 'MS:1001467'), None)
        if name_entry:
            species_str = name_entry.get('value', '')
            if taxid_entry:
                species_str += f" (NCBITaxon:{taxid_entry.get('value', '')})"
            species_list.append(species_str)
    if species_list:
        lines.append(f"  Species: {'; '.join(species_list)}")

    kw_values = [e.get('value', '') for e in (data.get('keywords') or []) if e.get('value')]
    if kw_values:
        lines.append(f"  Keywords: {', '.join(kw_values)}")

    instrument_names = [e.get('value') or e.get('name', '') for e in (data.get('instruments') or [])]
    if instrument_names:
        lines.append(f"  Instruments: {', '.join(instrument_names)}")

    pub_list = []
    for ref in (data.get('publications') or []):
        # Skip "no manuscript" placeholder accessions (e.g. MS:1002853)
        if ref.get('accession') == 'MS:1002853':
            continue
        doi = ref.get('doi') or ''
        pmid = ref.get('pmid') or ''
        title_r = ref.get('title', '')
        entry = title_r
        if doi:
            entry += f" (DOI: {doi})"
        elif pmid:
            entry += f" (PMID: {pmid})"
        if entry.strip():
            pub_list.append(entry.strip())
    if pub_list:
        lines.append(f"  Publications: {'; '.join(pub_list)}")

    return '\n'.join(lines)


# ── Sub-sample scraping ───────────────────────────────────────────────────────

def _scrape_massive(url: str, msv_id: str, max_samples: int) -> list:
    """Try multiple MassIVE API endpoints to get a file list."""
    if _req is None:
        return []

    # Extract task ID from dataset_files.jsp URL if present
    task_match = re.search(r'task=([a-fA-F0-9]+)', url)
    task_id = task_match.group(1) if task_match else None

    # Probe endpoints in priority order
    candidates = []
    if task_id:
        candidates.append(
            f"https://massive.ucsd.edu/ProteoSAFe/result.jsp?task={task_id}"
            f"&view=download_datasets&block=main"
        )
    if msv_id:
        candidates += [
            f"https://gnps.ucsd.edu/ProteoSAFe/proxi/v0.1/datasets/{msv_id}",
            f"https://gnps2.org/datasetsummary?task={msv_id}",
        ]

    _FILE_EXTS = re.compile(
        r'[\w\-]+\.(?:mzML|mzXML|raw|mgf|mzData|imzML|d|RAW|WIFF|thermo)\b',
        re.IGNORECASE
    )
    _FOLDER_NAMES = re.compile(
        r'"(?:name|title|filename|collection)":\s*"([^"]+)"',
        re.IGNORECASE
    )

    for api_url in candidates:
        try:
            resp = _req.get(api_url, timeout=15,
                            headers={'User-Agent': 'BioMetadataAudit/1.0',
                                     'Accept': 'application/json, text/html'})
            if resp.status_code != 200:
                continue
            text = resp.text

            # Try JSON parsing first
            try:
                data = json.loads(text)
                names = []
                # ProXI v0.1 datasets response: top-level list or {"datasets": [...]}
                items = data if isinstance(data, list) else data.get('samples', data.get('files', []))
                for item in (items if isinstance(items, list) else []):
                    if isinstance(item, dict):
                        n = (item.get('name') or item.get('filename')
                             or item.get('title') or item.get('sample_accession') or '')
                        if n:
                            names.append(n)
                if names:
                    return [{'name': n, 'source': api_url}
                            for n in list(dict.fromkeys(names))[:max_samples]]
            except (json.JSONDecodeError, TypeError):
                pass

            # Try extracting file names from HTML / plain text
            json_names = _FOLDER_NAMES.findall(text)
            file_names = _FILE_EXTS.findall(text)
            combined = list(dict.fromkeys(json_names + file_names))
            if combined:
                return [{'name': n, 'source': api_url}
                        for n in combined[:max_samples]]
        except Exception as exc:
            print(f"[scrape_massive] {api_url}: {exc}")

    return []


def scrape_project_samples(url: str, database: str = '', acc_id: str = '',
                           max_samples: int = 20) -> list:
    """
    Try to enumerate individual sub-sample file/sample names from a project page URL.

    Returns a list of dicts: [{'name': str, 'source': str}, ...]
    Empty list when scraping fails (caller should fall back to treating the
    project as a single entity).
    """
    if not url or _req is None:
        return []

    db_upper = (database or '').upper()
    msv_id_match = re.match(r'^MSV\d+', acc_id.upper()) if acc_id else None
    msv_id = msv_id_match.group(0) if msv_id_match else ''

    if 'massive.ucsd.edu' in url or db_upper == 'MASSIVE':
        return _scrape_massive(url, msv_id or acc_id, max_samples)

    # Generic: fetch URL and extract NCBI-style accessions or common file patterns
    _GENERIC_ACC = re.compile(
        r'\b(SAMN|SAMD|SAME|SRR|ERR|DRR|SRS|ERS)\d{5,}', re.IGNORECASE
    )
    _GENERIC_FILES = re.compile(
        r'[\w\-]+\.(?:fastq|fq|bam|sra|vcf|cram|gz)\b', re.IGNORECASE
    )
    try:
        resp = _req.get(url, timeout=15,
                        headers={'User-Agent': 'BioMetadataAudit/1.0'})
        if resp.status_code == 200:
            text = resp.text
            accs = _GENERIC_ACC.findall(text)
            files = _GENERIC_FILES.findall(text)
            combined = list(dict.fromkeys(accs + files))
            if combined:
                return [{'name': n, 'source': url}
                        for n in combined[:max_samples]]
    except Exception as exc:
        print(f"[scrape_project_samples] {url}: {exc}")

    return []
