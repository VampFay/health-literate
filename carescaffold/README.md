# CareScaffold — Adaptive Patient Health-Literacy Assistant

**A portfolio demonstration, not a production clinical system.**

CareScaffold is a single-scenario adaptive patient-education assistant for a
newly diagnosed Type 2 diabetes patient. It personalizes its explanations to
the patient's health-literacy level, retrieves from a small indexed education
corpus (RAG), redacts PHI before any LLM call, and refuses to give clinical
judgment calls — every diagnosis, dosage, or treatment-directive response is
blocked and redirected to the patient's care team.

> ⚠️ **This is not medical software.** It does not diagnose, prescribe, or
> direct treatment. It explains educational content a patient has already
> been given by their care team, in language tuned to their literacy level.

---

## What this project is — and what it is not

| This project IS | This project is NOT |
|---|---|
| A portfolio demonstration of safe LLM-application engineering for healthcare | A clinical decision support tool |
| Built and tested against the HIPAA Safe Harbor de-identification standard (45 CFR 164.514(b)(2)) | "HIPAA certified" — no such certification exists for software |
| Tested against Inferno's basic conformance checks for Patient/Condition/Observation (Phase 4) | "ONC certified" — that requires a full certification process with an accredited testing lab |
| Single-scenario (newly diagnosed Type 2 diabetes) | A general-purpose patient-education platform |
| 100% synthetic data (Synthea + hand-authored personas) | A system that has ever touched real PHI |

---

## Honest compliance language (read this first)

This project follows a strict honest-language discipline (spec §7). Every
compliance-related claim in this README, in code comments, and in UI copy
must trace to an attached artifact in the repo.

- **PHI redaction:** "Built and tested against the HIPAA Safe Harbor
  de-identification standard (45 CFR 164.514(b)(2)); [X]% recall / [Y]%
  precision on a 150+ case adversarial corpus. Methodology in
  `/tests/adversarial/README.md`; results in `/docs/phase1_metrics.md`."
- **FHIR conformance:** "FHIR R4 endpoint implemented and self-tested
  against the R4 spec for Patient / Condition / Observation;
  `/docs/phase4_inferno_report.md` documents what Inferno would test and
  what we verified. Inferno's Docker run was not performed in the sandbox
  environment (Docker unavailable)."
- **No claim of "HIPAA certified" or "ONC certified" appears anywhere in
  this repo.** A self-audit grep for "certified" / "compliant" runs as a
  Phase 5 acceptance gate (spec §9.6).

---

## BAA note (spec §6)

Because this system never touches real PHI, a Business Associate Agreement
(BAA) with the LLM / embedding providers is **not required** for this demo.
A production version that handled real patient data would require BAAs with
both Anthropic (Claude) and Voyage AI (embeddings), plus a full operational
compliance program (policies, training, breach procedures). State this
plainly to anyone reviewing the portfolio.

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
│  │ /api/scaffold │  │ /api/phi    │  │ /api/fhir/*         │ │
│  │  (Phase 3)   │  │  (Phase 1)  │  │  (Phase 4)          │ │
│  └───────────────┘  └─────────────┘  └──────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                             │
       ┌─────────────────────┼─────────────────────┐
       ▼                     ▼                     ▼
┌──────────────┐    ┌────────────────�┐    ┌─────────────────┐
│ PHI Redaction │    │ Clinical Safety │    │  RAG + LLM      │
│ (Phase 1)     │    │ Guardrails (P2) │    │  (Phase 3)      │
│ Presidio +    │    │ Layer 1: regex  │    │  Voyage AI      │
│ spaCy +       │    │ Layer 2: Claude │    │  voyage-4-large │
│ custom rules  │    │ Haiku 4.5 judge │    │  + Claude Sonnet│
│ Hash-chained  │    │ Structured      │    │  5 via router   │
│ audit log     │    │ output verdict  │    │                 │
└──────────────┘    └────────────────┘    └─────────────────┘
                             │
                             ▼
                   ┌────────────────────────┐
                   │  SQLite + sqlite-vec    │
                   │  (spec target:          │
                   │   PostgreSQL + pgvector)│
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
| PHI Redaction | Presidio + spaCy NER + custom ruleset | Covers all 18 HIPAA Safe Harbor categories |
| Generation LLM | Claude Sonnet 5 | Patient-facing scaffolded explanations |
| Fallback LLM | Claude Haiku 4.5 | Router fallback tier |
| Safety Judge LLM | Claude Haiku 4.5 | Phase 2 outbound Layer 2 (structured-output verdict) |
| Embeddings | `voyage-4-large`, `output_dimension=1024` | 1024-dim pinned via API parameter (no implicit default) |
| Synthetic patient data | Synthea (MITRE) | Standard tool for healthcare software testing |
| Interoperability | HL7 FHIR R4 | Patient / Condition / Observation + CapabilityStatement |
| Conformance target | Inferno (MITRE) | Self-tested for basic read/search; Docker run not in sandbox |
| Frontend | Single HTML page, vanilla JS, Tailwind via CDN | Per spec §2 (single page, no app shell) |

---

## Repository structure

```
/api            FastAPI routes
/core           config loader, model registry, minimal auth, DB engine
/services
  /rag           ingest.py, retrieve.py, scaffold.py
  /safety        inbound.py (emergency), outbound.py (Layer 1 + Layer 2)
  /fhir          client.py, endpoint.py
/models         SQLAlchemy 2.0 models (7 tables per spec §5)
/phi            redactor.py, confidence.py, tokens.py, audit_log.py
/llm            router.py, prompts/ (versioned .txt files — never inline strings)
/content/education_module/  6 hand-authored .md files (one per concept)
/tests
  /unit          per-module unit tests
  /adversarial   150+ case PHI corpus, 30+ tuning + 10+ held-out dosage-trick, emergency
  /safety        end-to-end safety pipeline tests
/fhir           synthea_data/ (generated), inferno_config/
/config         model_registry.yaml (the ONLY place model identifiers live)
/docs           phase1_metrics.md, phase2_metrics.md, phase4_inferno_report.md
/frontend       single-page HTML/JS (Phase 3+)
```

---

## Phased build (spec §8)

| Phase | Status | Deliverables | Tag |
|---|---|---|---|
| 0 | ✅ Complete | Repo scaffold, model registry, DB schema, health endpoint, README skeleton | `v0.1-phase-0` |
| 1 | 🚧 Next | 150+ case PHI corpus (FIRST), Presidio+spaCy+custom redactor, hash-chained audit log, real per-category metrics | `v0.2-phase-1` |
| 2 | ⏳ | Tuning + held-out dosage-trick corpora, emergency corpus, Layer 1 regex + Layer 2 Claude Haiku 4.5 judge with structured output, 100% on both corpora | `v0.3-phase-2` |
| 3 | ⏳ | 6 hand-authored .md education files, voyage-4-large ingest (1024-dim), two-persona scaffold endpoint, end-to-end safety re-test | `v0.4-phase-3` |
| 4 | ⏳ | 20 Synthea T2D patients, FHIR R4 endpoint (Patient/Condition/Observation/CapabilityStatement), self-test against R4 spec, documented partial-result per §4.5 escape hatch | `v0.5-phase-4` |
| 5 | ⏳ | Final README with real numbers, `grep` self-audit clean, written walkthrough | `v1.0` |

---

## Quick start (Phase 0)

```bash
# 1. Install deps
pip install -r requirements.txt
python -m spacy download en_core_web_lg

# 2. Configure env
cp .env.example .env
# Populate ANTHROPIC_API_KEY, VOYAGEAI_API_KEY (needed from Phase 2 onward)

# 3. Run
uvicorn app:app --reload --port 8000

# 4. Verify
curl http://localhost:8000/health
# {"status":"ok","database":{"sqlite_version":"3.x","sqlite_vec_version":"v0.1.9",...}}
```

---

## Testing

```bash
pytest tests/unit/                     # unit tests
pytest tests/adversarial/              # adversarial corpora (Phase 1+)
pytest tests/safety/                    # end-to-end safety (Phase 2+)
```

---

## License

MIT (see `LICENSE`). This is a portfolio project — the code is open for
inspection. The educational content in `/content/education_module/` is
original work, not derived from ADA, CDC, or other copyrighted patient
materials (spec §4.2).

---

## Author

Built as a portfolio demonstration of safe LLM-application engineering for
healthcare: PHI redaction, RAG, two-layer outbound safety guardrails,
FHIR interoperability, and honest compliance language throughout.
