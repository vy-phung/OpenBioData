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
                    'sra_experiment', 'sra_run',
                    'geo_series', 'geo_sample', 'unknown'
    """
    acc = accession_id.strip().upper()

    if re.match(r'^PRJ[A-Z]{2}\d+$', acc):               # PRJNA783802, PRJEB12345
        return 'bioproject'
    if re.match(r'^SAM[A-Z]{1,2}\d+$', acc):             # SAMN23469632, SAMEA12345
        return 'biosample'
    if re.match(r'^GSE\d+$', acc):                        # GSE108124
        return 'geo_series'
    if re.match(r'^GSM\d+$', acc):                        # GSM2479427
        return 'geo_sample'
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
        except (_http_client.IncompleteRead, ConnectionError, TimeoutError,
                EOFError, OSError) as exc:
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

def resolve_from_genbank(accession: str, known_bioproject: str = '') -> dict:
    """
    GenBank accession -> find parent BioSample -> build full record.
    Fetches the GenBank flat-file record and parses the DBLINK/BioSample field.
    Falls back to esearch biosample with accession as query.
    Returns {biosample_id: record} or {accession: record} if no BioSample found.

    If the caller already knows the parent BioProject (e.g. this accession
    came from enumerating a BioProject's nucleotide records), pass it as
    known_bioproject to skip the redundant bioproject lookup call and to
    still populate 'bioproject' even when no BioSample link is found.
    """
    print(f'  [GenBank] Resolving {accession}...')
    result = _empty_record(accession)
    result['bioproject'] = known_bioproject
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
        result['bioproject'] = known_bioproject or get_bioproject_from_biosample(biosample_id)
        result['experiment'] = get_sra_from_biosample(biosample_id)
        print(f'  [GenBank] {accession} -> BioSample: {biosample_id}')
        return {biosample_id: result}

    # No BioSample link found -- key by accession itself
    print(f'  [GenBank] WARNING: no BioSample found for {accession}')
    return {accession: result}


def resolve_from_biosample(biosample_id: str, known_bioproject: str = '') -> dict:
    """
    BioSample ID -> resolve all linked identifiers.
    Returns {biosample_id: record}.

    If the caller already knows the parent BioProject (e.g. this BioSample
    came from enumerating a BioProject's sample list), pass it as
    known_bioproject to skip the redundant bioproject lookup call.
    """
    print(f'  [BioSample] Resolving {biosample_id}...')
    record = {
        'bioproject': known_bioproject or get_bioproject_from_biosample(biosample_id),
        'biosample':  biosample_id,
        'accession':  get_genbank_from_biosample(biosample_id),
        'experiment': get_sra_from_biosample(biosample_id),
    }
    print(f'  [BioSample] {biosample_id} -> {record}')
    return {biosample_id: record}


def _biosample_ids_from_sra(bioproject_id: str, max_samples: int = MAX_SAMPLES) -> list:
    """
    Fallback: find samples for a BioProject by searching SRA and extracting
    each run's BioSample accession from its ExpXml summary field. Most SRA
    submissions have a linked BioSample (mandatory since ~2012), but a few
    legacy/unusual records don't -- for those, fall back to the run's own
    SRR/ERR (or SRX/ERX) accession instead of silently dropping the sample.

    Returns a deduplicated list of {'kind': 'biosample'|'experiment', 'id': str}.
    """
    entries = []
    seen = set()
    try:
        url = (f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
               f'?db=sra&term={bioproject_id}[bioproject]&retmax={max_samples}'
               f'&retmode=json&email={Entrez.email}')
        data = _json.loads(_urlopen_with_retry(url).decode('utf-8', errors='replace'))
        _safe_sleep()
        # Use .get() guards — NCBI may omit 'idlist' in error/empty responses
        esresult = data.get('esearchresult') or {}
        sra_ids = esresult.get('idlist') or []
        if not isinstance(sra_ids, list):
            sra_ids = []
        if not sra_ids:
            return entries
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
            exp_xml_str = str(exp_xml)
            sam_match = re.search(r'SAM[A-Z]+\d+', exp_xml_str)
            if sam_match:
                sam = sam_match.group(0)
                if sam not in seen:
                    seen.add(sam)
                    entries.append({'kind': 'biosample', 'id': sam})
                continue
            # No BioSample reference found -- fall back to the run's own
            # accession so the sample isn't lost entirely.
            run_match = (re.search(r'(?:SRR|ERR)\d+', exp_xml_str)
                         or re.search(r'(?:SRX|ERX)\d+', exp_xml_str))
            if run_match:
                run_id = run_match.group(0)
                if run_id not in seen:
                    seen.add(run_id)
                    entries.append({'kind': 'experiment', 'id': run_id})
    except Exception as e:
        print(f'  [BioProject-SRA] {bioproject_id}: {e}')
    return entries


def _find_bioproject_samples(bioproject_id: str, max_samples: int = MAX_SAMPLES) -> tuple:
    """
    Cheap enumeration step for a BioProject: finds the capped sample list
    WITHOUT doing the expensive per-sample cross-reference resolution
    (GenBank accession + SRA run lookups for each BioSample). This is what
    lets a caller process samples one at a time -- resolving + running each
    through the pipeline before moving on -- instead of waiting for every
    sample in the project to be fully resolved first.

    Resolution strategy (in order), same as resolve_from_bioproject:
      1. esearch biosample DB with bioproject ID (works when BioSamples are
         registered in the biosample database with a BioProject link)
      2. Raw elink bioproject->biosample via Entrez HTTP URL
      3. SRA fallback: esearch SRA with [bioproject] and parse BioSample from
         ExpXml (required for SRA-only projects like metagenomics BioProjects)
      4. ENA API fallback (for PRJEB projects not mirrored in NCBI biosample DB)
      5. Nucleotide fallback: esearch nucleotide with [BioProject], then elink
         nucleotide→biosample (5A) or use nucleotide accessions directly (5B).
         Handles WGS / targeted-locus GenBank-only projects (e.g. PRJNA1177498,
         PRJNA400168) that have no BioSample or SRA records at all.

    Returns (mode, data):
      mode == 'ids'         -> data is a capped list of BioSample accession
                                strings; caller must still resolve each one
                                individually (e.g. via resolve_from_biosample).
      mode == 'genbank_ids' -> data is a capped list of GenBank/nucleotide
                                accession strings (Strategy 5B -- no BioSample
                                link was found via NCBI elink). Caller must
                                still trace each one individually via
                                resolve_from_genbank() to discover its
                                biosample/experiment, if any exist.
      mode == 'mixed_ids'   -> data is a capped list of
                                {'kind': 'biosample'|'experiment', 'id': str}
                                dicts (Strategy 3 -- SRA fallback). Most SRA
                                runs resolve to a 'biosample' id; the rare
                                run with no BioSample reference in its ExpXml
                                falls back to 'experiment' (its own SRR/ERR
                                accession) so it isn't dropped. Caller
                                resolves each by its kind (resolve_from_biosample
                                or resolve_from_sra).
      mode == 'resolved'    -> data is already a complete {key: record} dict.
                                Strategy 4 (ENA) gets every field it'll ever
                                have from a single API call, so there's
                                nothing left to defer -- returning it
                                pre-resolved avoids pointless extra lookups.
      mode == 'empty'       -> data is {} (nothing found for this BioProject).
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
            if total > max_samples:
                print(f'  [BioProject] WARNING: {bioproject_id} has {total} BioSamples. '
                      f'Processing first {max_samples}.')
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
        sra_entries = _biosample_ids_from_sra(bioproject_id, max_samples)
        if sra_entries:
            seen_sra: set = set()
            capped_sra: list = []
            for e in sra_entries:
                if e['id'] in seen_sra:
                    continue
                seen_sra.add(e['id'])
                capped_sra.append(e)
                if len(capped_sra) >= max_samples:
                    break
            n_bio = sum(1 for e in capped_sra if e['kind'] == 'biosample')
            n_exp = len(capped_sra) - n_bio
            print(f'  [BioProject] Strategy 3 yielded {n_bio} biosample-linked + '
                  f'{n_exp} experiment-only sample(s)')
            return 'mixed_ids', capped_sra

    # ── Strategy 4: ENA API fallback (for PRJEB projects not mirrored in NCBI) ──
    # PRJEB projects have SAMEA samples that may not be linked to [BioProject] in
    # NCBI's biosample DB. The ENA Portal API always has the authoritative list.
    if not biosample_ids and bioproject_id.upper().startswith('PRJEB'):
        print(f'  [BioProject] Trying ENA API fallback for {bioproject_id}...')
        ena_result = _resolve_via_ena(bioproject_id, max_samples)
        if ena_result:
            print(f'  [BioProject] {bioproject_id} -> {len(ena_result)} samples via ENA API')
            return 'resolved', ena_result

    # ── Strategy 5: nucleotide (GenBank) fallback for WGS / GenBank-only BioProjects ──
    # Used when the BioProject has sequences in the nucleotide DB but no BioSamples
    # registered (e.g. PRJNA1177498 WGS, PRJNA400168 targeted loci).
    if not biosample_ids:
        print(f'  [BioProject] Trying nucleotide fallback for {bioproject_id}...')
        try:
            nuc_url = (
                f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
                f'?db=nucleotide&term={bioproject_id}[BioProject]'
                f'&retmax={max_samples}&retmode=json&email={Entrez.email}'
            )
            nuc_data = _json.loads(_urlopen_with_retry(nuc_url).decode('utf-8', errors='replace'))
            _safe_sleep()
            esres = nuc_data.get('esearchresult') or {}
            nuc_uids = esres.get('idlist') or []
            if not isinstance(nuc_uids, list):
                nuc_uids = []
            total_nuc = int(esres.get('count') or 0)

            if nuc_uids:
                if total_nuc > max_samples:
                    print(f'  [BioProject] WARNING: {bioproject_id} has {total_nuc} nucleotide records. '
                          f'Processing first {len(nuc_uids)}.')
                else:
                    print(f'  [BioProject] Strategy 5: {total_nuc} nucleotide record(s) found')

                # Sub-strategy A: elink nucleotide→biosample to find BioSample UIDs in bulk
                nuc_bs_uids: list = []
                try:
                    elink_url = (
                        f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi'
                        f'?dbfrom=nucleotide&db=biosample&id={",".join(nuc_uids)}'
                        f'&retmode=xml&email={Entrez.email}'
                    )
                    elink_raw = _urlopen_with_retry(elink_url).decode('utf-8', errors='replace')
                    _safe_sleep()
                    try:
                        elink_root = ET.fromstring(elink_raw)
                        for linkset_db in elink_root.findall('.//LinkSetDb'):
                            if linkset_db.findtext('DbTo', '').lower() == 'biosample':
                                for link in linkset_db.findall('Link/Id'):
                                    if link.text and link.text not in nuc_bs_uids:
                                        nuc_bs_uids.append(link.text)
                    except ET.ParseError:
                        nuc_bs_uids = re.findall(r'<Id>(\d+)</Id>', elink_raw)
                except Exception as e:
                    print(f'  [BioProject] Strategy 5 elink failed: {e}')

                if nuc_bs_uids:
                    print(f'  [BioProject] Strategy 5: {len(nuc_bs_uids)} BioSample UIDs via nucleotide elink')
                    try:
                        handle = Entrez.esummary(db='biosample',
                                                 id=','.join(nuc_bs_uids[:max_samples]))
                        bs_sum5 = Entrez.read(handle); handle.close()
                        _safe_sleep()
                        for doc in bs_sum5.get('DocumentSummarySet', {}).get('DocumentSummary', []):
                            acc5 = doc.get('Accession', '')
                            if acc5.startswith('SAM') and acc5 not in biosample_ids:
                                biosample_ids.append(acc5)
                        if biosample_ids:
                            print(f'  [BioProject] Strategy 5A yielded {len(biosample_ids)} BioSample accessions')
                    except Exception as e:
                        print(f'  [BioProject] Strategy 5 biosample esummary failed: {e}')

                # Sub-strategy B: no BioSample links — use nucleotide accessions directly
                if not biosample_ids:
                    print(f'  [BioProject] Strategy 5: no BioSample links, fetching accession strings')
                    try:
                        acc_url = (
                            f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi'
                            f'?db=nucleotide&id={",".join(nuc_uids)}'
                            f'&rettype=acc&retmode=text&email={Entrez.email}'
                        )
                        acc_text = _urlopen_with_retry(acc_url).decode('utf-8', errors='replace')
                        _safe_sleep()
                        nuc_accs = [a.split('.')[0] for a in acc_text.strip().splitlines()
                                    if a.strip()]
                    except Exception as e:
                        print(f'  [BioProject] Strategy 5 acc fetch failed: {e}')
                        nuc_accs = []

                    if nuc_accs:
                        print(f'  [BioProject] Strategy 5B yielded {len(nuc_accs)} nucleotide accessions '
                              f'(no BioSample link found in NCBI -- caller can still trace '
                              f'biosample/experiment per accession via resolve_from_genbank)')
                        return 'genbank_ids', nuc_accs
        except Exception as e:
            print(f'  [BioProject] Strategy 5 failed: {e}')

    if not biosample_ids:
        print(f'  [BioProject] WARNING: no BioSamples found for {bioproject_id}')
        return 'empty', {}

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

    return 'ids', biosample_ids


def resolve_from_bioproject(bioproject_id: str, max_samples: int = MAX_SAMPLES) -> dict:
    """
    BioProject -> find ALL linked BioSamples -> resolve each one.
    Eager/batch entry point: resolves every sample up front. For large
    projects where you want to resolve + process one sample at a time
    instead (so a cancellation takes effect between samples and the first
    result doesn't wait on the whole project), use enumerate_project_samples()
    + resolve_lazy_entry() instead.

    Returns multi-entry dict keyed by BioSample ID (up to max_samples).
    """
    mode, data = _find_bioproject_samples(bioproject_id, max_samples)
    if mode == 'resolved' or mode == 'empty':
        return data

    result = {}
    if mode == 'ids':
        for bs_id in data:
            result.update(resolve_from_biosample(bs_id, known_bioproject=bioproject_id))
    elif mode == 'genbank_ids':
        for acc in data:
            result.update(resolve_from_genbank(acc, known_bioproject=bioproject_id))
    elif mode == 'mixed_ids':
        for e in data:
            if e['kind'] == 'biosample':
                result.update(resolve_from_biosample(e['id'], known_bioproject=bioproject_id))
            else:
                result.update(resolve_from_sra(e['id'], known_bioproject=bioproject_id))

    print(f'  [BioProject] {bioproject_id} -> {len(result)} samples resolved')
    return result


def resolve_from_sra(sra_id: str, known_bioproject: str = '') -> dict:
    """
    SRR or SRX -> find parent BioSample via esearch biosample with SRA field tag,
    then resolve that BioSample normally.
    Returns {biosample_id: record} or {sra_id: empty_record} on failure.

    If the caller already knows the parent BioProject (e.g. this run came
    from enumerating a BioProject's SRA records), pass it as known_bioproject
    to skip the redundant bioproject lookup and to still populate
    'bioproject' even when no BioSample link is found.
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
                return resolve_from_biosample(bs_accession, known_bioproject=known_bioproject)

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
                return resolve_from_biosample(sam_match.group(0), known_bioproject=known_bioproject)

    except Exception as e:
        print(f'  [SRA] {sra_id}: {e}')

    return {sra_id: {**_empty_record(), 'experiment': sra_id, 'bioproject': known_bioproject}}


# ── 3b. GEO (Gene Expression Omnibus) resolvers ───────────────────────────────

def _geo_esearch(term: str, retmax: int = 1000) -> list:
    """Search NCBI GEO (db=gds), return list of internal UIDs."""
    url = (f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
           f'?db=gds&term={term}&retmax={retmax}&retmode=json&email={Entrez.email}')
    if _NCBI_API_KEY:
        url += f'&api_key={_NCBI_API_KEY}'
    data = _json.loads(_urlopen_with_retry(url).decode('utf-8', errors='replace'))
    _safe_sleep()
    return (data.get('esearchresult') or {}).get('idlist') or []


def _geo_esummary(uid: str) -> dict:
    """Fetch GEO esummary for a single internal UID, return the record dict."""
    url = (f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'
           f'?db=gds&id={uid}&retmode=json&email={Entrez.email}')
    if _NCBI_API_KEY:
        url += f'&api_key={_NCBI_API_KEY}'
    data = _json.loads(_urlopen_with_retry(url).decode('utf-8', errors='replace'))
    _safe_sleep()
    result = data.get('result') or {}
    uids_list = result.get('uids') or [uid]
    return result.get(str(uids_list[0])) or {} if uids_list else {}


def _fetch_geo_soft(accession: str) -> str:
    """
    Fetch GEO SOFT text format for a GSM or GSE accession.
    The SOFT format is the most reliable source for BioSample and SRA relation links.
    Returns raw text or '' on failure.
    """
    url = (f'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi'
           f'?acc={accession}&targ=self&form=text&view=quick')
    try:
        raw = _urlopen_with_retry(url)
        _safe_sleep()
        return raw.decode('utf-8', errors='replace')
    except Exception as e:
        print(f'  [GEO SOFT] Failed to fetch {accession}: {e}')
        return ''


def _parse_geo_soft_sample(soft_text: str) -> dict:
    """Parse GEO SOFT text for a GSM sample into key metadata fields."""
    info: dict = {
        'title': '',
        'organism': '',
        'characteristics': [],
        'biosample': '',
        'sra': '',
        'series_id': '',
        'platform': '',
        'submission_date': '',
    }
    for line in soft_text.splitlines():
        line = line.strip()
        if line.startswith('!Sample_title'):
            info['title'] = line.split('=', 1)[-1].strip()
        elif line.startswith('!Sample_organism_ch'):
            if not info['organism']:  # keep first channel only
                info['organism'] = line.split('=', 1)[-1].strip()
        elif line.startswith('!Sample_characteristics_ch'):
            char = line.split('=', 1)[-1].strip()
            if char:
                info['characteristics'].append(char)
        elif line.startswith('!Sample_series_id'):
            info['series_id'] = line.split('=', 1)[-1].strip()
        elif line.startswith('!Sample_platform_id'):
            info['platform'] = line.split('=', 1)[-1].strip()
        elif line.startswith('!Sample_submission_date'):
            info['submission_date'] = line.split('=', 1)[-1].strip()
        elif line.startswith('!Sample_relation'):
            val = line.split('=', 1)[-1].strip()
            if 'BioSample:' in val and not info['biosample']:
                m = re.search(r'SAM[A-Z]+\d+', val)
                if m:
                    info['biosample'] = m.group(0)
            elif 'SRA:' in val and not info['sra']:
                m = re.search(r'SRX\d+|SRR\d+|ERX\d+|ERR\d+', val)
                if m:
                    info['sra'] = m.group(0)
    info['characteristics'] = '; '.join(info['characteristics'])
    return info


def resolve_from_geo_sample(gsm_id: str) -> dict:
    """
    GSM sample accession -> standardized record.

    Strategy:
      1. GEO SOFT text (most reliable for BioSample/SRA relation links)
      2. GDS esummary JSON (bioproject, additional metadata)
      3. SRA esearch fallback (if experiment still missing)

    Returns {key: record} where key is SAMN accession if found, else GSM ID.
    Extra GEO fields (geo_sample, geo_series, geo_title, etc.) are included
    in the record and pass transparently through the pipeline.
    """
    print(f'  [GEO] Resolving sample {gsm_id}...')
    record = _empty_record()
    geo_meta = {
        'geo_sample':         gsm_id,
        'geo_series':         '',
        'geo_title':          '',
        'geo_organism':       '',
        'geo_characteristics': '',
        'geo_platform':       '',
        'geo_submission_date': '',
    }

    # 1. GEO SOFT text
    soft_text = _fetch_geo_soft(gsm_id)
    if soft_text:
        parsed = _parse_geo_soft_sample(soft_text)
        geo_meta['geo_title']           = parsed['title']
        geo_meta['geo_organism']        = parsed['organism']
        geo_meta['geo_characteristics'] = parsed['characteristics']
        geo_meta['geo_series']          = parsed['series_id']
        geo_meta['geo_platform']        = parsed['platform']
        geo_meta['geo_submission_date'] = parsed['submission_date']
        if parsed['biosample']:
            record['biosample'] = parsed['biosample']
        if parsed['sra']:
            record['experiment'] = parsed['sra']

    # 2. GDS esummary for bioproject + fill gaps
    try:
        uids = _geo_esearch(f'{gsm_id}[Accession]')
        if uids:
            summary = _geo_esummary(uids[0])
            if summary:
                if not geo_meta['geo_title']:
                    geo_meta['geo_title'] = str(summary.get('title', '') or '')
                if not geo_meta['geo_organism']:
                    geo_meta['geo_organism'] = str(summary.get('organism', '') or '')
                bp = str(summary.get('bioproject', '') or summary.get('BioProject', '') or '')
                if bp:
                    record['bioproject'] = bp
                if not record['biosample']:
                    bs = str(summary.get('biosample', '') or summary.get('BioSample', '') or '')
                    if bs.startswith('SAM'):
                        record['biosample'] = bs
    except Exception as e:
        print(f'  [GEO] GDS esummary failed for {gsm_id}: {e}')

    # 3. SRA fallback — find run accession if still missing
    if not record['experiment']:
        try:
            sra_url = (f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
                       f'?db=sra&term={gsm_id}[GEO+Accession]&retmode=json&email={Entrez.email}')
            if _NCBI_API_KEY:
                sra_url += f'&api_key={_NCBI_API_KEY}'
            sra_data = _json.loads(_urlopen_with_retry(sra_url).decode('utf-8', errors='replace'))
            _safe_sleep()
            sra_ids = (sra_data.get('esearchresult') or {}).get('idlist') or []
            if sra_ids:
                sra_sum_url = (f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'
                               f'?db=sra&id={",".join(sra_ids[:5])}&retmode=json&email={Entrez.email}')
                if _NCBI_API_KEY:
                    sra_sum_url += f'&api_key={_NCBI_API_KEY}'
                sra_sum = _json.loads(_urlopen_with_retry(sra_sum_url).decode('utf-8', errors='replace'))
                _safe_sleep()
                for k, v in (sra_sum.get('result') or {}).items():
                    if k == 'uids':
                        continue
                    exp_xml = str(v.get('expxml', '') or '')
                    run_m = re.search(r'SRR\d+|ERR\d+', exp_xml)
                    if run_m:
                        record['experiment'] = run_m.group(0)
                    if not record['biosample']:
                        sam_m = re.search(r'SAM[A-Z]+\d+', exp_xml)
                        if sam_m:
                            record['biosample'] = sam_m.group(0)
                    if run_m:
                        break
        except Exception as e:
            print(f'  [GEO] SRA fallback failed for {gsm_id}: {e}')

    key = record['biosample'] if record['biosample'] else gsm_id
    print(f'  [GEO] {gsm_id} -> key={key}, '
          f'bioproject={record["bioproject"]}, biosample={record["biosample"]}, '
          f'experiment={record["experiment"]}')
    return {key: {**record, **geo_meta}}


def _find_geo_series_samples(gse_id: str, max_samples: int = MAX_SAMPLES) -> list:
    """
    Cheap enumeration step for a GEO series: returns the capped list of GSM
    sample accessions WITHOUT resolving each one (no SOFT-text fetch, no
    BioSample/SRA tracing). Mirrors _find_bioproject_samples()'s role for
    BioProjects -- lets a caller resolve + process samples one at a time.

    Returns a list of GSM accession strings (possibly empty).
    """
    try:
        # Filter to GSE entry type to avoid picking up GSM/GDS records
        uids = _geo_esearch(f'{gse_id}[Accession]+AND+GSE[ETYP]')
        if not uids:
            uids = _geo_esearch(f'{gse_id}[Accession]')
        if not uids:
            print(f'  [GEO] No UID found for series {gse_id}')
            return []

        summary = _geo_esummary(uids[0])
        if not summary:
            print(f'  [GEO] Empty esummary for {gse_id}')
            return []

        series_title = str(summary.get('title', '') or '')
        print(f'  [GEO] Series: {series_title!r}')

        samples = summary.get('samples') or []
        gsm_accessions = [
            str(s.get('accession', '') or '')
            for s in samples
            if str(s.get('accession', '') or '').upper().startswith('GSM')
        ]

        if not gsm_accessions:
            print(f'  [GEO] No GSM samples in esummary for {gse_id}')
            return []

        print(f'  [GEO] {gse_id}: {len(gsm_accessions)} samples found; '
              f'capping at {max_samples}')
        return gsm_accessions[:max_samples]

    except Exception as e:
        print(f'  [GEO] _find_geo_series_samples {gse_id}: {e}')
        return []


def resolve_from_geo_series(gse_id: str, max_samples: int = MAX_SAMPLES) -> dict:
    """
    GSE series accession -> resolve all GSM samples within it.
    Eager/batch entry point: resolves every sample up front. For large
    series where you want to resolve + process one sample at a time instead,
    use enumerate_project_samples() + resolve_lazy_entry() instead.

    Returns combined {key: record} dict; key is SAMN when linkable, else GSM ID.
    """
    gsm_accessions = _find_geo_series_samples(gse_id, max_samples)
    all_results: dict = {}
    for gsm_id in gsm_accessions:
        sample_result = resolve_from_geo_sample(gsm_id)
        for v in sample_result.values():
            if not v.get('geo_series'):
                v['geo_series'] = gse_id
        all_results.update(sample_result)

    print(f'  [GEO] {gse_id} -> {len(all_results)} samples resolved')
    return all_results


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
    elif acc_type == 'geo_series':
        return resolve_from_geo_series(user_input, max_samples=max_samples)
    elif acc_type == 'geo_sample':
        return resolve_from_geo_sample(user_input)
    elif acc_type == 'genbank':
        return resolve_from_genbank(user_input)
    elif acc_type in ('sra_run', 'sra_experiment'):
        return resolve_from_sra(user_input)
    else:
        print(f'[resolve_accessions] WARNING: Unknown type for {user_input!r}. '
              f'Trying as GenBank.')
        return resolve_from_genbank(user_input)


# ── 5. Lazy / interleaved resolution for project-style inputs ─────────────────
#
# BioProject and GEO series inputs can expand into many samples. Resolving
# every sample's full record (GenBank + SRA cross-references) before any of
# them reach the pipeline means: (a) the first pipeline result is delayed by
# the whole project's resolution time, and (b) a mid-run cancellation can't
# take effect until that entire resolution phase finishes. These two
# functions let a caller enumerate the capped sample list cheaply, then
# resolve + process one sample at a time -- resolve_lazy_entry() right before
# that sample is handed to the pipeline, not in a batch upfront.

def enumerate_project_samples(token: str, acc_type: str, max_samples: int = MAX_SAMPLES) -> dict:
    """
    Lightweight, interleaving-friendly enumeration for project-style inputs.
    Returns a dict keyed exactly like resolve_accessions(), but entries that
    still need per-sample cross-reference lookups are left as lazy
    placeholders (marked with '_lazy_kind') instead of being fully resolved.

    For any acc_type other than 'bioproject'/'geo_series' there's nothing to
    defer (these are already single samples), so this just delegates to the
    normal eager resolve_accessions().
    """
    if acc_type == 'bioproject':
        mode, data = _find_bioproject_samples(token, max_samples)
        if mode == 'resolved' or mode == 'empty':
            return data
        if mode == 'genbank_ids':
            # No BioSample link found via elink -- still worth tracing each
            # accession individually (resolve_from_genbank can find a
            # BioSample/experiment that elink missed), just deferred per
            # sample instead of done for the whole project upfront.
            return {
                acc: {
                    **_empty_record(acc),
                    'bioproject': token,
                    '_lazy_kind': 'genbank',
                }
                for acc in data
            }
        if mode == 'mixed_ids':
            # Strategy 3 (SRA fallback): mostly biosample-linked runs, plus
            # any rare run with no BioSample reference falls back to its own
            # SRR/ERR accession instead of being dropped.
            result = {}
            for e in data:
                if e['kind'] == 'biosample':
                    result[e['id']] = {
                        **_empty_record(),
                        'biosample':  e['id'],
                        'bioproject': token,
                        '_lazy_kind': 'biosample',
                    }
                else:
                    result[e['id']] = {
                        **_empty_record(),
                        'experiment': e['id'],
                        'bioproject': token,
                        '_lazy_kind': 'experiment',
                    }
            return result
        # mode == 'ids': plain BioSample accession strings
        return {
            bs_id: {
                **_empty_record(),
                'biosample':  bs_id,
                'bioproject': token,
                '_lazy_kind': 'biosample',
            }
            for bs_id in data
        }

    if acc_type == 'geo_series':
        gsm_ids = _find_geo_series_samples(token, max_samples)
        return {
            gsm_id: {
                **_empty_record(),
                'geo_sample':  gsm_id,
                'geo_series':  token,
                '_lazy_kind':  'geo_sample',
            }
            for gsm_id in gsm_ids
        }

    return resolve_accessions(token, max_samples=max_samples)


def resolve_lazy_entry(key: str, entry: dict) -> dict:
    """
    Fully resolve one placeholder entry produced by enumerate_project_samples().
    Call this immediately before processing the sample through the pipeline
    -- not in a batch upfront -- so cancellation can take effect between
    samples and the first result isn't held up by the whole project.

    Returns the merged, fully-resolved record (same shape as
    resolve_accessions() values). If entry isn't a lazy placeholder, it's
    returned unchanged.
    """
    kind = entry.get('_lazy_kind')
    if kind == 'biosample':
        resolved = resolve_from_biosample(key, known_bioproject=entry.get('bioproject', ''))
        return resolved.get(key, entry)
    if kind == 'genbank':
        resolved = resolve_from_genbank(key, known_bioproject=entry.get('bioproject', ''))
        return next(iter(resolved.values()), entry)
    if kind == 'experiment':
        resolved = resolve_from_sra(key, known_bioproject=entry.get('bioproject', ''))
        return next(iter(resolved.values()), entry)
    if kind == 'geo_sample':
        resolved = resolve_from_geo_sample(key)
        merged = next(iter(resolved.values()), entry)
        if not merged.get('geo_series'):
            merged['geo_series'] = entry.get('geo_series', '')
        return merged
    return entry
