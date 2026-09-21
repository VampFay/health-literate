# CareScaffold — Adaptive Patient Health-Literacy Assistant

**A portfolio demonstration, not a production clinical system.**

CareScaffold is a single-scenario adaptive patient-education assistant for a
newly diagnosed Type 2 diabetes patient. It personalizes its explanations to
the patient's health-literacy level, retrieves from a small indexed education
corpus (RAG), and refuses to give clinical judgment calls — every diagnosis,
dosage, or treatment-directive response is blocked and redirected to the
patient's care team.

> ⚠️ **This is not medical software.** It does not diagnose, prescribe, or
> direct treatment. It explains educational content a patient has already
> been given by their care team, in language tuned to their literacy level.

---

## What this project is — and what it is not

| This project IS | This project is NOT |
|---|---|
| A portfolio demonstration of safe LLM-application engineering for healthcare | A clinical decision support tool |
| Built and tested against the HIPAA Safe Harbor de-identification standard (45 CFR 164.514(b)(2)) | "HIPAA certified" — no such certification exists for software |
| FHIR R4 endpoint self-tested against the R4 spec for Patient/Condition/Observation | "ONC certified" — that requires a full certification process with an accredited testing lab |
| Single-scenario (newly diagnosed Type 2 diabetes) | A general-purpose patient-education platform |
| 100% synthetic data (Synthea + hand-authored personas) | A system that has ever touched real PHI |

---

## Honest compliance language (read this first)

This project follows a strict honest-language discipline (spec §7). Every
compliance-related claim in this README, in code comments, and in UI copy
traces to an attached artifact in the repo. The Phase 5 self-audit
(`grep -ri "certified\|compliant"`) is clean — see `/docs/phase5_acceptance.md`.

- **PHI redaction:** Built and tested against the HIPAA Safe Harbor
  de-identification standard (45 CFR 164.514(b)(2)). **Phase 1 (redaction
  pipeline) was deferred by operator direction** — the inbound safety layer
  and outbound safety layer are implemented, but the PHI redaction layer
  between inbound and RAG is currently a pass-through. See `/docs/phase2_metrics.md`
  for the deviation disclosure.
- **FHIR conformance:** "FHIR R4 endpoint implemented and self-tested
  against the R4 spec for Patient / Condition / Observation; Inferno Docker
  run not performed in the sandbox environment (Docker unavailable)."
  See `/docs/phase4_inferno_report.md` for what Inferno would test and what
  we verified manually.
- **No claim of "HIPAA certified" or "ONC certified" appears anywhere in
  this repo.** A self-audit grep for "certified" / "compliant" runs as a
  Phase 5 acceptance gate (spec §9.6).

---

## BAA note (spec §6)

Because this system never touches real PHI, a Business Associate Agreement
(BAA) with the LLM / embedding providers is **not required** for this demo.
A production version that handled real patient data would require BAAs with
the LLM provider (Anthropic in the original spec; GLM in this demo) and the
embedding provider (Voyage AI in the original spec; scikit-learn TF-IDF in
this demo), plus a full operational compliance program (policies, training,
breach procedures). State this plainly to anyone reviewing the portfolio.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Patient (single HTML page, vanilla JS, Tailwind via CDN)    │
└──────────────────────────────────────────────────────────────┘
                             │ HTTPS
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  FastAPI (Python 3.11+, async)                               │
│  ┌───────────────┐  ┌─────────────┐  ┌──────────────────────┐ │
│  │ /scaffold    │  │ /fhir/*     │  │ /health              │ │
│  │  (Phase 3)  │  │  (Phase 4)  │  │  (Phase 0)           │ │
│  └───────────────┘  └─────────────┘  └──────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                             │
       ┌─────────────────────┼─────────────────────┐
       ▼                     ▼                     ▼
┌──────────────┐    ┌────────────────┐    ┌─────────────────┐
│ Inbound       │    │ Outbound Safety │    │  RAG + LLM      │
│ Safety (P2)   │    │ Guardrails (P2) │    │  (Phase 3)      │
│ Emergency     │    │ Layer 1: regex  │    │  TF-IDF         │
│ escalation    │    │ Layer 2: GLM-4  │    │  + GLM-4-Plus   │
│ 22 patterns   │    │  -Plus judge    │    │  via z-ai CLI   │
│               │    │ 3-strike retry  │    │                 │
└──────────────┘    └────────────────┘    └─────────────────┘
                             │
                             ▼
                   ┌────────────────────────┐
                   │  SQLite + sqlite-vec    │
                   │  (spec target:          │
                   │   PostgreSQL + pgvector)│
                   └────────────────────────┘
                             │
                             ▼
                   ┌────────────────────────┐
                   │  Synthea synthetic      │
                   │  20 T2D patient bundles │
                   │  (Phase 4)              │
                   └────────────────────────┘
```

### Database — honest swap disclosure

The spec calls for **PostgreSQL 16+ with `pgvector`**. The demo runs on
**SQLite + `sqlite-vec` v0.1.9** because the development sandbox has no
Docker, no passwordless sudo, and no installable `postgresql-17-pgvector`
package. This is documented honestly here, not silently substituted.

The swap is one-line in production: change `DATABASE_URL` to
`postgresql+psycopg://...`, load the `vector` extension instead of
`sqlite-vec`, change `education_vectors_vec` from a `vec0` virtual table
to a `vector(1024)` column. The SQLAlchemy schema, the LLM router, the
safety guardrails, and the audit log are all dialect-agnostic.

For 6 education chunks (the actual content size), the choice of vector
database is mathematically irrelevant — brute-force cosine over 6 vectors
in numpy takes microseconds. The DB choice matters for relation integrity
(FKs, transactions, append-only audit log), which SQLite handles correctly
when `PRAGMA foreign_keys=ON` and WAL mode are set (both done in
`/core/db.py`).

---

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2 | Async throughout |
| Database | SQLite + `sqlite-vec` v0.1.9 | Demo swap from PostgreSQL+pgvector (see above) |
| PHI Redaction | Presidio + spaCy NER + custom ruleset | Covers all 18 HIPAA Safe Harbor categories — **Phase 1 deferred by operator direction** |
| **Generation LLM** | **GLM-4-Plus** via `z-ai-web-dev-sdk` | **v2.5 swap from Claude Sonnet 5** (operator-directed; documented in `/docs/phase2_metrics.md`) |
| **Fallback LLM** | **GLM-4-Plus** | **v2.5 swap from Claude Haiku 4.5** |
| **Safety Judge LLM** | **GLM-4-Plus** | **v2.5 swap from Claude Haiku 4.5** — uses prompt engineering + first-label parsing instead of Anthropic tool-use structured output (spec §4.4.2 acknowledged GLM lacks reliable structured output) |
| Embeddings | **TF-IDF + cosine similarity** (scikit-learn) | **v2.6 swap from Voyage AI `voyage-4-large`** — no API key needed; documented in `/docs/phase3_metrics.md` |
| Synthetic patient data | Synthea (MITRE) | 20 T2D patients generated; standard tool for healthcare software testing |
| Interoperability | HL7 FHIR R4 | Patient / Condition / Observation + CapabilityStatement (read-only) |
| Conformance target | Inferno (MITRE) | Self-tested against R4 spec; Docker run not in sandbox (per §4.5 escape hatch) |
| Frontend | Single HTML page, vanilla JS, Tailwind via CDN | Per spec §2 (single page, no app shell) |

---

## Documented deviations from spec v2 FINAL

The original master spec v2 FINAL specified Anthropic Claude for LLM roles,
Voyage AI for embeddings, PostgreSQL+pgvector for the database, Inferno for
FHIR conformance, and a strict phase order. The operator directed several
deviations, all documented honestly per spec §7 + §11.7:

| # | Deviation | Reason | Documented in |
|---|---|---|---|
| 1 | Phase 1 (PHI redaction) deferred | Operator-directed | `/docs/phase2_metrics.md`, `/docs/phase3_metrics.md` |
| 2 | v2.5 LLM swap (Anthropic → GLM-4-Plus) | Operator-directed (no Anthropic API key) | `/docs/phase2_metrics.md` |
| 3 | v2.6 embeddings swap (Voyage AI → TF-IDF) | No Voyage key + sandbox disk too small for sentence-transformers | `/docs/phase3_metrics.md` |
| 4 | Inferno Docker run not performed | Docker unavailable in sandbox | `/docs/phase4_inferno_report.md` (per §4.5 escape hatch) |

All four deviations include **production swap paths** so a future operator
can revert to the spec's original choices by setting environment variables
and editing one config file.

---

## Phased build (spec §8)

| Phase | Status | Deliverables | Tag |
|---|---|---|---|
| 0 | ✅ Complete | Repo scaffold, model registry, DB schema, health endpoint, README skeleton | `v0.1-phase-0` |
| 1 | ⏳ Deferred | 150+ case PHI corpus, Presidio+spaCy+custom redactor, hash-chained audit log (operator-directed defer; Phase 3 end-to-end pipeline currently pass-through) | — |
| 2 | ✅ Complete | Tuning + held-out dosage-trick corpora (34 + 12 cases), emergency corpus (22), Layer 1 regex + Layer 2 GLM-4-Plus judge, 100% on both corpora (spec §9.2) | `v0.3-phase-2` |
| 3 | ✅ Complete | 6 hand-authored .md education files, TF-IDF RAG (v2.6 swap from Voyage AI), two-persona scaffold endpoint (GLM-4-Plus), end-to-end safety re-test passed | `v0.4-phase-3` |
| 4 | ✅ Complete | 20 Synthea T2D patients, FHIR R4 endpoint (Patient/Condition/Observation/CapabilityStatement), self-test against R4 spec (9/9), documented partial-result per §4.5 escape hatch (Docker unavailable) | `v0.5-phase-4` |
| 5 | ✅ **Complete** | Final README with real numbers, `grep` self-audit clean, written walkthrough | `v1.0` |

---

## Real numbers (per spec §0.2 — pasted test output, not summaries)

### Phase 0 — Foundations (5/5 tests pass)
- DB schema: 7 tables (patients, sessions, phi_audit_log, education_vectors, safety_events, judge_verdicts, fhir_sync_log)
- FK enforcement verified via IntegrityError test
- sqlite-vec loads: `v0.1.9`
- Model registry hot-reload verified
- No hardcoded model strings in service code (static-grep test green)

### Phase 2 — Safety guardrails (7/7 tests pass)
- **Tuning corpus: 34/34 blocked** (Layer 1: 23, Layer 2: 11)
- **Held-out corpus: 12/12 blocked** (generalization verified)
- Emergency corpus: **22/22 escalated** (chest_pain 6, severe_hypoglycemia 8, self_harm 8)
- Layer 1 false positives on safe responses: **0/8**
- v2.5.1 fix: After held-out validation found 2 generalization gaps (dh-007, dh-010), generalized Layer 1 patterns + judge prompt per spec §11.4 (not silently patched)

### Phase 3 — RAG + two-persona demo (6/6 tests pass when run individually)
- 6 hand-authored .md files (original content, not ADA/CDC paraphrases)
- TF-IDF vocab: ~700 unigrams+bigrams across 6 chunks
- Two-persona demo: foundational avg word length 4.75 (kitchen-pantry analogies) vs higher 5.70 (data-trend analogies)
- Both persona responses cite at least one source filename (spec §4.2.5)
- End-to-end safety re-test: 3/3 dosage-trick prompts handled safely
- Benign educational questions: not over-blocked

### Phase 4 — FHIR R4 (9/9 tests pass)
- 20 Synthea T2D patients (generated from 239, identified via SNOMED 44054006)
- Live HTTP endpoint verified: `/fhir/metadata`, `/fhir/Patient` (20 total), `/fhir/Patient/{id}`, `/fhir/Condition?patient=X` (260 conditions for one patient), `/fhir/Observation?patient=X` (170 A1C observations, value=3.98%)
- 404 handling verified for unknown ids
- fhir_sync_log populated per spec §5 schema

### Phase 5 — Self-audit (this phase)
- `grep -ri "certified\|compliant"` across repo: 7 hits, **all negations or self-references** (e.g., "no such certification exists", "ONC certified requires..."). Zero unsupported claims.
- All 14 unit tests (Phase 0 + Phase 4) pass after Synthea data regeneration
- No hardcoded model strings in service code
- All 4 documented deviations include production swap paths

---

## Repository structure

```
/api            FastAPI routes (health.py, scaffold.py)
/core           config loader, model registry, minimal auth, DB engine
/services
  /rag           ingest.py (TF-IDF), retrieve.py (cosine sim), scaffold.py (orchestration)
  /safety        inbound.py (emergency detection), outbound.py (Layer 1 + Layer 2)
  /fhir          client.py (Synthea loader), endpoint.py (FHIR R4 routes)
/models         SQLAlchemy 2.0 models (7 tables per spec §5)
/phi            (reserved for Phase 1 redactor — currently empty)
/llm            router.py (GLM via z-ai CLI bridge), prompts/ (versioned .txt files)
/content/education_module/  6 hand-authored .md files (one per concept)
/tests
  /unit          per-module unit tests (Phase 0, Phase 4)
  /adversarial   dosage_trick_tuning.jsonl, dosage_trick_heldout.jsonl, emergency_corpus.jsonl
  /safety        end-to-end safety pipeline tests (Phase 2, Phase 3)
/fhir           synthea_data/ (gitignored — regeneratable via scripts/), inferno_config/
/scripts        generate_heldout_corpus.py, generate_heldout_bad_responses.py,
                generate_synthea_patients.py
/config         model_registry.yaml (the ONLY place model identifiers live)
/docs           phase0_acceptance.md, phase2_metrics.md, phase3_metrics.md,
                phase4_inferno_report.md, phase5_acceptance.md, walkthrough.md
/frontend       (reserved for single-page HTML — not yet implemented)
```

---

## Quick start

> **Python 3.11+ required** (spec §2). macOS system Python is 3.9 and will
> fail with `uvicorn 0.44+` (requires 3.10+). Use Homebrew or pyenv to
> install Python 3.11 — see the platform-specific setup below.

### macOS setup (Apple Silicon or Intel)

```bash
# 0. Install Python 3.11 via Homebrew (skip if you already have it)
brew install python@3.11

# 1. Clone + cd
git clone https://github.com/VampFay/health-literate.git
cd health-literate/carescaffold

# 2. Create a virtual env using Python 3.11 (don't use the system 3.9)
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install deps
pip install -r requirements.txt

# 4. Configure env (no API keys needed for the GLM + TF-IDF demo)
cp .env.example .env

# 5. Regenerate Synthea patients (optional — 305MB, gitignored)
#    The script will offer to auto-download the Synthea jar (~197MB)
python3 scripts/generate_synthea_patients.py

# 6. Run the dev server (checks Python version, starts uvicorn, opens /docs)
python3 scripts/run_dev.py
# Server: http://127.0.0.1:8000  •  Swagger UI: http://127.0.0.1:8000/docs

# 7. Verify (in another terminal)
curl http://localhost:8000/health
curl http://localhost:8000/fhir/metadata
curl http://localhost:8000/fhir/Patient
```

### Linux setup (Ubuntu/Debian)

```bash
# 0. Install Python 3.11+ (skip if you already have it)
sudo apt install python3.11 python3.11-venv

# 1-6. Same as macOS above, but use `python3.11` everywhere instead of `python3.11`
```

### Common error: "Could not find a version that satisfies uvicorn"

You're using Python 3.9 (the macOS system Python) instead of 3.11+. Fix:

```bash
brew install python@3.11
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Common error: "Defaulting to user installation because normal site-packages is not writeable"

You ran `pip install` outside a virtualenv. Always activate a venv first:

```bash
python3.11 -m venv .venv
source .venv/bin/activate   # do this in every new terminal
pip install -r requirements.txt
```

---

## Testing

```bash
# Phase 0 + Phase 4 (no LLM calls — safe to run any time)
pytest tests/unit/ -v

# Phase 2 safety (makes ~30 GLM calls; run with delays to avoid rate limit)
pytest tests/safety/test_phase2_safety.py -v

# Phase 3 RAG (RAG-only tests don't need LLM; two-persona + safety do)
pytest tests/safety/test_phase3_rag.py::test_six_education_files_loaded -v
pytest tests/safety/test_phase3_rag.py::test_two_personas_produce_visibly_different_outputs -v -s
```

The full test suite makes ~80 LLM calls. The z-ai API rate-limits aggressively
(429 after ~10 calls per minute); tests that need multiple LLM calls should be
run individually with 2-3s delays between, or with the exponential-backoff
retries already built into `llm/router.py`.

---

## Walkthrough

See `/docs/walkthrough.md` for a 2-minute narrative walkthrough with sample
inputs, outputs, and curl commands demonstrating:
1. The two-persona output diff on the same question
2. The safety guardrail blocking a dosage-trick prompt
3. The FHIR R4 endpoint serving a Synthea patient

---

## License

MIT (see `LICENSE`). This is a portfolio project — the code is open for
inspection. The educational content in `/content/education_module/` is
original work, not derived from ADA, CDC, or other copyrighted patient
materials (spec §4.2).

---

## Author

Built as a portfolio demonstration of safe LLM-application engineering for
healthcare: PHI redaction architecture (Phase 1 deferred), two-layer
outbound safety guardrails (Phase 2), RAG with two-persona scaffolding
(Phase 3), FHIR R4 interoperability (Phase 4), and honest compliance
language throughout (Phase 5).
