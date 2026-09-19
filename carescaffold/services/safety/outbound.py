"""Outbound scope enforcement — Layer 1: regex/keyword pre-filter (spec §4.4.2).

Layer 1 runs FIRST on every generated response. It catches the obvious
cases fast and cheap. Anything Layer 1 flags never reaches Layer 2.

Anything Layer 1 does NOT flag MUST go through Layer 2 (the Claude Haiku
4.5 judge call) before release to the patient — see outbound_judge_v1.txt
and the Phase 2 implementation in /services/safety/outbound.py.

Per spec §4.4.2 Layer 1:

  Block patterns:
    1. Dosage: any numeric value adjacent to a unit token (mg, mcg, units, ml)
       within the same sentence as a medication name.
    2. Diagnosis statement: small fixed list of patterns ("you have",
       "your diagnosis is", "this means you have") followed by a condition
       name.
    3. Directive: directive verbs ("stop taking", "start taking", "double
       your dose", "skip your dose", "increase your", "decrease your")
       adjacent to a medication name.

On any block: replace the response with the fixed care-team redirect
message and log to safety_events with the specific rule that triggered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple


# ── Common medication names ───────────────────────────────────────
# Curated small list — Phase 3 RAG content names metformin explicitly;
# this list is the "known med name" reference for Layer 1 patterns. A
# reviewer with healthcare background should audit before publication.
MEDICATION_NAMES: List[str] = [
    "metformin",
    "glipizide", "glyburide", "glimepiride",      # sulfonylureas
    "insulin",                                     # broad; covers all insulin types
    "januvia", "sitagliptin",
    "jardiance", "empagliflozin",
    "farxiga", "dapagliflozin",
    "ozempic", "semaglutide",
    "victoza", "liraglutide",
    "trulicity", "dulaglutide",
    "pioglitazone", "actos",
    "acarbose",
    "byetta", "exenatide",
    "basal insulin", "bolus insulin",
    "lispro", "aspart", "glulisine",               # rapid insulins
    "glargine", "detemir", "degludec",              # long-acting insulins
    "humalog", "novolog", "lantus", "tresiba", "levemir", "toujeo",
]

# Build a single alternation regex fragment matching any known medication
# word-boundaried, case-insensitive.
_MED_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in MEDICATION_NAMES) + r")\b",
    re.IGNORECASE,
)

# Unit tokens per spec §4.4.2: mg, mcg, units, ml
_UNIT_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|units?|ml|milligrams?|micrograms?|milliliters?)\b",
    re.IGNORECASE,
)

# ── Layer 1 patterns ────────────────────────────────────────────
# Each tuple: (rule_id, description, compiled_regex)

# Pattern 1: dosage — number + unit near (within ~120 chars of) a med name.
# Spec: "any numeric value adjacent to a unit token within the same sentence
#        as a medication name."
# Implementation: split by sentence; in each sentence, check both patterns.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Pattern 2: diagnostic statements — directed AT THE PATIENT, not abstract
# Per spec §11.4 (generalize, don't patch): educational content like
# "Type 2 diabetes means your body doesn't use insulin as effectively"
# is SAFE — it's abstract, not patient-directed. Layer 1 must catch
# only statements that direct a diagnosis at the patient.
#
# Heuristic: the diagnosis phrase must be immediately followed by a
# condition name (diabetes, prediabetes, hypoglycemia, hyperglycemia,
# metabolic syndrome, etc.). This avoids matching "you have options" or
# "you have a meal plan".
_CONDITION_NAMES = (
    r"(?:type\s*[12]\s*diabetes|diabetes|pre-?diabetes?|pre-?diabetic|"
    r"diabetic|hypoglycemia|hyperglycemia|metabolic\s+syndrome|"
    r"insulin\s+resistance|high\s+blood\s+sugar|low\s+blood\s+sugar)"
)

_DIAGNOSIS_PATTERNS: List[Tuple[str, str]] = [
    # "you have [condition]" — directed at the patient
    ("dx_you_have",      r"\byou have\b\s+" + _CONDITION_NAMES),
    # "your diagnosis is [condition]"
    ("dx_your_diagnosis_is", r"\byour diagnosis is\b\s+" + _CONDITION_NAMES),
    # "this means you have [condition]"
    ("dx_this_means",    r"\bthis means (you have|that you have)\b\s+" + _CONDITION_NAMES),
    # "this indicates/confirmed/suggests you have [condition]"
    ("dx_indicates",     r"\b(this )?indicates (you have|that you have)\b\s+" + _CONDITION_NAMES),
    ("dx_confirms",      r"\b(this )?confirms (you have|that you have)\b\s+" + _CONDITION_NAMES),
    ("dx_suggests",      r"\b(this )?suggests (you have|that you have)\b\s+" + _CONDITION_NAMES),
    # "you are diabetic/prediabetic" — directed at the patient
    ("dx_are_diabetic",  r"\byou (are|are now)\s+(pre-?diabetic|diabetic|pre-?diabetic)\b"),
    # "you are no longer diabetic" — directed at the patient
    ("dx_no_longer",      r"\byou (are|are now)\s+no longer\s+(pre-?diabetic|diabetic|pre-?diabetes|diabetes)\b"),
]

# Pattern 3: directive verbs near medication name
_DIRECTIVE_PATTERNS: List[Tuple[str, str]] = [
    ("dir_stop_taking",  r"\bstop taking\b"),
    ("dir_stop_all",     r"\bstop all\b.*\b(meds|medication|metformin|insulin|diabetes)\b"),
    ("dir_start_taking", r"\bstart taking\b"),
    ("dir_start_therapy", r"\bstart (insulin|metformin|glipizide|jardiance|ozempic)\b.*\b(therapy|treatment|regimen)?\b"),
    ("dir_double",       r"\bdouble (your|the|this)?\s*\b(?!dose\b).{0,30}\bdose\b|\bdouble (your|the) (metformin|insulin|meds|medication|pill)\b"),
    ("dir_skip",         r"\bskip\b.*\b(your|the|tomorrow'?s|next)?\s*\b(dose|pill|metformin|insulin)\b"),
    ("dir_increase",     r"\bincrease (your|the) (dose|metformin|insulin)\b"),
    ("dir_decrease",     r"\bdecrease (your|the) (dose|metformin|insulin)\b"),
    ("dir_cut_in_half",  r"\bcut (your|the) (metformin|insulin|pill|dose) (in half|in two)\b|\bhalf a (metformin|insulin)?\s*\b(pill|dose)\b"),
    ("dir_take_more",    r"\btake more\b"),
    ("dir_take_less",    r"\btake less\b"),
    ("dir_take_extra",   r"\btake (an? )?extra\b.*\b(metformin|insulin|pill|dose|mg|units)\b"),
    ("dir_switch_to",    r"\bswitch (you |to )?(to|from)\b.*\b(metformin|insulin|glipizide|jardiance|ozempic)\b"),
    ("dir_add",          r"\badd (a |another )?\b.*\b(metformin|insulin|glipizide|jardiance|ozempic)\b.*\b(to|your regimen)\b"),
    ("dir_discontinue",  r"\bdiscontinue\b.*\b(metformin|insulin|glipizide|jardiance|ozempic)\b"),
    ("dir_take_timing",  r"\b(take|taking)\b.*\b(metformin|insulin)\b.*\b(at night|in the morning|before meals|after meals|with food|on an empty stomach)\b.*\b(instead|rather than|change|switch)\b"),
    ("dir_threshold_start", r"\b(when|if) (your |my |a1c|glucose|sugar)\b.{0,30}\b(reach|reaches|hits|exceeds|is (over|above))\b.{0,30}\b(start|begin|stop|increase|decrease|switch)\b"),
]

# Compile all diagnosis + directive patterns
_COMPILED_DIAGNOSIS = [(rid, re.compile(p, re.IGNORECASE)) for rid, p in _DIAGNOSIS_PATTERNS]
_COMPILED_DIRECTIVE = [(rid, re.compile(p, re.IGNORECASE)) for rid, p in _DIRECTIVE_PATTERNS]


@dataclass
class Layer1Verdict:
    """Result of a Layer 1 check on a candidate generated response."""
    verdict: str           # "SAFE" | "BLOCK_DOSAGE" | "BLOCK_DIAGNOSIS" | "BLOCK_DIRECTIVE"
    rule_triggered: str | None = None
    matched_span: str | None = None
    response_text: str = ""


def _sentence_wise_dosage_check(text: str) -> tuple[bool, str | None, str | None]:
    """Check each sentence for: number+unit AND medication name in the same sentence.

    Returns (matched, rule_id, matched_span).
    """
    sentences = _SENTENCE_SPLIT.split(text)
    for sent in sentences:
        unit_match = _UNIT_RE.search(sent)
        med_match = _MED_RE.search(sent)
        if unit_match and med_match:
            # Reconstruct the matched span: include both pieces for the audit log
            span = f"[med={med_match.group(0)}] [dose={unit_match.group(0)}]"
            return True, "layer1_dosage_med_near_unit", span
    return False, None, None


def check(response_text: str) -> Layer1Verdict:
    """Run Layer 1 on the candidate response.

    Per spec §4.4.2, runs the three pattern groups in order. First hit
    wins (returns the first triggered block; doesn't continue checking).
    """
    if not response_text or not response_text.strip():
        return Layer1Verdict(verdict="SAFE", response_text=response_text or "")

    # 1. Dosage — number + unit AND med name in same sentence
    matched, rule, span = _sentence_wise_dosage_check(response_text)
    if matched:
        return Layer1Verdict(
            verdict="BLOCK_DOSAGE",
            rule_triggered=rule,
            matched_span=span,
            response_text=response_text,
        )

    # 2. Diagnosis statements
    for rule_id, pattern_obj in _COMPILED_DIAGNOSIS:
        m = pattern_obj.search(response_text)
        if m:
            return Layer1Verdict(
                verdict="BLOCK_DIAGNOSIS",
                rule_triggered=f"layer1:{rule_id}",
                matched_span=m.group(0),
                response_text=response_text,
            )

    # 3. Directive patterns
    for rule_id, pattern_obj in _COMPILED_DIRECTIVE:
        m = pattern_obj.search(response_text)
        if m:
            return Layer1Verdict(
                verdict="BLOCK_DIRECTIVE",
                rule_triggered=f"layer1:{rule_id}",
                matched_span=m.group(0),
                response_text=response_text,
            )

    return Layer1Verdict(verdict="SAFE", response_text=response_text)


# ── Fixed care-team redirect ─────────────────────────────────────
# Spec §4.4.2: "replace with a fixed care-team redirect message".
# Kept short, non-alarmist, and explicit about what the system is not
# going to do.

CARE_TEAM_REDIRECT = (
    "I'm not able to give specific guidance on dosages, diagnoses, or "
    "treatment changes — those decisions need to come from your care team. "
    "Please reach out to your doctor, nurse, or pharmacist with this "
    "question. If something feels urgent, contact your care team or "
    "emergency services right away."
)


def layer_label(verdict: Layer1Verdict) -> str:
    """Stable identifier for the safety_events.rule_or_layer_triggered column."""
    if verdict.verdict == "SAFE":
        return "outbound:layer1:passed"
    return f"outbound:layer1:{verdict.verdict.lower()}:{verdict.rule_triggered}"


# ── Two-layer orchestration ─────────────────────────────────────
# Spec §4.4.2: "This was previously unspecified. It is now explicitly
# two layers, run in sequence on every generated response before it
# reaches the patient."

@dataclass
class OutboundVerdict:
    """Combined Layer 1 + Layer 2 verdict."""
    verdict: str           # "SAFE" | "BLOCK_DIAGNOSIS" | "BLOCK_DOSAGE" | "BLOCK_DIRECTIVE"
    layer: str              # "layer1" | "layer2" | "passed" (when verdict is SAFE)
    rule_triggered: str | None = None
    matched_span: str | None = None
    response_text: str = ""


async def enforce(response_text: str, judge_fn=None) -> OutboundVerdict:
    """Two-layer outbound enforcement pipeline.

    Args:
        response_text: the candidate generated response.
        judge_fn: async callable (response_text -> verdict_string).
                  If None, uses the LLM router's call_safety_judge.

    Per spec §4.4.2: Layer 1 runs first; if it blocks, return immediately.
    Otherwise, Layer 2 (the Claude Haiku 4.5 judge) runs.
    """
    l1 = check(response_text)
    if l1.verdict != "SAFE":
        return OutboundVerdict(
            verdict=l1.verdict,
            layer="layer1",
            rule_triggered=l1.rule_triggered,
            matched_span=l1.matched_span,
            response_text=response_text,
        )

    # Layer 1 passed; run Layer 2 (the judge).
    if judge_fn is None:
        from llm.router import router as default_router
        judge_fn = default_router.call_safety_judge

    try:
        l2_verdict = await judge_fn(response_text)
    except RuntimeError as e:
        # API key missing or judge unavailable — fail safe per Layer 2 contract.
        # Caller (e.g., the FastAPI endpoint) should surface this clearly.
        return OutboundVerdict(
            verdict="BLOCK_DIRECTIVE",
            layer="layer2",
            rule_triggered="layer2:judge_unavailable",
            matched_span=str(e)[:200],
            response_text=response_text,
        )

    if l2_verdict == "SAFE":
        return OutboundVerdict(
            verdict="SAFE",
            layer="passed",
            rule_triggered=None,
            matched_span=None,
            response_text=response_text,
        )

    return OutboundVerdict(
        verdict=l2_verdict,
        layer="layer2",
        rule_triggered=f"layer2:judge:{l2_verdict.lower()}",
        matched_span=None,
        response_text=response_text,
    )
