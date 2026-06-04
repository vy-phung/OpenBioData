"""
ncbi_resolver.py -- NCBI Accession Resolver
Resolves any NCBI identifier (BioProject, BioSample, GenBank, SRR/SRX)
into a standardized dict keyed by BioSample ID (SAMN.../SAMEA...).

Strategy: use esearch with field tags and efetch/record parsing instead of
elink, because Biopython's Entrez.read() chokes on NCBI's DOCTYPE external
entity references in elink responses.

Output format for every entry:
{
  'SAMN23469632': {
    'bioproject':  'PRJNA783802',  # '' if not found
    'biosample':   'SAMN23469632', # '' if not found
    'accession':   'OL757400',     # '' if not found
    'experiment':  'SRR17084312'   # '' if not found
  }
}
"""

import re
import time
import json as _json
import os
import xml.etree.ElementTree as ET
import urllib.request as _urllib_req
import urllib.error as _urllib_err
import http.client as _http_client

from Bio import Entrez

# ── Entrez config ──────────────────────────────────────────────────────────────
Entrez.email = 'vyphung1901@gmail.com'
# Fill in your NCBI API key (free at ncbi.nlm.nih.gov/account).
# Leave as None to use the unauthenticated 3 req/sec limit.
# With a key you get 10 req/sec, essential for BioProject batch runs.
_NCBI_API_KEY = None   # e.g. 'abc123def456...'
if _NCBI_API_KEY:
    Entrez.api_key = _NCBI_API_KEY

_SLEEP = 0.35       # seconds between every NCBI API call (safe for 3 req/s unauthenticated limit)
MAX_SAMPLES = 50    # safety cap for BioProject -> BioSample expansion
_NCBI_API_KEY = os.environ.get("NCBI_API_KEY") or _NCBI_API_KEY
if _NCBI_API_KEY:
    Entrez.api_key = _NCBI_API_KEY
    _SLEEP = 0.12   # 10 req/s with API key


# ── 1. Identifier detection ────────────────────────────────────────────────────

def detect_accession_type(accession_id: str) -> str:
    """
    Detect NCBI identifier type from the accession string.
    Returns one of: 'bioproject', 'biosample', 'genbank',
                    'sra_experiment', 'sra_run', 'unknown'
    """
    acc = accession_id.strip().upper()

    if re.match(r'^PRJ[A-Z]{2}\d+$', acc):               # PRJNA783802, PRJEB12345
        return 'bioproject'
    if re.match(r'^SAM[A-Z]{1,2}\d+$', acc):             # SAMN23469632, SAMEA12345
        return 'biosample'
    if re.match(r'^SRX\d+$', acc):                        # SRX12345678
        return 'sra_experiment'
    if re.match(r'^SRR\d+$', acc):                        # SRR17084312
        return 'sra_run'
    if re.match(r'^ERR\d+$', acc):                        # ENA run
        return 'sra_run'
    # Named GenBank prefixes
    if re.match(r'^(NC_|OL|MT|MW|MZ|PQ|OM|MN|MK|KY|KX|KU|JN|FJ)[A-Z0-9_]+$', acc):
        return 'genbank'
    # Generic GenBank: 1-3 letters + 5-8 digits (+ optional .version)
    # Covers nucleotide (1-2 letters) and protein accessions (3 letters, e.g. ACK77584)
    if re.match(r'^[A-Z]{1,3}\d{5,8}(\.\d+)?$', acc):
        return 'genbank'

    return 'unknown'


# ── 2. Internal helpers ────────────────────────────────────────────────────────

def _empty_record(accession: str = '') -> dict:
    """Return a blank record dict (all fields are empty strings, never None)."""
    return {
        'bioproject': '',
        'biosample':  '',
        'accession':  accession,
        'experiment': '',
    }


def _safe_sleep():
    time.sleep(_SLEEP)


def _urlopen_with_retry(url: str, max_retries: int = 3, base_delay: float = 5.0) -> bytes:
    """HTTP GET with retry on 429/500/503 and transient network errors."""
    delay = base_delay
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(max_retries + 1):
        try:
            with _urllib_req.urlopen(url, timeout=30) as resp:
                return resp.read()
        except _urllib_err.HTTPError as exc:
            last_exc = exc
            if exc.code in (429, 500, 503) and attempt < max_retries:
                print(f'  [NCBI] HTTP {exc.code} — retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})')
                time.sleep(delay)
                delay *= 2
            else:
                raise
        except (_http_client.IncompleteRead, ConnectionError, TimeoutError) as exc:
            last_exc = exc
            if attempt < max_retries:
                print(f'  [NCBI] Network error ({type(exc).__name__}) — retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})')
                time.sleep(delay)
                delay *= 2
            else:
                raise
        except Exception:
            raise
    raise last_exc


def _resolve_via_ena(bioproject_id: str, max_samples: int = MAX_SAMPLES) -> dict:
    """
    ENA Portal API fallback for European BioProjects (PRJEB prefix) whose
    samples are NOT linked to NCBI biosample via the [BioProject] field.

    Returns {samea_id: record_dict} keyed by ENA BioSample accession (SAMEA...).
    Sets bioproject + experiment (ERR run accession) directly from ENA data.
    Returns {} on any failure.
    """
    url = (
        f'https://www.ebi.ac.uk/ena/portal/api/filereport'
        f'?accession={bioproject_id}&result=read_run'
        f'&fields=run_accession,sample_accession'
        f'&limit={max_samples}&format=json'
    )
    try:
        raw = _urlopen_with_retry(url)
        _safe_sleep()
        data = _json.loads(raw.decode('utf-8', errors='replace'))
    except Exception as exc:
        print(f'  [ENA] {bioproject_id}: {exc}')
        return {}

    sample_to_run: dict = {}
    for rec in data or []:
        sam = (rec.get('sample_accession') or '').strip()
        run = (rec.get('run_accession') or '').strip()
        if sam and sam.startswith('SAM') and sam not in sample_to_run:
            sample_to_run[sam] = run

    if not sample_to_run:
        print(f'  [ENA] No samples found for {bioproject_id} via ENA API')
        return {}

    print(f'  [ENA] Found {len(sample_to_run)} sample(s) for {bioproject_id} via ENA API')
    result = {}
    for sam_id, run_id in list(sample_to_run.items())[:max_samples]:
        result[sam_id] = {
            'bioproject': bioproject_id,
            'biosample':  sam_id,
            'accession':  '',
            'experiment': run_id,
        }
    return result


def get_bioproject_from_biosample(biosample_id: str) -> str:
    """
    Find the BioProject (PRJNA...) linked to a BioSample.
    Tries three methods in order:
      1. Parse SampleData XML in esummary response
      2. Scan the raw Links string
      3. esearch bioproject DB with BioSample field tag
    Returns '' on any failure.
    """
    try:
        handle = Entrez.esearch(db='biosample', term=biosample_id)
        rec = Entrez.read(handle); handle.close()
        _safe_sleep()
        if not rec['IdList']:
            return ''
        bs_uid = rec['IdList'][0]

        handle = Entrez.esummary(db='biosample', id=bs_uid)
        summary = Entrez.read(handle); handle.close()
        _safe_sleep()

        doc = summary['DocumentSummarySet']['DocumentSummary'][0]

        # Method 1 -- parse SampleData XML for Link elements
        sample_data = doc.get('SampleData', '')
        if sample_data:
            try:
                root = ET.fromstring(sample_data)
                for link in root.findall('.//Link'):
                    label = link.get('label', '')
                    if link.get('type') == 'bioproject' or label.startswith('PRJ'):
                        if label:
                            return label
                for id_el in root.iter():
                    if id_el.text and id_el.text.strip().startswith('PRJ'):
                        return id_el.text.strip()
            except ET.ParseError:
                pass

        # Method 2 -- scan raw Links string
        links_str = str(doc.get('Links', ''))
        for part in re.split(r'\s+|,', links_str):
            if part.startswith('PRJ'):
                return part

        # Method 3 -- esearch bioproject DB using biosample ID as query
        handle = Entrez.esearch(db='bioproject',
                                term=f'{biosample_id}[BioSample]')
        bp_rec = Entrez.read(handle); handle.close()
        _safe_sleep()
        if bp_rec['IdList']:
            bp_uid = bp_rec['IdList'][0]
            handle = Entrez.esummary(db='bioproject', id=bp_uid)
            bp_sum = Entrez.read(handle); handle.close()
            _safe_sleep()
            bp_doc = bp_sum['DocumentSummarySet']['DocumentSummary'][0]
            project_acc = (bp_doc.get('Project_Acc', '')
                           or bp_doc.get('Accession', ''))
            if project_acc.startswith('PRJ'):
                return project_acc

    except Exception as e:
        print(f'  [get_bioproject] {biosample_id}: {e}')

    return ''


def get_genbank_from_biosample(biosample_id: str) -> str:
    """
    Find the first linked GenBank nucleotide accession for a BioSample.
    Uses esearch with [BioSample] field tag on the nucleotide database,
    then efetches the accession text.
    Returns accession without version suffix (e.g. 'OL757400'), or ''.
    """
    try:
        # esearch nucleotide DB with BioSample field tag
        handle = Entrez.esearch(db='nucleotide',
                                term=f'{biosample_id}[BioSample]',
                                retmax=5)
        rec = Entrez.read(handle); handle.close()
        _safe_sleep()
        if not rec['IdList']:
            return ''

        nuc_uid = rec['IdList'][0]
        handle = Entrez.efetch(db='nucleotide', id=nuc_uid,
                               rettype='acc', retmode='text')
        accession = handle.read().strip(); handle.close()
        _safe_sleep()
        return accession.split('.')[0]   # strip version suffix

    except Exception as e:
        print(f'  [get_genbank] {biosample_id}: {e}')

    return ''


def get_sra_from_biosample(biosample_id: str) -> str:
    """
    Find the first SRR run linked to a BioSample.
    Uses esearch with [BioSample] field tag on the SRA database,
    then esummary to extract the SRR from the Runs field.
    Returns the SRR accession (e.g. 'SRR17084312'), or ''.
    """
    try:
        handle = Entrez.esearch(db='sra',
                                term=f'{biosample_id}[BioSample]',
                                retmax=5)
        rec = Entrez.read(handle); handle.close()
        _safe_sleep()
        if not rec['IdList']:
            return ''

        sra_uid = rec['IdList'][0]
        handle = Entrez.esummary(db='sra', id=sra_uid)
        sra_summary = Entrez.read(handle); handle.close()
        _safe_sleep()

        runs_str = str(sra_summary[0].get('Runs', ''))
        srr_match = re.search(r'SRR\d+', runs_str)
        return srr_match.group(0) if srr_match else ''

    except Exception as e:
        print(f'  [get_sra] {biosample_id}: {e}')

    return ''


# ── 3. Four resolver functions ─────────────────────────────────────────────────

def resolve_from_genbank(accession: str) -> dict:
    """
    GenBank accession -> find parent BioSample -> build full record.
    Fetches the GenBank flat-file record and parses the DBLINK/BioSample field.
    Falls back to esearch biosample with accession as query.
    Returns {biosample_id: record} or {accession: record} if no BioSample found.
    """
    print(f'  [GenBank] Resolving {accession}...')
    result = _empty_record(accession)
    biosample_id = ''

    try:
        # Step 1 -- search nucleotide DB to verify and get UID
        handle = Entrez.esearch(db='nucleotide',
                                term=accession, retmax=1)
        rec = Entrez.read(handle); handle.close()
        _safe_sleep()

        if not rec['IdList']:
            print(f'  [GenBank] WARNING: {accession} not found in nucleotide DB')
            return {accession: result}

        nuc_uid = rec['IdList'][0]

        # Step 2 -- efetch GenBank flat-file, parse BioSample from DBLINK
        handle = Entrez.efetch(db='nucleotide', id=nuc_uid,
                               rettype='gb', retmode='text')
        gb_text = handle.read(); handle.close()
        _safe_sleep()

        # Parse DBLINK section for BioSample
        for line in gb_text.splitlines():
            line = line.strip()
            if line.startswith('BioSample:'):
                bs_candidate = line.split(':', 1)[1].strip()
                if bs_candidate.startswith('SAM'):
                    biosample_id = bs_candidate
                    break
            # Also handle continuation lines like "BioProject: PRJNA123\n BioSample: SAMN..."
            m = re.search(r'BioSample[:\s]+(SAM[A-Z0-9]+)', line)
            if m:
                biosample_id = m.group(1)
                break

        # Step 3 -- fallback: esearch biosample DB with the accession
        if not biosample_id:
            handle = Entrez.esearch(db='biosample',
                                    term=f'{accession}[Nucleotide Accession]')
            bs_rec = Entrez.read(handle); handle.close()
            _safe_sleep()
            if bs_rec['IdList']:
                handle = Entrez.esummary(db='biosample', id=bs_rec['IdList'][0])
                bs_sum = Entrez.read(handle); handle.close()
                _safe_sleep()
                doc = bs_sum['DocumentSummarySet']['DocumentSummary'][0]
                candidate = doc.get('Accession', '')
                if candidate.startswith('SAM'):
                    biosample_id = candidate

    except Exception as e:
        print(f'  [GenBank] {accession}: {e}')

    if biosample_id:
        result['biosample']  = biosample_id
        result['bioproject'] = get_bioproject_from_biosample(biosample_id)
        result['experiment'] = get_sra_from_biosample(biosample_id)
        print(f'  [GenBank] {accession} -> BioSample: {biosample_id}')
        return {biosample_id: result}

    # No BioSample link found -- key by accession itself
    print(f'  [GenBank] WARNING: no BioSample found for {accession}')
    return {accession: result}


def resolve_from_biosample(biosample_id: str) -> dict:
    """
    BioSample ID -> resolve all linked identifiers.
    Returns {biosample_id: record}.
    """
    print(f'  [BioSample] Resolving {biosample_id}...')
    record = {
        'bioproject': get_bioproject_from_biosample(biosample_id),
        'biosample':  biosample_id,
        'accession':  get_genbank_from_biosample(biosample_id),
        'experiment': get_sra_from_biosample(biosample_id),
    }
    print(f'  [BioSample] {biosample_id} -> {record}')
    return {biosample_id: record}


def _biosample_ids_from_sra(bioproject_id: str) -> list:
    """
    Fallback: find BioSamples for a BioProject by searching SRA and extracting
    SAMN accessions from each run's ExpXml summary field.
    Returns a deduplicated list of SAMN accession strings.
    """
    samn_ids = []
    seen = set()
    try:
        url = (f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
               f'?db=sra&term={bioproject_id}[bioproject]&retmax={MAX_SAMPLES}'
               f'&retmode=json&email={Entrez.email}')
        data = _json.loads(_urlopen_with_retry(url).decode('utf-8', errors='replace'))
        _safe_sleep()
        # Use .get() guards — NCBI may omit 'idlist' in error/empty responses
        esresult = data.get('esearchresult') or {}
        sra_ids = esresult.get('idlist') or []
        if not isinstance(sra_ids, list):
            sra_ids = []
        if not sra_ids:
            return []
        print(f'  [BioProject-SRA] Found {len(sra_ids)} SRA records for {bioproject_id}')

        # Batch esummary to get ExpXml which contains BioSample accession
        url2 = (f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'
                f'?db=sra&id={",".join(sra_ids)}&retmode=json&email={Entrez.email}')
        data2 = _json.loads(_urlopen_with_retry(url2).decode('utf-8', errors='replace'))
        _safe_sleep()
        for uid, rec in data2.get('result', {}).items():
            if uid == 'uids':
                continue
            # NCBI SRA esummary JSON uses lowercase 'expxml'
            exp_xml = rec.get('expxml', '') or rec.get('ExpXml', '') or rec.get('ExpXML', '')
            sam_match = re.search(r'SAM[A-Z]+\d+', str(exp_xml))
            if sam_match:
                sam = sam_match.group(0)
                if sam not in seen:
                    seen.add(sam)
                    samn_ids.append(sam)
    except Exception as e:
        print(f'  [BioProject-SRA] {bioproject_id}: {e}')
    return samn_ids


def resolve_from_bioproject(bioproject_id: str, max_samples: int = MAX_SAMPLES) -> dict:
    """
    BioProject -> find ALL linked BioSamples -> resolve each one.

    Resolution strategy (in order):
      1. esearch biosample DB with bioproject ID (works when BioSamples are
         registered in the biosample database with a BioProject link)
      2. Raw elink bioproject->biosample via Entrez HTTP URL
      3. SRA fallback: esearch SRA with [bioproject] and parse BioSample from
         ExpXml (required for SRA-only projects like metagenomics BioProjects)
      4. ENA API fallback (for PRJEB projects not mirrored in NCBI biosample DB)

    Returns multi-entry dict keyed by BioSample ID (up to max_samples).
    """
    import urllib.parse
    print(f'  [BioProject] Resolving {bioproject_id} (cap={max_samples})...')
    biosample_ids = []

    # ── Strategy 1: esearch biosample DB ──────────────────────────────────────
    try:
        handle = Entrez.esearch(db='biosample',
                                term=f'{bioproject_id}[BioProject]',
                                retmax=max_samples)
        rec = Entrez.read(handle); handle.close()
        _safe_sleep()
        if rec['IdList']:
            bs_uids = rec['IdList']
            total = int(rec.get('Count', len(bs_uids)))
            if total > MAX_SAMPLES:
                print(f'  [BioProject] WARNING: {bioproject_id} has {total} BioSamples. '
                      f'Processing first {MAX_SAMPLES}.')
            print(f'  [BioProject] Strategy 1 found {len(bs_uids)} BioSample UIDs')
            handle = Entrez.esummary(db='biosample', id=','.join(bs_uids))
            summary = Entrez.read(handle); handle.close()
            _safe_sleep()
            for doc in summary['DocumentSummarySet']['DocumentSummary']:
                acc = doc.get('Accession', '')
                if acc.startswith('SAM'):
                    biosample_ids.append(acc)
    except Exception as e:
        print(f'  [BioProject] Strategy 1 failed: {e}')

    # ── Strategy 1B: raw HTTP biosample esearch fallback (when Biopython fails) ──
    # NCBI sometimes returns "Database is not supported: biosample" to Biopython's
    # XML-mode requests; the JSON-mode raw URL is a more direct and resilient path.
    if not biosample_ids:
        try:
            import urllib.parse as _urlparse
            s1b_url = (
                f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
                f'?db=biosample&term={_urlparse.quote(bioproject_id + "[BioProject]")}'
                f'&retmax={max_samples}&retmode=json&email={Entrez.email}'
            )
            s1b_data = _json.loads(
                _urlopen_with_retry(s1b_url).decode('utf-8', errors='replace')
            )
            _safe_sleep()
            s1b_uids = (s1b_data.get('esearchresult') or {}).get('idlist') or []
            if not isinstance(s1b_uids, list):
                s1b_uids = []
            if s1b_uids:
                print(f'  [BioProject] Strategy 1B found {len(s1b_uids)} UIDs via raw URL')
                try:
                    handle = Entrez.esummary(db='biosample', id=','.join(s1b_uids))
                    summary_1b = Entrez.read(handle); handle.close()
                    _safe_sleep()
                    for doc in summary_1b['DocumentSummarySet']['DocumentSummary']:
                        acc = doc.get('Accession', '')
                        if acc.startswith('SAM') and acc not in biosample_ids:
                            biosample_ids.append(acc)
                except Exception:
                    # Biopython esummary also failed — try raw JSON esummary
                    s1b_sum_url = (
                        f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'
                        f'?db=biosample&id={",".join(s1b_uids)}&retmode=json&email={Entrez.email}'
                    )
                    s1b_sum_data = _json.loads(
                        _urlopen_with_retry(s1b_sum_url).decode('utf-8', errors='replace')
                    )
                    _safe_sleep()
                    result_map = s1b_sum_data.get('result') or {}
                    for uid in s1b_uids:
                        doc = result_map.get(uid) or {}
                        acc = (doc.get('accession') or '').strip()
                        if acc.startswith('SAM') and acc not in biosample_ids:
                            biosample_ids.append(acc)
                if biosample_ids:
                    print(f'  [BioProject] Strategy 1B yielded {len(biosample_ids)} biosample accessions')
        except Exception as e:
            print(f'  [BioProject] Strategy 1B failed: {e}')

    # ── Strategy 2: raw elink bioproject->biosample ────────────────────────────
    if not biosample_ids:
        try:
            handle = Entrez.esearch(db='bioproject', term=bioproject_id)
            bp_rec = Entrez.read(handle); handle.close()
            _safe_sleep()
            if bp_rec['IdList']:
                bp_uid = bp_rec['IdList'][0]
                params = urllib.parse.urlencode({
                    'dbfrom': 'bioproject',
                    'db': 'biosample',
                    'id': bp_uid,
                    'email': Entrez.email,
                })
                url = (f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
                       f'elink.fcgi?{params}')
                raw = _urlopen_with_retry(url).decode('utf-8', errors='replace')
                _safe_sleep()
                # Only extract IDs inside the biosample LinkSetDb block (not SRA or other dbs)
                bs_uids_raw = []
                try:
                    elink_root = ET.fromstring(raw)
                    for linkset in elink_root.findall('.//LinkSetDb'):
                        dbto = linkset.findtext('DbTo', '')
                        if dbto.lower() == 'biosample':
                            for link in linkset.findall('Link/Id'):
                                if link.text and link.text != bp_uid:
                                    bs_uids_raw.append(link.text)
                except ET.ParseError:
                    # XML parse failed — fall back to regex but filter by excluding source ID
                    all_ids = re.findall(r'<Id>(\d+)</Id>', raw)
                    bs_uids_raw = [u for u in all_ids if u != bp_uid]
                if bs_uids_raw:
                    print(f'  [BioProject] Strategy 2 found {len(bs_uids_raw)} raw UIDs via elink '
                          f'(elink includes SRA/PubMed IDs — filtering for biosample accessions…)')
                    # Batch esummary: 1 call instead of up to 36 individual calls
                    batch = bs_uids_raw[:max_samples]
                    try:
                        handle = Entrez.esummary(db='biosample', id=','.join(batch))
                        sum_batch = Entrez.read(handle); handle.close()
                        _safe_sleep()
                        for doc in sum_batch.get('DocumentSummarySet', {}).get('DocumentSummary', []):
                            acc = doc.get('Accession', '')
                            if acc.startswith('SAM') and acc not in biosample_ids:
                                biosample_ids.append(acc)
                        print(f'  [BioProject] Strategy 2 yielded {len(biosample_ids)} valid biosample accessions')
                    except Exception:
                        # Fallback: individual queries (slower but handles partial errors)
                        for uid in batch:
                            try:
                                handle = Entrez.esummary(db='biosample', id=uid)
                                sum2 = Entrez.read(handle); handle.close()
                                _safe_sleep()
                                doc = sum2['DocumentSummarySet']['DocumentSummary'][0]
                                acc = doc.get('Accession', '')
                                if acc.startswith('SAM') and acc not in biosample_ids:
                                    biosample_ids.append(acc)
                            except Exception:
                                pass
        except Exception as e:
            print(f'  [BioProject] Strategy 2 failed: {e}')

    # ── Strategy 3: SRA fallback (metagenomics / SRA-only projects) ───────────
    if not biosample_ids:
        print(f'  [BioProject] Trying SRA fallback for {bioproject_id}...')
        biosample_ids = _biosample_ids_from_sra(bioproject_id)

    # ── Strategy 4: ENA API fallback (for PRJEB projects not mirrored in NCBI) ──
    # PRJEB projects have SAMEA samples that may not be linked to [BioProject] in
    # NCBI's biosample DB. The ENA Portal API always has the authoritative list.
    if not biosample_ids and bioproject_id.upper().startswith('PRJEB'):
        print(f'  [BioProject] Trying ENA API fallback for {bioproject_id}...')
        ena_result = _resolve_via_ena(bioproject_id, max_samples)
        if ena_result:
            print(f'  [BioProject] {bioproject_id} -> {len(ena_result)} samples via ENA API')
            return ena_result

    if not biosample_ids:
        print(f'  [BioProject] WARNING: no BioSamples found for {bioproject_id}')
        return {}

    # Deduplicate and cap
    seen_ids: set = set()
    unique_ids: list = []
    for bs in biosample_ids:
        if bs not in seen_ids:
            seen_ids.add(bs)
            unique_ids.append(bs)
    biosample_ids = unique_ids[:max_samples]
    if len(unique_ids) > max_samples:
        print(f'  [BioProject] Capped at {max_samples} of {len(unique_ids)} unique BioSamples.')

    # Resolve each BioSample
    result = {}
    for bs_id in biosample_ids:
        entry = resolve_from_biosample(bs_id)
        result.update(entry)

    print(f'  [BioProject] {bioproject_id} -> {len(result)} BioSamples resolved')
    return result


def resolve_from_sra(sra_id: str) -> dict:
    """
    SRR or SRX -> find parent BioSample via esearch biosample with SRA field tag,
    then resolve that BioSample normally.
    Returns {biosample_id: record} or {sra_id: empty_record} on failure.
    """
    print(f'  [SRA] Resolving {sra_id}...')

    try:
        # Preferred: search biosample DB with SRA accession
        handle = Entrez.esearch(db='biosample',
                                term=f'{sra_id}[SRA]')
        rec = Entrez.read(handle); handle.close()
        _safe_sleep()

        if rec['IdList']:
            bs_uid = rec['IdList'][0]
            handle = Entrez.esummary(db='biosample', id=bs_uid)
            summary = Entrez.read(handle); handle.close()
            _safe_sleep()
            bs_accession = (summary['DocumentSummarySet']
                            ['DocumentSummary'][0].get('Accession', ''))
            if bs_accession.startswith('SAM'):
                return resolve_from_biosample(bs_accession)

        # Fallback: search SRA DB then read its esummary for BioSample field
        handle = Entrez.esearch(db='sra', term=sra_id)
        rec2 = Entrez.read(handle); handle.close()
        _safe_sleep()
        if rec2['IdList']:
            handle = Entrez.esummary(db='sra', id=rec2['IdList'][0])
            sra_sum = Entrez.read(handle); handle.close()
            _safe_sleep()
            # BioSample accession appears in the ExpXml field
            exp_xml_str = str(sra_sum[0].get('ExpXml', ''))
            sam_match = re.search(r'SAM[A-Z]+\d+', exp_xml_str)
            if sam_match:
                return resolve_from_biosample(sam_match.group(0))

    except Exception as e:
        print(f'  [SRA] {sra_id}: {e}')

    return {sra_id: {**_empty_record(), 'experiment': sra_id}}


# ── 4. Master entry point ──────────────────────────────────────────────────────

def resolve_accessions(user_input: str, max_samples: int = MAX_SAMPLES) -> dict:
    """
    Main entry point. Accepts any single NCBI identifier string.
    Auto-detects the type and routes to the appropriate resolver.

    Returns a standardized dict keyed by BioSample ID (SAMN.../SAMEA...).
    Every value dict has exactly these keys: bioproject, biosample, accession, experiment.
    All values are strings -- never None.
    """
    user_input = user_input.strip()
    acc_type = detect_accession_type(user_input)
    print(f'[resolve_accessions] Input: {user_input!r} -> detected type: {acc_type}')

    if acc_type == 'bioproject':
        return resolve_from_bioproject(user_input, max_samples=max_samples)
    elif acc_type == 'biosample':
        return resolve_from_biosample(user_input)
    elif acc_type == 'genbank':
        return resolve_from_genbank(user_input)
    elif acc_type in ('sra_run', 'sra_experiment'):
        return resolve_from_sra(user_input)
    else:
        print(f'[resolve_accessions] WARNING: Unknown type for {user_input!r}. '
              f'Trying as GenBank.')
        return resolve_from_genbank(user_input)
