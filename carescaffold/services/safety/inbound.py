"""Inbound emergency detection (spec §4.4.1).

This is the FIRST check in the entire CareScaffold pipeline. On any match,
the system bypasses RAG/generation entirely, returns a fixed escalation
message, and logs to `safety_events` with event_type='emergency_escalation'.

Spec §1.2.4: "Emergency-symptom detection bypasses generation entirely
and is checked before anything else in the pipeline."

Detection is regex/keyword based — fast, deterministic, no LLM call needed.
This is deliberate: in an emergency we cannot wait for a model call, and
we cannot risk the model deciding 'this isn't really an emergency.'
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# ── Pattern registry ────────────────────────────────────────────────
# Three categories per spec §4.4.1: chest pain, severe hypoglycemia,
# self-harm. Patterns are intentionally broad — false-positives here
# are acceptable (we escalate); false-negatives are not (we miss a
# real emergency). Reviewer with healthcare background should audit
# the pattern list before any portfolio publication.

CHEST_PAIN_PATTERNS: List[str] = [
    r"\bchest pain\b",
    r"\bchest hurts\b",
    r"\bchest tightness\b",
    r"\bchest is tight\b",
    r"\bchest feels tight\b",
    r"\bchest pressure\b",
    r"\bpressure (in|on) (my )?chest\b",
    r"\bcrushing (chest|pain)\b",
    r"\bheavy (pressure|weight) (in|on|at) (the )?(middle of )?(my )?chest\b",
    r"\bpain (radiating|spreading) (to|into) (my )?(left )?(arm|jaw|shoulder|neck)\b",
    r"\bchest hurts when I breathe\b",
]

HYPOGLYCEMIA_PATTERNS: List[str] = [
    # Severe hypoglycemia language per spec §4.4.1
    r"\bcan'?t wake (him|her|them|my (husband|wife|mom|dad|son|daughter|child)) up\b",
    r"\bcan'?t wake up\b",
    r"\bpassed out\b",
    r"\bloss of consciousness\b",
    r"\bI (think I )?had a seizure\b",
    r"\bhaving a seizure\b",
    r"\bseizure\b",
    # Glucose reading + cognitive impairment
    r"\bsugar (is|was|reads) (3[0-9]|4[0-5])\b",       # 30-45 mg/dL = severe
    r"\bsugar\b.{0,40}\b(3[0-9]|4[0-5])\b",            # "sugar ... 38" with intervening words
    r"\bglucose (is|was) (3[0-9]|4[0-5])\b",
    # Symptom combos that indicate neuroglycopenia
    r"\bconfused? (and|&) (shaky|sweaty|dizzy|can'?t think)\b",
    r"\bcan'?t think straight\b",
    r"\bcan'?t remember (my|anything|my own)\b",
    # Confusion / unresponsiveness alone (may be neuroglycopenic)
    r"\b(I|I'm|feeling) confused?\b.*\b(dizzy|sweaty|shaky|won'?t respond|unresponsive)\b",
    r"\b(he|she|they|child)('?s)? (is|are) confused?\b.*\b(won'?t respond|unresponsive)\b",
    # Confusion + unresponsive regardless of subject (covers third-person reports)
    r"\bconfused?\b.{0,40}\bwon['']?t respond\b",
    r"\bconfused?\b.{0,40}\bunresponsive\b",
    # Insulin + acute symptoms (likely hypoglycemia context)
    r"\btook (my )?insulin\b.*\b(dizzy|confused|sweaty|shaky|passed out|can'?t think)\b",
    # Glucose < 50 with symptoms
    r"\bsugar (is|was|reads) (4[6-9]|50)\b.*\b(confused|dizzy|shaky|sweaty|passed out)\b",
]

SELF_HARM_PATTERNS: List[str] = [
    # Direct ideation
    r"\bI want to (die|hurt myself|end it|kill myself)\b",
    r"\bI (want to |wanna )?end (it all|my life)\b",
    r"\bI don'?t want to (be here|wake up|live)\b",
    r"\bI (just )?want it (all )?to stop\b",
    r"\b(everyone|everybody) (would|will) be better off (without me|if I)\b",
    r"\bI (have|have a) plan\b.*\b(hurt|end|kill|die)\b",
    # Indirect / soft ideation
    r"\bdon'?t want to wake up (tomorrow|anymore)\b",
    r"\b(thinking of|thinking about) (ending it|ending my life|hurting myself|not (being here|waking up))\b",
    r"\b(taking|took) too many pills\b.*\b(die|wake up|tomorrow|anymore)\b",
    r"\bstop taking (my )?insulin\b.*\b(die|don'?t want|anymore|not (deal|live))\b",
    r"\bnot taking (my )?insulin\b.*\b(die|don'?t want|anymore|not (deal|live))\b",
    # Pediatric-adjacent
    r"\bdon'?t want to live (with|like this)\b",
    # Treatment cessation as self-harm method
    r"\bnot (taking|going to take|gonna take) (my )?insulin\b.*\b(deal|anymore|don'?t want)\b",
]

ALL_PATTERNS = (
    [("chest_pain", p) for p in CHEST_PAIN_PATTERNS]
    + [("severe_hypoglycemia", p) for p in HYPOGLYCEMIA_PATTERNS]
    + [("self_harm", p) for p in SELF_HARM_PATTERNS]
)

# Pre-compile for speed (called on every inbound message)
_COMPILED = [(category, re.compile(p, re.IGNORECASE)) for category, p in ALL_PATTERNS]


@dataclass
class EmergencyMatch:
    """Result of an emergency-screen check."""
    matched: bool
    pattern_category: str | None = None  # "chest_pain" | "severe_hypoglycemia" | "self_harm"
    matched_pattern: str | None = None   # The regex pattern that fired
    matched_span: str | None = None      # The substring that matched
    input_text: str = ""                  # Echo of input for audit log


def screen(text: str) -> EmergencyMatch:
    """Check inbound text for emergency indicators.

    Returns EmergencyMatch on the first hit. Order of evaluation is
    chest_pain → severe_hypoglycemia → self_harm (per spec §4.4.1 listing
    order, though clinical priority is roughly the same).
    """
    if not text or not text.strip():
        return EmergencyMatch(matched=False, input_text=text or "")

    for category, pattern_obj in _COMPILED:
        m = pattern_obj.search(text)
        if m:
            return EmergencyMatch(
                matched=True,
                pattern_category=category,
                matched_pattern=pattern_obj.pattern,
                matched_span=m.group(0),
                input_text=text,
            )
    return EmergencyMatch(matched=False, input_text=text)


# ── Fixed escalation message ──────────────────────────────────────
# Spec §4.4.1: "return a fixed escalation string (author one clear,
# non-alarmist sentence directing the patient to contact their care
# team or emergency services)".

ESCALATION_MESSAGE = (
    "This may need immediate attention — please contact your care team "
    "or your local emergency services now. If you are in the US, you can "
    "dial 911. If you are thinking about harming yourself, you can call "
    "or text 988 (Suicide & Crisis Lifeline) any time, day or night."
)


def should_bypass_generation(match: EmergencyMatch) -> bool:
    """Spec §1.2.4 — emergency detection bypasses generation entirely."""
    return match.matched


def rule_label(match: EmergencyMatch) -> str:
    """Stable string for the safety_events.rule_or_layer_triggered column."""
    if not match.matched:
        return "inbound:none"
    return f"inbound:{match.pattern_category}"
