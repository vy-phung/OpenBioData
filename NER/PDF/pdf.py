#!pip install pdfreader
# pdfreader is an optional fallback (imported lazily where used) -- PyMuPDF/tabula
# cover the common path, so a missing pdfreader must not break module import.
#!pip install bs4
from bs4 import BeautifulSoup
import requests
from NER import cleanText
#!pip install tabula-py
import tabula
import fitz  # PyMuPDF
import os

class PDF():
  def __init__(self, pdf, saveFolder, doi=None):
    self.pdf = pdf
    self.doi = doi
    self.saveFolder = saveFolder

  def openPDFFile(self):
    if "https" in self.pdf:
      name = self.pdf.split("/")[-1]
      name = self.downloadPDF(self.saveFolder)
      if name != "no pdfLink to download":
        fileToOpen = os.path.join(self.saveFolder, name)
      else:
        fileToOpen = self.pdf
    else:
      fileToOpen = self.pdf
    return open(fileToOpen, "rb")

  def downloadPDF(self, saveFolder):
    pdfLink = ''
    if ".pdf" not in self.pdf and "https" not in self.pdf:
      r = requests.get(self.pdf)
      soup = BeautifulSoup(r.content, 'html.parser')
      links = soup.find_all("a")
      for link in links:
        if ".pdf" in link.get("href", ""):
          if self.doi in link.get("href"):
            pdfLink = link.get("href")
            break
    else:
      pdfLink = self.pdf

    if pdfLink != '':
      response = requests.get(pdfLink)
      name = pdfLink.split("/")[-1]
      print("inside download PDF and name and link are: ", pdfLink, name)  
      print("saveFolder is: ", saveFolder)  
      with open(os.path.join(saveFolder, name), 'wb') as pdf:
        print("len of response content: ", len(response.content))  
        pdf.write(response.content)
      print("pdf downloaded")
      return name
    else:
      return "no pdfLink to download"

  def extractText(self):
    try:  
        fileToOpen = self.openPDFFile().name
        try:
          doc = fitz.open(fileToOpen)
          text = ""
          for page in doc:
            text += page.get_text("text") + "\n\n"
          doc.close()
    
          if len(text.strip()) < 100:
            print("Fallback to PDFReader due to weak text extraction.")
            text = self.extractTextWithPDFReader()
          return text
        except Exception as e:
          print("Failed with PyMuPDF, fallback to PDFReader:", e)
          return self.extractTextWithPDFReader()
    except:
        return ""
  def extract_text_excluding_tables(self):
    fileToOpen = self.openPDFFile().name
    text = ""
    try:
        doc = fitz.open(fileToOpen)
        for page in doc:
            blocks = page.get_text("dict")["blocks"]
            
            for block in blocks:
                if block["type"] == 0:  # text block
                    lines = block.get("lines", [])
                    
                    if not lines:
                        continue
                    avg_words_per_line = sum(len(l["spans"]) for l in lines) / len(lines)
                    if avg_words_per_line > 1:  # Heuristic: paragraph-like blocks
                        for line in lines:
                            text += " ".join(span["text"] for span in line["spans"]) + "\n"
        doc.close()
        if len(text.strip()) < 100:
          print("Fallback to PDFReader due to weak text extraction.")
          text = self.extractTextWithPDFReader()
        return text
    except Exception as e:
      print("Failed with PyMuPDF, fallback to PDFReader:", e)
      return self.extractTextWithPDFReader()

  def extractTextWithPDFReader(self):
    jsonPage = {}
    try:
        from pdfreader import PDFDocument, SimplePDFViewer
    except ImportError:
        print("⚠️ pdfreader not installed; skipping PDFReader fallback.")
        return jsonPage
    try:
        pdf = self.openPDFFile()
        print("open pdf file")  
        print(pdf)  
        doc = PDFDocument(pdf)
        viewer = SimplePDFViewer(pdf)
        all_pages = [p for p in doc.pages()]
        cl = cleanText.cleanGenText()
        pdfText = ""
        for page in range(1, len(all_pages)):
          viewer.navigate(page)
          viewer.render()
          if str(page) not in jsonPage:
            jsonPage[str(page)] = {}
          text = "".join(viewer.canvas.strings)
          clean, filteredWord = cl.textPreprocessing(text)
          jsonPage[str(page)]["normalText"] = [text]
          jsonPage[str(page)]["cleanText"] = [' '.join(filteredWord)]
          jsonPage[str(page)]["image"] = [viewer.canvas.images]
          jsonPage[str(page)]["form"] = [viewer.canvas.forms]
          jsonPage[str(page)]["content"] = [viewer.canvas.text_content]
          jsonPage[str(page)]["inline_image"] = [viewer.canvas.inline_images]
        pdf.close()
    except:
        jsonPage = {}        
    return self.mergeTextinJson(jsonPage)

  def extractTable(self,pages="all",saveFile=None,outputFormat=None):
    '''pages (str, int, iterable of int, optional) –
      An optional values specifying pages to extract from. It allows str,`int`, iterable of :int. Default: 1
      Examples: '1-2,3', 'all', [1,2]'''
    df = []
    if "https" in self.pdf:
      name = self.pdf.split("/")[-1]
      name = self.downloadPDF(self.saveFolder)
      if name != "no pdfLink to download":
        fileToOpen = self.saveFolder + "/" + name
      else: fileToOpen = self.pdf
    else: fileToOpen = self.pdf
    try:
      df = tabula.read_pdf(fileToOpen, pages=pages)
    # saveFile: "/content/drive/MyDrive/CollectData/NER/PDF/tableS1.csv"
    # outputFormat: "csv"
    #tabula.convert_into(self.pdf, saveFile, output_format=outputFormat, pages=pages)
    except:# ValueError:
      df = []
      print("No tables found in PDF file")
    return df

  def mergeTextinJson(self, jsonPDF):
    try:  
        cl = cleanText.cleanGenText()
        pdfText = ""
        if jsonPDF:  
            for page in jsonPDF:
              if len(jsonPDF[page]["normalText"]) > 0:
                for i in range(len(jsonPDF[page]["normalText"])):
                  text = jsonPDF[page]["normalText"][i]
                  if len(text) > 0:
                    text = cl.removeTabWhiteSpaceNewLine(text)
                    text = cl.removeExtraSpaceBetweenWords(text)
                  jsonPDF[page]["normalText"][i] = text
                  if i - 1 > 0:
                    if jsonPDF[page]["normalText"][i - 1][-1] != ".":
                      pdfText += ". "
                  pdfText += jsonPDF[page]["normalText"][i]
                if len(jsonPDF[page]["normalText"][i]) > 0:
                  if jsonPDF[page]["normalText"][i][-1] != ".":
                    pdfText += "."
                pdfText += "\n\n"
        return pdfText
    except:
        return ""

import os
import requests
from bs4 import BeautifulSoup
import fitz  # PyMuPDF
import tabula
from NER import cleanText

class PDFFast:
    _cache = {}  # cache for loaded documents

    def __init__(self, pdf_path_or_url, saveFolder, doi=None):
        self.pdf = pdf_path_or_url
        self.saveFolder = saveFolder or "."
        self.doi = doi
        self.local_path = self._ensure_local()
        self.doc = None  # Lazy load in PyMuPDF

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/pdf,text/html,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    }

    def _ensure_local(self):
        """Download if URL, else return local path."""
        if not self.pdf.startswith("http"):
            return self.pdf
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.pdf)
            qs = parse_qs(parsed.query)
            name = qs.get('file', [os.path.basename(parsed.path)])[0]
            if not name:
                name = 'download.pdf'
            local_path = os.path.join(self.saveFolder, name)
            if not os.path.exists(local_path):
                pdf_link = self._resolve_pdf_link(self.pdf)
                if not pdf_link:
                    raise FileNotFoundError(f"No PDF link found for {self.pdf}")
                print(f"⬇ Downloading PDF: {pdf_link}")
                r = requests.get(pdf_link, headers=self._HEADERS, timeout=30, allow_redirects=True)
                r.raise_for_status()
                ct = r.headers.get('Content-Type', '')
                if 'text/html' in ct:
                    raise ValueError(
                        f"Publisher returned HTML instead of PDF (blocked or requires login): {pdf_link}"
                    )
                with open(local_path, "wb") as f:
                    f.write(r.content)
            return local_path
        except Exception as e:
            print(f"❌ Could not download PDF {self.pdf}: {e}")
            return self.pdf

    def _resolve_pdf_link(self, url):
        """If URL is HTML, parse for .pdf link."""
        if url.lower().endswith(".pdf"):
            return url
        try:
            r = requests.get(url, timeout=15)
            soup = BeautifulSoup(r.content, "html.parser")
            for link in soup.find_all("a"):
                href = link.get("href", "")
                if ".pdf" in href and (not self.doi or self.doi in href):
                    return href if href.startswith("http") else f"https://{r.url.split('/')[2]}{href}"
        except Exception as e:
            print(f"❌ Failed to resolve PDF link: {e}")
        return None

    def _load_doc(self):
        """Load PyMuPDF document with caching."""
        if self.local_path in PDFFast._cache:
            return PDFFast._cache[self.local_path]
        doc = fitz.open(self.local_path)
        PDFFast._cache[self.local_path] = doc
        return doc

    def extract_text(self):
        """Extract all text quickly with PyMuPDF."""
        try:
            doc = self._load_doc()
            text = "\n\n".join(page.get_text(flags=1) for page in doc)
            return text.strip() or self.extract_text_pdfreader()
        except Exception as e:
            print(f"⚠️ PyMuPDF failed: {e}")
            return self.extract_text_pdfreader()

    def extract_text_excluding_tables(self):
        """Heuristic: skip table-like blocks."""
        text_parts = []
        try:
            doc = self._load_doc()
            for page in doc:
                for block in page.get_text("dict")["blocks"]:
                    if block["type"] != 0:  # skip non-text
                        continue
                    lines = block.get("lines", [])
                    avg_words = sum(len(l["spans"]) for l in lines) / max(1, len(lines))
                    if avg_words > 1:
                        for line in lines:
                            text_parts.append(" ".join(span["text"] for span in line["spans"]))
            return "\n".join(text_parts).strip()
        except Exception as e:
            print(f"⚠️ Table-exclusion failed: {e}")
            return self.extract_text_pdfreader()

    def extract_text_pdfreader(self):
        """Fallback using PDFReader (optional dependency)."""
        try:
            from pdfreader import PDFDocument, SimplePDFViewer
        except ImportError:
            print("⚠️ pdfreader not installed; skipping PDFReader fallback.")
            return ""
        try:
            with open(self.local_path, "rb") as f:
                doc = PDFDocument(f)
                viewer = SimplePDFViewer(f)
                jsonPage = {}
                cl = cleanText.cleanGenText()

                all_pages = [p for p in doc.pages()]
                for page_num in range(1, len(all_pages)):
                    viewer.navigate(page_num)
                    viewer.render()
                    text = "".join(viewer.canvas.strings)
                    clean, filtered = cl.textPreprocessing(text)
                    jsonPage[str(page_num)] = {
                        "normalText": [text],
                        "cleanText": [' '.join(filtered)],
                        "image": [viewer.canvas.images],
                        "form": [viewer.canvas.forms]
                    }
                return self._merge_text(jsonPage)
        except Exception as e:
            print(f"❌ PDFReader failed: {e}")
            return ""

    def _merge_text(self, jsonPDF):
        """Merge pages into one text string."""
        cl = cleanText.cleanGenText()
        pdfText = ""
        for page in jsonPDF:
            for text in jsonPDF[page]["normalText"]:
                t = cl.removeExtraSpaceBetweenWords(cl.removeTabWhiteSpaceNewLine(text))
                pdfText += t + "\n\n"
        return pdfText.strip()

    def extract_tables(self, pages="all"):
        """Extract tables with Tabula."""
        try:
            return tabula.read_pdf(self.local_path, pages=pages)
        except Exception:
            print("⚠️ No tables found.")
            return []