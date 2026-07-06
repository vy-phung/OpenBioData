"""
Standalone diagnostic (extends test_context_swap.py's method to 3 more
samples): does fixing Table 1 / Table 3's forward-fill and delimiting
change the disease/group-status answer the real pipeline produces, and
are the model's citations for that answer actually grounded in the
source text?

Does NOT modify any pipeline file. Calls model.query_document_info()
directly with the exact same parameters additional_pipeline.py uses at
additional_pipeline.py:1151 (niche_cases=None, saveLinkFolder=None,
llm_api_function=model.call_llm_api, standardization_schema=None).

Citation verification method: for each categorical/disease-type field's
explanation, extract every single-quoted 'verbatim excerpt' from its
[Sources: key (location, 'excerpt')] tag, then:
  1. Check whether that exact excerpt (whitespace-normalized) appears
     anywhere in the specific named source's own block of the raw
     context text (source blocks are delineated by the pipeline's own
     "The source - <key>: ... -----END OF THIS SOURCE <key> ----"
     markers -- this is how additional_pipeline.py itself demarcates
     sources, so it's the correct boundary to check against, not an
     arbitrary window).
  2. Check whether the sample's own real submitter label (e.g. "ind10",
     extracted mechanically from the BioSample XML's own
     <Id db_label="Sample name">...</Id> tag) appears anywhere in that
     same source block -- i.e. whether the source the model cited could
     even in principle establish a link between this specific sample
     and the disease/group value, or whether that link is an inference
     the model added on its own.
A quote that doesn't appear in its claimed source block at all is a
straightforward fabricated citation. A quote that IS present, but with
no occurrence of the sample's own ind-label anywhere in that source
block, means the source cited does not itself tie the value to this
specific sample -- the same failure mode documented for SAMN35361955's
fixed_context run ("ind1" claimed to map to Table 1's "#1" when the text
itself never states that mapping).

Usage: python test_context_swap_3samples.py
"""
import asyncio
import json
import re

from docx import Document

import model

SAMPLES = [
    ("SAMN35361964", "ind10", "T2D-P- (control)"),
    ("SAMN35361965", "ind11", "T2D-P+"),
    ("SAMN35361966", "ind12", "T2D+P-"),
]

CATEGORICAL_KEYWORDS = (
    "disease", "condition", "diagnosis", "periodont", "diabet",
    "control", "status", "group", "phenotype", "health",
)


def read_context_docx(path: str) -> str:
    """Reverses data_preprocess.save_text_to_docx() (one paragraph per line)."""
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


async def run_one(acc: str, context_text: str) -> dict:
    prompts = {acc: context_text}
    outputs = await model.query_document_info(
        niche_cases=None,
        saveLinkFolder=None,
        llm_api_function=model.call_llm_api,
        prompts=prompts,
        standardization_schema=None,
    )
    return outputs[acc]


def find_categorical_fields(result: dict) -> dict:
    found = {}
    for field, val in result.get("predicted_output", {}).items():
        if any(kw in field.lower() for kw in CATEGORICAL_KEYWORDS):
            found[field] = {"answer": val.get("answer"),
                             "explanation": val.get(f"{field}_explanation", "")}
    for field, val in result.get("_additional_fields", {}).items():
        if any(kw in field.lower() for kw in CATEGORICAL_KEYWORDS):
            found[field] = {"answer": val.get("value"),
                             "explanation": val.get("explanation", "")}
    return found


def get_source_blocks(text: str) -> dict:
    """Split the combined context into {source_key: block_text} using the
    pipeline's own 'The source - KEY: ... -----END OF THIS SOURCE KEY ----'
    delimiters (see additional_pipeline.py's text-building loop)."""
    blocks = {}
    for m in re.finditer(r"The source - (.+?): ", text):
        key = m.group(1)
        start = m.end()
        end_m = re.search(rf"-----END OF THIS SOURCE {re.escape(key)} ----", text[start:])
        end = start + end_m.start() if end_m else len(text)
        blocks[key] = blocks.get(key, "") + text[start:end]
    return blocks


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_citations(explanation: str, source_blocks: dict, ind_label: str) -> list:
    """Returns a list of dicts, one per (source_key, excerpt) pair found in
    the explanation's [Sources: ...] tag."""
    results = []
    m = re.search(r"\[Sources:(.*?)\]", explanation, re.S)
    if not m:
        return [{"note": "no [Sources: ...] tag found in explanation at all"}]
    sources_tag = m.group(1)
    # Each entry looks like: key (location, 'excerpt'); key2 (location2, 'excerpt2')
    for entry_m in re.finditer(r"([^;(]+?)\s*\(([^,]*),\s*'([^']*)'\)", sources_tag):
        key = entry_m.group(1).strip()
        location = entry_m.group(2).strip()
        excerpt = entry_m.group(3).strip()
        block = source_blocks.get(key)
        if block is None:
            # try loose match (model may abbreviate/rename the key)
            loose_matches = [k for k in source_blocks if key.lower() in k.lower() or k.lower() in key.lower()]
            block = source_blocks.get(loose_matches[0]) if loose_matches else None
            key_note = f" (no exact source block named '{key}'; " + (
                f"used closest match '{loose_matches[0]}'" if loose_matches else "no close match either"
            ) + ")"
        else:
            key_note = ""
        if block is None:
            results.append({
                "key": key, "location": location, "excerpt": excerpt,
                "quote_found": False,
                "detail": f"FABRICATED SOURCE: no such source block exists in the context at all{key_note}",
                "ind_label_in_block": None,
            })
            continue
        quote_found = norm(excerpt) in norm(block)
        ind_in_block = ind_label.lower() in block.lower()
        results.append({
            "key": key, "location": location, "excerpt": excerpt,
            "quote_found": quote_found,
            "detail": (
                "quote verified present in that source block" if quote_found
                else f"FABRICATED QUOTE: excerpt does not appear anywhere in the '{key}' source block{key_note}"
            ),
            "ind_label_in_block": ind_in_block,
        })
    if not results:
        results.append({"note": f"[Sources: ...] tag present but no (key, 'excerpt') pairs could be parsed from it: {sources_tag!r}"})
    return results


def format_citation_results(results: list) -> str:
    lines = []
    for r in results:
        if "note" in r:
            lines.append(f"  - NOTE: {r['note']}")
            continue
        lines.append(f"  - source `{r['key']}` @ \"{r['location']}\": '{r['excerpt']}'")
        lines.append(f"    -> {r['detail']}")
        if r["quote_found"]:
            ind_note = "YES" if r["ind_label_in_block"] else "NO -- this source block never mentions the sample's own ind-label at all"
            lines.append(f"    -> sample's own label found in this same source block? {ind_note}")
    return "\n".join(lines)


def format_result(label: str, result: dict, ind_label: str, source_blocks: dict) -> str:
    lines = [f"## {label}", ""]
    lines.append(f"- method_used: `{result.get('method_used')}`")
    lines.append(f"- accession_found_in_text: `{result.get('accession_found_in_text')}`")
    lines.append("")
    lines.append("### Pass 1 (`multi_prompts` / default fields)")
    predicted = result.get("predicted_output", {})
    if not predicted:
        lines.append("_(none)_")
    for field, val in predicted.items():
        lines.append(f"\n**{field}**")
        lines.append(f"- answer: `{val.get('answer')}`")
        expl_key = f"{field}_explanation"
        lines.append(f"- explanation: {val.get(expl_key, '')}")
    lines.append("")
    lines.append("### Pass 2 (`_extract_additional_fields` — generalized JSON extraction), ALL fields")
    additional = result.get("_additional_fields", {})
    if not additional:
        lines.append("_(none)_")
    for field, val in additional.items():
        lines.append(f"\n**{field}**")
        lines.append(f"- value: `{val.get('value')}`")
        lines.append(f"- explanation: {val.get('explanation', '')}")
    lines.append("")
    lines.append("### Categorical/disease-related field(s) — citation verification")
    categorical = find_categorical_fields(result)
    if not categorical:
        lines.append("_(none — no disease/condition/group/status-named field was produced this run)_")
    for field, val in categorical.items():
        lines.append(f"\n**{field}** = `{val['answer']}`")
        lines.append(f"- full explanation: {val['explanation']}")
        lines.append(f"- ind-label for this accession (from BioSample XML): `{ind_label}`")
        citation_results = verify_citations(val["explanation"], source_blocks, ind_label)
        lines.append(format_citation_results(citation_results))
    return "\n".join(lines)


async def main():
    all_report_sections = []
    summary_rows = []

    for acc, ind_label, gt in SAMPLES:
        bad_path = f"test-data/PRJNA976261/{acc}.docx"
        fixed_path = f"test-data/PRJNA976261/{acc}_fixed.docx"
        bad_context = read_context_docx(bad_path)
        fixed_context = read_context_docx(fixed_path)

        print(f"\n{'=' * 80}\n{acc} (ind_label={ind_label}, ground truth={gt})\n{'=' * 80}")

        print(f"--- {acc} bad_context ({len(bad_context)} chars) ---")
        bad_result = await run_one(acc, bad_context)
        bad_blocks = get_source_blocks(bad_context)
        bad_categorical = find_categorical_fields(bad_result)
        print("categorical fields:", {k: v["answer"] for k, v in bad_categorical.items()} or "(none)")

        print(f"--- {acc} fixed_context ({len(fixed_context)} chars) ---")
        fixed_result = await run_one(acc, fixed_context)
        fixed_blocks = get_source_blocks(fixed_context)
        fixed_categorical = find_categorical_fields(fixed_result)
        print("categorical fields:", {k: v["answer"] for k, v in fixed_categorical.items()} or "(none)")

        bad_md = format_result(f"{acc} — bad_context ({acc}.docx)", bad_result, ind_label, bad_blocks)
        fixed_md = format_result(f"{acc} — fixed_context ({acc}_fixed.docx)", fixed_result, ind_label, fixed_blocks)

        summary_rows.append({
            "acc": acc, "ind_label": ind_label, "gt": gt,
            "bad_categorical": bad_categorical,
            "fixed_categorical": fixed_categorical,
        })

        all_report_sections.append(
            f"# {acc} (submitter label `{ind_label}`, ground truth: {gt})\n\n"
            f"- bad_context: `{bad_path}` ({len(bad_context)} chars)\n"
            f"- fixed_context: `{fixed_path}` ({len(fixed_context)} chars)\n\n"
            f"---\n\n{bad_md}\n\n---\n\n{fixed_md}\n"
        )

    # ---- summary table ----
    summary_lines = [
        "## Summary across all 3 samples (6 runs)\n",
        "| Accession | ind_label | Ground truth | bad_context disease field(s) | fixed_context disease field(s) |",
        "|---|---|---|---|---|",
    ]
    for row in summary_rows:
        bad_str = "; ".join(f"{k}={v['answer']!r}" for k, v in row["bad_categorical"].items()) or "_(none)_"
        fixed_str = "; ".join(f"{k}={v['answer']!r}" for k, v in row["fixed_categorical"].items()) or "_(none)_"
        summary_lines.append(f"| {row['acc']} | {row['ind_label']} | {row['gt']} | {bad_str} | {fixed_str} |")

    report = (
        f"# Context swap test — 3 more samples (SAMN35361964/65/66)\n\n"
        f"Function called: `model.query_document_info()` (`model.py:1805`), "
        f"same call signature `additional_pipeline.py:1151` uses in production: "
        f"`niche_cases=None`, `saveLinkFolder=None`, "
        f"`llm_api_function=model.call_llm_api`, `standardization_schema=None`. "
        f"Disease/group-status fields come from Pass 2 "
        f"(`model._extract_additional_fields()`), same as the SAMN35361955 test.\n\n"
        f"Citation verification method: for each disease-type field's explanation, "
        f"every `'quoted excerpt'` inside its `[Sources: key (location, 'excerpt')]` "
        f"tag is checked against the actual named source's own text block in the "
        f"context (blocks delimited by the pipeline's own "
        f"`The source - KEY: ... -----END OF THIS SOURCE KEY ----` markers). A quote "
        f"absent from its claimed source block is flagged FABRICATED. Separately, "
        f"each source block is checked for whether it contains the sample's own "
        f"real submitter label (e.g. `ind10`, extracted from the BioSample XML's "
        f"`<Id db_label=\"Sample name\">` tag) — if the cited source never mentions "
        f"the sample's own ID at all, the source cannot actually establish that the "
        f"disease value belongs to *this* sample, even if the quote itself is real.\n\n"
        f"LLM provider: `model.call_llm_api()` tries Anthropic first "
        f"(`ANTHROPIC_API_KEY` is set in this environment) — Claude Haiku "
        f"(`claude-haiku-4-5-20251001`), not Gemini. LLM output is "
        f"non-deterministic.\n\n"
        + "\n".join(summary_lines)
        + "\n\n---\n\n"
        + "\n\n---\n\n".join(all_report_sections)
    )
    with open("test_output_context_swap_3samples.md", "w") as f:
        f.write(report)
    print("\n\nWrote test_output_context_swap_3samples.md")


if __name__ == "__main__":
    asyncio.run(main())
