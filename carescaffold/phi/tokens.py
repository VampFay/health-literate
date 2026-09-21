"""Synthetic token replacement, session-scoped (spec §4.1).

Pipeline step 4: "Replace confirmed identifiers with synthetic tokens
([STUDENT_NAME_1], [LOCATION_A]) using a session-scoped mapping table,
never a global one."

For CareScaffold (healthcare portfolio), the spec's [STUDENT_NAME_N]
becomes [PATIENT_NAME_N] etc. The token format is:
  [<CATEGORY>_<INDEX>]

Where CATEGORY is one of:
  PATIENT_NAME, DR_NAME, FAMILY_NAME, PEER_NAME
  CITY, ZIP, STREET, ADDRESS, REGION
  DATE, AGE
  PHONE, FAX
  EMAIL
  SSN
  MRN
  BENEFICIARY, INSURANCE_ID
  ACCOUNT, ROUTING
  LICENSE
  VIN, PLATE
  DEVICE, DEVICE_SERIAL
  URL, IP, PHOTO, BIOMETRIC
  UNIQUE_CODE

The mapping is session-scoped: each session gets its own mapping so
the same span in different sessions produces different tokens. The
mapping is never persisted globally (would defeat the purpose).

For the portfolio demo, we keep mappings in-memory per session.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from phi.redactor import RedactionSpan


# Map our 18-category short names to token prefixes
_CATEGORY_TO_TOKEN = {
    "names": "PATIENT_NAME",       # could also be DR_NAME / FAMILY_NAME / PEER_NAME based on context — keep simple here
    "geographic": "LOCATION",
    "dates": "DATE",
    "phone": "PHONE",
    "fax": "FAX",
    "email": "EMAIL",
    "ssn": "SSN",
    "mrn": "MRN",
    "beneficiary": "INSURANCE_ID",
    "account": "ACCOUNT",
    "license": "LICENSE",
    "vehicle": "VEHICLE",
    "device": "DEVICE",
    "url": "URL",
    "ip": "IP",
    "biometric": "BIOMETRIC",
    "photo": "PHOTO",
    "unique_code": "UNIQUE_CODE",
}


@dataclass
class TokenMap:
    """Per-session mapping of original span text → synthetic token.

    A new TokenMap should be created for each session (each /scaffold POST
    in the demo). Never persist this across sessions.
    """
    # category → next index (e.g. "names" → 1, then 2, ...)
    _counters: Dict[str, int] = field(default_factory=dict)
    # (category, original_text) → synthetic token
    _mapping: Dict[Tuple[str, str], str] = field(default_factory=dict)

    def get_token(self, category: str, original_text: str) -> str:
        """Get the existing token for this text, or create a new one."""
        key = (category, original_text)
        if key in self._mapping:
            return self._mapping[key]
        # Create new token: <CATEGORY_PREFIX>_<INDEX>
        prefix = _CATEGORY_TO_TOKEN.get(category, "PHI")
        idx = self._counters.get(category, 0) + 1
        self._counters[category] = idx
        token = f"[{prefix}_{idx}]"
        self._mapping[key] = token
        return token

    def reverse(self, token: str) -> str | None:
        """Reverse-lookup the original text for a token (for testing/debugging
        only — never expose to the LLM)."""
        for (cat, orig), tok in self._mapping.items():
            if tok == token:
                return orig
        return None

    def all_mappings(self) -> List[Tuple[str, str, str]]:
        """Return all mappings as (category, original, token) for audit log."""
        return [(cat, orig, tok) for (cat, orig), tok in self._mapping.items()]


def redact_text(text: str, spans: List[RedactionSpan], token_map: TokenMap) -> str:
    """Replace each span in text with its synthetic token.

    Args:
        text: original input
        spans: detected PHI spans (must be sorted by start, non-overlapping)
        token_map: session-scoped TokenMap to ensure same span → same token

    Returns: text with all spans replaced by synthetic tokens.
    """
    if not spans:
        return text
    # Sort by start descending so we can splice from the end without
    # shifting earlier offsets
    sorted_spans = sorted(spans, key=lambda s: s.start, reverse=True)
    result = text
    for s in sorted_spans:
        token = token_map.get_token(s.category, s.text)
        result = result[:s.start] + token + result[s.end:]
    return result
