"""
paper_resolver.py -- DOI/paper-link entry point for the pipeline.

Today the pipeline only goes accession -> metadata (ncbi_resolver.py,
input_handler.py). This module adds the missing direction: given a DOI,
PubMed/PMC link, or an uploaded PDF for a closed-access paper, find every
NCBI accession (BioProject, GEO series/sample, BioSample, SRA run/experiment,
GenBank) that the paper references, so the existing accession -> metadata
fan-out (which already expands a BioProject/GEO series to all of its
samples) can take over unchanged.

Two independent discovery methods are combined, since each catches cases
the other misses:
  1. Regex-scan of the paper's full text + supplementary files for literal
     accession numbers (catches accessions printed in a Data Availability
     statement, a table, or supplementary material).
  2. NCBI elink from the paper's PMID to bioproject/sra/gds (GEO) -- catches
     accessions NCBI has cross-linked to the paper even when the accession
     number never appears in the rendered text.
"""

import re
import json
import time
import urllib.parse
import urllib.request

from Bio import Entrez

try:
    import NCBI as _NCBI
except Exception:
    _NCBI = None

try:
    import data_preprocess as _data_preprocess
except Exception:
    _data_preprocess = None

try:
    from chat_input_parser import _eutils_search, _eutils_summary_accs
except Exception:
    _eutils_search = _eutils_summary_accs = None

Entrez.email = 'vyphung1901@gmail.com'

_ELINK_TARGET_DBS = ("bioproject", "sra", "gds")  # gds = GEO

# Accession patterns scanned over free text (vs. ncbi_resolver.detect_accession_type,
# which matches a single whole token) -- order matters: longer/more specific first.
_ACCESSION_PATTERNS = {
    'bioproject':     re.compile(r'\bPRJ[A-Z]{2}\d+\b'),
    'biosample':      re.compile(r'\bSAM[A-Z]{1,2}\d{6,}\b'),
    'geo_series':     re.compile(r'\bGSE\d{3,}\b'),
    'geo_sample':     re.compile(r'\bGSM\d{3,}\b'),
    'sra_experiment': re.compile(r'\bSRX\d{3,}\b'),
    'sra_run':        re.compile(r'\b(?:SRR|ERR|DRR)\d{3,}\b'),
    'ena_project':    re.compile(r'\bERP\d{2,}\b'),
    # Real GenBank accessions are a prefix followed by a fixed run of digits
    # (optionally a .version) -- e.g. MT123456, OL669415.1, NC_045512.2.
    # Gene symbols (MTHFD1L, SAMD11, OLFML2A...) share the same letter
    # prefixes but have no trailing digit run, so requiring digits here
    # keeps them from being mistaken for accessions.
    'genbank_named':  re.compile(
        r'\b(?:NC_\d{6}|(?:OL|MT|MW|MZ|PQ|OM|MN|MK|KY|KX|KU|JN|FJ)\d{5,8})(?:\.\d+)?\b'
    ),
}

_DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"\'<>]+')

# URLs printed in a PDF's body (Data Availability / Supplementary Material
# sections) that are worth auto-fetching, vs. arbitrary in-text URLs (e.g.
# references) that aren't.
_URL_RE = re.compile(r'https?://[^\s"\'<>\)\]]+')
_SUPPLEMENTARY_URL_HINTS = (
    "supplementary", "supplemental", "suppl", "data-availability",
    "datadryad", "dryad.org", "figshare", "zenodo", "osf.io",
    "ebi.ac.uk", "ncbi.nlm.nih.gov", "geo/query", "ena.embl",
)
_SUPPLEMENTARY_FILE_EXTS = (".zip", ".xlsx", ".xls", ".csv", ".docx", ".pdf")


def discover_supplementary_links_in_text(text: str) -> list:
    """Find URLs in a PDF/paper's extracted text worth auto-fetching --
    i.e. likely Data Availability / Supplementary Material links, not every
    reference URL in the bibliography."""
    if not text:
        return []
    found = []
    for url in _URL_RE.findall(text):
        url = url.rstrip('.,;)')
        low = url.lower()
        if any(h in low for h in _SUPPLEMENTARY_URL_HINTS) or low.endswith(_SUPPLEMENTARY_FILE_EXTS):
            found.append(url)
    return list(dict.fromkeys(found))


def discover_accessions_in_text(text: str) -> set:
    """Regex-scan free text for NCBI accession numbers of any kind."""
    found = set()
    if not text:
        return found
    for _kind, pattern in _ACCESSION_PATTERNS.items():
        for m in pattern.findall(text):
            found.add(m.upper().rstrip('.'))
    return found


def normalize_doi(doi_or_link: str) -> str:
    """Pull a bare DOI out of a doi.org URL or raw DOI string."""
    s = (doi_or_link or "").strip()
    if "doi.org/" in s:
        s = s.split("doi.org/", 1)[-1]
    m = _DOI_RE.search(s)
    return m.group(0).strip().rstrip('.') if m else s


_DOI_FULLMATCH_RE = re.compile(r'^10\.\d{4,9}/[^\s"\'<>]+$')

_CITATION_DOI_META_RE = re.compile(
    r'(?:citation_doi|dc\.identifier)["\']?\s*content=["\']?(10\.\d{4,9}/[^"\'<> ]+)',
    re.IGNORECASE,
)


def _scrape_doi_from_page(url: str) -> str:
    """Best-effort: GET the page and pull a DOI out of a <meta name="citation_doi">
    or <meta name="dc.identifier"> tag in the HTML <head>.

    Needed because normalize_doi() can only find a DOI when the URL embeds one
    (doi.org links, or a bare DOI string) -- many publisher article URLs
    (nature.com/articles/<id>, cell.com/.../fulltext/<PII>) don't, so
    normalize_doi() silently falls back to returning the un-parsed URL, which
    then fails every downstream Unpaywall/PMC/PMID lookup even when the paper
    is genuinely open access. Most publisher pages still expose citation
    metadata tags in plain HTML even when the body itself is paywalled or
    blocked for non-browser clients (e.g. Cell.com returns 403 for the body
    but the same request to nature.com succeeds).
    """
    try:
        import requests as _req
        headers = {"User-Agent": "Mozilla/5.0 (compatible; research-pipeline/1.0)"}
        r = _req.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return ""
        m = _CITATION_DOI_META_RE.search(r.text)
        return m.group(1).rstrip('.') if m else ""
    except Exception as e:
        print(f"[paper_resolver] page-meta DOI scrape failed for {url}: {e}")
        return ""


def resolve_real_doi(doi_or_link: str) -> str:
    """normalize_doi(), with a page-scrape fallback when the URL doesn't embed
    a DOI directly. Use this (not normalize_doi) wherever the result is about
    to be used for an Unpaywall/PMC/PMID lookup -- those all silently fail
    on a non-DOI string."""
    doi = normalize_doi(doi_or_link)
    if _DOI_FULLMATCH_RE.match(doi):
        return doi
    scraped = _scrape_doi_from_page(doi_or_link)
    return scraped or doi


def resolve_doi_to_pmid(doi: str) -> str:
    """DOI -> PMID via NCBI esearch (same pattern used elsewhere in the pipeline
    for the reverse direction, e.g. additional_pipeline.py's doi_pmc_fallback)."""
    try:
        handle = Entrez.esearch(db="pubmed", term=f"{doi}[doi]", retmax=1)
        record = Entrez.read(handle)
        handle.close()
        ids = record.get("IdList", [])
        return ids[0] if ids else ""
    except Exception as e:
        print(f"[paper_resolver] resolve_doi_to_pmid failed for {doi}: {e}")
        return ""


def _eutils_elink(dbfrom: str, db: str, uid: str) -> list:
    """Raw HTTP elink call -- avoids Biopython's Entrez.read() choking on
    elink's DOCTYPE external entity references (same reasoning as
    ncbi_resolver.py's docstring)."""
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
        f"?dbfrom={dbfrom}&db={db}&id={uid}&retmode=json"
    )
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        uids = []
        for linkset in data.get("linksets", []):
            for lsdb in linkset.get("linksetdbs", []):
                if lsdb.get("dbto") == db:
                    uids.extend(lsdb.get("links", []))
        return list(dict.fromkeys(uids))
    except Exception as e:
        print(f"[paper_resolver] elink {dbfrom}->{db} failed for {uid}: {e}")
        return []


def discover_accessions_via_ncbi_links(pmid: str) -> set:
    """PMID -> elink -> {bioproject, sra, gds (GEO)} -- catches accessions NCBI
    has cross-linked to the paper even if the number never appears in the text."""
    found = set()
    if not pmid or _eutils_summary_accs is None:
        return found
    for db in _ELINK_TARGET_DBS:
        uids = _eutils_elink("pubmed", db, pmid)
        if not uids:
            continue
        time.sleep(0.34)  # be polite to NCBI between elink/esummary calls
        accs = _eutils_summary_accs(db, uids)
        found.update(a.upper() for a in accs if a)
    return found


def check_accessible(doi_or_link: str) -> dict:
    """Best-effort open vs. closed-access classification for a DOI/link.

    Returns {"open": bool, "oa_url": str, "reason": str}. Treated as "open"
    if any of: Unpaywall finds an OA copy, PMC has the full text, or the
    publisher page itself responds without an auth wall (left to the caller
    -- this only checks the cheap signals NCBI.py already implements).
    """
    doi = resolve_real_doi(doi_or_link)
    if _NCBI is None or not doi:
        return {"open": False, "oa_url": "", "reason": "no DOI / NCBI module unavailable"}
    try:
        oa_url = _NCBI.get_unpaywall_oa_url(doi)
        if oa_url:
            return {"open": True, "oa_url": oa_url, "reason": "unpaywall OA copy found"}
    except Exception as e:
        print(f"[paper_resolver] unpaywall check failed for {doi}: {e}")
    try:
        pmid = resolve_doi_to_pmid(doi)
        if pmid:
            pmc = _NCBI.fetch_pmc_fulltext(pmid)
            if pmc and pmc.get("text"):
                return {"open": True, "oa_url": "", "reason": "PMC full text available"}
    except Exception as e:
        print(f"[paper_resolver] PMC check failed for {doi}: {e}")
    return {"open": False, "oa_url": "", "reason": "no OA/PMC copy found -- needs PDF upload"}


def resolve_paper(doi_or_link: str, data_dir, pdf_path: str = None,
                   pre_extracted_text: str = None) -> dict:
    """Given a DOI/link (and an optional PDF for closed-access papers),
    fetch the paper's text + supplementary material and discover every
    NCBI accession it references.

    Returns:
        {"input": str, "status": "ok"|"needs_pdf"|"failed",
         "discovered_accessions": set[str], "pmid": str, "doi": str,
         "pdf_used": bool, "text_chars": int}
    """
    doi = resolve_real_doi(doi_or_link)
    result = {
        "input": doi_or_link, "status": "failed", "discovered_accessions": set(),
        "pmid": "", "doi": doi, "pdf_used": False, "text_chars": 0,
    }

    combined_text = ""
    needs_pdf = False  # set if full-text fetch was skipped/failed AND elink found nothing either

    if pre_extracted_text:
        # Caller already extracted text for us (e.g. files uploaded via
        # /upload-context, which already runs PDF/zip/table extraction) --
        # use it directly instead of re-fetching or re-extracting.
        combined_text = pre_extracted_text
        result["pdf_used"] = True
    elif pdf_path:
        if _data_preprocess is None:
            result["status"] = "failed"
            return result
        # Local PDF: reuse the same PDF text+table extraction used for fetched papers.
        try:
            from NER.PDF import pdf as _pdf_mod
            combined_text = _pdf_mod.PDFFast(str(pdf_path), str(data_dir)).extract_text()
            tables = _data_preprocess.clean_tables_format(_pdf_mod.PDF(str(pdf_path), str(data_dir)).extractTable())
            tables_text = _data_preprocess._serialize_tables_as_text(tables)
            if tables_text:
                combined_text += "\n" + tables_text
        except Exception as e:
            print(f"[paper_resolver] local PDF extraction failed for {pdf_path}: {e}")
        result["pdf_used"] = True
    else:
        # Accessibility is decided purely by whether extract_url_text can actually
        # pull text from the link -- more reliable than the unpaywall/PMC "open vs.
        # closed" heuristic (which both over- and under-reports). We still consult
        # unpaywall for a better OA fetch target, but the verdict is "did we get
        # text?"; if not, the link is treated as inaccessible (needs_pdf).
        if _data_preprocess is None:
            result["status"] = "failed"
            return result
        candidate_links = []
        try:
            oa_url = check_accessible(doi_or_link).get("oa_url")
            if oa_url:
                candidate_links.append(oa_url)
        except Exception as e:
            print(f"[paper_resolver] OA lookup failed for {doi_or_link}: {e}")
        candidate_links.append(f"https://doi.org/{doi}" if doi else doi_or_link)
        for link in candidate_links:
            try:
                fetch = _data_preprocess.extract_url_text(link, data_dir)
                if fetch.get("status") == "ok" and (fetch.get("text") or "").strip():
                    combined_text = fetch.get("text", "")
                    break
            except Exception as e:
                print(f"[paper_resolver] extract_url_text failed for {link}: {e}")
        if not combined_text:
            needs_pdf = True

    result["text_chars"] = len(combined_text)
    discovered = discover_accessions_in_text(combined_text) if combined_text else set()

    # NCBI elink (Method 2) is independent of full-text access -- run it even
    # when the publisher page couldn't be fetched (e.g. anti-bot 403), since
    # it only needs the PMID, not the article text.
    pmid = resolve_doi_to_pmid(doi) if doi else ""
    result["pmid"] = pmid
    if pmid:
        discovered |= discover_accessions_via_ncbi_links(pmid)

    if needs_pdf and not discovered:
        result["status"] = "needs_pdf"
        return result
    if not combined_text and not discovered:
        result["status"] = "failed"
        return result

    result["discovered_accessions"] = discovered
    result["status"] = "ok" if discovered else "no_accessions_found"
    return result
