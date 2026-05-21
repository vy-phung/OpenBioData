from Bio import Entrez
import xml.etree.ElementTree as ET
import pipeline
import mtdna_classifier

# def fetch_bioproject(bioproject_id):
#     try:
#         # Set your email for NCBI
#         Entrez.email = "your.email@example.com"  # Replace with your email address

#         # Fetch the BioProject record using efetch
#         handle = Entrez.efetch(db="bioproject", id=bioproject_id, rettype="xml", retmode="xml")
#         xml_data = handle.read()  # Read the raw XML data
#         handle.close()

#         # Parse the XML data using ElementTree
#         root = ET.fromstring(xml_data)

#         # Initialize the output dictionary with default values
#         outputs = {
#             "bioproject_id": bioproject_id,
#             "title": "unknown",
#             "description": "unknown",
#             "publications": [],
#             "pubmed": [],
#             "biosamples": []
#         }

#         # Extract the title (usually under <Title>)
#         title_elem = root.find(".//ProjectDescr/Title")
#         if title_elem is not None:
#             outputs["title"] = title_elem.text

#         # Extract the description (usually under <Description>)
#         description_elem = root.find(".//ProjectDescr/Description")
#         if description_elem is not None:
#             outputs["description"] = description_elem.text

#         # Extract publications (usually under <Publication>)
#         publications = []
#         for publication in root.findall(".//ProjectDescr/Publication"):
#             pub_title_elem = publication.find(".//StructuredCitation/Title")
#             if pub_title_elem is not None:
#                 publications.append(pub_title_elem.text)
#         if publications:
#             outputs["publications"] = publications

#         # Extract PubMed IDs (under <DbType>ePubmed</DbType>)
#         pubmed_ids = []
#         for publication in root.findall(".//ProjectDescr/Publication"):
#             db_type_elem = publication.find(".//DbType")
#             if db_type_elem is not None and db_type_elem.text == "ePubmed":
#                 pubmed_id = publication.get("id")
#                 if pubmed_id:
#                     pubmed_ids.append(pubmed_id)
#         if pubmed_ids:
#             outputs["pubmed"] = pubmed_ids

#         # Extract biosample IDs (assuming they are in <OtherDbs>)
#         biosamples = []
#         for other_db in root.findall(".//OtherDbs/DbReference"):
#             biosample_id = other_db.get("id")
#             if biosample_id:
#                 biosamples.append(biosample_id)
#         if biosamples:
#             outputs["biosamples"] = biosamples

#         return outputs

#     except Exception as e:
#         print(f"Error fetching BioProject info: {e}")
#         #return {"bioproject_id": bioproject_id, "error": str(e)}
#         return {
#             "bioproject_id": bioproject_id,
#             "title": "unknown",
#             "description": "unknown",
#             "publications": [],
#             "pubmed": [],
#             "biosamples": []
#         }
import re, time, requests
import xml.etree.ElementTree as ET
def fetch_bioproject(bioproject_id):
    """
    Fetch BioProject metadata without relying on Entrez.esearch(db='bioproject'),
    which is unreliable on Colab/cloud IPs. Uses three fallback layers:
      1. Direct efetch with numeric ID extracted from accession string
      2. EuropePMC search by accession for publication/DOI
      3. NCBI elink via SRA (sra db esearch is more stable than bioproject)
    """
    outputs = {
        "bioproject_id": bioproject_id,
        "title": "unknown",
        "description": "unknown",
        "publications": [],
        "pubmed": [],
        "pubmed_dois": [],
        "biosamples": []
    }

    # ── Extract numeric ID directly from accession string ──────────────────
    # PRJNA385855 -> 385855, PRJEB12345 -> 12345
    numeric_id = re.sub(r'^PRJ[A-Z]+', '', bioproject_id)
    if not numeric_id.isdigit():
        print(f"Cannot parse numeric ID from {bioproject_id}")
        return outputs

    headers = {"User-Agent": f"research-pipeline/1.0 (mailto:{Entrez.email})"}

    # ── Layer 1: Direct efetch XML (no esearch needed) ──────────────────────
    xml_data = None
    for attempt in range(3):
        try:
            url = (
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                f"?db=bioproject&id={numeric_id}&rettype=xml&retmode=xml"
                f"&email={Entrez.email}&tool=research-pipeline"
            )
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 200:
                xml_data = r.content
                break
            print(f"efetch attempt {attempt+1} got HTTP {r.status_code}, retrying...")
            time.sleep(2 ** attempt)  # exponential backoff: 1s, 2s, 4s
        except Exception as e:
            print(f"efetch attempt {attempt+1} error: {e}")
            time.sleep(2 ** attempt)

    if xml_data:
        try:
            root = ET.fromstring(xml_data)

            title_elem = root.find(".//ProjectDescr/Title")
            if title_elem is not None:
                outputs["title"] = title_elem.text

            desc_elem = root.find(".//ProjectDescr/Description")
            if desc_elem is not None:
                outputs["description"] = desc_elem.text

            for pub in root.findall(".//ProjectDescr/Publication"):
                # Title: <StructuredCitation/Title> or fallback <Reference>
                title_text = None
                struct = pub.find(".//StructuredCitation/Title")
                ref    = pub.find("Reference")
                if struct is not None and struct.text:
                    title_text = struct.text
                elif ref is not None and ref.text:
                    title_text = ref.text
                if title_text:
                    outputs["publications"].append(title_text)

                # PMID: "id" attribute on <Publication> when DbType==ePubmed
                db_type = pub.find("DbType")
                if db_type is not None and db_type.text == "ePubmed":
                    pmid = pub.get("id")
                    if pmid:
                        outputs["pubmed"].append(pmid)
        except ET.ParseError as e:
            print(f"XML parse error: {e}")

    # ── Layer 2: EuropePMC search by BioProject accession ──────────────────
    # Works even when NCBI blocks Colab — searches full-text for the accession
    if not outputs["pubmed"]:
        try:
            r = requests.get(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={
                    "query": f'"{bioproject_id}"',   # exact phrase match
                    "format": "json",
                    "resultType": "core",
                    "pageSize": 5
                },
                headers=headers,
                timeout=15
            )
            if r.status_code == 200:
                results = r.json().get("resultList", {}).get("result", [])
                for result in results:
                    pmid = result.get("pmid")
                    doi  = result.get("doi")
                    title = result.get("title")
                    if pmid and pmid not in outputs["pubmed"]:
                        outputs["pubmed"].append(pmid)
                    if title and title not in outputs["publications"]:
                        outputs["publications"].append(title)
                    if pmid and doi:
                        outputs["pubmed_dois"].append({
                            "pmid": pmid,
                            "doi": doi,
                            "url": f"https://doi.org/{doi}"
                        })
                print(f"EuropePMC found {len(results)} results for {bioproject_id}")
        except Exception as e:
            print(f"EuropePMC search error: {e}")

    # ── Layer 3: NCBI elink via SRA db (more stable than bioproject db) ─────
    if not outputs["pubmed"]:
        try:
            # esearch sra db is more reliable on Colab than bioproject db
            r = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "sra", "term": bioproject_id,
                        "retmode": "json", "retmax": 1,
                        "email": Entrez.email},
                headers=headers, timeout=15
            )
            if r.status_code == 200:
                sra_ids = r.json().get("esearchresult", {}).get("idlist", [])
                if sra_ids:
                    # elink from SRA sample -> pubmed
                    r2 = requests.get(
                        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi",
                        params={"dbfrom": "bioproject", "db": "pubmed",
                                "id": numeric_id, "retmode": "json",
                                "email": Entrez.email},
                        headers=headers, timeout=15
                    )
                    if r2.status_code == 200:
                        linksets = r2.json().get("linksets", [])
                        for ls in linksets:
                            for ld in ls.get("linksetdbs", []):
                                if ld.get("dbto") == "pubmed":
                                    for link in ld.get("links", []):
                                        if str(link) not in outputs["pubmed"]:
                                            outputs["pubmed"].append(str(link))
        except Exception as e:
            print(f"SRA elink fallback error: {e}")

    # ── Resolve DOIs for any PMIDs not already resolved via EuropePMC ───────
    resolved_pmids = {d["pmid"] for d in outputs["pubmed_dois"]}
    for pmid in outputs["pubmed"]:
        if pmid not in resolved_pmids:
            doi = get_doi_via_europepmc(pmid)
            if doi:
                outputs["pubmed_dois"].append({
                    "pmid": pmid,
                    "doi": doi,
                    "url": f"https://doi.org/{doi}"
                })

    # ── BioSamples via elink ─────────────────────────────────────────────────
    outputs["biosamples"] = get_biosamples_from_bioproject(bioproject_id)

    return outputs


# # Example usage
# bioproject_info = fetch_bioproject("PRJNA976261")
# print(bioproject_info)

from Bio import Entrez

def search_sra_by_bioproject(bioproject_id):
    try:
        # Set your email for NCBI
        Entrez.email = "your.email@example.com"  # Replace with your email address

        # Search for data in the SRA database associated with the BioProject
        search_handle = Entrez.esearch(db="sra", term=bioproject_id, retmax=20)
        search_results = Entrez.read(search_handle)
        search_handle.close()

        # Print the raw results to see what we get
        print("SRA Search Results:", search_results)

        # Extract the list of SRA IDs from the search results
        sra_ids = search_results.get("IdList", [])

        if sra_ids:
            print(f"Found {len(sra_ids)} SRA entries for BioProject {bioproject_id}")
            return sra_ids
        else:
            return f"No SRA entries found for BioProject {bioproject_id}"

    except Exception as e:
        print(f"Error fetching SRA entries: {e}")
        return {"bioproject_id": bioproject_id, "error": str(e)}

# # Example usage
# bioproject_id = "PRJNA976261"
# sra_entries = search_sra_by_bioproject(bioproject_id)
# print(sra_entries)

import xml.etree.ElementTree as ET
from Bio import Entrez

def fetch_experiment_metadata(sra_ids):
    experiment_metadata = []
    for sra_id in sra_ids:
        try:
            # Fetch SRA record using its ID to retrieve experiment metadata
            fetch_handle = Entrez.efetch(db="sra", id=sra_id, retmode="xml")
            xml_data = fetch_handle.read()
            fetch_handle.close()

            # Print the raw XML data for inspection
            print(f"Raw XML data for SRA ID {sra_id}:\n{xml_data[:1000]}...\n")

            # Parse XML data using ElementTree
            root = ET.fromstring(xml_data)

            # Extract relevant metadata from the experiment section
            experiment_data = {
                "experiment_accession": root.findtext(".//EXPERIMENT/IDENTIFIERS/PRIMARY_ID", "No Experiment Accession"),
                "title": root.findtext(".//EXPERIMENT/TITLE", "No Title"),
                "study_accession": root.findtext(".//STUDY_REF/IDENTIFIERS/PRIMARY_ID", "No Study Accession"),
                "bio_project": root.findtext(".//STUDY_REF/IDENTIFIERS/EXTERNAL_ID[@namespace='BioProject']", "No BioProject ID"),
            }

            # Add the metadata to the list
            experiment_metadata.append(experiment_data)

        except Exception as e:
            print(f"Error fetching SRA ID {sra_id}: {e}")

    return experiment_metadata


# # Example usage with your SRA IDs
# sra_ids = ['28021305', '28021304', '28021303', '28021302', '28021301', '28021300', 
#            '28021299', '28021298', '28021297', '28021296', '27937649', '27937648']

# # Fetch experiment metadata
# experiment_metadata = fetch_experiment_metadata(sra_ids)

# if experiment_metadata:
#     print("Fetched Experiment Metadata:", experiment_metadata)
# else:
#     print("No Experiment metadata found from the given SRA IDs.")

import requests
import xml.etree.ElementTree as ET
from Bio import Entrez

Entrez.email = "your_email@example.com"

def get_experiment_xml(accession):
    output = ""
    ena_prefixes = ("ERS", "ERX", "ERR", "SAMEA")
    headers = {"User-Agent": f"research-pipeline/1.0 (mailto:{Entrez.email})"}

    if accession.startswith(ena_prefixes):
        url = f"https://www.ebi.ac.uk/ena/browser/api/xml/{accession}"
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"ENA error {response.status_code} for {accession}")
            return output
        xml_data = response.text
        root = ET.fromstring(xml_data)
        for elem in root.iter():
            output += f"{elem.tag}: {elem.text}\n"

    elif accession.startswith("SRS"):
      try:
          # Step 1: esearch with [accn] to get internal numeric ID
          r = requests.get(
              "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
              params={
                  "db": "sra",
                  "term": f"{accession}[accn]",
                  "retmode": "json",
                  "retmax": 20,
                  "email": Entrez.email,
                  "tool": "research-pipeline"
              },
              headers=headers, timeout=20
          )
          r.raise_for_status()
          ids = r.json().get("esearchresult", {}).get("idlist", [])

          if not ids:
              print(f"No SRA entries found for {accession}")
              return output

          # Step 2: esummary to extract the real SRX/SRR accession strings
          # efetch(db=sra) rejects numeric IDs — needs actual accessions
          r2 = requests.get(
              "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
              params={
                  "db": "sra",
                  "id": ",".join(ids),
                  "retmode": "json",
                  "email": Entrez.email,
                  "tool": "research-pipeline"
              },
              headers=headers, timeout=20
          )
          r2.raise_for_status()
          summary = r2.json().get("result", {})

          # Extract SRX accessions from expxml and SRR accessions from runs
          srx_accessions = []
          srr_accessions = []
          for uid in summary.get("uids", []):
              doc = summary.get(uid, {})

              # expxml contains: <Experiment acc="SRX3157041" ...>
              expxml = doc.get("expxml", "")
              srx_match = re.search(r'acc="(SRX\d+)"', expxml)
              if srx_match:
                  srx_accessions.append(srx_match.group(1))

              # runs contains: <Run acc="SRR6097086" ...>
              runs_xml = doc.get("runs", "")
              srr_matches = re.findall(r'acc="(SRR\d+)"', runs_xml)
              srr_accessions.extend(srr_matches)

          print(f"Found SRX: {srx_accessions}, SRR: {srr_accessions}")

          # Step 3: fetch full XML using real accessions (SRX preferred over SRR)
          fetch_accessions = srx_accessions or srr_accessions
          if fetch_accessions:
              r3 = requests.get(
                  "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                  params={
                      "db": "sra",
                      "id": ",".join(fetch_accessions),  # real SRX/SRR, not numeric
                      "retmode": "xml",
                      "email": Entrez.email,
                      "tool": "research-pipeline"
                  },
                  headers=headers, timeout=20
              )
              if r3.status_code == 200:
                  root = ET.fromstring(r3.content)
                  for elem in root.iter():
                      output += f"{elem.tag}: {elem.text}\n"
                  return output
              print(f"efetch with accessions got {r3.status_code}, trying ENA fallback...")

          # Step 4: ENA fallback — accepts SRS natively, no numeric ID needed
          r4 = requests.get(
              "https://www.ebi.ac.uk/ena/portal/api/filereport",
              params={
                  "accession": accession,
                  "result": "read_run",
                  "fields": ",".join([
                      "run_accession", "experiment_accession",
                      "sample_accession", "study_accession",
                      "tax_id", "scientific_name",
                      "instrument_model", "library_strategy",
                      "library_source", "library_layout", "read_count"
                  ]),
                  "format": "json"
              },
              headers=headers, timeout=20
          )
          if r4.status_code == 200 and r4.text.strip():
              for row in r4.json():
                  for k, v in row.items():
                      output += f"{k}: {v}\n"
          else:
              print(f"ENA fallback also failed with {r4.status_code}")

      except Exception as e:
          print(f"NCBI error for {accession}: {e}")

    else:
        # SRR, SRX, SAMN — direct efetch via requests
        try:
            r = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={
                    "db": "sra",
                    "id": accession,
                    "retmode": "xml",
                    "email": Entrez.email,
                    "tool": "research-pipeline"
                },
                headers=headers,
                timeout=20
            )
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for elem in root.iter():
                output += f"{elem.tag}: {elem.text}\n"
        except Exception as e:
            print(f"NCBI error for {accession}: {e}")

    return output

# # Example usage
# experiment_accession = "SRX20593386"  # Example SRA experiment ID
# get_experiment_xml(experiment_accession)  

from Bio import Entrez, Medline
import re

Entrez.email = "your_email@example.com"
def fetch_biosample_raw_metadata(biosample_id):
    output = ""
    try:
      handle = Entrez.efetch(db="biosample", id=str(biosample_id), retmode="xml")
      record = handle.read()
      handle.close()
      output = record 
    except Exception as e:
      print(f"Error fetching raw data for biosample {biosample_id}: {e}")
    return output

import requests

def fetch_ena_biosample_metadata(biosample_id):
    """Fetch BioSample metadata from ENA as JSON (for SAMEA... accessions)."""
    url = f"https://www.ebi.ac.uk/biosamples/samples/{biosample_id}.json"
    response = requests.get(url)
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error {response.status_code}")
        return ""
def fetch_ena_biosample_xml(biosample_id):
    url = f"https://www.ebi.ac.uk/biosamples/samples/{biosample_id}.xml"
    response = requests.get(url)
    
    if response.status_code == 200:
        return response.content  # raw XML bytes, like Entrez returns
    else:
        print(f"Error {response.status_code}")
        return ""

def fetch_biosample(biosample_id):
    if biosample_id.startswith("SAMEA") or biosample_id.startswith("SAME"):
        return fetch_ena_biosample_metadata(biosample_id)
    else:
        # Use Entrez for SAMN...
        return fetch_biosample_raw_metadata(biosample_id)

from Bio import Entrez

def get_biosamples_from_bioproject(bioproject_id):
    try:
        Entrez.email = "your.email@example.com"
        
        # 1. Get the numeric UID for the BioProject first
        # esearch needs to find the internal ID for PRJNA976261
        search_handle = Entrez.esearch(db="bioproject", term=bioproject_id)
        search_results = Entrez.read(search_handle)
        search_handle.close()
        
        if not search_results["IdList"]:
            return f"No BioProject UID found for {bioproject_id}"
            
        project_uid = search_results["IdList"][0]

        # 2. Use elink to find BioSamples linked to that BioProject UID
        # This is more reliable than a keyword search
        link_handle = Entrez.elink(dbfrom="bioproject", db="biosample", id=project_uid)
        link_results = Entrez.read(link_handle)
        link_handle.close()

        # 3. Extract the linked BioSample IDs
        biosample_ids = []
        if link_results[0].get("LinkSetDb"):
            # LinkSetDb[0] contains the links to the destination database (biosample)
            for link in link_results[0]["LinkSetDb"][0]["Link"]:
                biosample_ids.append(link["Id"])

        if not biosample_ids:
            return []

        # 4. Convert internal UIDs to Accession strings (SAMN...)
        summary_handle = Entrez.esummary(db="biosample", id=",".join(biosample_ids))
        summaries = Entrez.read(summary_handle)
        summary_handle.close()

        accessions = [docsum['Accession'] for docsum in summaries['DocumentSummarySet']['DocumentSummary']]
        
        return accessions

    except Exception as e:
        print(f"Error: {e}")
        return []

# # --- Test ---
# bioproject_input = "PRJNA976261"
# samples = get_biosamples_from_bioproject(bioproject_input)
# print(f"BioSample Accessions: {samples}")


import re
import json

def extract_biosample_links(markdown_text):
    """
    Parses NCBI BioSample Markdown text and extracts links for each Accession.
    """
    output = {}
    
    # Split the markdown into individual sample blocks
    # "Select item " is a clean delimiter for each sample in this text dump
    blocks = markdown_text.split("Select item ")
    
    # Skip the first block (index 0) because it's just the website header/navigation
    for block in blocks[1:]:
        
        # 1. Extract the Accession ID (e.g., SAMN35361966)
        accession_match = re.search(r'Accession:\s*(SAMN\d+)', block)
        if not accession_match:
            continue
        accession_id = accession_match.group(1)
        
        # 2. Extract the BioSample link
        # Looks for the standard biosample/UID pattern inside markdown parentheses
        biosample_match = re.search(r'\]\((https://www.ncbi.nlm.nih.gov/biosample/\d+)\)', block)
        biosample_url = biosample_match.group(1) if biosample_match else None
        
        # 3. Extract the BioProject link
        # Looks for the markdown link specifically labeled [BioProject]
        bioproject_match = re.search(r'\[BioProject\]\((.*?)\)', block)
        bioproject_url = bioproject_match.group(1) if bioproject_match else None
        
        # 4. Extract the SRA link
        # Looks for the markdown link specifically labeled [SRA]
        sra_match = re.search(r'\[SRA\]\((.*?)\)', block)
        sra_url = sra_match.group(1) if sra_match else None
        
        # 5. Accession Link
        # In the provided markdown, the word "Accession" is plain text, not a hyperlink.
        # So it defaults to None as requested.
        accession_url = None 
        
        # Build the dictionary for this accession
        output[accession_id] = {
            "biosample": biosample_url,
            "bioproject": bioproject_url,
            "sra": sra_url,
            "accession": accession_url
        }
        
    return output

# # --- Test the function with your text ---

# # (Assuming 'markdown_content' is a variable containing the text you pasted above)
# url = "https://www.ncbi.nlm.nih.gov/biosample?Db=biosample&DbFrom=bioproject&Cmd=Link&LinkName=bioproject_biosample&LinkReadableName=BioSample&ordinalpos=1&IdsFromResult=976261"
# markdown_content = pipeline.fetch_text_from_url(url)

# output_dict = extract_biosample_links(markdown_content)

# # To print it beautifully so you can verify it matches your requirement:
# print(json.dumps(output_dict, indent=4))

import re

def parse_bioproject_markdown(markdown_text):
    # Initialize the structure
    outputs = {
        "bioproject_id": "unknown",
        "title": "unknown",
        "description": "unknown",
        "publications": [],
        "pubmed": [],
        "biosamples": []
    }

    # 1. Extract BioProject Accession (ID)
    id_match = re.search(r"Accession:\s*([A-Z0-9]+)", markdown_text)
    if id_match:
        outputs["bioproject_id"] = id_match.group(1)

    # 2. Extract Title
    # It usually appears between the ID line and the dashed line
    title_match = re.search(r"ID: \d+\n\n(.*?)\n---", markdown_text, re.DOTALL)
    if title_match:
        outputs["title"] = title_match.group(1).strip()

    # 3. Extract Description
    # Matches the text between the dashed line and the "More..." or "Less..." tags
    # desc_match = re.search(r"---\n\n(.*?)(?:\[More\.\.\.\]|\[Less\.\.\.\])", markdown_text, re.DOTALL)
    # if desc_match:
    #     outputs["description"] = desc_match.group(1).strip()
    outputs["description"] = markdown_text

    # 4. Extract Publications (Titles)
    # Finds titles inside quotes that follow a PubMed link
    pub_titles = re.findall(r'\]\s+"(.*?)"', markdown_text)
    outputs["publications"] = [t for t in pub_titles if len(t) > 5]

    # 5. Extract PubMed IDs/Links
    # Finds all 8-digit strings in the specific PubMed URL format
    pmid_matches = re.findall(r"pubmed/(\d{8})", markdown_text)
    outputs["pubmed"] = sorted(list(set(pmid_matches)))

    # 6. Extract BioSample Links
    # Looks for any URL containing 'biosample'
    biosample_links = re.findall(r'\((https?://www\.ncbi\.nlm\.nih\.gov/biosample\S+)\)', markdown_text)
    # Also look for the 'Db=biosample' style links found in the table
    table_links = re.findall(r'\(https?://www\.ncbi\.nlm\.nih\.gov/bioproject\?Db=biosample\S+\)', markdown_text)
    
    # Combine and clean the list (strip trailing ) if any)
    all_biosample_links = [link.strip(')') for link in (biosample_links + table_links)]
    outputs["biosamples"] = sorted(list(set(all_biosample_links)))

    return outputs

# # --- Usage ---
# results = parse_bioproject_markdown(markdown_content)
# import json
# print(json.dumps(results, indent=4))

def extract_NCBI_directly(accession):
  outputs = {}
  if accession:
    if accession.startswith("PRJ"): # bioproject
      bioproject_info = fetch_bioproject(accession)
      # get biosamples from bioproject
      biosamples = get_biosamples_from_bioproject(accession)
      bioproject_info["biosamples"] = biosamples
      outputs[accession] = bioproject_info
    elif accession.startswith("SAM"): # biosample
      biosample_info = fetch_biosample(accession)
      outputs[accession] = biosample_info
    elif accession.startswith("SR") or accession.startswith("ER"): # experimental data
      experiment_data = get_experiment_xml(accession)
      outputs[accession] = experiment_data  
    else: # accession
      text = mtdna_classifier.fetch_ncbi_metadata(accession)
      outputs[accession] = text
  return outputs    

def extract_NCBI_fromLinks(links):
  """ncbi_links = {"bioproject":"https://www.ncbi.nlm.nih.gov/bioproject/976261", # example: https://www.ncbi.nlm.nih.gov/bioproject/976261: PRJNA976261
      "biosample":"https://www.ncbi.nlm.nih.gov/biosample/SAMN35361956", # example: https://www.ncbi.nlm.nih.gov/biosample/SAMN35361956: SAMN35361956
      "accession": "https://www.ncbi.nlm.nih.gov/nuccore/KU131308", # example: https://www.ncbi.nlm.nih.gov/nuccore/KU131308: KU131308
      }"""
  outputs = {}
  for link in links:
    if link.startswith("https://www.ncbi.nlm.nih.gov/bioproject/"):
      markdown_text = pipeline.fetch_text_from_url(link)
      bioproject_info = parse_bioproject_markdown(markdown_text)
      # get biosamples from biosample links
      if biosamples:
        biosamples_lists = []
        for biosample_url in biosamples:
          biosample_text = pipeline.fetch_text_from_url(biosample_url)
          biosamples_lists += extract_biosample_links(biosample_text)
        if biosamples_lists:  biosamples_lists = pipeline.unique_preserve_order(biosamples_lists)
        bioproject_info["biosamples"] = biosamples_lists
      outputs[link] = bioproject_info
    else:
      markdown_text = pipeline.fetch_text_from_url(link)
      outputs[link] = markdown_text
  return outputs  

import requests

def get_doi_via_europepmc(pmid):
    if not pmid:  return None
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=ext_id:{pmid}%20src:med&format=json"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        if not response.text.strip():
            print(f"EuropePMC returned empty response for PMID {pmid}")
            return None
        data = response.json()
    except Exception as e:
        print(f"EuropePMC request failed for PMID {pmid}: {e}")
        return None

    results = data.get('resultList', {}).get('result', [])
    if not results:
        return None
    return results[0].get('doi')
      