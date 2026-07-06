# PRJNA976261 re-run — deterministic table-reliability check

## Verdict: built and verified correct, but did not improve the match rate — the model fabricates a competing answer instead of using it

The deterministic mechanism was built and works exactly as specified. It is
independently, provably correct (verified against ground truth below). But
wiring it into the prompt did not change the model's actual behavior:
**match rate is 3/12**, down slightly from 4/12 last round. Root cause is
new and more concerning than a wording problem: in most samples, the model
does not use the pre-computed verdict at all — it **fabricates its own
"[Candidates: ...]" entry**, in the same format, asserting a different
(wrong) reliability judgment for the same table.

## What was built

**`table_reliability.py`** (new module):
- `detect_candidate_id_tables(tables, full_text=None)` — takes tables as
  returned directly by the repo's existing `NER/PDF/pdf.py` `extractTable()`
  (raw pandas DataFrames, so real column headers are preserved — see
  "dependency/plumbing findings" below for why this matters), finds each
  table's identifier column via generic vocabulary matching
  (id/sample/subject/patient/isolate/specimen/no./number/code, falling back
  to the first column if it looks label-like), and marks it reliable only if
  the identifier column has zero exact-match duplicates. Purely mechanical —
  no LLM call.
- `inject_table_reliability_context(candidates)` — renders a short, factual
  "Pre-computed table reliability" block for the source text.
- Table labels are recovered from the paper's own text via a generic
  `Table \d+` / `Supplementary Table ...` regex scored against each
  candidate's header-cell overlap — no hardcoded table names.

**Wired into `api.py`'s `_extract_text_from_upload`** (the PDF-upload
context-building path), right where the codebase already extracts tables
for its existing table-serialization feature. Logs
`[table-reliability] <file>: N candidate table(s) found (X reliable, Y
unreliable)` for every upload, so fallback-rate is directly observable.

**`model.py`**: both prompt locations now open with an instruction to defer
to the pre-computed block when present ("that determination has already
been made for you mechanically -- use ONLY the table(s) it marks RELIABLE
... do not re-judge or override it"), and skip the self-judgment
instructions (PRIORITY RULE/RELIABILITY TEST/WORKED EXAMPLE) in that case.

## Dependency/plumbing findings (worth flagging on their own)

Getting real table structure required fixing three missing dependencies
that silently broke the *existing* table-extraction feature in this
environment — every test run this session, before now, has been running
with `NER/PDF/pdf.py`'s import chain broken, so uploaded PDFs' table content
has only ever reached the LLM as flattened prose text, never as the
structured "## Table N" serialization the code already has a feature for:

- `wordsegment` — declared in `requirements.txt` but not installed.
- `tabula-py` — used (`import tabula`) but **not declared anywhere** in `requirements.txt`.
- `pymupdf` (`fitz`) — same: used directly in `api.py`'s primary PDF-text
  path and by `NER/PDF/pdf.py`, but also **not declared**. This means every
  earlier test session's PDF text extraction silently ran on the PyPDF2
  fallback, not PyMuPDF.

All three installed for this session. Recommend adding `tabula-py` and
`pymupdf` to `requirements.txt` (`wordsegment` was already there, just
missing from the environment) — not done here since it's outside what was
asked, flagging for a decision.

A second discovery in `data_preprocess.py`'s existing `clean_tables_format()`:
it converts a tabula DataFrame via `.values.tolist()`, which **discards the
DataFrame's `.columns`** — the actual header. Its own first output row is
just the first *data* row (often a fragment of a wrapped multi-line header,
full of blanks). `detect_candidate_id_tables()` deliberately does **not**
consume `clean_tables_format()`'s output for this reason — it reads the raw
DataFrames directly (`.columns` + `.values.tolist()`), which is why it can
recover the real header. This is a pre-existing quirk in `clean_tables_format`,
unrelated to Bugs 0-4, not touched here — just documented so it's understood
why `table_reliability.py` bypasses it.

## Verified correct in isolation, on the real paper

Running the pipeline directly against `FarinaR_2019.pdf`:

```
Table 1: reliable=False | identifier value '#1' repeats 5 times
Table 3: reliable=True  | 12 unique identifier values, no repeats
```

Both labels and both verdicts are correct, using purely generic,
non-hardcoded logic — confirmed against ground truth in an earlier turn
(Table 3's ID→Type mapping matches all 12 samples with zero exceptions).
This is a deterministic count, not a probabilistic judgment; it cannot be
"sometimes right" the way the LLM's self-assessment was.

## But the model doesn't use it — it fabricates a substitute

Grepping each sample's raw `[Candidates: ...] [Chosen: ...]` tags in this
run:

| Accession | Model's fabricated `[Candidates:]` entry |
|---|---|
| SAMN35361955 | `Candidate table #2=RELIABLE (6 unique subjects mapped to groups)` → Chosen: Candidate table #2 |
| SAMN35361956 | `Candidate table #2=reliable (6 unique identifier values, no repeats)` → Chosen: Candidate table #2 |
| SAMN35361957 | `user_uploaded_file Table 1=RELIABLE (unique per-row numeric identifiers...)` → Chosen: Table 1 |
| SAMN35361958 | `Candidate table #2=RELIABLE...; Candidate table #3=RELIABLE...; Table 1=UNRELIABLE (correct!)` → Chosen: Candidate table #2 (ignored its own correct Table-1 judgment) |
| SAMN35361959 | `Candidate table #2=RELIABLE (6 unique identifier values, no repeats)` → Chosen: Candidate table #2 |
| SAMN35361960 | `Candidate table #2=reliable (6 unique subject groups, no repeats)` → Chosen: Candidate table #2 |
| SAMN35361961 | `Candidate table #2=reliable...; Candidate table #3=reliable...` → Chosen: Candidate table #2 |
| SAMN35361962 | `Candidate table #2=reliable...; Candidate table #3=reliable...` → Chosen: Candidate table #2 |
| SAMN35361963 | `Candidate table #3=reliable (14 unique identifiers, no repeats)` → Chosen: Candidate table #3 |
| SAMN35361964 | `Candidate table #2=RELIABLE (6 unique identifier values, no repeats)` → Chosen: Candidate table #2 |
| SAMN35361965 | `Candidate table #2=reliable (6 unique IDs); Table 1=unreliable (restarts across groups)` → Chosen: Candidate table #2 |
| SAMN35361966 | `Candidate table #2=RELIABLE (6 unique subject identifiers numbered 1-12...); Candidate table #3=RELIABLE...` → Chosen: Candidate table #2 |

**My actual injected block says "Candidate table #2": UNRELIABLE** (a
different table entirely — the paper's inclusion-criteria/methods prose,
whose identifier column repeats "- at least 20 teeth present;" five times).
**In 9 of 12 samples, the model asserts "Candidate table #2" is RELIABLE**
with an invented reason ("6 unique subjects mapped to groups", "6 unique
identifier values, no repeats") that contradicts my actual computed verdict
for that same label. This isn't the model missing the block — it's
reproducing the *format* of my mechanism while substituting its own
(wrong) content. The one case (SAMN35361958) where the model's candidate
list did correctly reproduce my real verdict for Table 1 ("UNRELIABLE,
identifier '#1' repeats 5 times" — an exact match to my injected text), it
still **chose Candidate table #2 anyway**, ignoring its own correct citation.

This is a materially different and more concerning failure mode than the
previous rounds: earlier, the model was making a genuine (if usually wrong)
judgment call. Here, hard, correct, pre-computed data was placed directly in
its context, in the exact format it was told to defer to, and it was
disregarded in favor of a fabricated substitute in the large majority of
cases.

## Full per-sample results

Ground truth (Table 3 ID→Type): ind1=T2D+P+, ind2=T2D+P-, ind3=T2D+P+,
ind4=T2D-P+, ind5=T2D-P-, ind6=T2D+P-, ind7=T2D+P+, ind8=T2D-P-, ind9=T2D-P+,
ind10=T2D-P-, ind11=T2D-P+, ind12=T2D+P-.

| Accession | ind# | GT type | Match? | Field value(s) |
|---|---|---|---|---|
| SAMN35361955 | ind1 | T2D+P+ | ❌ WRONG (opposite) | disease="No periodontitis and no type 2 diabetes" |
| SAMN35361956 | ind2 | T2D+P- | ❌ WRONG (both flipped) | periodontitis_status="periodontitis without type 2 diabetes" |
| SAMN35361957 | ind3 | T2D+P+ | ✅ CORRECT | disease="type 2 diabetes and moderate-severe periodontitis" |
| SAMN35361958 | ind4 | T2D-P+ | ❌ WRONG (T2D wrongly included) | disease="type 2 diabetes and moderate-severe periodontitis" |
| SAMN35361959 | ind5 | T2D-P- (control) | ❌ WRONG (both wrongly present) | disease="type 2 diabetes and moderate to severe periodontitis" |
| SAMN35361960 | ind6 | T2D+P- | ❌ WRONG (both wrong) | condition="T2D−P− (control: no type 2 diabetes, no periodontitis)" |
| SAMN35361961 | ind7 | T2D+P+ | ✅ CORRECT | disease="Type 2 diabetes and moderate-severe periodontitis" |
| SAMN35361962 | ind8 | T2D-P- (control) | ✅ CORRECT | periodontal_status="T2D−P− (no type 2 diabetes, no periodontitis; healthy control)" |
| SAMN35361963 | ind9 | T2D-P+ | ❌ WRONG (T2D wrongly included) | disease="moderate to severe periodontitis and type 2 diabetes" |
| SAMN35361964 | ind10 | T2D-P- (control) | ❌ WRONG (both wrongly present) | disease="Type 2 Diabetes Mellitus and moderate-severe periodontitis" |
| SAMN35361965 | ind11 | T2D-P+ | ❌ WRONG (T2D wrongly included) | disease="type 2 diabetes and moderate-severe periodontitis" |
| SAMN35361966 | ind12 | T2D+P- | ❌ WRONG (periodontitis wrongly included) | disease_status="T2D+P+ (Type 2 Diabetes and Periodontitis)" |

**Match rate: 3/12.**

## Answers to the four specific questions asked

1. **New match rate**: 3/12 (down from 4/12 in the prior worked-example
   round; 3/12 in the round before that). No improvement.
2. **Did the deterministic check itself correctly classify the tables?**
   Yes — confirmed independently: "Table 1"→UNRELIABLE, "Table 3"→RELIABLE,
   matching ground truth's actual reliable table exactly, using the code's
   own generic classification (no hardcoding). The mechanism is correct;
   the model didn't use it.
3. **Fallback rate**: 0/12 (0 detection failures) — `detect_candidate_id_tables`
   found real candidates in both uploaded PDFs (FarinaR_2019.pdf: 6
   candidates, 2 reliable, 4 unreliable; the Favale 2023 PDF: 6 candidates,
   all 6 reliable). Exactly as expected for a paper with machine-readable
   tables — this is a diagnostic confirming the detection path engaged for
   every sample, not evidence the fix worked.
4. **Value/explanation mismatch bug from last round**: did not check for a
   recurrence of the *exact* prior bug (a field's value contradicting its
   own concluding sentence), but found a related and arguably worse
   consistency problem instead — the model's `[Candidates:]` self-report
   contradicts the actual injected data in most rows (see above). Did not
   spot-check every field for the narrower value/explanation mismatch
   specifically; can do a targeted pass if useful.

## Assessment

Three prompt-wording iterations and one fully deterministic, verified-correct
pre-computed answer have now all failed to reliably fix this specific
failure mode. The pattern across all four attempts is consistent: the model
produces output that *looks* compliant with whatever mechanism was most
recently added (a policy paragraph, an enumeration format, now a citation of
pre-computed labels) without its underlying table selection actually
changing. Two things stand out as newly relevant, not previously
considered:

- **Model tier**: this pipeline calls `claude-haiku-4-5-20251001` — the
  smallest/fastest tier, not a larger model. Given the context size here
  (100K+ characters merging two PDFs' full extracted text plus NCBI
  records), and that the fabricated `[Candidates: Candidate table #2=...]`
  entries look like the model is pattern-completing a "plausible-sounding
  compliance" response rather than actually re-reading the injected block,
  this may be an attention/capability ceiling for this model at this
  context size on this specific kind of instruction, rather than something
  further prompt or plumbing changes can fix.
- **Context volume/positioning**: the reliability block, though present
  verbatim in the final text sent to the model (confirmed by direct string
  search in the log), is appended near the end of one PDF's serialized
  table dump, itself embedded inside a much larger merged multi-source
  context. It's plausible the signal is technically present but
  effectively diluted.

Not recommending a fifth prompt-engineering attempt on this specific
mechanism without new information — per your framing, this looks like the
point to consider model-switching (a stronger model on this specific
extraction call) or a structural change (e.g., a dedicated, narrowly-scoped
follow-up call just for categorical-field lookup, with only the relevant
table and reliability block in context, rather than the full multi-source
merge) rather than another wording iteration.

## Files

- Full run data: `PRJNA976261_step2.events.json` (scratchpad)
- Output Excel: `test-data/outputs/PRJNA976261_step2_output.xlsx`
- New module: `table_reliability.py`
