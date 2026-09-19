"""LLM Router — single point of model identifier access (spec §4.3).

Spec §1.2.5: NEVER hardcode model strings in business logic. The router
reads from /config/model_registry.yaml via /core/config.py. Adding or
changing a model is a one-line YAML edit; the next router call picks
it up via the registry's mtime-based hot-reload.

This file is a thin wrapper. The actual client instantiation for
Anthropic and Voyage AI happens lazily on first call, so we don't fail
at import time when API keys aren't set (Phases 0 & 1 don't need them).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.config import embeddings_config, get_settings, models_for_role, retry_config, timeouts

log = logging.getLogger(__name__)


# ── Anthropic client (lazy) ──────────────────────────────────────
_anthropic_client: Any = None


def _get_anthropic_client() -> Any:
    """Lazily instantiate the Anthropic async client.

    Raises RuntimeError if ANTHROPIC_API_KEY is not configured. Service
    code should catch this and surface a clear error to the operator.
    """
    global _anthropic_client
    if _anthropic_client is None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not configured. Set it in .env "
                "(required from Phase 2 onward per spec §4.4.2 Layer 2)."
            )
        import anthropic
        _anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


@dataclass
class FallbackEvent:
    """Structured record of a fallback trigger for observability."""

    role: str
    primary: str
    fallback: str
    reason: str
    latency_ms: int


# Allowed verdicts for the outbound safety judge (spec §4.4.2)
JUDGE_VERDICTS = ("SAFE", "BLOCK_DIAGNOSIS", "BLOCK_DOSAGE", "BLOCK_DIRECTIVE")


class LLMRouter:
    """Routes model calls through config-driven role lookup.

    Phase 0 skeleton — actual LLM/Embedding clients wired in Phase 2 (judge)
    and Phase 3 (generation + embeddings). This class exposes the interface
    now so service-layer code can be written against it without import-time
    dependencies on Anthropic / Voyage AI SDKs.
    """

    async def call_generation(self, prompt: str, **kwargs: Any) -> str:
        raise NotImplementedError(
            "Generation call wired in Phase 3 (spec §4.2)."
        )

    async def call_safety_judge(self, response_text: str) -> str:
        """Phase 2 Layer 2 — Claude Haiku 4.5 judge call with structured output.

        Spec §4.4.2: "Use Claude's tool-use / structured output capability
        for the judge call to force one of the four fixed labels — do not
        parse free text for this."

        Implementation: uses Anthropic's tool-use API with a single tool
        `submit_verdict` that takes a `verdict` enum argument. The model
        is constrained to invoke this tool, giving us a structured label.

        Returns one of: SAFE, BLOCK_DIAGNOSIS, BLOCK_DOSAGE, BLOCK_DIRECTIVE.
        """
        from llm.prompts import OUTBOUND_JUDGE_V1

        judge_model = self.judge_model()
        client = _get_anthropic_client()

        # Define a tool that forces the model to return one of 4 labels.
        # Anthropic's tool-use schema enforces the enum constraint.
        verdict_tool = {
            "name": "submit_verdict",
            "description": "Submit the safety verdict for this patient-education response.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "verdict": {
                        "type": "string",
                        "enum": list(JUDGE_VERDICTS),
                        "description": (
                            "One of: SAFE (no safety issue), BLOCK_DIAGNOSIS "
                            "(response states a diagnosis), BLOCK_DOSAGE "
                            "(response gives dosage info), BLOCK_DIRECTIVE "
                            "(response tells patient to alter treatment)."
                        ),
                    },
                },
                "required": ["verdict"],
            },
        }

        prompt_text = OUTBOUND_JUDGE_V1.replace("{response_text}", response_text)

        try:
            response = await client.messages.create(
                model=judge_model,
                max_tokens=1024,
                tools=[verdict_tool],
                tool_choice={"type": "tool", "name": "submit_verdict"},
                messages=[{"role": "user", "content": prompt_text}],
            )
        except Exception as e:
            log.error("Safety judge call failed: %s", e)
            # FAIL SAFE: if the judge is unavailable, BLOCK rather than release.
            # The judge is a defense-in-depth layer; if it fails, we cannot
            # verify the response is safe, so we block by default.
            return "BLOCK_DIRECTIVE"

        # Extract verdict from the tool call
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_verdict":
                verdict = block.input.get("verdict", "")
                if verdict in JUDGE_VERDICTS:
                    return verdict
                # Unexpected verdict value — fail safe
                return "BLOCK_DIRECTIVE"

        # No tool_use block found — fail safe
        log.warning("Judge response had no tool_use block; failing safe.")
        return "BLOCK_DIRECTIVE"

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "Document embedding wired in Phase 3 (spec §4.2 ingest)."
        )

    async def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError(
            "Query embedding wired in Phase 3 (spec §4.2 retrieve)."
        )

    # ── Config accessors (used by tests + Phase 2/3 implementations) ──
    def generation_models(self) -> tuple[str, str | None]:
        return models_for_role("generation")

    def judge_model(self) -> str:
        primary, _ = models_for_role("safety_judge")
        return primary

    def embedding_model(self) -> tuple[str, int]:
        cfg = embeddings_config()
        return cfg.primary, cfg.output_dimension

    def latency_failover_ms(self) -> int:
        return int(timeouts().get("latency_failover_ms", 2500))

    def retry_max_attempts(self) -> int:
        return int(retry_config().get("max_attempts", 2))


# Module-level singleton for service code
router = LLMRouter()
