# reference: https://www.crummy.com/software/BeautifulSoup/bs4/doc/#for-html-documents
from bs4 import BeautifulSoup
import requests
def openFile(path, mode="r"):
    return open(path, mode)
def saveFile(data, path):
    with open(path, "w") as f:
        f.write(str(data))
from NER import cleanText
import pandas as pd
from lxml.etree import ParserError, XMLSyntaxError
import aiohttp
import asyncio


async def async_fetch_html_playwright(url: str, timeout_ms: int = 20000) -> str:
    """Render a page with a real headless browser and return its HTML.

    Fallback for pages blocked by bot-protection (Cloudflare/Akamai JS
    challenges etc.) that a plain HTTP request can't pass -- this executes
    JS like a normal browser visit would, regardless of whether the
    underlying content is open access (the block is about traffic pattern,
    not access tier). Returns '' on any failure (timeout, no browser
    binaries installed, navigation error, etc.) so callers can fall through
    to the next fallback.
    """
    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        print(f"[playwright] not available: {e}")
        return ""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ))
                await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                # Give an automatic JS challenge (e.g. Cloudflare) a moment to resolve.
                await page.wait_for_timeout(3000)
                return await page.content()
            finally:
                await browser.close()
    except Exception as e:
        print(f"[playwright] fetch failed for {url}: {e}")
        return ""


class HTML():
  def __init__(self, htmlFile, htmlLink, htmlContent: str=None):
    self.htmlLink = htmlLink
    self.htmlFile = htmlFile
    self.htmlContent = htmlContent  # NEW: store raw HTML if provided  
  def fetch_crossref_metadata(self, doi):
    """Fetch metadata from CrossRef API for a given DOI."""
    try:
        # Define headers with User-Agent
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36'
        }
          
        url = f"https://api.crossref.org/works/{doi}"
        # Pass headers in the request
        r = requests.get(url, headers=headers, timeout=10)
          
        if r.status_code == 200:
            return r.json().get("message", {})
        else:
            print(f"⚠️ CrossRef fetch failed ({r.status_code}) for DOI: {doi}")
            return {}
    except Exception as e:
        print(f"❌ CrossRef exception: {e}")
        return {}
  # def openHTMLFile(self):
  #   headers = {
  #       "User-Agent": (
  #           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
  #           "AppleWebKit/537.36 (KHTML, like Gecko) "
  #           "Chrome/114.0.0.0 Safari/537.36"
  #       ),
  #       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  #       "Referer": self.htmlLink,
  #       "Connection": "keep-alive"
  #   }

  #   session = requests.Session()
  #   session.headers.update(headers)

  #   if self.htmlLink != "None":
  #       try:
  #           r = session.get(self.htmlLink, allow_redirects=True, timeout=15)
  #           if r.status_code != 200:
  #               print(f"❌ HTML GET failed: {r.status_code} — {self.htmlLink}")
  #               return BeautifulSoup("", 'html.parser')
  #           soup = BeautifulSoup(r.content, 'html.parser')
  #       except Exception as e:
  #           print(f"❌ Exception fetching HTML: {e}")
  #           return BeautifulSoup("", 'html.parser')
  #   else:
  #       with open(self.htmlFile) as fp:
  #           soup = BeautifulSoup(fp, 'html.parser')
  #   return soup
  
  def openHTMLFile(self):
      """Return a BeautifulSoup object from cached htmlContent, file, or requests."""
      # If raw HTML already provided (from async aiohttp), use it directly
      if self.htmlContent is not None:
          return BeautifulSoup(self.htmlContent, "html.parser")
      
      not_need_domain = ['https://broadinstitute.github.io/picard/',
              'https://software.broadinstitute.org/gatk/best-practices/',
              'https://www.ncbi.nlm.nih.gov/genbank/',
              'https://www.mitomap.org/']
      if self.htmlLink in not_need_domain:
        return BeautifulSoup("", 'html.parser')        
      headers = {
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/114.0.0.0 Safari/537.36"
          ),
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "en-US,en;q=0.9",
          "Referer": "https://www.google.com/",
          #"Referer": self.htmlLink,
          "Connection": "keep-alive"
      }

      session = requests.Session()
      session.headers.update(headers)
      try:
          if self.htmlLink and self.htmlLink != "None":
              r = session.get(self.htmlLink, allow_redirects=True, timeout=15)
              if r.status_code != 200 or not r.text.strip():
                  print(f"❌ HTML GET failed ({r.status_code}) or empty page: {self.htmlLink}")
                  return BeautifulSoup("", 'html.parser')
              # Update to the final redirected URL (e.g. doi.org -> nature.com) so
              # getSupMaterial()'s relative-link resolution uses the real page's
              # domain/path instead of the doi.org redirect entry point -- otherwise
              # a relative href like "/articles/x#ref" resolves against doi.org and
              # produces a broken URL like "https://doi.org/articles/x#ref".
              if r.url and r.url != self.htmlLink:
                  self.htmlLink = r.url
              soup = BeautifulSoup(r.content, 'html.parser')
          elif self.htmlFile:
              with open(self.htmlFile, encoding='utf-8') as fp:
                  soup = BeautifulSoup(fp, 'html.parser')
      except (ParserError, XMLSyntaxError, OSError) as e:
          print(f"🚫 HTML parse error for {self.htmlLink}: {type(e).__name__}")
          return BeautifulSoup("", 'html.parser')
      except Exception as e:
          print(f"❌ General exception for {self.htmlLink}: {e}")
          return BeautifulSoup("", 'html.parser')

      return soup

  async def async_fetch_html(self):
    """Async fetch HTML content with aiohttp."""
    not_need_domain = [
        "https://broadinstitute.github.io/picard/",
        "https://software.broadinstitute.org/gatk/best-practices/",
        "https://www.ncbi.nlm.nih.gov/genbank/",
        "https://www.mitomap.org/",
    ]
    if self.htmlLink in not_need_domain:
        return ""  # Skip domains we don't need

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
        "Connection": "keep-alive",
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(self.htmlLink, timeout=15) as resp:
                if resp.status != 200:
                    print(f"❌ HTML GET failed ({resp.status}) — {self.htmlLink}")
                    return ""
                return await resp.text()
    except Exception as e:
        print(f"❌ Async fetch failed for {self.htmlLink}: {e}")
        return ""

  @classmethod
  async def bulk_fetch(cls, links: list[str]):
      """Fetch multiple links concurrently, return list of HTML() objects with htmlContent filled."""
      tasks = [cls("", link).async_fetch_html() for link in links]
      results = await asyncio.gather(*tasks, return_exceptions=True)

      out = []
      for link, content in zip(links, results):
          if isinstance(content, Exception):
              print(f"⚠️ Exception while fetching {link}: {content}")
              out.append(cls("", link, htmlContent=""))
          else:
              out.append(cls("", link, htmlContent=content))
      return out

    
  def getText(self):
    try:
      soup = self.openHTMLFile()
      s = soup.find_all("html")
      text = ""
      if s:
        for t in range(len(s)):
          text = s[t].get_text()
      cl = cleanText.cleanGenText()
      text = cl.removeExtraSpaceBetweenWords(text)
      return text
    except:
      print("failed get text from html")
      return "" 

  async def async_getListSection(self, scienceDirect=None):
    try:
        json = {}
        textJson, textHTML = "", ""

        # Use preloaded HTML (fast path)
        soup = self.openHTMLFile()
        try:
            h2_tags = soup.find_all('h2')
            for idx, h2 in enumerate(h2_tags):
                section_title = h2.get_text(strip=True)
                json.setdefault(section_title, [])
                next_h2 = h2_tags[idx+1] if idx+1 < len(h2_tags) else None
                for p in h2.find_all_next("p"):
                    if next_h2 and p == next_h2:
                        break
                    json[section_title].append(p.get_text(strip=True))
        except Exception:
            pass  # continue to fallback
        # If no sections or explicitly ScienceDirect
        is_sciencedirect_source = "sciencedirect" in self.htmlLink.lower()

        #if scienceDirect is not None or len(json) == 0:
        if is_sciencedirect_source and (scienceDirect is not None or len(json) == 0):
            print("async fetching ScienceDirect metadata...")
            api_key = "d0f25e6ae2b275e0d2b68e0e98f68d70"
            doi = self.htmlLink.split("https://doi.org/")[-1]
            base_url = f"https://api.elsevier.com/content/article/doi/{doi}"
            headers = {"Accept": "application/json", "X-ELS-APIKey": api_key}

            # async with aiohttp.ClientSession() as session:
            #     async with session.get(base_url, headers=headers, timeout=15) as resp:
            #         if resp.status == 200:
            #             data = await resp.json()
            #             if isinstance(data, dict):
            #                 json["fullText"] = data
            
            try:
                timeout = aiohttp.ClientTimeout(total=8)  # hard 8 seconds
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(base_url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if isinstance(data, dict):
                                json["fullText"] = data
                        else:
                            print(f"ScienceDirect returned status {resp.status}")
            except asyncio.TimeoutError:
                print("⚠️ ScienceDirect request timed out (skipped).")
            except aiohttp.ClientError as e:
                print(f"⚠️ ScienceDirect client error: {e}")
            except Exception as e:
                print(f"⚠️ Unknown ScienceDirect error: {e}")


        # Merge text
        textJson = self.mergeTextInJson(json)
        textHTML = self.getText()
        text = textHTML if len(textHTML) > len(textJson) else textJson
        try:
            tables_text = self.getTablesAsText()
            if tables_text:
                text += "\n" + tables_text
        except Exception as e:
            print("⚠️ async_getListSection: table serialization failed:", e)
        return text

    except Exception as e:
        print("❌ async_getListSection failed:", e)
        return ""
  
  def getListSection(self, scienceDirect=None):
    try:  
        json = {}
        text = ""
        textJson, textHTML = "",""
        if scienceDirect == None:
          # soup = self.openHTMLFile()
          # # get list of section
          # json = {}
          # for h2Pos in range(len(soup.find_all('h2'))):
          #   if soup.find_all('h2')[h2Pos].text not in json:
          #     json[soup.find_all('h2')[h2Pos].text] = []
          #   if h2Pos + 1 < len(soup.find_all('h2')):
          #     content = soup.find_all('h2')[h2Pos].find_next("p")
          #     nexth2Content = soup.find_all('h2')[h2Pos+1].find_next("p")
          #     while content.text != nexth2Content.text:
          #       json[soup.find_all('h2')[h2Pos].text].append(content.text)
          #       content = content.find_next("p")
          #   else:
          #     content = soup.find_all('h2')[h2Pos].find_all_next("p",string=True)
          #     json[soup.find_all('h2')[h2Pos].text] = list(i.text for i in content)

            soup = self.openHTMLFile()
            h2_tags = soup.find_all('h2')
            json = {}
    
            for idx, h2 in enumerate(h2_tags):
                section_title = h2.get_text(strip=True)
                json.setdefault(section_title, [])
                
                # Get paragraphs until next H2
                next_h2 = h2_tags[idx+1] if idx+1 < len(h2_tags) else None
                for p in h2.find_all_next("p"):
                    if next_h2 and p == next_h2:
                        break
                    json[section_title].append(p.get_text(strip=True))  
          # format
        '''json = {'Abstract':[], 'Introduction':[], 'Methods'[],
        'Results':[], 'Discussion':[], 'References':[],
        'Acknowledgements':[], 'Author information':[], 'Ethics declarations':[],
        'Additional information':[], 'Electronic supplementary material':[],
        'Rights and permissions':[], 'About this article':[], 'Search':[], 'Navigation':[]}'''
        if scienceDirect!= None or len(json)==0:
          # Replace with your actual Elsevier API key
          api_key = os.environ["SCIENCE_DIRECT_API"]  
          # ScienceDirect article DOI or PI (Example DOI)
          doi =  self.htmlLink.split("https://doi.org/")[-1]  #"10.1016/j.ajhg.2011.01.009"
          # Base URL for the Elsevier API
          base_url = "https://api.elsevier.com/content/article/doi/"
          # Set headers with API key
          headers = {
              "Accept": "application/json",
              "X-ELS-APIKey": api_key
          }
          # Make the API request
          response = requests.get(base_url + doi, headers=headers)
    # Check if the request was successful
          if response.status_code == 200:
            data = response.json()
            supp_data = data["full-text-retrieval-response"]#["coredata"]["link"]
            # if "originalText" in list(supp_data.keys()):
            #   if type(supp_data["originalText"])==str:
            #     json["originalText"] = [supp_data["originalText"]]
            #   if type(supp_data["originalText"])==dict:
            #     json["originalText"] = [supp_data["originalText"][key] for key in supp_data["originalText"]]
            # else:
            #   if type(supp_data)==dict:
            #     for key in supp_data:
            #       json[key] = [supp_data[key]]
            if type(data)==dict:
                json["fullText"] = data
        textJson = self.mergeTextInJson(json)
        textHTML = self.getText()
        if len(textHTML) > len(textJson):
          text = textHTML
        else: text = textJson
        try:
            tables_text = self.getTablesAsText()
            if tables_text:
                text += "\n" + tables_text
        except Exception as e:
            print("⚠️ getListSection: table serialization failed:", e)
        return text #json
    except:
        print("failed all")
        return ""
  def getReference(self):
    # get reference to collect more next data
    ref = []
    json = self.getListSection()
    for key in json["References"]:
      ct = cleanText.cleanGenText(key)
      cleanText, filteredWord = ct.cleanText()
      if cleanText not in ref:
        ref.append(cleanText)
    return ref
  def getSupMaterial(self):
    """Find supplementary/additional material download links on a publisher page.

    Strategy:
      1. Heading-based scan (h2/h3/h4) — works for most journals.
      2. Global file-link scan — catches OUP/Oxford, Wiley, Springer, and any
         publisher that places download anchors outside a dedicated heading block.
         Looks for .zip, .xlsx, .xls, .docx, .pdf, .csv, .tsv links anywhere on
         the page and resolves relative URLs to absolute.
    """
    from urllib.parse import urljoin as _urljoin, urlparse as _urlparse

    _SUPP_KW = {"supplementary", "supplemental", "material", "additional", "support",
                "data availability", "code availability", "software availability",
                "availability of data", "associated data"}
    _FILE_EXTS = {".zip", ".xlsx", ".xls", ".docx", ".doc", ".pdf", ".csv", ".tsv", ".txt"}

    def _is_supp_heading(text):
        t = text.lower()
        return any(kw in t for kw in _SUPP_KW)

    def _is_file_href(href):
        if not href:
            return False
        path = _urlparse(href).path.lower()
        return any(path.endswith(ext) for ext in _FILE_EXTS)

    def _resolve(href):
        if href.startswith("http"):
            return href
        return _urljoin(base_url, href)

    json = {}
    soup = self.openHTMLFile()
    # Read AFTER openHTMLFile() runs -- it updates self.htmlLink to the final
    # redirected URL (e.g. doi.org -> nature.com), so relative hrefs on the
    # page resolve against the real page domain instead of the doi.org
    # redirect entry point (which produced broken URLs like
    # "https://doi.org/articles/x#ref" before this fix).
    base_url = self.htmlLink or ""
    seen = set()

    # ── Pass 1: heading-based (h2, h3, h4) ────────────────────────────────
    all_headings = soup.find_all(["h2", "h3", "h4"])
    for idx, heading in enumerate(all_headings):
        if not _is_supp_heading(heading.get_text()):
            continue
        title = heading.get_text(strip=True)
        json.setdefault(title, [])

        # collect <a href> tags until the next heading of the same or higher level
        next_heading = all_headings[idx + 1] if idx + 1 < len(all_headings) else None
        collected = []
        for a in heading.find_all_next("a", href=True):
            if next_heading and a == next_heading.find_next("a", href=True):
                break
            href = _resolve(a["href"])
            if href not in seen:
                seen.add(href)
                collected.append(href)
        json[title].extend(collected)

    # ── Pass 2: global file-link scan ──────────────────────────────────────
    # Catches OUP silverchair CDN links, Wiley action/downloadSupplement, etc.
    global_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if _is_file_href(href):
            full = _resolve(href)
            if full not in seen:
                seen.add(full)
                global_links.append(full)

    # Also check <a> tags whose visible text mentions supplementary/data
    _text_kw = {"supplementary", "supplemental", "supp", "additional data", "data availability",
                "code availability", "software availability", "associated data"}
    for a in soup.find_all("a", href=True):
        link_text = a.get_text(strip=True).lower()
        if any(kw in link_text for kw in _text_kw):
            full = _resolve(a["href"])
            if full not in seen:
                seen.add(full)
                global_links.append(full)

    if global_links:
        json.setdefault("Supplementary Files", []).extend(global_links)

    return json
  def extractTable(self):
    soup = self.openHTMLFile()
    df = []
    if len(soup)>0:
      try:
        df = pd.read_html(str(soup))
      except ValueError:
        df = []
        print("No tables found in HTML file")
    return df

  def getTablesAsText(self):
    """Serialize every HTML <table> as labeled, row-wise "col=val" lines.

    getText()/getListSection() concatenate table cell text with no row or
    column boundaries, so an LLM reading the blob cannot reliably tell which
    value (e.g. disease status) belongs to which row (e.g. subject_id). This
    keeps each row's fields explicitly paired so that association survives.
    """
    try:
      tables = self.extractTable()
    except Exception as e:
      print("❌ getTablesAsText: extractTable failed:", e)
      return ""
    out = []
    for t_idx, df in enumerate(tables):
      try:
        df = df.fillna("").astype(str)
        cols = [str(c).strip() for c in df.columns]
        out.append(f"\n## Table {t_idx + 1}")
        for _, row in df.iterrows():
          pairs = [f"{cols[i]}={row.iloc[i]}" for i in range(len(cols)) if str(row.iloc[i]).strip()]
          if pairs:
            out.append("Row: " + ", ".join(pairs))
      except Exception as e:
        print(f"❌ getTablesAsText: failed to serialize table {t_idx}:", e)
    return "\n".join(out)
  def mergeTextInJson(self,jsonHTML):
    try:
      #cl = cleanText.cleanGenText()
      htmlText = ""
      if jsonHTML:
      #   try:
      #     for sec, entries in jsonHTML.items():
      #         for i, entry in enumerate(entries):
      #             # Only process if it's actually text
      #             if isinstance(entry, str):
      #                 if entry.strip():
      #                     entry, filteredWord = cl.textPreprocessing(entry, keepPeriod=True)
      #             else:
      #                 # Skip or convert dicts/lists to string if needed
      #                 entry = str(entry)

      #             jsonHTML[sec][i] = entry

      #             # Add spacing between sentences
      #             if i - 1 >= 0 and jsonHTML[sec][i - 1] and jsonHTML[sec][i - 1][-1] != ".":
      #                 htmlText += ". "
      #             htmlText += entry

      #         # Add final period if needed
      #         if entries and isinstance(entries[-1], str) and entries[-1] and entries[-1][-1] != ".":
      #             htmlText += "."
      #         htmlText += "\n\n"
      #   except:
        htmlText += str(jsonHTML)
      return htmlText
    except:
      print("failed merge text in json")
      return ""  
  