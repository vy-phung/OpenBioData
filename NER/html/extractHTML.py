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
        return textHTML if len(textHTML) > len(textJson) else textJson

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
    # check if there is material or not
    json = {}
    soup = self.openHTMLFile()
    for h2Pos in range(len(soup.find_all('h2'))):
      if "supplementary" in soup.find_all('h2')[h2Pos].text.lower() or "material" in soup.find_all('h2')[h2Pos].text.lower() or "additional" in soup.find_all('h2')[h2Pos].text.lower() or "support" in soup.find_all('h2')[h2Pos].text.lower():
        #print(soup.find_all('h2')[h2Pos].find_next("a").get("href"))
        link, output = [],[]
        if soup.find_all('h2')[h2Pos].text not in json:
          json[soup.find_all('h2')[h2Pos].text] = []
        for l in soup.find_all('h2')[h2Pos].find_all_next("a",href=True):
            link.append(l["href"])
        if h2Pos + 1 < len(soup.find_all('h2')):
          nexth2Link = soup.find_all('h2')[h2Pos+1].find_next("a",href=True)["href"]
          if nexth2Link in link:
            link = link[:link.index(nexth2Link)]
        # only take links having "https" in that
        for i in link:
          if "https" in i:  output.append(i)
        json[soup.find_all('h2')[h2Pos].text].extend(output)
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
  