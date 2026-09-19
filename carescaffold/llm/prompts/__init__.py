"""LLM prompt templates package.

Spec §4.3: "Prompt templates live in /llm/prompts/, versioned by filename
(e.g., scaffold_explain_v2.txt), never inlined as Python string literals
in service code — this is what makes prompt iteration reviewable and
revertible."

Loading discipline: load each template once at module import time and
cache it. Do NOT inline the text in service code. If a template needs to
change, edit the file here and bump the version number in the filename.
"""
from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


def load(filename: str) -> str:
    """Load a prompt template by filename from this directory.

    Raises FileNotFoundError with a clear message if the file is missing.
    """
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt template not found: {path}. Spec §4.3 requires all "
            f"prompts to live as versioned files in /llm/prompts/."
        )
    return path.read_text(encoding="utf-8")


# Pre-load the Phase 2 + Phase 3 prompt templates
OUTBOUND_JUDGE_V1 = load("outbound_judge_v1.txt")
SCAFFOLD_FOUNDATIONAL_V1 = load("scaffold_foundational_v1.txt")
SCAFFOLD_HIGHER_V1 = load("scaffold_higher_v1.txt")
