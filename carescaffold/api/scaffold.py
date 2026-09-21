"""POST /scaffold endpoint (spec §4.2.5).

Accepts a patient question + persona and returns a scaffolded, cited,
safety-checked educational response.

Pipeline:
  1. Inbound safety (Phase 2 — already enforced upstream in the caller)
  2. RAG retrieve (Phase 3)
  3. Persona-templated LLM generation (Phase 3)
  4. Outbound safety Layer 1 + Layer 2 (Phase 2)
  5. Return response + citations + safety verdict
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.auth import AuthSubject
from services.rag.scaffold import ScaffoldResponse, generate
from services.safety.inbound import ESCALATION_MESSAGE, screen

log = logging.getLogger(__name__)

router = APIRouter()


class ScaffoldRequest(BaseModel):
    """Request body for POST /scaffold."""
    question: str = Field(..., min_length=1, max_length=2000,
                         description="The patient's question (already redacted if Phase 1 is wired up).")
    persona: Literal["foundational", "higher"] = Field(
        default="foundational",
        description="Health-literacy persona for the response.",
    )
    top_k: int = Field(default=2, ge=1, le=4,
                       description="Number of chunks to retrieve (spec §4.2.4: 1 or 2).")


class ScaffoldResponseBody(BaseModel):
    """Response body."""
    response: str
    citations: list[str]
    persona: str
    safety_verdict: str
    safety_layer: str
    retrieved_chunks: list[dict]
    escalated: bool = False
    escalation_message: str | None = None


@router.post("", response_model=ScaffoldResponseBody)
@router.post("/", response_model=ScaffoldResponseBody)
async def scaffold_endpoint(req: ScaffoldRequest, _: AuthSubject) -> ScaffoldResponseBody:
    """Generate a scaffolded educational response for a T2D patient."""
    # Phase 2 inbound safety check (spec §1.2.4 — checked first, before
    # anything else in the pipeline). If emergency is detected, return
    # the fixed escalation message; do NOT call the LLM.
    inbound = screen(req.question)
    if inbound.matched:
        log.warning(
            "Inbound safety escalation: category=%s, span=%r, question=%r",
            inbound.pattern_category, inbound.matched_span, req.question[:100],
        )
        return ScaffoldResponseBody(
            response=ESCALATION_MESSAGE,
            citations=[],
            persona=req.persona,
            safety_verdict="SAFE",  # escalation bypasses outbound safety
            safety_layer="inbound",
            retrieved_chunks=[],
            escalated=True,
            escalation_message=ESCALATION_MESSAGE,
        )

    # Standard pipeline: retrieve → generate → outbound safety
    try:
        result: ScaffoldResponse = await generate(req.question, persona=req.persona, top_k=req.top_k)
    except Exception as e:
        log.error("Scaffold generation failed: %s", e)
        return ScaffoldResponseBody(
            response=(
                f"I had trouble generating a response right now — this might be a "
                f"connectivity issue with the AI service. Please try again in a moment. "
                f"If the problem persists, check that the z-ai CLI is installed and "
                f"the RAG education files are loaded. (Technical: {str(e)[:200]})"
            ),
            citations=[],
            persona=req.persona,
            safety_verdict="SAFE",
            safety_layer="error",
            retrieved_chunks=[],
        )
    return ScaffoldResponseBody(
        response=result.response,
        citations=result.citations,
        persona=result.persona,
        safety_verdict=result.safety_verdict,
        safety_layer=result.safety_layer,
        retrieved_chunks=[
            {
                "source_file": r.source_file,
                "topic": r.topic,
                "similarity": r.similarity,
            }
            for r in result.retrieved_chunks
        ],
    )
