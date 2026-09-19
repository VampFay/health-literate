# CareScaffold — Multi-Agent Work Log

Append-only shared work log for the CareScaffold build. Each entry starts
with `---` and follows the template in the project root instructions.

---
Task ID: p0
Agent: main (Super Z)
Task: Phase 0 — Foundations. Repo scaffold, config loader, model registry, DB schema, health endpoint, README skeleton with §6/§7 honest-language blocks.

Work Log:
- Read v2 FINAL master spec from user; confirmed internally consistent (no conflicts to surface per §0.4)
- Verified sandbox environment: Python 3.12, Node 24, Java 21, FastAPI/Pydantic/spaCy preinstalled; no Docker, no passwordless sudo, no postgres server binary
- Researched DB options: tried `postgresql-17-pgvector` via apt (failed — no sudo), verified `sqlite-vec v0.1.9` works via pip with real cosine MATCH queries
- Decision: SQLite + sqlite-vec (documented honestly in README per spec §7)
- Set up GitHub auth: PAT stored in `~/.config/git/credentials` (chmod 600), git identity configured as VampFay
- Created directory structure exactly per spec §3
- Wrote Phase 0 files: requirements.txt, .env.example, .gitignore, config/model_registry.yaml (verbatim from §4.3), core/config.py (with mtime-based hot-reload), core/auth.py (single-key minimal), core/db.py (async engine + sqlite-vec loader via SQLAlchemy's async-adapter traversal), models/__init__.py (7 tables with server-side default timestamps for raw-SQL inserts), llm/router.py (no hardcoded model strings), app.py (lifespan creates tables + vec0 virtual table), api/health.py, tests/conftest.py + tests/unit/test_phase0_smoke.py (5 tests)
- Fixed two issues during testing: (a) pytest 9 async fixture pattern, (b) SQLAlchemy aiosqlite async-adapter wrapping raw sqlite3.Connection (needed traversal `dbapi_conn._connection._connection` to reach `enable_load_extension`)
- All 5 Phase 0 tests pass; live HTTP test against `uvicorn` confirms /health returns 200 with correct registry contents

Stage Summary:
- Phase 0 complete. 5/5 unit tests pass. Live /health endpoint returns 200.
- DB swap to SQLite+sqlite-vec documented honestly in README §Database (spec target: PostgreSQL+pgvector; sandbox constraints prevent that here)
- API keys (ANTHROPIC_API_KEY, VOYAGEAI_API_KEY) still needed by Phase 2/3 — not for Phase 0/1
- Ready to commit + push + tag `v0.1-phase-0`, then start Phase 1 (PHI redaction corpus FIRST per spec §11.1)
