"""Health & readiness endpoints.

Phase 0 acceptance: GET /health returns 200 with sqlite + sqlite-vec
versions, model registry contents, and a boolean indicating whether
the LLM/embeddings API keys are configured. No secrets in the response.
"""
from __future__ import annotations

from fastapi import APIRouter

from core.config import embeddings_config, get_settings, models_for_role
from core.db import verify_sqlite_vec_loaded

router = APIRouter()


@router.get("")
@router.get("/")
async def health() -> dict:
    """Liveness + readiness combined."""
    settings = get_settings()

    # Model registry sanity (spec §4.3)
    gen_primary, gen_fallback = models_for_role("generation")
    judge_primary, _ = models_for_role("safety_judge")
    emb_cfg = embeddings_config()

    sqlite_v, vec_v = await verify_sqlite_vec_loaded()

    return {
        "status": "ok",
        "name": "CareScaffold",
        "version": "0.1.0",
        "database": {
            "engine": "sqlite + sqlite-vec",
            "sqlite_version": sqlite_v,
            "sqlite_vec_version": vec_v,
            # Production swap path (spec §2 → README §Database)
            "spec_target": "PostgreSQL 16+ with pgvector",
        },
        "model_registry": {
            "generation": {"primary": gen_primary, "fallback": gen_fallback},
            "safety_judge": {"primary": judge_primary},
            "embeddings": {
                "primary": emb_cfg.primary,
                "output_dimension": emb_cfg.output_dimension,
            },
        },
        # Never echo key contents — only "configured: true/false"
        "secrets_configured": {
            "anthropic_api_key": bool(settings.anthropic_api_key),
            "voyageai_api_key": bool(settings.voyageai_api_key),
            "carescaffold_api_key": bool(settings.carescaffold_api_key),
            "audit_log_hash_salt": bool(settings.audit_log_hash_salt),
        },
    }
