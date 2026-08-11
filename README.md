# OpenBioData

A metadata recovery tool that traces missing BioSample and SRA metadata on NCBI (disease status, isolation source, location, host, etc.) back to the source publication where the information actually appears. Give it an accession (BioProject, BioSample, SRR, GEO, or GenBank) or a paper link, and it finds the associated NCBI records and publications, including supplementary tables, cross-checks and expands the metadata, and returns each recovered value with a **confidence score and direct citation (PMID + table/section)**, so you can verify where it came from in seconds instead of digging manually.

<img width="800" height="450" alt="OpenBioData demo" src="https://github.com/user-attachments/assets/5218ab97-b47e-4074-a5ba-a582ad1266d8" />

---

## What it does
- Traces accessions back to source publications and supplementary materials
- Extracts and expands metadata that may be missing from the record itself
- Gives every extracted value a confidence score
- Cites its source (PMID + table/section) so you can check it yourself

---

## Try it hosted

https://app.openbiodata.it.com/, up to 10 samples without an account, 30 if you sign in.

Why the cap: it runs on my own infrastructure and calls the Claude API per sample, so this protects against runaway cost while I validate accuracy at scale. A 500-sample BioProject counts each sample toward your limit, not the project as one unit. Signing in also saves your run history so a reload doesn't lose your progress.

---

## Run it yourself

Requirements: Python 3.x, and an LLM API key: Anthropic, Gemini, etc. *(No Google/GCP credentials needed for local use - those only power hosted-app features like sign-in.)*

```bash
git clone https://github.com/vy-phung/OpenBioData
cd OpenBioData
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY and/or GOOGLE_API_KEY
echo 'ANTHROPIC_API_KEY=<input-your-anthropic-key>' > .env
echo 'NEW_GEMINI_API=<input-your-gemini-key>' > .env
```

**Option 1: same UI as the hosted version:**
```bash
python api.py
```
Then open http://localhost:8000, paste an accession, submit, watch the result stream in.

**Option 2: command line, one accession at a time:**
```bash
python openbiodata.py <accession>
```

Example:

```
$ python openbiodata.py SAMN20283122
```
Output:
```
> Loading backend…
> Parsing accession input…
> Resolving accession via NCBI…
> Processing 1 sample(s)…
> [1/1] Fetching NCBI data for SAMN20283122…
> [1/1] Searching literature for SAMN20283122…
> ⚠ Cannot access paper(s) — they may require a subscription or block server access. Upload the PDF(s) directly to improve accuracy.
> [1/1] Data gathered for SAMN20283122, queued for batched LLM inference…
> Running batched LLM inference for 1 sample(s) (batch 1/1)…
> [1/1] ✓ SAMN20283122 done (42.153 seconds)
✅ Extracted metadata for 1 sample(s)

==========================================================================================
SAMN20283122   BioProject: PRJNA514286   SRA: SRS9522164   GenBank: NZ_DBJOSC000000000
==========================================================================================
Confidence: 30 (🔴 Low) — weakest field: instrument_model (30)

• organism: Organism name is Listeria monocytogenes.
• collection_date: NCBI BioSample attribute collection_date is '2012-12'.
• geo_loc_name: NCBI BioSample attribute geo_loc_name is 'USA:NY'.
• host: NCBI BioSample attribute host is 'Homo sapiens'.
• project_name: NCBI BioSample attribute project_name is 'GenomeTrakr; LFFM-FY5'.
• sequenced_by: NCBI BioSample attribute sequenced_by is 'New York State Department of Health'.
• purpose_of_sampling: NCBI BioSample attribute purpose_of_sampling is 'baseline surveillance/monitoring'.
• library_strategy: NCBI experiment attribute library_strategy is 'WGS'.
• library_source: NCBI experiment attribute library_source is 'GENOMIC'.
• library_selection: NCBI experiment attribute library_selection is 'other'.
• instrument_model: NCBI experiment attribute instrument_model is 'PromethION'.
• collected_by: NCBI BioSample attribute collected_by is 'New York State Department of Health'.
• strain: NCBI BioSample attribute strain is 'PNUSAL010798'.
• ifsac_category: NCBI BioSample attribute IFSAC+ Category is 'clinical/research| human'.
• biosample_accession: NCBI BioSample accession is 'SAMN20283122'.
• sra_accession: NCBI BioSample links SRA accession is 'SRS9522164'.
• bioproject_accession: NCBI BioSample links BioProject accession is 'PRJNA514286'.
• dna_extraction_kit: The paper mentions 'DNA was extracted using the Qiagen DNeasy 96 PowerSoil Pro QIAcube HT Kit'.

------------------------------------------------------------------------------------------
Each field carries its own citation back to the exact source — e.g.:
• dna_extraction_kit → https://doi.org/10.1186/s13073-024-01379-4 (Methods, "Qiagen DNeasy 96 PowerSoil Pro QIAcube HT Kit")
• host → NCBI_biosample (host attribute, "Homo sapiens")

All linked sources:
https://doi.org/10.1093/ismeco/ycag093
https://doi.org/10.1186/s13073-024-01379-4
NCBI_bioproject · NCBI_biosample · NCBI_experiment

Time cost: 42.153 seconds
==========================================================================================
```

A confidence score is still low (see [Known issues](#known-issues)).

Output (DOCX evidence files) saves to a temporary system folder by default, treat it as disposable, not a delivered file, unless you're self-hosting and know where your temp directory lives.

This is early-stage: if you hit a blocker getting it running locally, open an issue rather than assuming it's you.

---

## What it outputs

One row per accession:
- BioSample ID, BioProject, SRA accession
- Each requested metadata field
- Confidence score (0–100) and tier (High / Medium / Low)
- One-line explanation of where the value came from
- Source citation (PMID + table or section)
- Flags where the NCBI record and the paper disagree

Excel export available.

---

## How it differs from other metadata curation tools

fetchngs, pysradb, and ffq pull metadata and raw data that's already recorded in SRA/ENA/GEO's own structured database. None of them go beyond the database record itself to check the paper a sample was published in (or later papers that cite/reuse it) for richer or more accurate metadata. Authors sometimes mistype metadata, or submit only the bare minimum, while the real, complete picture is sitting in the paper's text, tables, or supplementary materials.

A reasonable workflow: use fetchngs/pysradb/ffq to get your accession list, then run OpenBioData on it if you want that metadata recovered, verified, and cross-checked against the literature.

---

## Known issues

- Command line option hasn't kept up with the User Interface option
- Confidence score has not fully works at the confidence score rule and still gives the low confidence score for every input
- Accession can occasionally misattribute when trying to access NCBI records
- Accession-type detection is implemented three separate times.
- Getting all metadata mentioned in the sources still have not extracted all metadata.

None of these block real use. They're documented so you know what you're looking at. See `ARCHITECTURE.md` for the full technical picture and `CONTRIBUTING.md` for open issues, several tagged `good first issue`.

---

## About the code

I wrote the core pipeline myself, with real help from Claude Code, especially the LLM extraction layer and the CLI. 

---

## Data & Privacy

Hosted version: see [PRIVACY.md](./PRIVACY.md). Self-hosted: nothing is sent to me, but the tool still sends paper/accession text to Anthropic (and Google, if you set up the Gemini fallback) to run extraction — same as any tool built on their APIs.

---

## License

MIT — see [LICENSE](./LICENSE).

---

## Contact

Vy Phung
vyphung1901@gmail.com
