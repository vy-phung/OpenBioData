from Bio import Entrez, Medline
import aiohttp
import asyncio
import re
import os
import requests as _requests

def search_serper(query, max_results=3):
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        return []
    try:
        resp = _requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
            timeout=10,
        )
        if resp.status_code == 429:
            return []
        resp.raise_for_status()
        return [item["link"] for item in resp.json().get("organic", []) if item.get("link")]
    except Exception:
        return []

def search_pubmed_free(query, max_results=3):
    try:
        r = _requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": query, "retmode": "json", "retmax": max_results},
            timeout=10,
        )
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        return [f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" for pmid in ids]
    except Exception:
        return []

def search_europepmc_free(query, max_results=3):
    try:
        r = _requests.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": query, "format": "json", "pageSize": max_results, "resultType": "lite"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("resultList", {}).get("result", [])
        links = []
        for item in results:
            doi = item.get("doi")
            if doi:
                links.append(f"https://doi.org/{doi}")
            pmid = item.get("pmid")
            if pmid:
                links.append(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
        return links
    except Exception:
        return []

def _search_any(query, max_results=3):
    """Search using Serper (if key set) then PubMed then EuropePMC as fallbacks."""
    links = search_serper(query, max_results)
    if not links:
        links = search_pubmed_free(query, max_results)
    if not links:
        links = search_europepmc_free(query, max_results)
    return links
try:
    import mtdna_classifier
except ImportError:
    mtdna_classifier = None
try:
    from NER.html import extractHTML
except ImportError:
    extractHTML = None
try:
    import data_preprocess
except ImportError:
    data_preprocess = None
try:
    import pipeline
except ImportError:
    pipeline = None
# Setup
def fetch_ncbi(accession_number):
  try:
    Entrez.email = "your.email@example.com" # Required by NCBI, REPLACE WITH YOUR EMAIL
    handle = Entrez.efetch(db="nucleotide", id=str(accession_number), rettype="gb", retmode="xml")
    record = Entrez.read(handle)
    handle.close()
    outputs = {"authors":"unknown",
              "institution":"unknown",
              "isolate":"unknown",
              "definition":"unknown",
              "title":"unknown",
              "seq_comment":"unknown",
              "collection_date":"unknown" } #'GBSeq_update-date': '25-OCT-2023', 'GBSeq_create-date' 
    gb_seq = None
    # Validate record structure: It should be a list with at least one element (a dict)
    if isinstance(record, list) and len(record) > 0:
        if isinstance(record[0], dict):
            gb_seq = record[0]
        else:
            print(f"Warning: record[0] is not a dictionary for {accession_number}. Type: {type(record[0])}")
        # extract collection date  
        if "GBSeq_create-date" in gb_seq and outputs["collection_date"]=="unknown":
          outputs["collection_date"] = gb_seq["GBSeq_create-date"]
        else:
          if "GBSeq_update-date" in gb_seq and outputs["collection_date"]=="unknown":
            outputs["collection_date"] = gb_seq["GBSeq_update-date"]
        # extract definition
        if "GBSeq_definition" in gb_seq and outputs["definition"]=="unknown":
          outputs["definition"] = gb_seq["GBSeq_definition"]
        # extract related-reference things
        if "GBSeq_references" in gb_seq:
          for ref in gb_seq["GBSeq_references"]:
            # extract authors
            if "GBReference_authors" in ref and outputs["authors"]=="unknown":
              outputs["authors"] = "and ".join(ref["GBReference_authors"])
            # extract title
            if "GBReference_title" in ref and outputs["title"]=="unknown":
              outputs["title"] = ref["GBReference_title"]  
            #  extract submitted journal
            if 'GBReference_journal' in ref and outputs["institution"]=="unknown":
              outputs["institution"] = ref['GBReference_journal']
        # extract seq_comment
        if 'GBSeq_comment'in gb_seq and outputs["seq_comment"]=="unknown":
          outputs["seq_comment"] = gb_seq["GBSeq_comment"]
        # extract isolate
        if "GBSeq_feature-table" in gb_seq:
          if 'GBFeature_quals' in gb_seq["GBSeq_feature-table"][0]:
            for ref in gb_seq["GBSeq_feature-table"][0]["GBFeature_quals"]:
              if ref['GBQualifier_name'] == "isolate" and outputs["isolate"]=="unknown":
                outputs["isolate"] = ref["GBQualifier_value"]
    else:
        print(f"Warning: No valid record or empty record list from NCBI for {accession_number}.")

    # If gb_seq is still None, return defaults
    if gb_seq is None:
        return {"authors":"unknown",
              "institution":"unknown",
              "isolate":"unknown",
              "definition":"unknown",
              "title":"unknown",
              "seq_comment":"unknown",
              "collection_date":"unknown" }
    return outputs   
  except:
    print("error in fetching ncbi data")   
    return {"authors":"unknown",
              "institution":"unknown",
              "isolate":"unknown",
              "definition":"unknown",
              "title":"unknown",
              "seq_comment":"unknown",
              "collection_date":"unknown" }
# Fallback if NCBI crashed or cannot find accession on NBCI
def google_accession_search(accession_id):
    """
    Search for metadata by accession ID using Google Custom Search.
    Falls back to known biological databases and archives.
    """
    queries = [
        f"{accession_id}",
        f"{accession_id} site:ncbi.nlm.nih.gov",
        f"{accession_id} site:pubmed.ncbi.nlm.nih.gov",
        f"{accession_id} site:europepmc.org",
        f"{accession_id} site:researchgate.net",
        f"{accession_id} mtDNA",
        f"{accession_id} mitochondrial DNA"
    ]
    
    links = []
    _search_fn = (
        (lambda q, n: mtdna_classifier.search_google_custom(q, n))
        if mtdna_classifier is not None
        else _search_any
    )
    for query in queries:
        for link in _search_fn(query, 2):
            if link not in links:
                links.append(link)
    return links
             
# Method 1: Smarter Google
def smart_google_queries(accession_id, metadata: dict):
    print("inside smart google queries")
    queries = [
        f'"{accession_id}"',
        f'"{accession_id}" site:ncbi.nlm.nih.gov',
        f'"{accession_id}" site:pubmed.ncbi.nlm.nih.gov',
        f'"{accession_id}" site:europepmc.org',
        f'"{accession_id}" site:researchgate.net',
        f'"{accession_id}" mtDNA',
        f'"{accession_id}" mitochondrial DNA'
    ]
    # Extract useful fields
    #isolate = metadata.get("isolate")
    author = metadata.get("authors")
    title = metadata.get("title")
    institution = metadata.get("institution")
    definition = metadata.get("definition")
    date = metadata.get("collection_date")
    combined = []
    print("yeah get info")
    # Construct queries
    # if isolate and isolate!="unknown" and isolate!="Unpublished":
    #     queries.append(f'"{isolate}" mitochondrial DNA')
    #     queries.append(f'"{isolate}" site:ncbi.nlm.nih.gov')
        
    organism = None
    print("this is definition: ", definition)
    if definition and definition != "unknown":
        print("inside definition")
        match = re.match(r"([A-Z][a-z]+ [a-z]+)", definition)
        print("match: ", match)
        if match:
            organism = match.group(1)
            print("organism: ", organism)
            queries.append(f'"{accession_id}" "{organism}" mitochondrial')
    print("done definition")
    if author and author!="unknown" and author!="Unpublished":
        try:
          author_name = ".".join(author.split(' ')[0].split(".")[:-1])  # Use last name only
        except:
          try:
            author_name = author.split(',')[0]  # Use last name only
          except:  
            author_name = author
        queries.append(f'"{accession_id}" "{author_name}" mitochondrial DNA')
        #queries.append(f'"{author_name}" mtDNA site:researchgate.net')
    print("done author")    
    # if institution and institution!="unknown" and institution!="Unpublished":
    #     try:
    #       short_inst = ",".join(institution.split(',')[:2])  # Take first part of institution
    #     except:
    #       try:
    #         short_inst = institution.split(',')[0]
    #       except:
    #         short_inst = institution
    #     queries.append(f'"{accession_id}" "{short_inst}" mtDNA sequence')
    #     queries.append(f'"{author_name}" "{short_inst}"')
    #     #queries.append(f'"{short_inst}" isolate site:nature.com')
    if institution and institution != "unknown":
        # journal = substring before the first digit
        journal_match = re.match(r"(.+?)(\d)", institution)
        journal, year = "", ""
        if journal_match:
            journal = journal_match.group(1).strip()
        
        # year = last 4-digit number
        year_match = re.findall(r"(19\d{2}|20\d{2})", institution)
        if year_match:
            year = year_match[-1]

        if journal and accession_id:
            queries.append(f'"{accession_id}" "{journal}"')

        if year:
            queries.append(f'"{accession_id}" "{year}"')
    print("done institution")
    if title and title!='unknown' and title not in ["Unpublished","Direct Submission"]:
      queries.append(f'"{title}"')  
    print("done title")    
    return queries


async def process_link(session, link, saveLinkFolder, keywords, accession):
    output = []
    title_snippet = link.lower()

    # use async extractor for web, fallback to sync for local files
    if link.startswith("http"):
        article_text = await data_preprocess.async_extract_text(link, saveLinkFolder)
    else:
        article_text = data_preprocess.extract_text(link, saveLinkFolder)

    for keyword in keywords:
        if article_text and keyword.lower() in article_text.lower():
            output.append([link, keyword.lower(), article_text])
            return output
        if keyword.lower() in title_snippet:
            output.append([link, keyword.lower()])
            return output
    return output

async def async_filter_links_by_metadata(search_results, saveLinkFolder, accession=None):
    TRUSTED_DOMAINS = [
        "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov",
        "biorxiv.org", "researchgate.net", "nature.com", "sciencedirect.com"
    ]

    keywords = ["mtDNA", "mitochondrial", "accession", "isolate", "Homo sapiens", "sequence"]
    if accession:
        keywords = [accession] + keywords

    filtered, better_filter = {}, {}
    print("before doing session")
    async with aiohttp.ClientSession() as session:
        tasks = []
        for link in search_results:
            if link:
                print("link: ", link)
                tasks.append(process_link(session, link, saveLinkFolder, keywords, accession))
                print("done")
        results = await asyncio.gather(*tasks)
        print("outside session")
    # merge results
    for output_link in results:
        for out_link in output_link:
            if isinstance(out_link, list) and len(out_link) > 1:
                kw = out_link[1]
                if accession and kw == accession.lower():
                    if len(out_link) == 2:
                        better_filter[out_link[0]] = ""
                    elif len(out_link) == 3:
                        better_filter[out_link[0]] = out_link[2]
                if len(out_link) == 2:
                    better_filter[out_link[0]] = ""
                elif len(out_link) == 3:
                    better_filter[out_link[0]] = out_link[2]
            else:
                filtered[out_link] = ""

    return better_filter or filtered

def filter_links_by_metadata(search_results, saveLinkFolder, accession=None):
    TRUSTED_DOMAINS = [
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "biorxiv.org",
    "researchgate.net",
    "nature.com",
    "sciencedirect.com"
    ]
    def is_trusted_link(link):
      for domain in TRUSTED_DOMAINS:
        if domain in link:
          return True
      return False
    def is_relevant_title_snippet(link, saveLinkFolder, accession=None):
      output = []
      keywords = ["mtDNA", "mitochondrial", "Homo sapiens"]
      #keywords = ["mtDNA", "mitochondrial"]
      if accession:
        keywords = [accession] + keywords
      title_snippet = link.lower()
      #print("save link folder inside this filter function: ", saveLinkFolder)  
      article_text = data_preprocess.extract_text(link,saveLinkFolder)
      print("article text done")
      #print(article_text)  
      try:
        ext = link.split(".")[-1].lower()
        if ext not in ["pdf", "docx", "xlsx"]:
            html = extractHTML.HTML("", link)
            jsonSM = html.getSupMaterial()
            if jsonSM:
                output += sum((jsonSM[key] for key in jsonSM), [])
      except Exception:
        pass  # continue silently
      for keyword in keywords:
        if article_text:
          if keyword.lower() in article_text.lower():
            if link not in output:
              output.append([link,keyword.lower(), article_text])
            return output
        if keyword.lower() in title_snippet.lower():
          if link not in output:
            output.append([link,keyword.lower()])
          print("link and keyword for title: ", link, keyword)    
          return output
      return output
    
    filtered = {}
    better_filter = {}
    if len(search_results) > 0:
      print(search_results)
      for link in search_results:
          # if is_trusted_link(link):
          #   if link not in filtered:
          #     filtered.append(link)
          # else:
          print(link)
          if link:    
            output_link = is_relevant_title_snippet(link,saveLinkFolder, accession)
            print("output link: ")
            print(output_link)
            for out_link in output_link:
              if isinstance(out_link,list) and len(out_link) > 1:
                print(out_link)
                kw = out_link[1]
                if accession and kw == accession.lower():
                  if len(out_link) == 2:
                    better_filter[out_link[0]] = ""
                  elif len(out_link) == 3:
                    # save article
                    better_filter[out_link[0]] = out_link[2]
                if len(out_link) == 2:
                  better_filter[out_link[0]] = ""
                elif len(out_link) == 3:
                  # save article
                  better_filter[out_link[0]] = out_link[2]
              else: filtered[out_link] = ""
          print("done with link and here is filter: ",filtered)      
    if better_filter:
      filtered = better_filter                  
    return filtered

def smart_google_search(accession_id, metadata):
  print("doing smart google queries")
  queries = smart_google_queries(accession_id, metadata)
  links = []
  _search_fn = (
      (lambda q, n: mtdna_classifier.search_google_custom(q, n))
      if mtdna_classifier is not None
      else _search_any
  )
  for q in queries:
      print("\n🔍 Query:", q)
      results = _search_fn(q, 2)
      for link in results:
          print(f"- {link}")
          if link not in links:
              links.append(link)
  #filter_links = filter_links_by_metadata(links)
  return links
# Method 2: Prompt LLM better or better ai search api with all
# the total information from even ncbi and all search