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

from core.config import embeddings_config, models_for_role, retry_config, timeouts

log = logging.getLogger(__name__)


@dataclass
class FallbackEvent:
    """Structured record of a fallback trigger for observability."""

    role: str
    primary: str
    fallback: str
    reason: str
    latency_ms: int


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

    async def call_safety_judge(self, prompt: str, **kwargs: Any) -> str:
        raise NotImplementedError(
            "Safety judge call wired in Phase 2 (spec §4.4.2 Layer 2)."
        )

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
