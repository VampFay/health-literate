"""PHI redaction pipeline (Phase 1) — spec §4.1.

Pipeline (spec §4.1):
  1. Intercept all patient input server-side before any LLM call
  2. Run Presidio + spaCy NER + supplementary regex/dictionary layer
     tuned for K-12-specific identifiers (in this portfolio context,
     T2D-patient-specific identifiers — see deviation note below)
  3. Confidence threshold (start 0.85): below threshold → human review
     queue rather than auto-redact
  4. Replace confirmed identifiers with synthetic tokens, session-scoped
  5. Log every redaction decision to append-only hash-chained audit log

The 18 HIPAA Safe Harbor categories (45 CFR 164.514(b)(2)):
  1. Names
  2. Geographic subdivisions smaller than a state
  3. All elements of dates (except year) + ages > 89
  4. Telephone numbers
  5. Fax numbers
  6. Email addresses
  7. Social Security numbers
  8. Medical record numbers
  9. Health plan beneficiary numbers
  10. Account numbers
  11. Certificate/license numbers
  12. Vehicle identifiers and serial numbers
  13. Device identifiers and serial numbers
  14. URLs
  15. IP addresses
  16. Biometric identifiers
  17. Full-face photographs and comparable images
  18. Any other unique identifying number, characteristic, or code

Implementation note: Presidio's defaults cover categories 1, 2, 4, 6, 7,
15 well. Categories 3 (dates/ages), 5 (fax), 8-13 (MRN, beneficiary,
account, license, vehicle, device), 14 (URLs), 16-18 (biometric, photos,
unique codes) need custom patterns. This module provides both.

Deviation note (spec §4.1): the spec text says "K-12-specific identifiers"
because the original master spec was for an EdTech product. CareScaffold
adapted that text for healthcare; here we substitute "T2D-patient-specific
identifiers" where appropriate.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

# ── Try to import Presidio + spaCy; degrade gracefully if missing ──
# This module is structured to work even without Presidio installed,
# using only the custom ruleset. When Presidio is available, we add
# its recognizer results on top.

_PRESIDIO_AVAILABLE = False
_SPACY_AVAILABLE = False
try:
    # Presidio's predefined_recognizers module doesn't export all recognizer
    # classes via its __init__.py — different versions expose different ones.
    # We import only what's reliably available + the core AnalyzerEngine.
    from presidio_analyzer import (
        AnalyzerEngine,
        RecognizerRegistry,
        PatternRecognizer,
        Pattern,
    )
    # Don't import individual recognizer classes here — AnalyzerEngine
    # loads them via its registry.
    _PRESIDIO_AVAILABLE = True
except ImportError:
    pass

try:
    import spacy
    _SPACY_AVAILABLE = True
except ImportError:
    pass

log = logging.getLogger(__name__)


# ── Custom patterns for categories Presidio doesn't cover well ──

# 8. Medical record numbers (spec §4.1: "MRN-123456" + context window)
MRN_PATTERNS = [
    # Explicit MRN prefix
    (r"\bMRN[:\s-]?\d{6,10}\b", "mrn", 0.95),
    # "record number", "chart number" nearby + digits
    (r"\b(?:record|chart|medical record|patient ID|hospital ID)[:\s-]?[A-Z]*\d{5,10}\b", "mrn", 0.85),
    # Just 6-8 digits in a context that mentions records
    (r"\b\d{8}\b", "mrn", 0.55),  # low confidence alone; needs context
    # Alphanumeric MRN like ABC-12345
    (r"\b[A-Z]{2,4}-\d{4,8}\b", "mrn", 0.65),  # could also be license/device
]

# 9. Health plan beneficiary numbers
BENEFICIARY_PATTERNS = [
    (r"\b(?:BCBS|Blue Cross|Aetna|Cigna|United|Humana|Kaiser|Medicare|Medicaid|HMO|PPO)[:\s-]?[A-Z0-9-]{5,20}\b", "beneficiary", 0.92),
    (r"\b(?:member ID|policy number|health plan ID|insurance ID)[:\s-]?[A-Z0-9-]{5,20}\b", "beneficiary", 0.90),
    (r"\b[A-Z]{1,2}\d{6,10}\b", "beneficiary", 0.55),  # e.g. M1234567, 1AB2CD3EF4
]

# 10. Account numbers
ACCOUNT_PATTERNS = [
    (r"\b\d{10,16}\b", "account", 0.55),  # 10-16 digit number — could be account/cc
    (r"\b(?:account|acct|AC)[:\s-]?\d{5,16}\b", "account", 0.90),
    (r"\b(?:routing|ACH)[:\s-]?\d{9}\b", "account", 0.85),  # routing is 9 digits
]

# 11. Certificate/license numbers
LICENSE_PATTERNS = [
    (r"\b(?:DL|driver'?s? license|license|RN|MD|PHM)[:\s-]?[A-Z]{0,2}-?\d{4,10}\b", "license", 0.85),
    (r"\b[A-Z]-\d{6,8}\b", "license", 0.55),  # D1234567, S-5551234
]

# 12. Vehicle identifiers (VIN is 17 chars; license plates vary)
VEHICLE_PATTERNS = [
    # VIN: 17 chars, no I/O/Q, mixed letters + numbers
    (r"\b[A-HJ-NPR-Z0-9]{17}\b", "vehicle", 0.75),  # VIN format
    (r"\b(?:VIN|license plate|plate)[:\s-]?[A-Z0-9-]{4,12}\b", "vehicle", 0.85),
    (r"\b\d[A-Z]{3}\d{4}\b", "vehicle", 0.55),  # plate like 7ABC1234
]

# 13. Device identifiers (spec §4.1 edge case: "my Dexcom G7" referenced casually)
DEVICE_DICTIONARY = [
    "Dexcom G7", "Dexcom G6", "Dexcom",
    "Omnipod 5", "Omnipod Dash", "Omnipod",
    "Freestyle Libre", "Libre 3", "Libre 14 day", "Libre 2",
    "Medtronic 670G", "Medtronic 770G", "Medtronic MiniMed", "Medtronic",
    "Tandem t:slim X2", "Tandem", "t:slim",
    "Apple Watch",
    "Abbott",
]
DEVICE_SERIAL_PATTERNS = [
    (r"\bSN[-:\s]?[A-Z]{2,4}-?\d{4,10}\b", "device", 0.92),  # SN-DM1234567
    (r"\bDEV[-:\s]?[A-Z0-9-]{6,20}\b", "device", 0.92),
]

# 14. URLs (Presidio has URL recognizer but may miss some forms)
URL_PATTERNS = [
    (r"\bhttps?://[^\s]+", "url", 0.98),
    (r"\bwww\.[^\s]+", "url", 0.92),
    (r"\b[a-z0-9-]+\.(?:com|org|net|gov|edu|example|io)(?:/[^\s]*)?", "url", 0.75),
]

# 15. IP addresses
IP_PATTERNS = [
    (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "ip", 0.85),  # IPv4
    (r"\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){7}\b", "ip", 0.85),  # IPv6
]

# 16. Biometric identifiers (keyword-based; Presidio doesn't catch these)
BIOMETRIC_KEYWORDS = [
    "fingerprint", "fingerprints", "finger print", "finger prints",
    "voiceprint", "voice print", "voiceprint ID",
    "retinal scan", "retina scan", "retinal scan was used",
    "iris scan", "iris scan failed", "iris recognition",
    "faceprint", "face print", "facial recognition",
    "palm print", "palmprint",
    "biometric", "biometrics",
]

# 17. Photos (file references + URLs that look like patient images)
PHOTO_PATTERNS = [
    (r"\b/[^\s]*\.(?:jpg|jpeg|png|gif|webp|bmp)\b", "photo", 0.85),
    (r"\b(?:photo|image|selfie|headshot|scan)[:\s-]?[^\s]+\.(?:jpg|jpeg|png|gif|webp)\b", "photo", 0.95),
]

# 18. Unique codes
UNIQUE_CODE_PATTERNS = [
    (r"\b(?:PAT|CLN|CASE|STU|TRK|APT|SP|TK|EMP)[:\s-]?[A-Z0-9-]{3,20}\b", "unique_code", 0.92),
]

# 3. Dates (spec §4.1: relative dates like "three days after my mom's surgery")
DATE_PATTERNS = [
    # Full date formats
    (r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b", "dates", 0.95),
    (r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", "dates", 0.95),  # MM/DD/YYYY
    (r"\b\d{4}-\d{2}-\d{2}\b", "dates", 0.95),  # ISO date
    # Date + ordinal
    (r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2}(?:st|nd|rd|th)?\b", "dates", 0.90),
    # Relative dates (spec §4.1 edge case)
    (r"\b(?:last|next|this|previous|coming|upcoming)\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", "dates", 0.85),
    (r"\b(?:mid-|early |late )?(?:January|February|March|April|May|June|July|August|September|October|November|December)\b", "dates", 0.70),
    (r"\b\d+\s+days?\s+(?:after|before|since|ago)\b.*?(?:surgery|operation|appointment|visit|shot|injection|dose|metformin|insulin)\b", "dates", 0.80),  # relative date with medical context
    # Ages > 89
    (r"\b(?:9[0-9]|1[0-4][0-9]|150)\b\s*(?:years|y/?o|year old|years old)?", "dates", 0.60),  # only ages 90+
]

# 5. Fax (vs phone — needs "fax" context)
FAX_PATTERNS = [
    (r"\bfax[:\s-]?\+?[\d\s\-.()]{7,15}\b", "fax", 0.95),
    (r"\b\d{3}\.\d{3}\.\d{4}\b", "fax", 0.50),  # could be phone or fax — low confidence alone
]

# 4. Phone (custom high-confidence patterns; Presidio gives ~0.75 for these
# which is below our 0.85 threshold, so we add explicit patterns)
PHONE_PATTERNS = [
    # US format with parens: (555) 123-4567 — very clearly a phone
    (r"\(\d{3}\)\s*\d{3}[-.]?\d{4}\b", "phone", 0.95),
    # US format with dashes: 555-123-4567
    (r"\b\d{3}[-.]\d{3}[-.]\d{4}\b", "phone", 0.90),
    # US format with country code: +1-555-123-4567
    (r"\+1[-.\s]?\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b", "phone", 0.95),
    # 10 digits no separators: 5551234567 (less certain — could be other ID)
    (r"\b\d{10}\b", "phone", 0.60),
    # International: +44 20 7946 0958
    (r"\+\d{1,3}[\s-]?\d{1,4}[\s-]?\d{3,4}[\s-]?\d{3,4}\b", "phone", 0.85),
]


# ── Compile all custom patterns ──────────────────────────────────
ALL_CUSTOM_PATTERNS: list[tuple[re.Pattern, str, float]] = []
for patterns_list, label in [
    (MRN_PATTERNS, "mrn"),
    (BENEFICIARY_PATTERNS, "beneficiary"),
    (ACCOUNT_PATTERNS, "account"),
    (LICENSE_PATTERNS, "license"),
    (VEHICLE_PATTERNS, "vehicle"),
    (DEVICE_SERIAL_PATTERNS, "device"),
    (URL_PATTERNS, "url"),
    (IP_PATTERNS, "ip"),
    (PHOTO_PATTERNS, "photo"),
    (UNIQUE_CODE_PATTERNS, "unique_code"),
    (DATE_PATTERNS, "dates"),
    (FAX_PATTERNS, "fax"),
    (PHONE_PATTERNS, "phone"),
]:
    for pat, cat, conf in patterns_list:
        ALL_CUSTOM_PATTERNS.append((re.compile(pat, re.IGNORECASE), cat, conf))

# Device dictionary — compiled for fast matching
_DEVICE_RE = re.compile(
    r"\b(" + "|".join(re.escape(d) for d in DEVICE_DICTIONARY) + r")\b",
    re.IGNORECASE,
)

# Biometric keywords — compiled
_BIOMETRIC_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in BIOMETRIC_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


# ── Per-span detection result ────────────────────────────────────
@dataclass
class RedactionSpan:
    """One detected PHI span."""
    start: int
    end: int
    text: str
    category: str   # one of the 18 categories (mapped to short names)
    confidence: float
    source: str = "custom"  # "presidio" | "spacy" | "custom"


# ── Main redactor ───────────────────────────────────────────────
def detect(text: str, threshold: float = 0.0) -> List[RedactionSpan]:
    """Detect all PHI spans in text.

    Args:
        text: the input text to scan
        threshold: only return spans with confidence >= threshold. Use 0.0
            to return all detections (the audit log + review queue logic
            in /phi/confidence.py decides what to do with low-confidence
            ones — the redactor itself just reports).

    Returns: list of RedactionSpan objects, sorted by start position.
    Overlapping spans are deduplicated (highest-confidence wins).
    """
    spans: List[RedactionSpan] = []

    # 1. Custom regex patterns (always run)
    for pattern, category, base_conf in ALL_CUSTOM_PATTERNS:
        for m in pattern.finditer(text):
            # Confidence is the base confidence for the pattern; could be
            # boosted by context (e.g. "MRN" label nearby) but we keep it simple.
            conf = base_conf
            spans.append(RedactionSpan(
                start=m.start(), end=m.end(), text=m.group(0),
                category=category, confidence=conf, source="custom",
            ))

    # 2. Device dictionary
    for m in _DEVICE_RE.finditer(text):
        spans.append(RedactionSpan(
            start=m.start(), end=m.end(), text=m.group(0),
            category="device", confidence=0.90, source="custom",
        ))

    # 3. Biometric keywords
    for m in _BIOMETRIC_RE.finditer(text):
        spans.append(RedactionSpan(
            start=m.start(), end=m.end(), text=m.group(0),
            category="biometric", confidence=0.85, source="custom",
        ))

    # 4. Presidio (if available) — adds NER-based detection for names,
    # geographic, phone, email, SSN, US bank, IP, URL
    if _PRESIDIO_AVAILABLE:
        try:
            analyzer = _get_presidio_analyzer()
            results = analyzer.analyze(
                text=text,
                language="en",
                entities=[
                    "PERSON", "LOCATION", "PHONE_NUMBER", "EMAIL_ADDRESS",
                    "US_SSN", "IP_ADDRESS", "URL", "US_BANK_NUMBER",
                    "DATE_TIME", "NRP",
                ],
            )
            # Map Presidio entity names to our 18-category short names
            presidio_to_internal = {
                "PERSON": "names",
                "LOCATION": "geographic",
                "PHONE_NUMBER": "phone",
                "EMAIL_ADDRESS": "email",
                "US_SSN": "ssn",
                "IP_ADDRESS": "ip",
                "URL": "url",
                "US_BANK_NUMBER": "account",
                "DATE_TIME": "dates",
                "NRP": "names",  # nationality/religion/politics — treat as name-ish
            }
            for r in results:
                internal_cat = presidio_to_internal.get(r.entity_type, "unique_code")
                spans.append(RedactionSpan(
                    start=r.start, end=r.end, text=text[r.start:r.end],
                    category=internal_cat, confidence=float(r.score),
                    source="presidio",
                ))
        except Exception as e:
            log.warning("Presidio analysis failed: %s — falling back to custom-only", e)

    # 5. Deduplicate + filter by threshold + sort
    spans = _deduplicate(spans)
    spans = [s for s in spans if s.confidence >= threshold]
    spans.sort(key=lambda s: s.start)
    return spans


# ── Presidio singleton (lazy) ───────────────────────────────────
_presidio_analyzer = None


def _get_presidio_analyzer():
    global _presidio_analyzer
    if _presidio_analyzer is None:
        registry = RecognizerRegistry()
        registry.load_predefined_recognizers()
        # AnalyzerEngine picks up the default NLP engine (spaCy) automatically
        # if installed; no need to pass nlp_recognizer (which is not a valid
        # kwarg in current Presidio versions).
        _presidio_analyzer = AnalyzerEngine(
            registry=registry,
            default_score_threshold=0.0,  # we filter ourselves
            supported_languages=["en"],
        )
    return _presidio_analyzer


def _deduplicate(spans: List[RedactionSpan]) -> List[RedactionSpan]:
    """Deduplicate overlapping spans: keep the highest-confidence one per region.

    For spans that overlap by more than 50%, keep only the one with higher
    confidence. Non-overlapping spans are all kept.
    """
    if not spans:
        return []
    # Sort by start, then by descending confidence
    spans_sorted = sorted(spans, key=lambda s: (s.start, -s.confidence))
    kept: List[RedactionSpan] = []
    for s in spans_sorted:
        # Check overlap with already-kept spans
        overlaps = False
        for k in kept:
            # Compute overlap fraction relative to the smaller span
            overlap_start = max(s.start, k.start)
            overlap_end = min(s.end, k.end)
            if overlap_end > overlap_start:
                overlap_len = overlap_end - overlap_start
                smaller_len = min(s.end - s.start, k.end - k.start)
                if smaller_len > 0 and overlap_len / smaller_len > 0.5:
                    overlaps = True
                    # If the new span has higher confidence, replace
                    if s.confidence > k.confidence:
                        kept[kept.index(k)] = s
                    break
        if not overlaps:
            kept.append(s)
    return kept
