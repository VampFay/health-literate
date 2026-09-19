# Phase 0 — Foundations — Acceptance Evidence

**Phase:** 0 — Foundations
**Date:** 2026-09-19
**Spec section:** §8 Phase 0, §9 implicit (foundation for all later phases)
**Status:** ✅ Complete

---

## Phase 0 deliverables (spec §3, §4.3, §5)

| # | Deliverable | File | Status |
|---|---|---|---|
| 1 | Repo scaffold matching spec §3 structure | `api/`, `core/`, `services/`, `models/`, `phi/`, `llm/`, `content/`, `tests/`, `fhir/`, `config/`, `docs/` | ✅ |
| 2 | Model registry YAML (spec §4.3, verbatim) | `config/model_registry.yaml` | ✅ |
| 3 | Config loader with hot-reload via mtime | `core/config.py` | ✅ |
| 4 | Async SQLAlchemy engine + sqlite-vec loader | `core/db.py` | ✅ |
| 5 | All 7 tables from spec §5 (UUID PKs, FK constraints, server-default timestamps) | `models/__init__.py` | ✅ |
| 6 | LLM Router (no hardcoded model strings; reads from registry) | `llm/router.py` | ✅ |
| 7 | FastAPI app factory with lifespan (creates tables + vec virtual table on startup) | `app.py` | ✅ |
| 8 | `/health` endpoint returns DB + registry + secrets-configured status | `api/health.py` | ✅ |
| 9 | Minimal single-key auth (spec §3 "minimal auth — this is a demo") | `core/auth.py` | ✅ |
| 10 | README with §6 BAA note + §7 honest-language block + DB-swap disclosure | `README.md` | ✅ |
| 11 | `.env.example` (documented, not populated with secrets) | `.env.example` | ✅ |
| 12 | `.gitignore` (excludes .env, *.db, __pycache__, sandbox state) | `.gitignore` | ✅ |
| 13 | pytest setup with empty adversarial + safety dirs | `pytest.ini`, `tests/conftest.py` | ✅ |

---

## Database decision (researched)

The spec calls for PostgreSQL 16+ with `pgvector`. Environment constraints in this sandbox:
- No Docker (rules out the standard `pgvector/pgvector` container)
- No passwordless sudo (rules out `apt install postgresql-17-pgvector`)
- No existing `postgres` server binary anywhere on PATH
- `sqlite-vec v0.1.9` available via pip and verified working

**Decision:** Use SQLite + `sqlite-vec` v0.1.9 for the demo. Production swap path is one line — change `DATABASE_URL` to `postgresql+psycopg://...` and load the `vector` extension instead of `sqlite-vec`. Schema is dialect-agnostic.

This swap is documented honestly in `README.md` §Database, per spec §7 ("No compliance claim without a backing artifact" — we don't claim PostgreSQL conformance we didn't run).

---

## Acceptance test output (spec §0.2 — paste actual output, don't summarize)

### Run 1 — Unit tests

Command: `python3 -m pytest tests/unit/test_phase0_smoke.py -v`

```
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.0.2, pluggy-1.6.0 -- /home/z/.venv/bin/python3
cachedir: .pytest_cache
metadata: {'Python': '3.12.14', 'Platform': 'Linux-5.10.134-013.15.kangaroo.al8.x86_64-x86_64-with-glibc2.41', 'Packages': {'pytest': '9.0.2', 'pluggy': '1.6.0'}, 'Plugins': {'Faker': '40.1.2', 'metadata': '3.1.1', 'asyncio': '1.3.0', 'ddtrace': '4.2.2', 'cov': '7.0.0', 'json-report': '1.5.0', 'anyio': '4.13.0', 'langsmith': '0.13.0'}}
rootdir: /home/z/my-project/carescaffold
configfile: pytest.ini
plugins: Faker-40.1.2, metadata-3.1.1, asyncio-1.3.0, ddtrace: 4.2.2, cov: 7.0.0, json-report: 1.5.0, anyio: 4.13.0, langsmith: 0.13.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 5 items

tests/unit/test_phase0_smoke.py::test_health_returns_ok PASSED           [ 20%]
tests/unit/test_phase0_smoke.py::test_model_registry_hot_reload PASSED   [ 40%]
tests/unit/test_phase0_smoke.py::test_no_hardcoded_model_strings_in_services PASSED [ 60%]
tests/unit/test_phase0_smoke.py::test_db_schema_creates_and_fk_enforced PASSED [ 80%]
tests/unit/test_phase0_smoke.py::test_audit_log_hash_chain_seed PASSED   [100%]

============================== 5 passed in 1.17s ===============================
```

**Result: 5/5 PASS.**

### Run 2 — Live HTTP smoke test

Command: `unset DATABASE_URL && uvicorn app:app --port 8765 --log-level warning & sleep 2 && curl -s http://localhost:8765/health`

```json
{
    "status": "ok",
    "name": "CareScaffold",
    "version": "0.1.0",
    "database": {
        "engine": "sqlite + sqlite-vec",
        "sqlite_version": "3.53.1",
        "sqlite_vec_version": "v0.1.9",
        "spec_target": "PostgreSQL 16+ with pgvector"
    },
    "model_registry": {
        "generation": {
            "primary": "claude-sonnet-5",
            "fallback": "claude-haiku-4-5-20251001"
        },
        "safety_judge": {
            "primary": "claude-haiku-4-5-20251001"
        },
        "embeddings": {
            "primary": "voyage-4-large",
            "output_dimension": 1024
        }
    },
    "secrets_configured": {
        "anthropic_api_key": false,
        "voyageai_api_key": false,
        "carescaffold_api_key": false,
        "audit_log_hash_salt": true
    }
}
```

OpenAPI docs at `/docs` return HTTP 200.

---

## Phase 0 acceptance checklist

- [x] **5/5 unit tests pass** — output pasted above, not summarized
- [x] **Health endpoint live** — returns 200 with DB + registry + secrets status
- [x] **DB schema creates cleanly** — all 7 tables from spec §5 present
- [x] **FK enforcement verified** — `PRAGMA foreign_keys=ON`, insert-without-parent raises `IntegrityError`
- [x] **sqlite-vec loads** — `vec_version()` returns `v0.1.9` via SQL
- [x] **Model registry hot-reload** — editing YAML is reflected on next `Registry.get()` call without restart
- [x] **No hardcoded model strings** — static-grep test scans all `.py` files outside the allowed list; zero hits
- [x] **Audit log hash chain seed** — first entry has `prev_entry_hash=NULL`; schema ready for Phase 1 implementation
- [x] **README has §6 BAA-not-required block + §7 honest-language block + DB-swap disclosure**
- [x] **`.env` is gitignored; `.env.example` is committed (no secrets)**
- [x] **No PAT/API key in any committed file** (scanned with grep before commit)

---

## Forward-looking notes for Phase 1

- **API keys still needed by Phase 2** (Claude Haiku 4.5 judge) and Phase 3 (Claude Sonnet 5 generation + Voyage AI embeddings). Paste `ANTHROPIC_API_KEY` and `VOYAGEAI_API_KEY` into `carescaffold/.env` before Phase 2 starts.
- **Audit log hash chain** implementation will live in `phi/audit_log.py` — schema shape is now pinned; Phase 1 fills in the SHA-256 chain logic.
- **DB isolation in tests** is handled by `_isolate_db` fixture in `tests/conftest.py` — each test gets a fresh SQLite file DB in `tmp_path`.
- **First audit log entry has `prev_entry_hash=NULL`** — confirmed by `test_audit_log_hash_chain_seed`. Phase 1 must preserve this.
