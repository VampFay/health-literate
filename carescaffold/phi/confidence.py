"""Confidence thresholding + review queue (spec §4.1).

Pipeline step 3: below threshold (start 0.85) → human review queue
rather than auto-redact. This module wraps the redactor and applies
the threshold, splitting detections into "auto-redact" vs "review queue".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from phi.redactor import RedactionSpan, detect

# Spec §4.1: confidence threshold starts at 0.85
# Tune against the adversarial corpus and document the chosen value
# + rationale in /docs/phase1_metrics.md.
DEFAULT_THRESHOLD = 0.85


@dataclass
class RedactionDecision:
    """The result of one redaction call: spans split into auto-redact vs review."""
    auto_redact: List[RedactionSpan]      # confidence >= threshold
    review_queue: List[RedactionSpan]    # confidence < threshold (don't auto-redact)
    all_spans: List[RedactionSpan]        # all detections (for audit log)


def classify(text: str, threshold: float = DEFAULT_THRESHOLD) -> RedactionDecision:
    """Detect PHI in text and split by confidence threshold.

    Args:
        text: the patient input to scan
        threshold: spans with confidence >= threshold are auto-redacted;
            below threshold go to the review queue

    Returns: RedactionDecision with auto_redact + review_queue + all_spans
    """
    all_spans = detect(text, threshold=0.0)  # get everything
    auto = [s for s in all_spans if s.confidence >= threshold]
    review = [s for s in all_spans if s.confidence < threshold]
    return RedactionDecision(
        auto_redact=auto,
        review_queue=review,
        all_spans=all_spans,
    )
