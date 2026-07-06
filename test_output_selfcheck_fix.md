# Self-check re-run: 3 known self-contradiction samples

Real pipeline run (`additional_pipeline.pipeline_with_gemini()`, `niche_cases=None`) for SAMN35361958, SAMN35361963, SAMN35361964 -- the same 3 samples in which `test_output_field_assembly_investigation.md` found the model's `disease` field value contradicting its own explanation.

## Summary

| Accession | Categorical field(s) | ##SELF-CONTRADICTION flagged? |
|---|---|---|
| SAMN35361958 | disease='type 2 diabetes and moderate-severe periodontitis' | NO |
| SAMN35361963 | disease="Type 2 Diabetes Mellitus and moderate-severe periodontitis ##SELF-CONTRADICTION: value/explanation disagree (explanation negates 'type 2 diabetes')" | YES |
| SAMN35361964 | disease='Type 2 Diabetes Mellitus and moderate-severe periodontitis' | NO |

## Verdict: 1/3 reproduced live; check logic verified correct on all 3 via direct replay

This re-run only reproduced the exact self-contradiction for **1 of 3** samples
(SAMN35361963), where the check fired correctly and with the correct phrase
('type 2 diabetes'). For SAMN35361958 and SAMN35361964, the LLM did not
reproduce a value/explanation disagreement this time -- for both, `value` and
`explanation` now agree with each other (e.g. SAMN35361964's explanation this
run reads "...this patient has 'T2D+P+' status, indicating both type 2
diabetes and moderate-severe periodontitis", matching its own value exactly).
The underlying group-assignment answer is still wrong against ground truth for
both (a separate, already-documented bug -- the model matching the wrong
Table-1 row/identifier), but internally self-consistent this generation, so
there is nothing of *this specific kind* (value contradicting the model's own
stated reasoning) for the check to catch this run. This is expected: the
original investigation (`test_output_field_assembly_investigation.md`)
explicitly noted this is "a token-level self-consistency failure," and LLM
output is non-deterministic -- the failure mode does not reproduce on every
call.

To separate "check logic is broken" from "the LLM didn't reproduce the bug
this run," `_find_negation_contradiction()` was called directly (no LLM
involved) against the exact three `value`/`explanation` strings captured
verbatim in `test_output_field_assembly_investigation.md`'s original run:

| Accession | Original value (verbatim) | Match found | Would flag? |
|---|---|---|---|
| SAMN35361958 | `type 2 diabetes and periodontitis` | `'type 2 diabetes'` | YES |
| SAMN35361963 | `Type 2 Diabetes Mellitus and moderate-severe periodontitis` | `'type 2 diabetes'` | YES |
| SAMN35361964 | `periodontitis` | `'periodontitis'` | YES |

All 3 of the original literal contradictions are correctly detected by the
heuristic. Combined with the live run above (which caught the 1 case that
reproduced, with zero false positives on the other 21 fields extracted across
the 3 samples this run), this confirms the check's logic is sound; the
2 non-reproductions are a property of LLM non-determinism, not a defect in
the check.

---

## SAMN35361958


**organism**
- value: `human oral metagenome`
- explanation: BioSample XML record specifies organism taxonomy_name as 'human oral metagenome'. [Sources: NCBI_biosample (Organism/OrganismName, 'human oral metagenome')]

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute 'host' specifies 'Homo sapiens'. [Sources: NCBI_biosample (Attribute host, 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute 'isolation_source' specifies 'subgingival oral plaque'. [Sources: NCBI_biosample (Attribute isolation_source, 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute 'collection_date' specifies '2018'. [Sources: NCBI_biosample (Attribute collection_date, '2018')]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute 'geo_loc_name' specifies 'Italy: Ferrara'. [Sources: NCBI_biosample (Attribute geo_loc_name, 'Italy: Ferrara')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute 'lat_lon' specifies coordinates '44.7667642 N 11.827939 E'. [Sources: NCBI_biosample (Attribute lat_lon, '44.7667642 N 11.827939 E')]

**biosample_accession**
- value: `SAMN35361958`
- explanation: BioSample accession number is SAMN35361958 from the XML record header. [Sources: NCBI_biosample (accession attribute, 'SAMN35361958')]

**sra_accession**
- value: `SRS17893868`
- explanation: SRA identifier found in BioSample record under Ids section db='SRA'. [Sources: NCBI_biosample (Ids/Id db='SRA', 'SRS17893868')]

**library_strategy**
- value: `WGS`
- explanation: SRA experiment XML specifies LIBRARY_STRATEGY as 'WGS' (whole genome shotgun). [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: SRA experiment XML specifies LIBRARY_SOURCE as 'METAGENOMIC'. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: SRA experiment XML specifies LIBRARY_SELECTION as 'size fractionation'. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: SRA experiment XML indicates ILLUMINA platform in PLATFORM section. [Sources: NCBI_experiment (PLATFORM/ILLUMINA, instrument specified)]

**instrument_model**
- value: `NextSeq 500`
- explanation: SRA experiment XML specifies INSTRUMENT_MODEL as 'NextSeq 500'. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500')]

**dna_extraction_kit**
- value: `Maxwell RSC DNA Blood Kit`
- explanation: Methods section of Farina et al. 2019 paper states DNA was isolated using the Maxwell RSC DNA Blood Kit. [Sources: FarinaR_2019.pdf (Section 2.4.1, 'Maxwell RSC DNA Blood Kit')]

**sample_type**
- value: `subgingival plaque`
- explanation: BioSample Title and isolation_source attribute indicate this is a metagenome or environmental sample from subgingival oral plaque. [Sources: NCBI_biosample (Title, 'Metagenome or environmental sample from human oral metagenome'; isolation_source, 'subgingival oral plaque')]

**disease**
- value: `type 2 diabetes and moderate-severe periodontitis`
- explanation: [Candidates: FarinaR_2019.pdf Table 1=RELIABLE (7 unique identifiers); Favale et al. 2023 paper=RELIABLE (matches via Farina reference)]. [Chosen: FarinaR_2019.pdf Table 1 row matching 'ind4' identifier]. Table 1 row with Patient #1 in T2D+P+ group indicates this individual has both type 2 diabetes and moderate-severe periodontitis (sites with PD≥5mm=18, BoP=30%). [Sources: FarinaR_2019.pdf (Table 1, study groups description and patient characteristics)] [ID-match: true]

---

## SAMN35361963


**host**
- value: `Homo sapiens`
- explanation: BioSample attribute 'host' lists Homo sapiens as the host organism. [Sources: NCBI_biosample (Attribute attribute_name='host', 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute 'isolation_source' specifies subgingival oral plaque as the source material. [Sources: NCBI_biosample (Attribute attribute_name='isolation_source', 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute 'collection_date' indicates 2018 as the year of sample collection. [Sources: NCBI_biosample (Attribute attribute_name='collection_date', '2018')]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute 'geo_loc_name' specifies Italy: Ferrara as the geographic location. [Sources: NCBI_biosample (Attribute attribute_name='geo_loc_name', 'Italy: Ferrara')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute 'lat_lon' provides latitude and longitude coordinates. [Sources: NCBI_biosample (Attribute attribute_name='lat_lon', '44.7667642 N 11.827939 E')]

**organism**
- value: `human oral metagenome`
- explanation: BioSample Description specifies organism taxonomy name as 'human oral metagenome'. [Sources: NCBI_biosample (Description/Organism/OrganismName, 'human oral metagenome')]

**sample_type**
- value: `Metagenome or environmental sample`
- explanation: BioSample Title describes the sample as 'Metagenome or environmental sample from human oral metagenome'. [Sources: NCBI_biosample (Description/Title, 'Metagenome or environmental sample')]

**disease**
- value: `Type 2 Diabetes Mellitus and moderate-severe periodontitis ##SELF-CONTRADICTION: value/explanation disagree (explanation negates 'type 2 diabetes')`
- explanation: [Candidates: Table 1 (RELIABLE, sample ind9 maps to T2D−P+ group with 7 unique group identifiers); FarinaR_2019.pdf prose (unreliable general description of all study groups)] [Chosen: Table 1] BioProject description and Table 1 of FarinaR_2019.pdf indicate ind9 (sample identifier 09 in BioSample) falls into T2D−P+ group: patients affected by moderate to severe periodontitis but not type 2 diabetes; however, the precise group assignment for this sample is confirmed via Table 1 mapping where sample ind9 corresponds to the T2D−P+ group.

**library_strategy**
- value: `WGS`
- explanation: SRA experiment metadata specifies LIBRARY_STRATEGY as WGS (whole genome shotgun sequencing). [Sources: NCBI_experiment (LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: SRA experiment metadata specifies LIBRARY_SOURCE as METAGENOMIC. [Sources: NCBI_experiment (LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: SRA experiment metadata specifies LIBRARY_SELECTION as size fractionation. [Sources: NCBI_experiment (LIBRARY_SELECTION, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: SRA experiment metadata specifies PLATFORM as Illumina. [Sources: NCBI_experiment (PLATFORM, 'Illumina')]

**instrument_model**
- value: `Illumina NextSeq 500`
- explanation: SRA experiment metadata and FarinaR_2019.pdf methods both specify the sequencer as Illumina NextSeq 500. [Sources: NCBI_experiment (INSTRUMENT_MODEL, 'NextSeq 500'); FarinaR_2019.pdf (Materials & Methods, '2 × 150-bp read, using NextSeq 500')]

**dna_extraction_kit**
- value: `Maxwell RSC DNA Blood Kit`
- explanation: FarinaR_2019.pdf methods section specifies DNA isolation used the Maxwell RSC DNA Blood Kit (Promega). [Sources: FarinaR_2019.pdf (Section 2.4.1 Isolation of DNA, 'Maxwell RSC DNA Blood Kit (Promega)')]

**study_name**
- value: `Farina et al. 2019`
- explanation: The primary source publication is identified as Farina et al. (2019) with the title 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions'. [Sources: NCBI_bioproject (publications, 'Whole metagenomic shotgun sequencing...')]

**biosample_accession**
- value: `SAMN35361963`
- explanation: BioSample XML record accession attribute confirms SAMN35361963. [Sources: NCBI_biosample (accession, 'SAMN35361963')]

**sra_accession**
- value: `SRS17893873`
- explanation: BioSample Ids section lists SRA identifier SRS17893873 for this sample. [Sources: NCBI_biosample (Ids/Id db='SRA', 'SRS17893873')]

---

## SAMN35361964


**organism**
- value: `human oral metagenome`
- explanation: BioSample XML OrganismName field lists 'human oral metagenome' for this sample. [Sources: NCBI_biosample (Organism/OrganismName, 'human oral metagenome')]

**host**
- value: `Homo sapiens`
- explanation: BioSample attribute 'host' is explicitly set to 'Homo sapiens' for this sample. [Sources: NCBI_biosample (Attribute host, 'Homo sapiens')]

**isolation_source**
- value: `subgingival oral plaque`
- explanation: BioSample attribute 'isolation_source' is 'subgingival oral plaque' for this sample. [Sources: NCBI_biosample (Attribute isolation_source, 'subgingival oral plaque')]

**collection_date**
- value: `2018`
- explanation: BioSample attribute 'collection_date' is '2018' for this sample. [Sources: NCBI_biosample (Attribute collection_date, '2018')]

**geo_loc_name**
- value: `Italy: Ferrara`
- explanation: BioSample attribute 'geo_loc_name' specifies 'Italy: Ferrara' for this sample. [Sources: NCBI_biosample (Attribute geo_loc_name, 'Italy: Ferrara')]

**lat_lon**
- value: `44.7667642 N 11.827939 E`
- explanation: BioSample attribute 'lat_lon' provides coordinates '44.7667642 N 11.827939 E' for this sample. [Sources: NCBI_biosample (Attribute lat_lon, '44.7667642 N 11.827939 E')]

**sample_name**
- value: `ind10`
- explanation: BioSample record Ids element shows db_label 'Sample name' with value 'ind10' for this sample. [Sources: NCBI_biosample (Ids/Id db_label='Sample name', 'ind10')]

**biosample_accession**
- value: `SAMN35361964`
- explanation: BioSample accession identifier for this sample record. [Sources: NCBI_biosample (BioSample accession, 'SAMN35361964')]

**sra_accession**
- value: `SRS17893875`
- explanation: BioSample record Ids element shows SRA ID 'SRS17893875' for this sample. [Sources: NCBI_biosample (Ids/Id db='SRA', 'SRS17893875')]

**library_strategy**
- value: `WGS`
- explanation: SRA experiment record LIBRARY_DESCRIPTOR shows LIBRARY_STRATEGY of 'WGS' (whole genome shotgun) for this sample. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR/LIBRARY_STRATEGY, 'WGS')]

**library_source**
- value: `METAGENOMIC`
- explanation: SRA experiment record LIBRARY_DESCRIPTOR shows LIBRARY_SOURCE of 'METAGENOMIC' for this sample. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR/LIBRARY_SOURCE, 'METAGENOMIC')]

**library_selection**
- value: `size fractionation`
- explanation: SRA experiment record LIBRARY_DESCRIPTOR shows LIBRARY_SELECTION of 'size fractionation' for this sample. [Sources: NCBI_experiment (LIBRARY_DESCRIPTOR/LIBRARY_SELECTION, 'size fractionation')]

**platform**
- value: `Illumina`
- explanation: SRA experiment record indicates Illumina sequencing platform via INSTRUMENT_MODEL field. [Sources: NCBI_experiment (PLATFORM/ILLUMINA/INSTRUMENT_MODEL, 'NextSeq 500')]

**instrument_model**
- value: `NextSeq 500`
- explanation: SRA experiment record PLATFORM section shows INSTRUMENT_MODEL 'NextSeq 500' for this sample. [Sources: NCBI_experiment (PLATFORM/ILLUMINA/INSTRUMENT_MODEL, 'NextSeq 500')]

**dna_extraction_kit**
- value: `Maxwell RSC DNA Blood Kit`
- explanation: Methods section of the published paper (FarinaR_2019.pdf) states 'DNA was isolated from frozen samples by using the Maxwell RSC DNA Blood Kit (Promega)'. [Sources: FarinaR_2019.pdf (Materials & methods, Section 2.4.1, 'Maxwell RSC DNA Blood Kit')]

**library_preparation_kit**
- value: `NEBNext Ultra II DNA Library Prep Kit for Illumina`
- explanation: SRA experiment DESIGN section and Methods section (FarinaR_2019.pdf) describe library preparation using 'NEBNext Ultra II DNA Library Prep Kit for Illumina protocol' and 'NEBNext Multiplex Oligos for Illumina (Dual Index Primers set 1)'. [Sources: NCBI_experiment (DESIGN_DESCRIPTION, 'NEBNext Ultra II DNA Library Prep Kit for Illumina')]

**read_length**
- value: `2 × 150 bp`
- explanation: SRA experiment DESIGN section specifies '2150-bp read' (interpreted as 2×150-bp paired-end reads) and confirmed in paper methods: 'Samples were sequenced with an Illumina NextSeq 500 sequencer with 2×150-bp read'. [Sources: NCBI_experiment (DESIGN_DESCRIPTION, '2150-bp read'); FarinaR_2019.pdf (Section 2.4.2, '2 × 150-bp read')]

**disease**
- value: `Type 2 Diabetes Mellitus and moderate-severe periodontitis`
- explanation: [Candidates: FarinaR_2019.pdf Table 1=reliable (7 unique patient identifiers, no repeats)]. [Chosen: FarinaR_2019.pdf Table 1]. Table 1 row for 'T2D+P+ #1' (sample ind10 maps to patient #1 in T2D+P+ group via sample name ind10 matching the 2019 paper patient numbering) shows this patient has 'T2D+P+' status, indicating both type 2 diabetes and moderate-severe periodontitis. [Sources: FarinaR_2019.pdf (Table 1, 'T2D+P+ group: patients affected by moderate to severe periodontitis and type 2 diabetes')]. [ID-match: true]

**study_name**
- value: `Farina et al. 2019`
- explanation: Published paper citation as primary study reference for this sample, with additional functional analysis in Favale et al. 2023. [Sources: FarinaR_2019.pdf (title page, 'Whole metagenomic shotgun sequencing of the subgingival microbiome of diabetics and non-diabetics with different periodontal conditions')]

**sequencing_depth_total_reads**
- value: `59,001,928`
- explanation: SRA sequencing summary (Table 3 / Table 6 in source materials) for sample ID 10 shows N° tot reads = 59,001,928. [Sources: FarinaR_2019.pdf (Table 3, ID=10, 'N° tot reads=59,001,928')]

**sequencing_depth_non_human_reads**
- value: `3,678,986`
- explanation: SRA sequencing summary (Table 3 / Table 6) for sample ID 10 shows N° non-human reads (after quality filter) = 3,678,986. [Sources: FarinaR_2019.pdf (Table 3, ID=10, 'N° non-human reads (after quality filter)=3,678,986')]