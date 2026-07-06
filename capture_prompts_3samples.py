"""
Standalone diagnostic: capture the exact, full prompt text sent to the LLM
API for each of the 3 samples' bad_context/fixed_context runs (the same
runs test_context_swap_3samples.py performs), and save them to
test-data/PRJNA976261/<sample>_prompt.txt.

Does NOT modify any pipeline file. Monkeypatches model.call_llm_api at
the module level with a wrapper that records the `prompt` argument before
delegating to the real function -- this works because
model.query_document_info() calls the module-level `call_llm_api` name
directly at its two call sites (Pass 1: model.py:1863, inside Pass 2's
model._extract_additional_fields(): model.py ~1758), NOT the
`llm_api_function` parameter passed into query_document_info (that
parameter is accepted but never actually used internally -- confirmed by
grep, only one match for "llm_api_function" in model.py: the def line
itself). Patching model.call_llm_api therefore intercepts every real call
transparently; the wrapper still calls through to the original function,
so LLM behavior/output is unaffected -- only the raw prompt text is
additionally recorded.

Usage: python capture_prompts_3samples.py
"""
import asyncio

from docx import Document

import model

SAMPLES = ["SAMN35361964", "SAMN35361965", "SAMN35361966"]

_original_call_llm_api = model.call_llm_api


def read_context_docx(path: str) -> str:
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


async def capture_for_context(acc: str, label: str, context_text: str) -> list:
    """Runs model.query_document_info() for one context version, capturing
    every prompt sent to call_llm_api in call order (Pass 1 then Pass 2)."""
    captured = []

    def capturing_call_llm_api(prompt, model_name=None):
        captured.append(prompt)
        return _original_call_llm_api(prompt, model_name)

    model.call_llm_api = capturing_call_llm_api
    try:
        prompts = {acc: context_text}
        print(f"{acc} -- capturing prompts for {label} ({len(context_text)} chars)")
        await model.query_document_info(
            niche_cases=None,
            saveLinkFolder=None,
            llm_api_function=model.call_llm_api,
            prompts=prompts,
            standardization_schema=None,
        )
    finally:
        model.call_llm_api = _original_call_llm_api

    print(f"{acc} -- {label}: captured {len(captured)} prompt(s)")
    return captured


async def process_sample(acc: str):
    bad_path = f"test-data/PRJNA976261/{acc}.docx"
    fixed_path = f"test-data/PRJNA976261/{acc}_fixed.docx"
    bad_context = read_context_docx(bad_path)
    fixed_context = read_context_docx(fixed_path)

    bad_prompts = await capture_for_context(acc, "bad_context", bad_context)
    fixed_prompts = await capture_for_context(acc, "fixed_context", fixed_context)

    def section(label: str, path: str, prompts: list) -> str:
        parts = [f"{'=' * 100}\n{acc} -- {label} ({path})\n{'=' * 100}\n"]
        pass_names = ["Pass 1 (multi_prompts / country + modern-ancient)",
                      "Pass 2 (_extract_additional_fields / generalized JSON extraction)"]
        for i, p in enumerate(prompts):
            pass_name = pass_names[i] if i < len(pass_names) else f"Call #{i+1}"
            parts.append(f"\n----- {pass_name} -- FULL PROMPT ({len(p)} chars) -----\n")
            parts.append(p)
            parts.append("\n----- END -----\n")
        if not prompts:
            parts.append("\n(no calls captured -- query_document_info returned before invoking call_llm_api)\n")
        return "\n".join(parts)

    out_path = f"test-data/PRJNA976261/{acc}_prompt.txt"
    content = (
        f"Full LLM API prompts captured for {acc}\n"
        f"Captured by monkeypatching model.call_llm_api() (see capture_prompts_3samples.py) "
        f"around the same model.query_document_info() call additional_pipeline.py makes at "
        f"additional_pipeline.py:1151 (niche_cases=None, saveLinkFolder=None, "
        f"llm_api_function=model.call_llm_api, standardization_schema=None).\n\n"
        + section("bad_context", bad_path, bad_prompts)
        + "\n\n"
        + section("fixed_context", fixed_path, fixed_prompts)
    )
    with open(out_path, "w") as f:
        f.write(content)
    print(f"Wrote {out_path}\n")


async def main():
    for acc in SAMPLES:
        await process_sample(acc)


if __name__ == "__main__":
    asyncio.run(main())
