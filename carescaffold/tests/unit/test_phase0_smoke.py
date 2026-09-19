"""Phase 0 smoke tests: health endpoint, model registry, DB schema."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_health_returns_ok():
    from app import app
    client = TestClient(app)
    with client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["name"] == "CareScaffold"
    assert "sqlite_version" in body["database"]
    assert "sqlite_vec_version" in body["database"]
    assert body["model_registry"]["generation"]["primary"] == "glm-4-plus"
    # v2.6 swap: embeddings output_dimension is "dynamic" for TF-IDF
    # (was 1024 for Voyage AI voyage-4-large per spec §4.2)
    assert body["model_registry"]["embeddings"]["output_dimension"] in (1024, "dynamic")


@pytest.mark.asyncio
async def test_model_registry_hot_reload(tmp_path, monkeypatch):
    """Editing model_registry.yaml should be picked up without restart."""
    import yaml
    from core.config import ModelRegistry

    # Write a temp registry
    registry_path = tmp_path / "model_registry.yaml"
    registry_path.write_text(yaml.safe_dump({
        "roles": {
            "generation": {"primary": "test-model-a", "fallback": "test-model-b"},
            "safety_judge": {"primary": "test-model-j"},
            "embeddings": {"primary": "test-embed", "output_dimension": 1024},
        },
        "timeouts": {"latency_failover_ms": 2500},
        "retry": {"max_attempts": 2, "on_status": [429, 500, 503]},
    }))
    reg = ModelRegistry(path=registry_path)
    p, f = reg.get()["roles"]["generation"]["primary"], reg.get()["roles"]["generation"]["fallback"]
    assert p == "test-model-a" and f == "test-model-b"

    # Edit the file
    registry_path.write_text(yaml.safe_dump({
        "roles": {
            "generation": {"primary": "swapped-model", "fallback": None},
            "safety_judge": {"primary": "test-model-j"},
            "embeddings": {"primary": "test-embed", "output_dimension": 1024},
        },
    }))
    reg.invalidate()
    assert reg.get()["roles"]["generation"]["primary"] == "swapped-model"


@pytest.mark.asyncio
async def test_no_hardcoded_model_strings_in_services():
    """Spec §1.2.5: model identifiers must not appear as string literals
    in any service module outside /llm/router.py and /config/.

    This is a static check — run as part of every CI gate.
    """
    import re
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent
    forbidden_patterns = [
        r'"claude-',           # "claude-sonnet-5" etc.
        r"'claude-",
        r'"voyage-',
        r"'voyage-",
        r'"gpt-',               # any future OpenAI fall-in
        r"'gpt-",
        r'"glm-',                # GLM models also must live in the registry only
        r"'glm-",
    ]
    allowed_files = {
        "llm/router.py",         # explicitly reads from registry
        "core/config.py",        # typed accessors over registry
        "config/model_registry.yaml",  # the registry itself
        "tests/unit/test_phase0_smoke.py",  # this file (uses patterns in assertions)
    }
    violations = []
    for py_file in project_root.rglob("*.py"):
        rel = py_file.relative_to(project_root).as_posix()
        if rel in allowed_files or py_file.parent.name in {"tests", "adversarial", "safety", "unit"}:
            continue
        text = py_file.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            for m in re.finditer(pattern, text):
                line_no = text[:m.start()].count("\n") + 1
                violations.append(f"{rel}:{line_no}: {m.group(0)}")
    assert not violations, "Hardcoded model strings found: " + ", ".join(violations)


@pytest.mark.asyncio
async def test_db_schema_creates_and_fk_enforced():
    """Spec §5: 7 tables must exist; FK enforcement on (SQLite needs PRAGMA)."""
    from sqlalchemy import inspect, text
    from core.db import get_engine, get_session_factory
    from models import Base

    # Tables are created by the app lifespan; tests need to create them manually.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS education_vectors_vec "
            "USING vec0(embedding float[1024])"
        ))

    factory = get_session_factory()
    async with factory() as session:
        # Check tables exist
        tables = await session.run_sync(lambda sync_session: inspect(sync_session.bind).get_table_names())
        expected = {
            "patients", "sessions", "phi_audit_log", "education_vectors",
            "safety_events", "judge_verdicts", "fhir_sync_log",
        }
        assert expected.issubset(set(tables)), f"Missing tables: {expected - set(tables)}"

        # Check FK enforcement
        pragma_fk = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
        assert pragma_fk == 1, "FK enforcement not enabled"

        # Insert a session without a parent patient → should fail
        with pytest.raises(Exception):
            await session.execute(text(
                "INSERT INTO sessions (id, patient_id, started_at) "
                "VALUES ('00000000-0000-0000-0000-000000000000', "
                "'nonexistent-patient', '2026-09-19T00:00:00Z')"
            ))
            await session.commit()


@pytest.mark.asyncio
async def test_audit_log_hash_chain_seed():
    """First audit log entry should have NULL prev_entry_hash; subsequent
    entries should hash the previous row. Phase 1 will fill in the real
    implementation; this test pins the schema shape now."""
    from sqlalchemy import text
    from core.db import get_engine, get_session_factory
    from models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS education_vectors_vec "
            "USING vec0(embedding float[1024])"
        ))

    factory = get_session_factory()
    async with factory() as session:
        # Create a patient + session so FK is satisfied
        await session.execute(text(
            "INSERT INTO patients (id, literacy_persona) "
            "VALUES ('p1', 'foundational')"
        ))
        await session.execute(text(
            "INSERT INTO sessions (id, patient_id) "
            "VALUES ('s1', 'p1')"
        ))
        await session.execute(text(
            "INSERT INTO phi_audit_log (id, session_id, span_hash, category, confidence, action, prev_entry_hash) "
            "VALUES ('a1', 's1', '0' * 64, 'names', 0.95, 'redacted', NULL)"
        ))
        await session.commit()
        row = (await session.execute(text("SELECT prev_entry_hash FROM phi_audit_log WHERE id='a1'"))).one()
        assert row[0] is None
