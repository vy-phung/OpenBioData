# Context swap test — 3 more samples (SAMN35361964/65/66)

Function called: `model.query_document_info()` (`model.py:1805`), same call signature `additional_pipeline.py:1151` uses in production: `niche_cases=None`, `saveLinkFolder=None`, `llm_api_function=model.call_llm_api`, `standardization_schema=None`. Disease/group-status fields come from Pass 2 (`model._extract_additional_fields()`), same as the SAMN35361955 test.

Citation verification method: for each disease-type field's explanation, every `'quoted excerpt'` inside its `[Sources: key (location, 'excerpt')]` tag is checked against the actual named source's own text block in the context (blocks delimited by the pipeline's own `The source - KEY: ... -----END OF THIS SOURCE KEY ----` markers). A quote absent from its claimed source block is flagged FABRICATED. Separately, each source block is checked for whether it contains the sample's own real submitter label (e.g. `ind10`, extracted from the BioSample XML's `<Id db_label="Sample name">` tag) — if the cited source never mentions the sample's own ID at all, the source cannot actually establish that the disease value belongs to *this* sample, even if the quote itself is real.

LLM provider: this run forced the Gemini fallback path by unsetting `ANTHROPIC_API_KEY` for the subprocess only (`env -u ANTHROPIC_API_KEY python test_context_swap_3samples.py`) — `model.call_llm_api()`'s Anthropic branch (`model.py:105-122`) is skipped whenever that key is absent, falling through to its Gemini branch (`model.py:124-146`), model `gemini-2.5-flash-lite`. Confirmed actually used (not just attempted) by the presence of `DEBUG: LLM Input tokens` in the run log for all 6 calls — that line only prints when `call_llm_api()` returns a non-None `model_instance`, which only happens on the Gemini return path. LLM output is non-deterministic.

## Summary across all 3 samples (6 runs)

| Accession | ind_label | Ground truth | bad_context disease field(s) | fixed_context disease field(s) |
|---|---|---|---|---|
| SAMN35361964 | ind10 | T2D-P- (control) | _(none)_ | _(none)_ |
| SAMN35361965 | ind11 | T2D-P+ | disease='periodontitis'; health_status='type 2 diabetes' | disease='type 2 diabetes' |
| SAMN35361966 | ind12 | T2D+P- | disease='periodontitis'; condition='type 2 Diabetes Mellitus' | condition='periodontitis negative, type 2 diabetes negative' |

---

# SAMN35361964 (submitter label `ind10`, ground truth: T2D-P- (control))

- bad_context: `test-data/PRJNA976261/SAMN35361964.docx` (315986 chars)
- fixed_context: `test-data/PRJNA976261/SAMN35361964_fixed.docx` (313928 chars)

---

## SAMN35361964 — bad_context (SAMN35361964.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The geographic location attribute is 'Italy: Ferrara' and latitude and longitude are '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara'); NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E'); NCBI_experiment (Country, 'Italy')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample was collected in 2018. [Sources: NCBI_biosample (collection_date attribute, '2018'); NCBI_experiment (SAMPLE_ATTRIBUTES tag, 'collection_date', '2018')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: The geographic location of the sample is 'Italy: Ferrara'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara'), NCBI_experiment (geo_loc_name attribute, 'Italy: Ferrara')]

**host**
- value: `Homo sapiens`
- explanation: The host of the sample is 'Homo sapiens'. [Sources: NCBI_biosample (host attribute, 'Homo sapiens'), NCBI_experiment (host attribute, 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: The isolation source of the sample is 'subgingival oral plaque'. [Sources: NCBI_biosample (isolation_source attribute, 'subgingival oral plaque'), NCBI_experiment (isolation_source attribute, 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: The collection date of the sample is '2018'. [Sources: NCBI_biosample (collection_date attribute, '2018'), NCBI_experiment (collection_date attribute, '2018')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: The latitude and longitude of the sample are '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E'), NCBI_experiment (lat_lon attribute, '44.7667642 N 11.827939 E')]

**organism**
- value: `human oral metagenome`
- explanation: The organism is 'human oral metagenome'. [Sources: NCBI_biosample (OrganismName, 'human oral metagenome')]

**id**
- value: `10`
- explanation: The sample identifier is '10'. [Sources: NCBI_biosample (id attribute, '10')]

**library_strategy**
- value: `WGS`
- explanation: The library strategy is 'WGS'. [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: The library source is 'METAGENOMIC'. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: The library selection is 'size fractionation'. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')]

**instrument_model**
- value: `NextSeq 500`
- explanation: The instrument model used for sequencing is 'NextSeq 500'. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500')]

**biosample_accession**
- value: `SAMN35361964`
- explanation: The BioSample accession number is 'SAMN35361964'. [Sources: NCBI_biosample (accession attribute, 'SAMN35361964'), NCBI_experiment (EXTERNAL_ID, 'SAMN35361964')]

**sra_accession**
- value: `SRR24828457`
- explanation: The SRA accession number is 'SRR24828457'. [Sources: NCBI_biosample (Id db='SRA' is_primary='0', 'SRS17893875'), NCBI_experiment (PRIMARY_ID, 'SRR24828457')]

**study_title**
- value: `Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions`
- explanation: The title of the study is 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'. [Sources: NCBI_bioproject (title, 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'), NCBI_experiment (STUDY_TITLE, 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions')]

**bioproject_accession**
- value: `PRJNA976261`
- explanation: The BioProject accession number is 'PRJNA976261'. [Sources: NCBI_bioproject (bioproject_id, 'PRJNA976261'), NCBI_experiment (EXTERNAL_ID, 'PRJNA976261'), NCBI_biosample (Link type='entrez' target='bioproject' label='PRJNA976261', '976261')]

### Categorical/disease-related field(s) — citation verification
_(none — no disease/condition/group/status-named field was produced this run)_

---

## SAMN35361964 — fixed_context (SAMN35361964_fixed.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The attributes include 'geo_loc_name' with the value 'Italy: Ferrara'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The 'collection_date' attribute is '2018'. [Sources: NCBI_biosample (collection_date attribute, '2018')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: The BioSample record for SAMN35361964 indicates the geographic location as 'Italy: Ferrara'. [Sources: NCBI_biosample (attribute 'geo_loc_name', 'Italy: Ferrara')]

**host**
- value: `Homo sapiens`
- explanation: The BioSample record for SAMN35361964 lists the host as 'Homo sapiens'. [Sources: NCBI_biosample (attribute 'host', 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: The BioSample record for SAMN35361964 specifies the isolation source as 'subgingival oral plaque'. [Sources: NCBI_biosample (attribute 'isolation_source', 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: The BioSample record for SAMN35361964 indicates the collection date as '2018'. [Sources: NCBI_biosample (attribute 'collection_date', '2018')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: The BioSample record for SAMN35361964 provides the latitude and longitude as '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (attribute 'lat_lon', '44.7667642 N 11.827939 E')]

**organism**
- value: `human oral metagenome`
- explanation: The BioSample record for SAMN35361964 lists the organism as 'human oral metagenome'. [Sources: NCBI_biosample (OrganismName, 'human oral metagenome')]

**library_strategy**
- value: `WGS`
- explanation: The SRA experiment data specifies the library strategy as 'WGS'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR: LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: The SRA experiment data specifies the library source as 'METAGENOMIC'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR: LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: The SRA experiment data specifies the library selection as 'size fractionation'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR: LIBRARY_SELECTION, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: The SRA experiment data indicates the sequencing platform as 'Illumina'. [Sources: NCBI_experiment (DESIGN_DESCRIPTION: 'Samples were sequenced with an Illumina NextSeq 500 sequencer')]

**instrument_model**
- value: `NextSeq 500`
- explanation: The SRA experiment data specifies the instrument model as 'NextSeq 500'. [Sources: NCBI_experiment (DESIGN_DESCRIPTION: 'Samples were sequenced with an Illumina NextSeq 500 sequencer')]

**study_title**
- value: `Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions`
- explanation: The title provided in the BioProject record is 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'. [Sources: NCBI_bioproject (title, 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions')]

### Categorical/disease-related field(s) — citation verification
_(none — no disease/condition/group/status-named field was produced this run)_


---

# SAMN35361965 (submitter label `ind11`, ground truth: T2D-P+)

- bad_context: `test-data/PRJNA976261/SAMN35361965.docx` (315986 chars)
- fixed_context: `test-data/PRJNA976261/SAMN35361965_fixed.docx` (313928 chars)

---

## SAMN35361965 — bad_context (SAMN35361965.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The geographic location is listed as 'Italy: Ferrara' in the NCBI_biosample record for SAMN35361965. [Sources: NCBI_biosample (geographic location, 'Italy: Ferrara')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample is described as a metagenome or environmental sample from a human oral metagenome. The study population consisted of adult individuals, implying they are modern. [Sources: NCBI_biosample (Title, 'Metagenome or environmental sample from human oral metagenome'); user_uploaded_file (Section 2.2.2, 'Twelve adult (≥40 years) Caucasian individuals were recruited')] [Conflict: none] [ID-match: false]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: The geographic location is 'Italy: Ferrara' in the BioSample record. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara')]

**host**
- value: `Homo sapiens`
- explanation: The host organism is listed as 'Homo sapiens' in the BioSample record. [Sources: NCBI_biosample (host attribute, 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: The isolation source is 'subgingival oral plaque' according to the BioSample record and the experiment description. [Sources: NCBI_biosample (isolation_source attribute, 'subgingival oral plaque'), NCBI_experiment (SAMPLE_ATTRIBUTE, 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: The collection date is listed as '2018' in the BioSample record and confirmed in the experiment details. [Sources: NCBI_biosample (collection_date attribute, '2018'), NCBI_experiment (SAMPLE_ATTRIBUTE, '2018')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: The latitude and longitude are '44.7667642 N 11.827939 E' as recorded in the BioSample and experiment data. [Sources: NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E'), NCBI_experiment (SAMPLE_ATTRIBUTE, '44.7667642 N 11.827939 E')]

**organism**
- value: `human oral metagenome`
- explanation: The organism is 'human oral metagenome' as specified in the BioSample record. [Sources: NCBI_biosample (Organism taxonomy_name attribute, 'human oral metagenome')]

**library_strategy**
- value: `WGS`
- explanation: The library strategy is 'WGS' according to the experiment record. [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: The library source is 'METAGENOMIC' as specified in the experiment record. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: The library selection method is 'size fractionation' as per the experiment record. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: The sequencing platform is 'Illumina' based on the experiment details. [Sources: NCBI_experiment (ILLUMINA, 'Illumina')]

**instrument_model**
- value: `NextSeq 500`
- explanation: The instrument model used for sequencing was 'NextSeq 500' as stated in the experiment record. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500')]

**sra_accession**
- value: `SRR24828456`
- explanation: The SRA accession for this sample is SRR24828456. [Sources: NCBI_experiment (PRIMARY_ID, 'SRR24828456')]

**biosample_accession**
- value: `SAMN35361965`
- explanation: The BioSample accession is SAMN35361965. [Sources: NCBI_biosample (accession attribute, 'SAMN35361965'), NCBI_experiment (EXTERNAL_ID, 'SAMN35361965')]

**sample_type**
- value: `Metagenome or environmental sample`
- explanation: The sample type is described as 'Metagenome or environmental sample' in the BioSample record. [Sources: NCBI_biosample (Title attribute, 'Metagenome or environmental sample from human oral metagenome')]

**disease**
- value: `periodontitis`
- explanation: [Candidates: Table 1=RELIABLE (12 unique identifier values, no repeats) (group, 'T2D-P+'); Table 4=UNRELIABLE (identifier value '#1' repeats 5 times -- not a unique per-row mapping (e.g. an index that restarts across sub-groups/sections)) (Group, 'T2D-P+'); Table 6=RELIABLE (12 unique identifier values, no repeats) (Type, 'T2D-P+'); Table 2=RELIABLE (6 unique identifier values, no repeats) (group, 't2d-p+'); Table 5=RELIABLE (31 unique identifier values, no repeats) (group, 'p+'); Table 7=RELIABLE (47 unique identifier values, no repeats) (group, 'p+'); Table 8=RELIABLE (33 unique identifier values, no repeats) (group, 'p+')] [Chosen: Table 2] The patient belongs to the 't2d-p+' group, indicating periodontitis. [Sources: user_uploaded_file (Table 2, group, 't2d-p+')] [ID-match: true]

**health_status**
- value: `type 2 diabetes`
- explanation: [Candidates: Table 1=RELIABLE (12 unique identifier values, no repeats) (group, 'T2D+P+'); Table 4=UNRELIABLE (identifier value '#1' repeats 5 times -- not a unique per-row mapping (e.g. an index that restarts across sub-groups/sections)) (Group, 'T2D+P+'); Table 6=RELIABLE (12 unique identifier values, no repeats) (Type, 'T2D+P-'); Table 2=RELIABLE (6 unique identifier values, no repeats) (group, 't2d+p-'); Table 5=RELIABLE (31 unique identifier values, no repeats) (group, 't2d+'); Table 7=RELIABLE (47 unique identifier values, no repeats) (group, 't2d+'); Table 8=RELIABLE (33 unique identifier values, no repeats) (group, 't2d+')] [Chosen: Table 2] The patient belongs to the 't2d+p-' group, indicating type 2 diabetes. [Sources: user_uploaded_file (Table 2, group, 't2d+p-')] [ID-match: true]

### Categorical/disease-related field(s) — citation verification

**disease** = `periodontitis`
- full explanation: [Candidates: Table 1=RELIABLE (12 unique identifier values, no repeats) (group, 'T2D-P+'); Table 4=UNRELIABLE (identifier value '#1' repeats 5 times -- not a unique per-row mapping (e.g. an index that restarts across sub-groups/sections)) (Group, 'T2D-P+'); Table 6=RELIABLE (12 unique identifier values, no repeats) (Type, 'T2D-P+'); Table 2=RELIABLE (6 unique identifier values, no repeats) (group, 't2d-p+'); Table 5=RELIABLE (31 unique identifier values, no repeats) (group, 'p+'); Table 7=RELIABLE (47 unique identifier values, no repeats) (group, 'p+'); Table 8=RELIABLE (33 unique identifier values, no repeats) (group, 'p+')] [Chosen: Table 2] The patient belongs to the 't2d-p+' group, indicating periodontitis. [Sources: user_uploaded_file (Table 2, group, 't2d-p+')] [ID-match: true]
- ind-label for this accession (from BioSample XML): `ind11`
  - NOTE: [Sources: ...] tag present but no (key, 'excerpt') pairs could be parsed from it: " user_uploaded_file (Table 2, group, 't2d-p+')"

**health_status** = `type 2 diabetes`
- full explanation: [Candidates: Table 1=RELIABLE (12 unique identifier values, no repeats) (group, 'T2D+P+'); Table 4=UNRELIABLE (identifier value '#1' repeats 5 times -- not a unique per-row mapping (e.g. an index that restarts across sub-groups/sections)) (Group, 'T2D+P+'); Table 6=RELIABLE (12 unique identifier values, no repeats) (Type, 'T2D+P-'); Table 2=RELIABLE (6 unique identifier values, no repeats) (group, 't2d+p-'); Table 5=RELIABLE (31 unique identifier values, no repeats) (group, 't2d+'); Table 7=RELIABLE (47 unique identifier values, no repeats) (group, 't2d+'); Table 8=RELIABLE (33 unique identifier values, no repeats) (group, 't2d+')] [Chosen: Table 2] The patient belongs to the 't2d+p-' group, indicating type 2 diabetes. [Sources: user_uploaded_file (Table 2, group, 't2d+p-')] [ID-match: true]
- ind-label for this accession (from BioSample XML): `ind11`
  - NOTE: [Sources: ...] tag present but no (key, 'excerpt') pairs could be parsed from it: " user_uploaded_file (Table 2, group, 't2d+p-')"

---

## SAMN35361965 — fixed_context (SAMN35361965_fixed.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The geo_loc_name attribute is 'Italy: Ferrara' and the subjects were recruited in Ferrara, Italy. [Sources: NCBI_biosample (geographic location, 'Italy: Ferrara'); https://doi.org/10.1016/j.archoralbio.2019.05.025 (Materials & methods, 'University of Ferrara, Italy'); https://doi.org/10.1111/omi.12418 (Materials and Methods, 'University of Ferrara, Italy')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample is associated with a study of living individuals. [Sources: NCBI_biosample (Title, 'Metagenome or environmental sample from human oral metagenome'); https://doi.org/10.1016/j.archoralbio.2019.05.025 (Materials & methods, 'Twelve adult (≥40 years) Caucasian individuals were recruited'); https://doi.org/10.1111/omi.12418 (Materials and Methods, '12 adults (≥40 years) with north-Italian ancestry were recruited')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**organism**
- value: `human oral metagenome`
- explanation: Organism name is 'human oral metagenome'. [Sources: NCBI_biosample (<Organism taxonomy_name='human oral metagenome'>)]

**collection_date**
- value: `2018`
- explanation: The collection date is 2018. [Sources: NCBI_biosample (collection_date attribute, '2018')]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: Geographic location is Italy: Ferrara. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara'), NCBI_experiment (geo_loc_name, 'Italy: Ferrara')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: Latitude and longitude are 44.7667642 N 11.827939 E. [Sources: NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E'), NCBI_experiment (lat_lon, '44.7667642 N 11.827939 E')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: The isolation source is subgingival oral plaque. [Sources: NCBI_biosample (isolation_source attribute, 'subgingival oral plaque'), NCBI_experiment (isolation_source, 'subgingival oral plaque')]

**host**
- value: `Homo sapiens`
- explanation: The host is Homo sapiens. [Sources: NCBI_biosample (host attribute, 'Homo sapiens'), NCBI_experiment (host, 'Homo sapiens')]

**library_strategy**
- value: `WGS`
- explanation: The library strategy is WGS. [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: The library source is METAGENOMIC. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: The library selection method is size fractionation. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')]

**instrument_model**
- value: `NextSeq 500`
- explanation: The instrument model used for sequencing is NextSeq 500. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500')]

**biosample_accession**
- value: `SAMN35361965`
- explanation: This sample's BioSample accession is SAMN35361965. [Sources: NCBI_biosample (accession, 'SAMN35361965'), NCBI_experiment (EXTERNAL_ID, 'SAMN35361965')]

**sra_accession**
- value: `SRR24828456`
- explanation: This sample's SRA accession is SRR24828456. [Sources: NCBI_biosample (Id db='SRA', 'SRS17893874'), NCBI_experiment (PRIMARY_ID, 'SRR24828456')]

**study_title**
- value: `Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions`
- explanation: The study title is 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'. [Sources: NCBI_bioproject (title, 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions')]

**disease**
- value: `type 2 diabetes`
- explanation: [Candidates: Table 1=RELIABLE (7 unique identifier values, no repeats), Table 4=UNRELIABLE (identifier value '#1' repeats 5 times -- not a unique per-row mapping (e.g. an index that restarts across sub-groups/sections)), Table 5=UNRELIABLE (identifier value '#2' repeats 5 times -- not a unique per-row mapping (e.g. an index that restarts across sub-groups/sections)), Table 6=RELIABLE (12 unique identifier values, no repeats), Table 2=RELIABLE (6 unique identifier values, no repeats), Table S1=RELIABLE (14 unique identifier values, no repeats), Table S2=RELIABLE (4 unique identifier values, no repeats), Table 7=RELIABLE (47 unique identifier values, no repeats), Table 8=RELIABLE (33 unique identifier values, no repeats)] [Chosen: Table 1] Based on the study title, this sample is part of a study investigating type 2 diabetes. [ID-match: false]

### Categorical/disease-related field(s) — citation verification

**disease** = `type 2 diabetes`
- full explanation: [Candidates: Table 1=RELIABLE (7 unique identifier values, no repeats), Table 4=UNRELIABLE (identifier value '#1' repeats 5 times -- not a unique per-row mapping (e.g. an index that restarts across sub-groups/sections)), Table 5=UNRELIABLE (identifier value '#2' repeats 5 times -- not a unique per-row mapping (e.g. an index that restarts across sub-groups/sections)), Table 6=RELIABLE (12 unique identifier values, no repeats), Table 2=RELIABLE (6 unique identifier values, no repeats), Table S1=RELIABLE (14 unique identifier values, no repeats), Table S2=RELIABLE (4 unique identifier values, no repeats), Table 7=RELIABLE (47 unique identifier values, no repeats), Table 8=RELIABLE (33 unique identifier values, no repeats)] [Chosen: Table 1] Based on the study title, this sample is part of a study investigating type 2 diabetes. [ID-match: false]
- ind-label for this accession (from BioSample XML): `ind11`
  - NOTE: no [Sources: ...] tag found in explanation at all


---

# SAMN35361966 (submitter label `ind12`, ground truth: T2D+P-)

- bad_context: `test-data/PRJNA976261/SAMN35361966.docx` (315986 chars)
- fixed_context: `test-data/PRJNA976261/SAMN35361966_fixed.docx` (313928 chars)

---

## SAMN35361966 — bad_context (SAMN35361966.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The bio_sample accession is SAMN35361966. [Sources: NCBI_biosample (accession, 'SAMN35361966')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The geo_loc_name attribute is 'Italy: Ferrara'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara'); NCBI_experiment (Country, 'Italy')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute geo_loc_name is 'Italy: Ferrara'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara')]

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute host is 'Homo sapiens'. [Sources: NCBI_biosample (host attribute, 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute isolation_source is 'subgingival oral plaque'. [Sources: NCBI_biosample (isolation_source attribute, 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute collection_date is '2018'. [Sources: NCBI_biosample (collection_date attribute, '2018')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute lat_lon is '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E')]

**organism**
- value: `human oral metagenome`
- explanation: BioSample organism taxonomy_name is 'human oral metagenome'. [Sources: NCBI_biosample (Organism taxonomy_name, 'human oral metagenome')]

**library_strategy**
- value: `WGS`
- explanation: SRA experiment LIBRARY_DESCRIPTOR LIBRARY_STRATEGY is 'WGS'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: SRA experiment LIBRARY_DESCRIPTOR LIBRARY_SOURCE is 'METAGENOMIC'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: SRA experiment LIBRARY_DESCRIPTOR LIBRARY_SELECTION is 'size fractionation'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR LIBRARY_SELECTION, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: SRA experiment DESIGN_DESCRIPTION uses Illumina sequencing. [Sources: NCBI_experiment (DESIGN_DESCRIPTION, 'Illumina NextSeq 500 sequencer')]

**instrument_model**
- value: `NextSeq 500`
- explanation: SRA experiment LIBRARY_DESCRIPTOR INSTRUMENT_MODEL is 'NextSeq 500'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR INSTRUMENT_MODEL, 'NextSeq 500')]

**sra_accession**
- value: `SRR24828455`
- explanation: NCBI Biosample accession for this sample is SRS17893876, which corresponds to SRA accession SRR24828455. [Sources: NCBI_biosample (Id db="SRA", 'SRS17893876'), NCBI_experiment (RUN PRIMARY_ID, 'SRR24828455')]

**biosample_accession**
- value: `SAMN35361966`
- explanation: The BioSample accession is SAMN35361966. [Sources: NCBI_biosample (accession, 'SAMN35361966')]

**study_title**
- value: `Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions`
- explanation: The title of the BioProject is 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'. [Sources: NCBI_bioproject (title, 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions')]

**disease**
- value: `periodontitis`
- explanation: The study title and abstract indicate that periodontitis is a condition studied. [Sources: NCBI_bioproject (title, 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'), https://doi.org/10.1111/omi.12418 (Abstract, 'periodontitis')] [ID-match: false]

**condition**
- value: `type 2 Diabetes Mellitus`
- explanation: The study title and abstract indicate that type 2 Diabetes Mellitus is a condition studied. [Sources: NCBI_bioproject (title, 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'), https://doi.org/10.1111/omi.12418 (Abstract, 'type 2 diabetes')] [ID-match: false]

### Categorical/disease-related field(s) — citation verification

**disease** = `periodontitis`
- full explanation: The study title and abstract indicate that periodontitis is a condition studied. [Sources: NCBI_bioproject (title, 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'), https://doi.org/10.1111/omi.12418 (Abstract, 'periodontitis')] [ID-match: false]
- ind-label for this accession (from BioSample XML): `ind12`
  - source `NCBI_bioproject` @ "title": 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'
    -> quote verified present in that source block
    -> sample's own label found in this same source block? NO -- this source block never mentions the sample's own ind-label at all
  - source `, https://doi.org/10.1111/omi.12418` @ "Abstract": 'periodontitis'
    -> quote verified present in that source block
    -> sample's own label found in this same source block? NO -- this source block never mentions the sample's own ind-label at all

**condition** = `type 2 Diabetes Mellitus`
- full explanation: The study title and abstract indicate that type 2 Diabetes Mellitus is a condition studied. [Sources: NCBI_bioproject (title, 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'), https://doi.org/10.1111/omi.12418 (Abstract, 'type 2 diabetes')] [ID-match: false]
- ind-label for this accession (from BioSample XML): `ind12`
  - source `NCBI_bioproject` @ "title": 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'
    -> quote verified present in that source block
    -> sample's own label found in this same source block? NO -- this source block never mentions the sample's own ind-label at all
  - source `, https://doi.org/10.1111/omi.12418` @ "Abstract": 'type 2 diabetes'
    -> quote verified present in that source block
    -> sample's own label found in this same source block? NO -- this source block never mentions the sample's own ind-label at all

---

## SAMN35361966 — fixed_context (SAMN35361966_fixed.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The attribute geo_loc_name is "Italy: Ferrara" from the BioSample record. [Sources: NCBI_biosample (Attributes, 'geographic location: Italy: Ferrara'); https://doi.org/10.1111/omi.12418 (Abstract, 'recruited in the metropolitan area of Ferrara (Italy)')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample is described as "Metagenome or environmental sample from human oral metagenome" and the study population description refers to healthy subjects. [Sources: NCBI_biosample (Description, 'Metagenome or environmental sample from human oral metagenome'); user_uploaded_file (Section 2.1, 'healthy subjects')] [Conflict: none] [ID-match: false]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute geo_loc_name is 'Italy: Ferrara'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara')]

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute host is 'Homo sapiens'. [Sources: NCBI_biosample (host attribute, 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute isolation_source is 'subgingival oral plaque'. [Sources: NCBI_biosample (isolation_source attribute, 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute collection_date is '2018'. [Sources: NCBI_biosample (collection_date attribute, '2018')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute lat_lon is '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E')]

**organism**
- value: `human oral metagenome`
- explanation: BioSample organism taxonomy name is 'human oral metagenome'. [Sources: NCBI_biosample (Organism taxonomy_name, 'human oral metagenome')]

**library_strategy**
- value: `WGS`
- explanation: Experiment library_strategy is 'WGS'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR: LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: Experiment library_source is 'METAGENOMIC'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR: LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: Experiment library_selection is 'size fractionation'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR: LIBRARY_SELECTION, 'size fractionation')]

**instrument_model**
- value: `NextSeq 500`
- explanation: Experiment instrument_model is 'NextSeq 500'. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR: PLATFORM: ILLUMINA: INSTRUMENT_MODEL, 'NextSeq 500')]

**biosample_accession**
- value: `SAMN35361966`
- explanation: The BioSample accession for this sample is SAMN35361966. [Sources: NCBI_biosample (accession='SAMN35361966')]

**sra_accession**
- value: `SRR24828455`
- explanation: The SRA accession for this sample is SRR24828455. [Sources: NCBI_biosample (Id db='SRA', 'SRS17893876')]

**sex**
- value: `female`
- explanation: Patient #1 in the T2D+P- group is described as male, Patient #2 as male, and Patient #3 as male in Table 4. However, the sample 'ind12' is associated with Patient #12, who is described as female in Table 4. [Candidates: Table 4=RELIABLE (12 unique identifier values, no repeats); Table 1=RELIABLE (7 unique identifier values, no repeats); Table 6=RELIABLE (12 unique identifier values, no repeats); Table 2=RELIABLE (6 unique identifier values, no repeats); Table 5=RELIABLE (31 unique identifier values, no repeats); Table 7=RELIABLE (47 unique identifier values, no repeats); Table 8=RELIABLE (33 unique identifier values, no repeats)] [Chosen: Table 4] Based on Table 4, Patient #12 (associated with ind12) is female. [Sources: user_uploaded_file (Table 4, Row: Group=T2D− P−, Patient=#2, Gender=Female)] [ID-match: true]

**age**
- value: `54`
- explanation: Table 4 indicates that Patient #12 (associated with ind12) is 54 years old. [Candidates: Table 4=RELIABLE (12 unique identifier values, no repeats); Table 1=RELIABLE (7 unique identifier values, no repeats); Table 6=RELIABLE (12 unique identifier values, no repeats); Table 2=RELIABLE (6 unique identifier values, no repeats); Table 5=RELIABLE (31 unique identifier values, no repeats); Table 7=RELIABLE (47 unique identifier values, no repeats); Table 8=RELIABLE (33 unique identifier values, no repeats)] [Chosen: Table 4] [Sources: user_uploaded_file (Table 4, Row: Group=T2D− P−, Patient=#2, Age (years)=54)] [ID-match: true]

**condition**
- value: `periodontitis negative, type 2 diabetes negative`
- explanation: [Candidates: Table 1=RELIABLE (7 unique identifier values, no repeats); Table 6=RELIABLE (12 unique identifier values, no repeats); Table 2=RELIABLE (6 unique identifier values, no repeats); Table 5=RELIABLE (31 unique identifier values, no repeats); Table 7=RELIABLE (47 unique identifier values, no repeats); Table 8=RELIABLE (33 unique identifier values, no repeats)] [Chosen: Table 6] Patient ID 10 (T2D− P−) corresponds to this sample. [Sources: user_uploaded_file (Table 6, Row: ID=10.0, Type=T2D− P−)] [ID-match: true]

### Categorical/disease-related field(s) — citation verification

**condition** = `periodontitis negative, type 2 diabetes negative`
- full explanation: [Candidates: Table 1=RELIABLE (7 unique identifier values, no repeats); Table 6=RELIABLE (12 unique identifier values, no repeats); Table 2=RELIABLE (6 unique identifier values, no repeats); Table 5=RELIABLE (31 unique identifier values, no repeats); Table 7=RELIABLE (47 unique identifier values, no repeats); Table 8=RELIABLE (33 unique identifier values, no repeats)] [Chosen: Table 6] Patient ID 10 (T2D− P−) corresponds to this sample. [Sources: user_uploaded_file (Table 6, Row: ID=10.0, Type=T2D− P−)] [ID-match: true]
- ind-label for this accession (from BioSample XML): `ind12`
  - NOTE: [Sources: ...] tag present but no (key, 'excerpt') pairs could be parsed from it: ' user_uploaded_file (Table 6, Row: ID=10.0, Type=T2D− P−)'
