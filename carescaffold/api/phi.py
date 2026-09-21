"""POST /phi/redact endpoint — Phase 1 PHI redaction API.

Per spec §4.1 pipeline:
  1. Intercept patient input server-side before any LLM call
  2. Run Presidio + spaCy + custom ruleset
  3. Confidence threshold (0.85) → review queue for sub-threshold spans
  4. Replace confirmed identifiers with synthetic tokens, session-scoped
  5. Log every redaction decision to append-only hash-chained audit log

This endpoint exposes steps 1-5 as a single POST. The /scaffold endpoint
(spec §4.2) calls this internally before the LLM call.

Usage:
  POST /phi/redact
  {
    "session_id": "<uuid>",
    "text": "<patient input>"
  }

  Response:
  {
    "redacted_text": "<text with PHI replaced by tokens>",
    "spans": [...],
    "audit_entries": [...],
    "review_queue_count": N
  }
"""
from __future__ import annotations

import uuid
from typing import Annotated, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.auth import AuthSubject
from phi.confidence import classify, DEFAULT_THRESHOLD
from phi.tokens import TokenMap, redact_text
from phi.audit_log import append_entries
from phi.redactor import RedactionSpan

router = APIRouter()


class RedactRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    session_id: str | None = None  # auto-generated if None
    threshold: float = Field(DEFAULT_THRESHOLD, ge=0.0, le=1.0)


class SpanOut(BaseModel):
    start: int
    end: int
    text: str  # NOTE: this is the ORIGINAL span, not the token — needed for
                # the audit log + tests. Production deployments may want to
                # omit this from the response and only return the token.
    category: str
    confidence: float
    source: str
    action: str


class RedactResponse(BaseModel):
    redacted_text: str
    spans: List[SpanOut]
    audit_log_count: int
    review_queue_count: int
    session_id: str


@router.post("", response_model=RedactResponse)
@router.post("/", response_model=RedactResponse)
async def redact(req: RedactRequest, _: AuthSubject) -> RedactResponse:
    """Redact PHI from text per spec §4.1 pipeline."""
    session_id = req.session_id or str(uuid.uuid4())

    # Steps 2+3: detect + classify by confidence
    decision = classify(req.text, threshold=req.threshold)

    # Step 4: replace confirmed spans with synthetic tokens (session-scoped)
    token_map = TokenMap()
    redacted = redact_text(req.text, decision.auto_redact, token_map)

    # Build span output with action labels
    spans_out: List[SpanOut] = []
    actions: List[str] = []
    for s in decision.all_spans:
        if s.confidence >= req.threshold:
            action = "tokenized" if s in decision.auto_redact else "redacted"
        else:
            action = "queued_for_review"
        spans_out.append(SpanOut(
            start=s.start, end=s.end, text=s.text,
            category=s.category, confidence=s.confidence,
            source=s.source, action=action,
        ))
        actions.append(action)

    # Step 5: write audit log (hash-chained)
    audit_entries = await append_entries(session_id, decision.all_spans, actions)

    return RedactResponse(
        redacted_text=redacted,
        spans=spans_out,
        audit_log_count=len(audit_entries),
        review_queue_count=len(decision.review_queue),
        session_id=session_id,
    )
