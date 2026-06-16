import os
import requests
from spire.doc import Document
from spire.doc.common import *
from spire.xls import Workbook, FileFormat

class WordDocFast:
    _cache = {}  # Cache Document objects by file path/URL

    def __init__(self, wordDoc, saveFolder):
        self.wordDoc = wordDoc
        self.saveFolder = saveFolder or "."
        self.doc = self._load_document()

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/vnd.openxmlformats-officedocument.wordprocessingml.document,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    }

    def _load_document(self):
        if self.wordDoc in WordDocFast._cache:
            return WordDocFast._cache[self.wordDoc]

        local_path = self.wordDoc
        if self.wordDoc.startswith("http"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.wordDoc)
            qs = parse_qs(parsed.query)
            name = qs.get('file', [os.path.basename(parsed.path)])[0]
            if not name:
                name = 'download.docx'
            local_path = os.path.join(self.saveFolder, name)
            if not os.path.exists(local_path):
                r = requests.get(self.wordDoc, headers=self._HEADERS, timeout=30, allow_redirects=True)
                r.raise_for_status()
                ct = r.headers.get('Content-Type', '')
                if 'text/html' in ct:
                    raise ValueError(
                        f"Publisher returned HTML instead of Word doc (blocked or requires login): {self.wordDoc}"
                    )
                with open(local_path, "wb") as f:
                    f.write(r.content)

        doc = Document()
        doc.LoadFromFile(local_path)
        WordDocFast._cache[self.wordDoc] = doc
        return doc

    def extractText(self):
        """Extract full text (faster than page-by-page parsing)."""
        try:
          return self.doc.GetText()
        except:
          try:
            return self.extractTextBySections()
          except:
            print("extract word doc text failed")
            return ''
    def extractTextBySections(self):
        """Stream text section-by-section (can be faster for large docs)."""
        all_text = []
        for s in range(self.doc.Sections.Count):
            section = self.doc.Sections.get_Item(s)
            for p in range(section.Paragraphs.Count):
                text = section.Paragraphs.get_Item(p).Text.strip()
                if text:
                    all_text.append(text)
        return "\n".join(all_text)

    def extractTablesAsList(self):
        """Extract tables as list-of-lists (faster)."""
        tables = []
        for s in range(self.doc.Sections.Count):
            section = self.doc.Sections.get_Item(s)
            for t in range(section.Tables.Count):
                table = section.Tables.get_Item(t)
                table_data = []
                for r in range(table.Rows.Count):
                    row_data = []
                    for c in range(table.Rows.get_Item(r).Cells.Count):
                        cell = table.Rows.get_Item(r).Cells.get_Item(c)
                        cell_text = " ".join(
                            cell.Paragraphs.get_Item(p).Text.strip()
                            for p in range(cell.Paragraphs.Count)
                        ).strip()
                        row_data.append(cell_text)
                    table_data.append(row_data)
                tables.append(table_data)
        return tables

    def extractTablesAsExcel(self):
        """Export tables to Excel."""
        wb = Workbook()
        wb.Worksheets.Clear()
        for s in range(self.doc.Sections.Count):
            section = self.doc.Sections.get_Item(s)
            for t in range(section.Tables.Count):
                table = section.Tables.get_Item(t)
                ws = wb.Worksheets.Add(f"Table_{s+1}_{t+1}")
                for r in range(table.Rows.Count):
                    row = table.Rows.get_Item(r)
                    for c in range(row.Cells.Count):
                        cell = row.Cells.get_Item(c)
                        cell_text = " ".join(
                            cell.Paragraphs.get_Item(p).Text
                            for p in range(cell.Paragraphs.Count)
                        ).strip()
                        ws.SetCellValue(r + 1, c + 1, cell_text)
        name = os.path.basename(self.wordDoc) + ".xlsx"
        out_path = os.path.join(self.saveFolder, name)
        wb.SaveToFile(out_path, FileFormat.Version2016)
