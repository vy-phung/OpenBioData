# Contributing to OpenBioData / BioMetadataAudit

Thanks for being here. This project is early and genuinely better with more eyes on it.
Below is what's actually true about the state of the code today, and where
help would matter most.

## Before you dive in

- Read `ARCHITECTURE.md` first. It's a real, honest map of what each of the
  four core modules (trace back, mine literature, cross-check, recover/
  verify/expand) actually does today, including where things overlap or are
  still tangled. Don't assume clean module boundaries — check the doc.
- Open an issue before a large PR. For anything bigger than a small fix,
  a quick issue first saves both of us a rewritten PR later.
- Small, scoped PRs are much easier to review and merge than large ones —
  especially right now, since parts of the pipeline are still being
  untangled (see `ARCHITECTURE.md`'s Module 3/4 notes).

## Known limitations — and where help is genuinely wanted

This isn't a hidden-bugs list. It's what's real about the current state,
because pretending otherwise wastes everyone's time.

- **Cross-check and recover/verify/expand are interleaved**, not separable
  stages — they run inside the same functions and the same LLM calls rather
  than as clean, independently-callable phases. If you want to help split
  these apart, this is a bigger, high-value contribution — start with an
  issue/discussion first.
- **Accession-type detection is implemented three separate times**
  independently, with inconsistent coverage (one branch doesn't recognize
  GEO accessions at all). Consolidating this into one shared dispatcher is
  a contained, good first issue.
- **Depositing-paper text isn't deduplicated across samples in the same
  batch.** When multiple samples from the same project share a source
  paper, that paper's full text currently gets re-sent once per sample
  inside a batched call rather than once per batch. Doesn't affect
  correctness, does affect cost and context-window headroom for large
  projects. Good first issue if you're comfortable reading through the
  batching code in `model.py` and `additional_pipeline.py`.
- **Single-sample Pass 2 extraction has no retry on JSON parse failure.**
  The batched version does (added recently); the single-sample path
  (`_extract_additional_fields()`) still doesn't. Low urgency — failure
  blast radius is one sample — but worth matching eventually. Good first
  issue.
- **Independent reactive batch splits can converge on different field-set
  names.** When a batch exceeds the output token ceiling and gets split
  into sub-batches, each sub-batch computes its own field-name union
  independently. Verified on real data: each sub-batch was internally
  fully consistent, but two sub-batches from the same project can disagree
  with each other on how to name the same kind of field (e.g.
  `disease_or_phenotype` vs. `disease` + `disease_status`). Good first
  issue — see the linked issue for a couple of possible directions.

## How to propose a fix

1. Check `ARCHITECTURE.md` for the module you're touching.
2. Open an issue describing what you found and your proposed approach.
3. Keep PRs scoped to one thing — one module, one behavior change.
4. If you're touching extraction/confidence/citation logic, please test
   against a real accession and describe what you checked in the PR.

## Code of conduct

Be kind, be patient, assume good faith. This is a young project built by
one person — mistakes (mine and yours) are expected and fine.
