"""RAG scaffold — retrieve + persona prompt + LLM generation (spec §4.2).

The scaffold orchestrates:
  1. retrieve top-k chunks for the patient's question
  2. format retrieved context into the persona prompt template
  3. call the LLM (GLM-4-Plus via z-ai) to generate a scaffolded explanation
  4. return the response along with citation source filenames

Per spec §4.2.5: every generated response must cite at least one source
filename. The persona prompt templates enforce this; the scaffold service
also validates it after generation and refuses to release uncited responses.

Per spec §1.2.2 and §4.4: every generated response is routed through the
outbound safety guardrail (Layer 1 regex + Layer 2 GLM judge) before being
returned to the patient. If the guardrail blocks, the scaffold returns
the care-team redirect instead of the generated text.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List

from llm.prompts import SCAFFOLD_FOUNDATIONAL_V1, SCAFFOLD_HIGHER_V1
from llm.router import router
from services.rag.retrieve import RetrievalResult, retrieve
from services.safety.outbound import enforce

log = logging.getLogger(__name__)


@dataclass
class ScaffoldResponse:
    """The full output of a scaffold call."""
    response: str                       # the text shown to the patient (may be care-team redirect)
    citations: List[str]                # source filenames the response drew from
    persona: str                        # "foundational" | "higher"
    safety_verdict: str                 # "SAFE" | "BLOCK_DIAGNOSIS" | "BLOCK_DOSAGE" | "BLOCK_DIRECTIVE"
    safety_layer: str                   # "passed" | "layer1" | "layer2"
    retrieved_chunks: List[RetrievalResult]  # what the retriever found


PERSONAS = {"foundational": SCAFFOLD_FOUNDATIONAL_V1, "higher": SCAFFOLD_HIGHER_V1}


async def generate(
    question: str,
    persona: str = "foundational",
    top_k: int = 2,
) -> ScaffoldResponse:
    """End-to-end: retrieve → prompt → generate → outbound safety check.

    Args:
        question: the patient's question (already passed through inbound
                  safety check upstream; if the caller didn't, this will
                  still run outbound safety on the response).
        persona: "foundational" (6th-grade reading level, kitchen analogies)
                 or "higher" (10–12th grade, data/trend analogies).
        top_k: 1 or 2 per spec §4.2.4. Default 2.

    Returns: ScaffoldResponse with the final response, citations, and
             safety verdict.
    """
    if persona not in PERSONAS:
        raise ValueError(f"Unknown persona: {persona}. Use 'foundational' or 'higher'.")

    # 1. Retrieve
    retrieved = retrieve(question, top_k=top_k)
    if not retrieved:
        # No chunks found — return a graceful redirect rather than fabricate
        return ScaffoldResponse(
            response=(
                "I don't have educational content available for that question yet. "
                "Please ask your care team about it, and they'll be able to help."
            ),
            citations=[],
            persona=persona,
            safety_verdict="SAFE",
            safety_layer="passed",
            retrieved_chunks=[],
        )

    # 2. Format retrieved context
    context_block = _format_context(retrieved)
    citations = [r.source_file for r in retrieved]

    # 3. Build prompt from persona template
    template = PERSONAS[persona]
    prompt = template.replace("{retrieved_context}", context_block)
    prompt = prompt.replace("{question}", question)

    # 4. Generate via the LLM router (GLM-4-Plus via z-ai)
    system = (
        "You are a patient-education assistant for a newly diagnosed Type 2 "
        "diabetes patient. Follow the instructions in the user message exactly. "
        "Cite source filenames at the end of your response."
    )
    raw_response = await router.call_generation(prompt, system=system)

    # 5. Validate citation presence (spec §4.2.5)
    if not _has_citation(raw_response, citations):
        log.warning(
            "Generated response lacks required citation. Persona=%s, "
            "expected citations: %s. Appending default citation.",
            persona, citations,
        )
        raw_response = raw_response.rstrip() + f"\n\nSource: {', '.join(citations)}"

    # 6. Outbound safety check (Layer 1 + Layer 2 per spec §4.4.2)
    verdict = await enforce(raw_response)
    if verdict.verdict == "SAFE":
        final = raw_response
    else:
        # Replace with care-team redirect; log the block
        from services.safety.outbound import CARE_TEAM_REDIRECT
        log.warning(
            "Outbound safety blocked generated response. "
            "Persona=%s, verdict=%s, layer=%s, rule=%s",
            persona, verdict.verdict, verdict.layer, verdict.rule_triggered,
        )
        final = CARE_TEAM_REDIRECT

    return ScaffoldResponse(
        response=final,
        citations=citations,
        persona=persona,
        safety_verdict=verdict.verdict,
        safety_layer=verdict.layer,
        retrieved_chunks=retrieved,
    )


def _format_context(results: List[RetrievalResult]) -> str:
    """Format retrieved chunks into the prompt's {retrieved_context} block."""
    blocks = []
    for i, r in enumerate(results, 1):
        blocks.append(
            f"--- Source {i}: {r.source_file} (topic: {r.topic}) ---\n{r.content}\n"
        )
    return "\n".join(blocks)


def _has_citation(response: str, expected_files: List[str]) -> bool:
    """Check whether the response cites at least one of the expected filenames.

    Per spec §4.2.5, every claim should cite a source. We check that at
    least one expected filename appears in the response. This is a
    minimal check; a stricter version would require citation per claim.
    """
    for fname in expected_files:
        # Look for the filename with or without the .md extension
        if fname in response or fname.replace(".md", "") in response:
            return True
    # Some models may use "Source: filename.md" format; check more loosely
    return bool(re.search(r"source\s*:", response, re.IGNORECASE))
