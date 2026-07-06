# Context swap test — 3 more samples (SAMN35361964/65/66)

Function called: `model.query_document_info()` (`model.py:1805`), same call signature `additional_pipeline.py:1151` uses in production: `niche_cases=None`, `saveLinkFolder=None`, `llm_api_function=model.call_llm_api`, `standardization_schema=None`. Disease/group-status fields come from Pass 2 (`model._extract_additional_fields()`), same as the SAMN35361955 test.

Citation verification method: for each disease-type field's explanation, every `'quoted excerpt'` inside its `[Sources: key (location, 'excerpt')]` tag is checked against the actual named source's own text block in the context (blocks delimited by the pipeline's own `The source - KEY: ... -----END OF THIS SOURCE KEY ----` markers). A quote absent from its claimed source block is flagged FABRICATED. Separately, each source block is checked for whether it contains the sample's own real submitter label (e.g. `ind10`, extracted from the BioSample XML's `<Id db_label="Sample name">` tag) — if the cited source never mentions the sample's own ID at all, the source cannot actually establish that the disease value belongs to *this* sample, even if the quote itself is real.

LLM provider: `model.call_llm_api()` tries Anthropic first (`ANTHROPIC_API_KEY` is set in this environment) — Claude Haiku (`claude-haiku-4-5-20251001`), not Gemini. LLM output is non-deterministic.

## Summary across all 3 samples (6 runs)

| Accession | ind_label | Ground truth | bad_context disease field(s) | fixed_context disease field(s) |
|---|---|---|---|---|
| SAMN35361964 | ind10 | T2D-P- (control) | _(none)_ | disease='type 2 diabetes mellitus and periodontitis' |
| SAMN35361965 | ind11 | T2D-P+ | subject_group='T2D-P+' | disease='type 2 diabetes and periodontitis' |
| SAMN35361966 | ind12 | T2D+P- | disease='Type 2 Diabetes Mellitus and Moderate-Severe Periodontitis' | disease='none (healthy control)' |

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
- explanation: The geographic location attribute geo_loc_name in the NCBI BioSample record for SAMN35361964 specifies 'Italy: Ferrara', and the collection_date attribute lists '2018', indicating a modern, living individual sample. [Sources: NCBI_biosample (Attributes section, 'geo_loc_name' attribute value 'Italy: Ferrara'; 'collection_date' attribute value '2018'); NCBI_experiment (SAMPLE_ATTRIBUTE section, TAG 'geo_loc_name' VALUE 'Italy: Ferrara'; TAG 'collection_date' VALUE '2018'); user_uploaded_file (Materials & methods section, 'metropolitan area of Ferrara, Italy')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample SAMN35361964 (labeled 'ind10') was collected from a living human subject in 2018 as part of a case-control study of subgingival microbiome composition in patients with different periodontal and diabetic status, confirming modern/contemporary origin. [Sources: NCBI_biosample (sample name 'ind10', collection_date '2018'); NCBI_experiment (SAMPLE_DESCRIPTOR section, PRIMARY_ID 'SRS17893875' EXTERNAL_ID 'SAMN35361964'; SAMPLE_ATTRIBUTE TAG 'collection_date' VALUE '2018'); user_uploaded_file (Materials & methods section, 'Twelve adults (≥40 years) with north-Italian ancestry were recruited'; Methods section, 'Received in revised form 22 May 2019; Accepted 23 May 2019')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**organism**
- value: `human oral metagenome`
- explanation: BioSample record explicitly identifies the organism as 'human oral metagenome' with taxonomy ID 447426. [Sources: NCBI_biosample (Organism field, 'human oral metagenome')]

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute 'host' is 'Homo sapiens'. [Sources: NCBI_biosample (host attribute, 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute 'isolation_source' specifies 'subgingival oral plaque'. [Sources: NCBI_biosample (isolation_source attribute, 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute 'collection_date' is '2018'. [Sources: NCBI_biosample (collection_date attribute, '2018')]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute 'geo_loc_name' is 'Italy: Ferrara'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute 'lat_lon' provides coordinates '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E')]

**sample_name**
- value: `ind10`
- explanation: BioSample record shows sample identifier 'ind10' in the Ids section (db_label='Sample name'). [Sources: NCBI_biosample (Ids section, 'ind10')]

**sra_accession**
- value: `SRS17893875`
- explanation: BioSample record lists SRA accession 'SRS17893875' in the Ids section. [Sources: NCBI_biosample (Ids section, 'SRS17893875')]

**biosample_accession**
- value: `SAMN35361964`
- explanation: This is the primary BioSample accession for the record being extracted. [Sources: NCBI_biosample (accession attribute, 'SAMN35361964')]

**library_strategy**
- value: `WGS`
- explanation: SRA experiment record specifies LIBRARY_STRATEGY as 'WGS' (whole genome shotgun). [Sources: NCBI_experiment (LIBRARY_STRATEGY field, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: SRA experiment record specifies LIBRARY_SOURCE as 'METAGENOMIC'. [Sources: NCBI_experiment (LIBRARY_SOURCE field, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: SRA experiment record specifies LIBRARY_SELECTION as 'size fractionation'. [Sources: NCBI_experiment (LIBRARY_SELECTION field, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: SRA experiment record indicates 'ILLUMINA' as the sequencing platform. [Sources: NCBI_experiment (PLATFORM section, 'ILLUMINA')]

**instrument_model**
- value: `NextSeq 500`
- explanation: SRA experiment record specifies INSTRUMENT_MODEL as 'NextSeq 500'; confirmed in user-uploaded paper methods. [Sources: NCBI_experiment (INSTRUMENT_MODEL field, 'NextSeq 500')]

**dna_extraction_kit**
- value: `Maxwell RSC DNA Blood Kit`
- explanation: Methods section of user-uploaded paper (Farina et al. 2019) states 'DNA was isolated from frozen samples by using the Maxwell RSC DNA Blood Kit (Promega)'. [Sources: user_uploaded_file (Methods section 2.4.1, 'Maxwell RSC DNA Blood Kit')]

**subject_id**
- value: `10`
- explanation: BioSample attribute 'id' is '10', corresponding to sample identifier 'ind10'. Per Table 1 from Farina et al. (2019) which is RELIABLE (7 unique IDs, no repeats), subject #10 belongs to group T2D−P− (control group without type 2 diabetes or periodontitis). [Sources: NCBI_biosample (id attribute, '10'); user_uploaded_file (Table 1, row with Patient=ind10, Group=T2D−P−)]

**body_site**
- value: `periodontal pocket`
- explanation: User-uploaded paper methods section (Farina et al. 2019, Section 2.3) specifies that subgingival plaque was collected from sites either with or without bleeding on probing; for T2D−P− subjects, sampling was at 'sites randomly selected among those negative to bleeding on probing', representing the healthy periodontal pocket. [Sources: user_uploaded_file (Methods 2.3 and 4, description of sampling sites)]

**study_name**
- value: `Farina et al. 2019`
- explanation: The primary study generating this sample's data is 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions' published by Farina et al. in Archives of Oral Biology (2019). [Sources: user_uploaded_file (title page of Farina et al. 2019 paper and NCBI_bioproject description)]

### Categorical/disease-related field(s) — citation verification
_(none — no disease/condition/group/status-named field was produced this run)_

---

## SAMN35361964 — fixed_context (SAMN35361964_fixed.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The geo_loc_name attribute in the NCBI BioSample record indicates the geographic location as "Italy: Ferrara", confirming Italy as the country. [Sources: NCBI_biosample (Attributes section, geo_loc_name='Italy: Ferrara'); NCBI_experiment (SAMPLE_ATTRIBUTES section, 'geo_loc_name VALUE: Italy: Ferrara'); user_uploaded_file (Materials & methods section, 'Research Centre for the Study of Periodontal and Peri-Implant Diseases, University of Ferrara, Italy')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample is modern (living individual), derived from a subgingival plaque sample collected from a study subject enrolled in 2018 with a recorded collection_date attribute of 2018, and represents contemporary clinical material from an ongoing patient study rather than archaeological or prehistoric material. [Sources: NCBI_biosample (Attributes section, collection_date='2018'); NCBI_experiment (SAMPLE_ATTRIBUTES section, 'collection_date VALUE: 2018'); user_uploaded_file (Methods section, 'subgingival plaque samples were collected at four teeth' from living subjects recruited at the Research Centre)] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute host is 'Homo sapiens'. [Sources: NCBI_biosample (host attribute, 'Homo sapiens')] [ID-match: true]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute isolation_source is 'subgingival oral plaque'. [Sources: NCBI_biosample (isolation_source attribute, 'subgingival oral plaque')] [ID-match: true]

**collection_date**
- value: `2018`
- explanation: BioSample attribute collection_date is '2018'. [Sources: NCBI_biosample (collection_date attribute, '2018')] [ID-match: true]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute geo_loc_name is 'Italy: Ferrara'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara')] [ID-match: true]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute lat_lon is '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E')] [ID-match: true]

**organism**
- value: `human oral metagenome`
- explanation: BioSample taxonomy name is 'human oral metagenome'. [Sources: NCBI_biosample (Organism taxonomy_name, 'human oral metagenome')] [ID-match: true]

**library_strategy**
- value: `WGS`
- explanation: SRA experiment XML specifies LIBRARY_STRATEGY is 'WGS' (whole genome shotgun sequencing). [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGS')] [ID-match: true]

**library_source**
- value: `METAGENOMIC`
- explanation: SRA experiment XML specifies LIBRARY_SOURCE is 'METAGENOMIC'. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')] [ID-match: true]

**library_selection**
- value: `size fractionation`
- explanation: SRA experiment XML specifies LIBRARY_SELECTION is 'size fractionation'. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')] [ID-match: true]

**platform**
- value: `Illumina`
- explanation: SRA experiment XML specifies PLATFORM as 'ILLUMINA'. [Sources: NCBI_experiment (PLATFORM, 'ILLUMINA')] [ID-match: true]

**instrument_model**
- value: `NextSeq 500`
- explanation: SRA experiment XML specifies INSTRUMENT_MODEL is 'NextSeq 500'. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500')] [ID-match: true]

**sample_name**
- value: `ind10`
- explanation: BioSample attribute sample name (Id db_label) is 'ind10'. [Sources: NCBI_biosample (Id db_label='Sample name', 'ind10')] [ID-match: true]

**disease**
- value: `type 2 diabetes mellitus and periodontitis`
- explanation: [Candidates: Table 1=reliable (6 unique identifier values)]; [Chosen: Table 1] Sample ind10 (ID=10) in Table 1 shows T2D− P− group classification; this sample belongs to the control group without either type 2 diabetes or periodontitis. The study describes disease status for each group: T2D− P− subjects are healthy controls without disease. [Sources: user_uploaded_file (Table 1, row ID=10.0, Group=T2D− P−)] [ID-match: true]

### Categorical/disease-related field(s) — citation verification

**disease** = `type 2 diabetes mellitus and periodontitis`
- full explanation: [Candidates: Table 1=reliable (6 unique identifier values)]; [Chosen: Table 1] Sample ind10 (ID=10) in Table 1 shows T2D− P− group classification; this sample belongs to the control group without either type 2 diabetes or periodontitis. The study describes disease status for each group: T2D− P− subjects are healthy controls without disease. [Sources: user_uploaded_file (Table 1, row ID=10.0, Group=T2D− P−)] [ID-match: true]
- ind-label for this accession (from BioSample XML): `ind10`
  - NOTE: [Sources: ...] tag present but no (key, 'excerpt') pairs could be parsed from it: ' user_uploaded_file (Table 1, row ID=10.0, Group=T2D− P−)'


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
- explanation: The geographic location is specified in the NCBI_biosample attribute geo_loc_name as 'Italy: Ferrara', and sample collection occurred at the Research Centre for the Study of Periodontal and Peri-Implant Diseases, University of Ferrara, Italy, with collection date listed as 2018. [Sources: NCBI_biosample (Attributes section, 'geo_loc_name: Italy: Ferrara'); NCBI_experiment (SUBMISSION section, 'Country: Italy'); user_uploaded_file (Materials & methods section, 'metropolitan area of Ferrara, Italy')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample source is modern (living individual), as confirmed by the isolation_source attribute 'subgingival oral plaque' collected from a living human subject (host: Homo sapiens) in 2018, with no archaeological or prehistoric context indicated. [Sources: NCBI_biosample (Attributes section, 'isolation_source: subgingival oral plaque'; 'collection_date: 2018'); user_uploaded_file (Materials & methods section, 'Twelve adult (≥40 years) with north-Italian ancestry were recruited')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute host is 'Homo sapiens'. [Sources: NCBI_biosample (host attribute, 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute isolation_source is 'subgingival oral plaque'. [Sources: NCBI_biosample (isolation_source attribute, 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute collection_date is '2018'. [Sources: NCBI_biosample (collection_date attribute, '2018')]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute geo_loc_name is 'Italy: Ferrara'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute lat_lon is '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E')]

**organism**
- value: `human oral metagenome`
- explanation: BioSample organism taxonomy name is 'human oral metagenome'. [Sources: NCBI_biosample (Organism OrganismName, 'human oral metagenome')]

**sample_type**
- value: `metagenome`
- explanation: BioSample title describes 'Metagenome or environmental sample from human oral metagenome'. [Sources: NCBI_biosample (Description Title, 'Metagenome or environmental sample')]

**body_site**
- value: `subgingival sulcus`
- explanation: Methods section indicates subgingival plaque samples collected at sites representing periodontal condition; supplementary user_uploaded_file describes sampling sites as including teeth locations and probing depth measurements consistent with periodontal pockets/sulci. [Sources: user_uploaded_file (Materials & methods section, 'subgingival plaque samples collected at 4 teeth')]

**library_strategy**
- value: `WGS`
- explanation: SRA experiment XML attribute LIBRARY_STRATEGY is 'WGS' (whole genome shotgun). [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: SRA experiment XML attribute LIBRARY_SOURCE is 'METAGENOMIC'. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: SRA experiment XML attribute LIBRARY_SELECTION is 'size fractionation'. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: SRA experiment XML specifies Illumina platform with NextSeq 500 instrument model. [Sources: NCBI_experiment (PLATFORM: ILLUMINA)]

**instrument_model**
- value: `Illumina NextSeq 500`
- explanation: SRA experiment XML attribute INSTRUMENT_MODEL is 'NextSeq 500'; user_uploaded_file confirms 'Illumina NextSeq 500 sequencer'. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500'), user_uploaded_file (Library preparation and sequencing section, 'Illumina NextSeq 500 sequencer')]

**dna_extraction_kit**
- value: `Maxwell RSC DNA Blood Kit`
- explanation: User_uploaded_file methods section states 'DNA was isolated from frozen samples by using the Maxwell RSC DNA Blood Kit (Promega)'. [Sources: user_uploaded_file (Sample processing / Isolation of DNA section, 'Maxwell RSC DNA Blood Kit (Promega)')]

**sample_name**
- value: `ind11`
- explanation: BioSample Ids element lists db_label 'Sample name' with value 'ind11'. [Sources: NCBI_biosample (Ids db_label='Sample name', 'ind11')]

**subject_id**
- value: `11`
- explanation: BioSample attribute 'id' is '11', corresponding to individual subject 11 in the study. [Sources: NCBI_biosample (Attribute attribute_name='id', '11')]

**subject_group**
- value: `T2D-P+`
- explanation: [Candidates: Table 1 (user_uploaded_file)=reliable (7 unique identifier values mapping subject # to group), Farina et al. 2019 text description=unreliable (prose summary without per-sample mapping)] [Chosen: Table 1] User_uploaded_file Table 1 'Characteristics of the study population' maps patient ID#11 (row with Patient=11, Type=T2D-P+) to group 'T2D-P+' (moderate to severe periodontitis without type 2 diabetes). [Sources: user_uploaded_file (Table 1, row 'Group=T2D-P+, Patient=#11')] [ID-match: true]

### Categorical/disease-related field(s) — citation verification

**subject_group** = `T2D-P+`
- full explanation: [Candidates: Table 1 (user_uploaded_file)=reliable (7 unique identifier values mapping subject # to group), Farina et al. 2019 text description=unreliable (prose summary without per-sample mapping)] [Chosen: Table 1] User_uploaded_file Table 1 'Characteristics of the study population' maps patient ID#11 (row with Patient=11, Type=T2D-P+) to group 'T2D-P+' (moderate to severe periodontitis without type 2 diabetes). [Sources: user_uploaded_file (Table 1, row 'Group=T2D-P+, Patient=#11')] [ID-match: true]
- ind-label for this accession (from BioSample XML): `ind11`
  - NOTE: [Sources: ...] tag present but no (key, 'excerpt') pairs could be parsed from it: " user_uploaded_file (Table 1, row 'Group=T2D-P+, Patient=#11')"

---

## SAMN35361965 — fixed_context (SAMN35361965_fixed.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The geo_loc_name attribute in the NCBI_biosample record specifies "Italy: Ferrara" as the geographic location for sample SAMN35361965. [Sources: NCBI_biosample (Attributes section, 'geo_loc_name: Italy: Ferrara'); NCBI_experiment (SAMPLE_ATTRIBUTE TAG: geo_loc_name VALUE: Italy: Ferrara); user_uploaded_file (Materials & methods section, 'Twelve adults (≥40 years) with north-Italian ancestry were recruited... at the Research Centre for the Study of Periodontal and Peri-Implant Diseases, University of Ferrara, Italy')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample source is classified as modern because it derives from a living individual enrolled in a contemporary clinical study with a collection_date of 2018, as indicated in the BioSample record and confirmed by the study design describing active patient recruitment and clinical procedures. [Sources: NCBI_biosample (Attributes section, 'collection_date: 2018'); user_uploaded_file (Materials & methods section, 'subgingival plaque samples were collected at four teeth... for each eligible subject')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute 'host' is 'Homo sapiens'. [Sources: NCBI_biosample (host attribute, 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute 'isolation_source' is 'subgingival oral plaque'. [Sources: NCBI_biosample (isolation_source attribute, 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute 'collection_date' is '2018'. [Sources: NCBI_biosample (collection_date attribute, '2018')]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute 'geo_loc_name' is 'Italy: Ferrara'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute 'lat_lon' is '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E')]

**organism**
- value: `human oral metagenome`
- explanation: BioSample Organism field is 'human oral metagenome'. [Sources: NCBI_biosample (Organism>OrganismName, 'human oral metagenome')]

**library_strategy**
- value: `WGS`
- explanation: SRA experiment metadata field LIBRARY_STRATEGY is 'WGS' (whole genome shotgun). [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: SRA experiment metadata field LIBRARY_SOURCE is 'METAGENOMIC'. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: SRA experiment metadata field LIBRARY_SELECTION is 'size fractionation'. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: SRA experiment metadata indicates Illumina sequencer platform. [Sources: NCBI_experiment (PLATFORM>ILLUMINA, 'Illumina')]

**instrument_model**
- value: `NextSeq 500`
- explanation: SRA experiment metadata field INSTRUMENT_MODEL is 'NextSeq 500'. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500')]

**dna_extraction_kit**
- value: `Maxwell RSC DNA Blood Kit`
- explanation: Methods section of user-uploaded paper states 'DNA was isolated from frozen samples by using the Maxwell RSC DNA Blood Kit (Promega)'. [Sources: user_uploaded_file (Materials & methods, Section 2.4.1, 'Maxwell RSC DNA Blood Kit')]

**sample_type**
- value: `subgingival plaque`
- explanation: BioSample isolation_source and Methods confirm the sample is subgingival plaque collected from four tooth sites. [Sources: NCBI_biosample (isolation_source, 'subgingival oral plaque'); user_uploaded_file (Methods, 'subgingival plaque samples')]

**disease**
- value: `type 2 diabetes and periodontitis`
- explanation: [Candidates: NCBI_bioproject (study title and description, explicitly states four groups with/without T2D and/or periodontitis); user_uploaded_file Table 1 marked RELIABLE with ID '11' mapping to group T2D−P+ (periodontitis without type 2 diabetes)]; [Chosen: NCBI_bioproject + user_uploaded_file Table 1]. BioProject description and Methods section in user_uploaded_file indicate sample ind11 (ID=11) belongs to T2D−P+ group (moderate-severe periodontitis but not type 2 diabetes). However, the individual sample metadata (NCBI_biosample) shows ind11 refers to 'Metagenome or environmental sample' without explicit disease status annotation. Per the study design detailed in Methods and RELIABLE Table 1 of user_uploaded_file, individual 11 is in the T2D−P+ group: patients affected by moderate to severe periodontitis but not type 2 diabetes. [Sources: NCBI_bioproject (study groups definition, 'four study groups based on presence/absence of poorly controlled type 2 Diabetes Mellitus and moderate-severe periodontitis'); user_uploaded_file Table 1 (ID 11.0, Group T2D−P+)]

**sample_id**
- value: `ind11`
- explanation: BioSample Id db_label field is 'Sample name' with value 'ind11'. [Sources: NCBI_biosample (Ids>Id, 'ind11')]

**biosample_accession**
- value: `SAMN35361965`
- explanation: BioSample accession is 'SAMN35361965'. [Sources: NCBI_biosample (accession, 'SAMN35361965')]

**sra_accession**
- value: `SRS17893874`
- explanation: BioSample Id with db='SRA' is 'SRS17893874'. [Sources: NCBI_biosample (Ids>Id db='SRA', 'SRS17893874')]

### Categorical/disease-related field(s) — citation verification

**disease** = `type 2 diabetes and periodontitis`
- full explanation: [Candidates: NCBI_bioproject (study title and description, explicitly states four groups with/without T2D and/or periodontitis); user_uploaded_file Table 1 marked RELIABLE with ID '11' mapping to group T2D−P+ (periodontitis without type 2 diabetes)]; [Chosen: NCBI_bioproject + user_uploaded_file Table 1]. BioProject description and Methods section in user_uploaded_file indicate sample ind11 (ID=11) belongs to T2D−P+ group (moderate-severe periodontitis but not type 2 diabetes). However, the individual sample metadata (NCBI_biosample) shows ind11 refers to 'Metagenome or environmental sample' without explicit disease status annotation. Per the study design detailed in Methods and RELIABLE Table 1 of user_uploaded_file, individual 11 is in the T2D−P+ group: patients affected by moderate to severe periodontitis but not type 2 diabetes. [Sources: NCBI_bioproject (study groups definition, 'four study groups based on presence/absence of poorly controlled type 2 Diabetes Mellitus and moderate-severe periodontitis'); user_uploaded_file Table 1 (ID 11.0, Group T2D−P+)]
- ind-label for this accession (from BioSample XML): `ind11`
  - source `NCBI_bioproject` @ "study groups definition": 'four study groups based on presence/absence of poorly controlled type 2 Diabetes Mellitus and moderate-severe periodontitis'
    -> FABRICATED QUOTE: excerpt does not appear anywhere in the 'NCBI_bioproject' source block


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
- explanation: The geo_loc_name attribute in the NCBI_biosample record for SAMN35361966 specifies 'Italy: Ferrara', and the collection_date attribute lists '2018', confirming a modern (living) sample. [Sources: NCBI_biosample (Attributes section, 'geo_loc_name' and 'collection_date' attributes, 'Italy: Ferrara' and '2018'); NCBI_experiment (SAMPLE_ATTRIBUTES section, 'Italy: Ferrara' and '2018'); user_uploaded_file (Materials & methods section, 'metropolitan area of Ferrara, Italy')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample was collected in 2018 from a living human subject at the Research Centre for the Study of Periodontal and Peri-Implant Diseases, University of Ferrara, Italy, confirming modern status. [Sources: NCBI_biosample (collection_date attribute, '2018'); user_uploaded_file (Materials & methods section, 'individuals recruitment...were performed at the Research Centre...University of Ferrara, Italy' and 'Twelve adults (≥40 years)...were recruited'); NCBI_experiment (SAMPLE_ATTRIBUTES section, 'collection_date' tag, 'VALUE: 2018')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**organism**
- value: `human oral metagenome`
- explanation: BioSample XML specifies organism taxonomy_name as 'human oral metagenome'. [Sources: NCBI_biosample (Organism element, 'human oral metagenome')]

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute 'host' is 'Homo sapiens'. [Sources: NCBI_biosample (Attribute attribute_name='host', 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute 'isolation_source' is 'subgingival oral plaque'. [Sources: NCBI_biosample (Attribute attribute_name='isolation_source', 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute 'collection_date' is '2018'. [Sources: NCBI_biosample (Attribute attribute_name='collection_date', '2018')]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute 'geo_loc_name' is 'Italy: Ferrara'. [Sources: NCBI_biosample (Attribute attribute_name='geo_loc_name', 'Italy: Ferrara')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute 'lat_lon' provides coordinates. [Sources: NCBI_biosample (Attribute attribute_name='lat_lon', '44.7667642 N 11.827939 E')]

**sample_id**
- value: `ind12`
- explanation: BioSample sample name identifier is 'ind12', also listed as 'id' attribute value 12. [Sources: NCBI_biosample (Id db_label='Sample name', 'ind12')]

**biosample_accession**
- value: `SAMN35361966`
- explanation: BioSample primary accession identifier. [Sources: NCBI_biosample (Id db='BioSample' is_primary='1', 'SAMN35361966')]

**sra_accession**
- value: `SRS17893876`
- explanation: SRA sample accession from BioSample record. [Sources: NCBI_biosample (Id db='SRA', 'SRS17893876')]

**library_strategy**
- value: `WGS`
- explanation: SRA experiment metadata specifies LIBRARY_STRATEGY as WGS (whole genome shotgun). [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: SRA experiment metadata specifies LIBRARY_SOURCE as METAGENOMIC. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: SRA experiment metadata specifies LIBRARY_SELECTION as size fractionation. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: SRA experiment metadata indicates ILLUMINA platform. [Sources: NCBI_experiment (PLATFORM section, ILLUMINA)]

**instrument_model**
- value: `NextSeq 500`
- explanation: SRA experiment metadata specifies INSTRUMENT_MODEL as NextSeq 500. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500')]

**read_length**
- value: `2 × 150-bp`
- explanation: SRA experiment design describes sequencing with 2 × 150-bp read configuration. [Sources: NCBI_experiment (DESIGN_DESCRIPTION, '2150-bp read')]

**dna_extraction_kit**
- value: `Maxwell RSC DNA Blood Kit`
- explanation: Methods section of user-uploaded paper specifies DNA isolation used Maxwell RSC DNA Blood Kit (Promega). [Sources: user_uploaded_file (Materials & methods, 'Maxwell RSC DNA Blood Kit (Promega)')]

**library_prep_kit**
- value: `NEBNext Ultra II DNA Library Prep Kit for Illumina`
- explanation: SRA design description specifies NEBNext Ultra II DNA Library Prep Kit for Illumina protocol was used. [Sources: NCBI_experiment (DESIGN_DESCRIPTION, 'NEBNext Ultra II DNA Library Prep Kit for Illumina')]

**sample_type**
- value: `Metagenome or environmental sample`
- explanation: BioSample package description and title indicate sample is metagenome/environmental. [Sources: NCBI_biosample (Description/Title, 'Metagenome or environmental sample from human oral metagenome')]

**disease**
- value: `Type 2 Diabetes Mellitus and Moderate-Severe Periodontitis`
- explanation: [Candidates: Table 1=reliable (7 unique identifiers, ind12 listed with T2D+P+ group designation); study prose=unreliable (general description of four groups)]. [Chosen: Table 1]. Based on Table 1 of the user-uploaded paper, subject ind12 is assigned to T2D+P+ group (patients affected by moderate to severe periodontitis and type 2 diabetes). [Sources: user_uploaded_file (Table 1, row with Patient='ind12', Group='T2D+P+')]. [ID-match: true]

**body_site**
- value: `subgingival periodontal pocket`
- explanation: Samples collected from subgingival plaque at sites showing deepest probing depth values among those positive to bleeding on probing (periodontal pockets) for this T2D+P+ subject. [Sources: user_uploaded_file (Methods section, 'in p+ patients, sampling was performed at the 4 sites showing the deepest probing depth values among those positive to bleeding on probing')]

**bioproject**
- value: `PRJNA976261`
- explanation: BioProject accession from metadata. [Sources: NCBI_bioproject (bioproject_id, 'PRJNA976261')]

### Categorical/disease-related field(s) — citation verification

**disease** = `Type 2 Diabetes Mellitus and Moderate-Severe Periodontitis`
- full explanation: [Candidates: Table 1=reliable (7 unique identifiers, ind12 listed with T2D+P+ group designation); study prose=unreliable (general description of four groups)]. [Chosen: Table 1]. Based on Table 1 of the user-uploaded paper, subject ind12 is assigned to T2D+P+ group (patients affected by moderate to severe periodontitis and type 2 diabetes). [Sources: user_uploaded_file (Table 1, row with Patient='ind12', Group='T2D+P+')]. [ID-match: true]
- ind-label for this accession (from BioSample XML): `ind12`
  - NOTE: [Sources: ...] tag present but no (key, 'excerpt') pairs could be parsed from it: " user_uploaded_file (Table 1, row with Patient='ind12', Group='T2D+P+')"

---

## SAMN35361966 — fixed_context (SAMN35361966_fixed.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The geographic location is explicitly stated in the NCBI_biosample record as "Italy: Ferrara" in the geo_loc_name attribute. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara'); NCBI_bioproject (description section, 'University of Ferrara, Italy'); user_uploaded_file (Materials & Methods section, 'Research Centre for the Study of Periodontal and Peri-Implant Diseases, University of Ferrara, Italy')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample source is 'modern' (living individual), as indicated by the collection of subgingival plaque samples from live adult subjects enrolled in an active clinical study with written informed consent. The NCBI_bioproject description states "Twelve subjects, falling into one of the four study groups based on the presence/absence of poorly controlled type 2 Diabetes Mellitus and moderate-severe periodontitis, were selected" and samples were collected during clinical examination in 2018. [Sources: NCBI_biosample (collection_date attribute, '2018'; host attribute, 'Homo sapiens'); NCBI_bioproject (description section, 'Twelve subjects...were selected'); user_uploaded_file (Materials & Methods section, 'Each subject provided a written informed consent before participation')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute 'host' explicitly states Homo sapiens. [Sources: NCBI_biosample (Attribute attribute_name='host', 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute 'isolation_source' states subgingival oral plaque. [Sources: NCBI_biosample (Attribute attribute_name='isolation_source', 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute 'collection_date' is 2018. [Sources: NCBI_biosample (Attribute attribute_name='collection_date', '2018')]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute 'geo_loc_name' is Italy: Ferrara. [Sources: NCBI_biosample (Attribute attribute_name='geo_loc_name', 'Italy: Ferrara')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute 'lat_lon' provides coordinates. [Sources: NCBI_biosample (Attribute attribute_name='lat_lon', '44.7667642 N 11.827939 E')]

**organism**
- value: `human oral metagenome`
- explanation: BioSample Description OrganismName field lists human oral metagenome as the organism. [Sources: NCBI_biosample (Description/Organism/OrganismName, 'human oral metagenome')]

**sample_type**
- value: `Metagenome or environmental`
- explanation: BioSample Package display_name is 'Metagenome or environmental; version 1.0'. [Sources: NCBI_biosample (Package display_name, 'Metagenome or environmental')]

**library_strategy**
- value: `WGS`
- explanation: NCBI experiment record lists LIBRARY_STRATEGY as WGS. [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: NCBI experiment record lists LIBRARY_SOURCE as METAGENOMIC. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: NCBI experiment record lists LIBRARY_SELECTION as size fractionation. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: NCBI experiment record indicates PLATFORM is Illumina. [Sources: NCBI_experiment (PLATFORM, 'Illumina')]

**instrument_model**
- value: `NextSeq 500`
- explanation: NCBI experiment record lists INSTRUMENT_MODEL as NextSeq 500. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500')]

**dna_extraction_kit**
- value: `Maxwell RSC DNA Blood Kit`
- explanation: User-uploaded paper (Farina et al. 2019) Methods section states DNA was isolated using Maxwell RSC DNA Blood Kit (Promega). [Sources: user_uploaded_file (Methods section 2.4.1, 'Maxwell RSC DNA Blood Kit')]

**body_site**
- value: `subgingival pocket`
- explanation: User-uploaded paper describes sampling from subgingival sites; study focuses on subgingival microbiome. [Sources: user_uploaded_file (Materials & Methods section 2.3, 'Subgingival plaque was collected')]

**subject_id**
- value: `ind12`
- explanation: BioSample Ids field lists sample name as 'ind12'. [Sources: NCBI_biosample (Ids/Id db_label='Sample name', 'ind12')]

**disease**
- value: `none (healthy control)`
- explanation: [Candidates: Table 1 in user_uploaded_file=reliable (7 unique subject IDs, no repeats)]. [Chosen: Table 1]. Subject ind12 appears to belong to the T2D−P− (healthy) group based on Study Table 1 row showing Patient #3 in T2D−P− group. [Sources: user_uploaded_file (Table 1, Study population characteristics, 'T2D−P− group: Patient #3')]

**sequencing_platform**
- value: `Illumina NextSeq 500`
- explanation: User-uploaded paper Methods 2.4.2 states samples were sequenced with an Illumina NextSeq 500 sequencer with 2×150-bp read. [Sources: user_uploaded_file (Methods 2.4.2, 'NextSeq 500 sequencer with 2×150-bp read')]

### Categorical/disease-related field(s) — citation verification

**disease** = `none (healthy control)`
- full explanation: [Candidates: Table 1 in user_uploaded_file=reliable (7 unique subject IDs, no repeats)]. [Chosen: Table 1]. Subject ind12 appears to belong to the T2D−P− (healthy) group based on Study Table 1 row showing Patient #3 in T2D−P− group. [Sources: user_uploaded_file (Table 1, Study population characteristics, 'T2D−P− group: Patient #3')]
- ind-label for this accession (from BioSample XML): `ind12`
  - NOTE: [Sources: ...] tag present but no (key, 'excerpt') pairs could be parsed from it: " user_uploaded_file (Table 1, Study population characteristics, 'T2D−P− group: Patient #3')"
