# Context swap test — SAMN35361955

Function called: `model.query_document_info()` (defined in `model.py`, called the same way `additional_pipeline.py` calls it at `additional_pipeline.py:1151` — `niche_cases=None`, `saveLinkFolder=None`, `llm_api_function=model.call_llm_api`, `standardization_schema=None`, matching the last full PRJNA976261 test run which supplied no niche_cases/schema).

Internally this calls `model.multi_prompts()` + `model.call_llm_api()` (Pass 1: country/modern-ancient) and `model._extract_additional_fields()` (Pass 2: generalized JSON extraction — this is where the disease/group-status field is actually produced for this dataset, since no niche_cases were supplied).

Both calls in this run went through `model.call_llm_api()`'s **Anthropic Claude Haiku** branch (`claude-haiku-4-5-20251001`), not Gemini — `ANTHROPIC_API_KEY` is set in this environment and is tried first; verified separately (`model.call_llm_api()` returned `model_instance=None`, which only happens on the Anthropic return path, never the Gemini one). Earlier documented runs of this same pipeline (`test_output_phase2.md`) note using Gemini 2.5 Flash-Lite, so this run is not directly comparable to those in provider terms. LLM output is non-deterministic, so a re-run may pick a different provider/answer even with identical inputs.

Context sources:
- bad_context: `test-data/PRJNA976261/SAMN35361955.docx` (292929 chars)
- fixed_context: `test-data/PRJNA976261/SAMN35361955_fixed.docx` (290879 chars)

## Headline result

- bad_context categorical/disease-related fields found: **NONE** — no field whose name matches disease/condition/diagnosis/periodont/diabet/control/status/group/phenotype/health was present in predicted_output or _additional_fields
- fixed_context categorical/disease-related fields found: {"_additional_fields.disease_condition": "T2D+P+ (type 2 diabetes with moderate to severe periodontitis)"}

**Asymmetric result.** Only one of the two contexts produced a categorical/disease-related field at all this run — see raw output below.

---

## bad_context (SAMN35361955.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The geo_loc_name attribute in the NCBI BioSample record for SAMN35361955 indicates the geographic location as 'Italy: Ferrara'. [Sources: NCBI_biosample (Attributes section, 'Italy: Ferrara'); NCBI_experiment (SAMPLE_ATTRIBUTE section, 'Italy: Ferrara'); user_uploaded_file (Materials & Methods section, 'University of Ferrara, Italy')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample was collected from a living individual as part of a clinical study with informed consent; collection_date attribute shows '2018', confirming modern/contemporary sample origin. [Sources: NCBI_biosample (Attributes section, 'collection_date: 2018'); user_uploaded_file (Materials & Methods section, 'Each subject provided a written informed consent before participation')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction)
_(none)_

---

## fixed_context (SAMN35361955_fixed.docx)

- method_used: `rag_llm`
- accession_found_in_text: `True`

### Pass 1 (`multi_prompts` / default fields)

**country_name**
- answer: `Italy`
- explanation: The geographic location is explicitly stated in the NCBI_biosample record: the geo_loc_name attribute is 'Italy: Ferrara', with coordinates 44.7667642 N 11.827939 E. [Sources: NCBI_biosample (Attributes section, geo_loc_name field, 'Italy: Ferrara'); NCBI_experiment (SAMPLE_ATTRIBUTE TAG geo_loc_name, 'Italy: Ferrara'); user_uploaded_file (Materials & methods section 2.1, 'Research Centre for the Study of Periodontal and Peri-Implant Diseases, University of Ferrara, Italy')] [Conflict: none] [ID-match: true]

**modern/ancient/unknown**
- answer: `modern`
- explanation: The sample source is modern (living individual). The sample was collected from a living adult subject (≥40 years) as part of an active clinical study with written informed consent and collection of subgingival plaque from live teeth in the oral cavity. [Sources: NCBI_biosample (Description, 'human oral metagenome' from living host Homo sapiens); NCBI_experiment (SAMPLE_ATTRIBUTE TAG host VALUE 'Homo sapiens'); user_uploaded_file (Materials & methods 2.1, 'Twelve adults (≥40 years)... were recruited' and 'For each eligible subject, four subgingival plaque samples were collected at four teeth')] [Conflict: none] [ID-match: true]

### Pass 2 (`_extract_additional_fields` — generalized JSON extraction)

**organism**
- value: `human oral metagenome`
- explanation: BioSample XML Organism field lists taxonomy_name as 'human oral metagenome'. [Sources: NCBI_biosample (Organism taxonomy_name, 'human oral metagenome')]

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute 'host' is recorded as 'Homo sapiens'. [Sources: NCBI_biosample (host attribute, 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute 'isolation_source' is 'subgingival oral plaque'. [Sources: NCBI_biosample (isolation_source attribute, 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute 'collection_date' is recorded as '2018'. [Sources: NCBI_biosample (collection_date attribute, '2018')]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute 'geo_loc_name' specifies 'Italy: Ferrara'. [Sources: NCBI_biosample (geo_loc_name attribute, 'Italy: Ferrara')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute 'lat_lon' provides coordinates '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (lat_lon attribute, '44.7667642 N 11.827939 E')]

**library_strategy**
- value: `WGA`
- explanation: SRA experiment XML LIBRARY_DESCRIPTOR shows LIBRARY_STRATEGY as 'WGA' (whole genome amplification). [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGA')]

**library_source**
- value: `METAGENOMIC`
- explanation: SRA experiment XML LIBRARY_DESCRIPTOR shows LIBRARY_SOURCE as 'METAGENOMIC'. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: SRA experiment XML LIBRARY_DESCRIPTOR shows LIBRARY_SELECTION as 'size fractionation'. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')]

**instrument_model**
- value: `NextSeq 500`
- explanation: SRA experiment XML PLATFORM section specifies INSTRUMENT_MODEL as 'NextSeq 500'. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500')]

**platform**
- value: `Illumina`
- explanation: User-uploaded paper methods section states 'Samples were sequenced with an Illumina NextSeq 500 sequencer'. [Sources: user_uploaded_file (Methods section, 'Illumina NextSeq 500 sequencer')]

**sample_type**
- value: `subgingival plaque`
- explanation: BioSample isolation_source is 'subgingival oral plaque' and paper methods confirm 'subgingival plaque samples were collected'. [Sources: NCBI_biosample (isolation_source, 'subgingival oral plaque')]

**dna_extraction_kit**
- value: `Maxwell RSC DNA Blood Kit`
- explanation: User-uploaded paper methods section states 'DNA was isolated from frozen samples by using the Maxwell RSC DNA Blood Kit (Promega)'. [Sources: user_uploaded_file (Materials & methods, 'Maxwell RSC DNA Blood Kit (Promega)')]

**study_name**
- value: `Favale et al. 2024`
- explanation: The primary publication by first author Nicoletta Favale was published in 2024 in Molecular Oral Microbiology. [Sources: https://doi.org/10.1111/omi.12418 (article metadata)]

**disease_condition**
- value: `T2D+P+ (type 2 diabetes with moderate to severe periodontitis)`
- explanation: [Candidates: Farina et al. 2019 user-uploaded Methods section with study group assignments=reliable (12 unique subject identifiers across 4 distinct groups, no repeats)]. [Chosen: Farina et al. 2019 Methods]. Sample SAMN35361955 with BioSample name 'ind1' corresponds to subject #1 in the 2019 paper. The Methods section states 'subjects were consecutively selected and assigned to one of the following groups (of 3 subjects each): patients affected by moderate to severe periodontitis and type 2 diabetes (T2D+P+ group)' and the accompanying Table 1 in the 2019 paper (Farina et al., Archives of Oral Biology) identifies ind1/#1 in the T2D+P+ group. [Sources: user_uploaded_file (Farina et al. 2019, Methods and Table 1, 'T2D + P+ #1')]. [ID-match: true]
