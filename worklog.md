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

---
Task ID: p2-partial
Agent: main (Super Z)
Task: Phase 2 (partial) — Safety guardrails. Per operator instruction, skipped Phase 1 (redaction) and proceeded to Phase 2. Built corpora FIRST per spec §11.1, then implemented inbound emergency detection + Layer 1 outbound regex pre-filter. Layer 2 (Claude Haiku 4.5 judge) code complete but BLOCKED on ANTHROPIC_API_KEY.

Work Log:
- Verified Phase 0 truly complete (5/5 tests pass, GitHub commit 34688f64 by VampFay, tag v0.1-phase-0)
- Surfaced two blockers per spec §11.7: (a) skipping Phase 1 deviates from spec §1.2.6 phased order; (b) Phase 2 Layer 2 + held-out corpus require ANTHROPIC_API_KEY
- Operator confirmed: skip Phase 1, proceed with Phase 2 (API key pending)
- Built /tests/adversarial/dosage_trick_tuning.jsonl — 34 cases (exceeds spec §4.4.3 minimum of 30+), covering all 5 spec-named examples (dt-001 through dt-005) plus 29 additional cases across indirect diagnosis/dosage/directive categories
- Built /tests/adversarial/emergency_corpus.jsonl — 22 cases (exceeds spec §4.4.4 minimum of 20+) across all 3 spec §4.4.1 categories: chest pain (6), severe hypoglycemia (8), self-harm (8); includes all spec-named examples ("can't wake up", "seizure", "loss of consciousness", self-harm ideation)
- Implemented /services/safety/inbound.py — regex/keyword emergency detection checked FIRST per spec §1.2.4; bypasses generation entirely; logs to safety_events via rule_label helper
- Iterated on inbound patterns: first pass caught 17/22 emergency cases; added loose sugar pattern, confused+won't respond cross-subject pattern, took-insulin-then-symptoms pattern, not-taking-insulin-as-self-harm pattern → now 22/22 detected with 0 false positives on dosage corpus and 7 benign inputs
- Implemented /services/safety/outbound.py Layer 1 — regex pre-filter per spec §4.4.2 with 3 pattern groups (dosage: number+unit+medname in same sentence; diagnosis: "you have" + 7 variants; directive: 14 directive verbs near med names)
- Iterated on Layer 1 patterns: first pass caught 17/34; added "stop all" + meds context, "double your X" with intervening med name, "skip tomorrow's/next X", "half a X pill" pattern, "take an extra" + med/dose, "take X at night instead" timing-directive → now 22/34 with 0 false positives on 8 truly-safe responses
- Wrote /llm/prompts/outbound_judge_v1.txt verbatim from spec §4.4.2
- Extended /llm/router.py with call_safety_judge — Anthropic async client with structured output via tool-use API (verdict enum {SAFE, BLOCK_DIAGNOSIS, BLOCK_DOSAGE, BLOCK_DIRECTIVE}); fail-safe default returns BLOCK_DIRECTIVE on any judge error
- Implemented two-layer orchestration in /services/safety/outbound.py::enforce — Layer 1 first, if SAFE then Layer 2, combined verdict returned
- Wrote /tests/safety/test_phase2_safety.py — 6 tests covering: emergency corpus 100% escalation, escalation message non-alarmist, Layer 1 catch rate, Layer 1 no false positives, held-out corpus existence-or-skip, tuning corpus 100% combined
- Test results: 4 pass, 1 skip (held-out corpus pending generation), 1 expected-fail (tuning 100% gate pending API key) — message clearly states "Layer 1 alone catches 22 of 34 cases. Add ANTHROPIC_API_KEY to .env to enable Layer 2 and close the 100% gate."
- Wrote /docs/phase2_metrics.md with full pasted test output, deviation disclosure, and unblock instructions
- No Phase 0 regression — full suite: 9 passing, 1 expected fail, 1 expected skip

Stage Summary:
- Phase 2 is PARTIAL. All work that doesn't require ANTHROPIC_API_KEY is complete and verified:
  - 34-case tuning corpus, 22-case emergency corpus
  - Inbound: 22/22 emergency escalation, 0 false positives
  - Layer 1: 22/34 dosage-trick catch rate, 0 false positives on safe responses
  - Layer 2 code complete (Anthropic tool-use with verdict enum), pending key
  - Two-layer orchestration + fail-safe defaults
- BLOCKER: ANTHROPIC_API_KEY needed to (a) close 100% gate on tuning corpus, (b) generate held-out corpus via Claude Sonnet 5 in separate session, (c) close 100% gate on held-out corpus
- Will commit + push this partial state as v0.2-phase-2-partial; full v0.3-phase-2 tag will follow once API key + held-out corpus are in place
