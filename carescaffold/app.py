"""FastAPI app factory.

Spec §3: /api/ contains route definitions. This module assembles the
FastAPI app from route modules added in each phase.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.db import dispose_engine, get_engine, verify_sqlite_vec_loaded
from models import Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables + verify sqlite-vec + ingest RAG content.
    Shutdown: dispose engine."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Create the sqlite-vec virtual table for embeddings storage.
        # Idempotent — `CREATE VIRTUAL TABLE IF NOT EXISTS`.
        from sqlalchemy import text
        await conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS education_vectors_vec "
            "USING vec0(embedding float[1024])"
        ))
    log = logging.getLogger("carescaffold")
    sqlite_v, vec_v = await verify_sqlite_vec_loaded()
    log.info("CareScaffold startup OK — sqlite=%s sqlite_vec=%s", sqlite_v, vec_v)

    # Phase 3: ingest RAG content (6 markdown files) at startup.
    try:
        from services.rag.ingest import ingest_all
        n_chunks, source_files = await ingest_all()
        log.info("RAG ingest OK — %d chunks: %s", n_chunks, source_files)
    except Exception as e:
        log.error("RAG ingest failed: %s", e)
        # Continue startup — RAG calls will return graceful redirects

    # Phase 4: load Synthea FHIR bundles at startup.
    try:
        from services.fhir.client import load_all as fhir_load_all
        n_p, n_c, n_o = await fhir_load_all()
        log.info(
            "FHIR load OK — %d patients, %d conditions, %d A1C observations",
            n_p, n_c, n_o,
        )
    except Exception as e:
        log.error("FHIR load failed: %s", e)

    yield
    await dispose_engine()


def create_app() -> FastAPI:
    """Construct and return the CareScaffold FastAPI app."""
    app = FastAPI(
        title="CareScaffold — Adaptive Patient Health-Literacy Assistant",
        version="0.1.0",
        description=(
            "Portfolio demo (NOT a clinical system). Single-scenario adaptive "
            "patient-education assistant for newly diagnosed Type 2 diabetes "
            "patients. See README for honest-language compliance framing."
        ),
        lifespan=lifespan,
    )

    # Phase 0: /api/health
    from api.health import router as health_router
    app.include_router(health_router, prefix="/health", tags=["health"])

    # Phase 3: /api/scaffold
    from api.scaffold import router as scaffold_router
    app.include_router(scaffold_router, prefix="/scaffold", tags=["scaffold"])

    # Phase 4: /fhir/* (FHIR R4 endpoint, mounted at root per FHIR convention)
    from services.fhir.endpoint import router as fhir_router
    app.include_router(fhir_router)

    return app


app = create_app()
