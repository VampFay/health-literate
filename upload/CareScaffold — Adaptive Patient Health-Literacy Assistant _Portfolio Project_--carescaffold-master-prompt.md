# MASTER BUILD SPECIFICATION
## CareScaffold — Adaptive Patient Health-Literacy Assistant (Portfolio Project)
### For execution by an autonomous coding agent

---

## 0. HOW TO USE THIS DOCUMENT

You are building a **portfolio demonstration**, not a production clinical system. Every design decision below optimizes for: (a) technical rigor that survives scrutiny, (b) zero risk of real PHI exposure, (c) honest, falsifiable claims in the final writeup. If any instruction here would require real patient data, real clinical validation, or a real regulatory certification to back up a claim — stop and flag it. Do not quietly substitute a weaker synthetic version and then describe it as if it were the real thing.

---

## 1. MISSION & NON-NEGOTIABLE CONSTRAINTS

### 1.1 Mission
Build a single-scenario adaptive patient-education assistant: a newly diagnosed Type 2 diabetes patient asks questions about their diagnosis, A1C targets, metformin basics, and dietary guidance. The system personalizes its explanation to the patient's health-literacy level, retrieves from a small indexed education corpus, redacts PHI before any LLM call, and refuses to give clinical judgment calls.

### 1.2 Non-negotiable constraints
1. **No real PHI, ever, at any point in development, testing, or demo.** All patient data is synthetic, generated via Synthea (MITRE's open-source synthetic patient generator) or hand-authored fictional personas. This is not a workaround — it is the correct and standard way to build/test healthcare software without triggering real regulatory exposure. State this plainly in the README.
2. **No diagnosis. No dosage instructions. No treatment directives.** The system's outbound validation must block any response that tells the patient what they specifically have, what dose to take, or what to change about their treatment. Every such case redirects to "talk to your care team." This is the single highest-priority guardrail in this spec — treat its test coverage as the top acceptance gate, above the RAG demo, above the FHIR integration.
3. **No compliance claim without a backing artifact.** Do not write "HIPAA certified" anywhere — no such certification exists for software the way 1EdTech certifies LTI conformance. The honest claim is: "PHI redaction pipeline built and tested against the 18 HIPAA Safe Harbor identifier categories (45 CFR 164.514(b)(2))." Say exactly that, with the actual test results attached.
4. **Emergency-symptom detection bypasses generation entirely.** If inbound text plausibly describes a medical emergency (chest pain, signs of severe hypoglycemia, self-harm indicators), the system does not attempt to answer — it immediately returns an escalation message and logs the event. Do not route this through the normal RAG/generation path at all; it should be the first check, before redaction even completes if possible.
5. **No hardcoded model version strings in business logic.** All model identifiers live in an external config (Section 6.2), same discipline as any production system — because Anthropic and Voyage AI both revise their model lineups over time and you don't want a deploy to silently break on it.
6. **Build in the phased order in Section 9.** Redaction pipeline and its test corpus come before generation logic, same reasoning as always: building both together risks unconsciously tuning one to the other.

---

## 2. ARCHITECTURE & TECH STACK

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2 | |
| Database | PostgreSQL 16+ with `pgvector` | |
| PHI Redaction | Presidio Analyzer/Anonymizer + spaCy NER + a supplementary ruleset covering all 18 HIPAA Safe Harbor categories not well covered by Presidio's defaults (medical record numbers, health plan beneficiary numbers, device identifiers, biometric identifiers, dates other than year) | See Section 4.1 for the full checklist |
| Generation LLM | **Claude Sonnet 5** (`claude-sonnet-5`) | Deep reasoning / scaffolded explanation role |
| Fast/degraded-mode LLM | **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) | Fallback tier — see Section 6.3 |
| Embeddings | **Voyage AI `voyage-4`** (32K token context, $0.06/M input as of last verification — reconfirm pricing before relying on it) | Anthropic's recommended embedding partner for Claude-based RAG. `voyage-context-4` (released mid-2026) is a newer contextualized-embedding option worth evaluating if you want a stretch goal — verify its current spec before committing, don't assume it matches voyage-4's interface |
| Synthetic patient data | **Synthea** (MITRE, open-source) | Generates realistic FHIR-format synthetic patients — this is the standard tool for exactly this use case, not an improvised substitute |
| Interoperability protocol | **HL7 FHIR R4** | Pull synthetic Patient / Condition / Observation (A1C lab value) resources |
| Conformance testing | **Inferno** (MITRE, built for ONC's Health IT Certification Program) | Direct analog to 1EdTech's LTI conformance suite — run your FHIR endpoint against it and attach the actual output |
| Frontend | Single page, plain HTML/JS or one Next.js page | Do not build a full app shell for a one-scenario demo |

---

## 3. REPOSITORY STRUCTURE

```
/api            — FastAPI routes
/core           — config, model registry, auth (minimal — this is a demo, not multi-tenant SaaS)
/services       — personalization, RAG, safety checks, FHIR client
/models         — DB models
/phi            — redaction pipeline, hash-chained audit log
/llm            — Claude client wrapper, fallback router, versioned prompt templates
/fhir           — FHIR client, Synthea-generated fixture data, Inferno test config
/tests
  /unit
  /adversarial  — the 18-category PHI test corpus (build this BEFORE the redaction logic)
  /safety       — test cases for emergency detection and scope-refusal (Section 4.4) — 
                  this directory's coverage is the top acceptance gate, per Section 1.2.2
/config
  model_registry.yaml
```

---

## 4. CORE MODULES

### 4.1 PHI Redaction & Audit Pipeline (HIPAA Safe Harbor, 18 categories)

Build your adversarial test corpus against this exact list (45 CFR 164.514(b)(2)) — this precision is your strongest portfolio claim, so don't approximate it:

1. Names
2. Geographic subdivisions smaller than a state
3. All elements of dates (except year) directly related to an individual, including birth date, admission date, discharge date, death date, and all ages over 89
4. Telephone numbers
5. Fax numbers
6. Email addresses
7. Social Security numbers
8. Medical record numbers
9. Health plan beneficiary numbers
10. Account numbers
11. Certificate/license numbers
12. Vehicle identifiers and serial numbers
13. Device identifiers and serial numbers
14. URLs
15. IP addresses
16. Biometric identifiers
17. Full-face photographs and comparable images
18. Any other unique identifying number, characteristic, or code

**Pipeline:**
1. Intercept all patient input server-side before any LLM call.
2. Run Presidio + spaCy + your supplementary ruleset for the categories Presidio's defaults handle poorly (medical record numbers, device identifiers, beneficiary numbers — these need custom patterns).
3. Confidence thresholding: below threshold (start at 0.85, document your actual chosen value and the tuning rationale) → route to a simulated human review queue rather than auto-redact.
4. Replace confirmed identifiers with synthetic tokens, session-scoped mapping only.
5. Log every redaction decision to an append-only, hash-chained audit log (each entry hashes the previous entry).

**Acceptance target:** report actual recall/precision per category on a 150+ case adversarial corpus you build yourself, covering edge cases like: a medical record number embedded mid-sentence, a device identifier referenced casually ("my Dexcom G7"), relative dates ("three days after my mom's surgery" — which requires inferring a date is present at all).

### 4.2 Adaptive Patient Education RAG Engine

- Write one diabetes-education module yourself (diagnosis basics, A1C target explanation, metformin basics, dietary guidance) — do not copy ADA or other copyrighted patient materials.
- Index into `pgvector` via `voyage-4` embeddings.
- Two literacy personas:
  - **Persona A (foundational literacy):** everyday/cooking analogies, ~6th-grade reading level (e.g., explain blood sugar using a kitchen-pantry-stocking analogy).
  - **Persona B (higher literacy/tech-comfortable):** data-oriented analogies referencing trends and tracking, ~10th–12th-grade reading level.
- Every response cites back to the source chunk it drew from.
- Prompt templates versioned in `/llm/prompts/`, never inlined as string literals.

### 4.3 LLM Router

Same config-driven pattern as any production system — model IDs never hardcoded in service logic:

```yaml
# /config/model_registry.yaml
roles:
  generation:
    primary: claude-sonnet-5
    fallback: claude-haiku-4-5-20251001
  embeddings:
    primary: voyage-4
timeouts:
  latency_failover_ms: 2500
retry:
  max_attempts: 2
  on_status: [429, 500, 503]
```

### 4.4 Clinical Safety Guardrails (the highest-priority module in this spec)

**Inbound — emergency detection, checked first, before redaction completes if feasible:**
- Chest pain, severe hypoglycemia symptoms (confusion, loss of consciousness language, seizure), self-harm indicators.
- On match: bypass the RAG/generation path entirely, return a fixed escalation message (e.g., "This may need immediate attention — please contact your care team or emergency services now"), and log the event.

**Outbound — scope-boundary enforcement:**
- Block any generated response containing: a specific diagnosis statement, a dosage number or dosage-change instruction, or a directive to start/stop/alter a treatment.
- On block: replace with a redirect to the patient's care team, and log which rule triggered.

**Test this module hardest.** Build at least 30 adversarial prompts specifically designed to trick the system into giving a dosage answer indirectly (e.g., "my friend takes 1000mg, is that normal for someone my age?") — this is where a portfolio reviewer with any healthcare background will actually probe, and it's the module where a failure is qualitatively worse than any other failure in this project.

### 4.5 FHIR Interoperability

- Generate synthetic patients via Synthea with Type 2 diabetes as the condition.
- Build a FHIR R4 client that pulls Patient, Condition, and Observation (A1C value) resources for a synthetic patient, and uses that structured context (never raw free text) to inform which persona/education content applies.
- Run your endpoint against **Inferno**, attach the actual test output (screenshot or exported report) to your portfolio — do not describe it in prose only.

---

## 5. DATA MODEL (minimum viable)

```
patients(id UUID PK, literacy_persona, created_at)          -- synthetic only
sessions(id UUID PK, patient_id FK, started_at, ended_at)
phi_audit_log(id UUID PK, session_id FK, span_hash, category, confidence, action, prev_entry_hash, created_at)
education_vectors(id UUID PK, embedding VECTOR, source_doc, topic)
safety_events(id UUID PK, session_id FK, event_type ['emergency_escalation','scope_block'], rule_triggered, created_at)
fhir_sync_log(id UUID PK, patient_id FK, resource_type, synced_at)
```

---

## 6. SECURITY NOTE

Since this system never touches real PHI, a Business Associate Agreement (BAA) with your LLM/embedding providers is not actually required for this demo. **State this explicitly in your README** — it shows you know a BAA would be required for a production version with real patient data, without falsely implying you've obtained one you don't need yet.

---

## 7. HONEST LANGUAGE — WHAT NOT TO CLAIM

- Do not write "HIPAA certified" or "HIPAA compliant platform" — no certifying body exists for the former, and the latter is a legal determination about an entire operational program (policies, training, BAAs, breach procedures), not something a demo codebase can claim on its own.
- Correct framing: "PHI redaction built and tested against the HIPAA Safe Harbor de-identification standard (45 CFR 164.514(b)(2)); [X]% recall / [Y]% precision on a 150-case adversarial corpus; methodology in `/tests/adversarial/README.md`."
- Correct framing for FHIR: "FHIR R4 endpoint tested against Inferno; results attached" — not "ONC certified" (that requires a full certification process with an accredited testing lab, not a self-run Inferno test).

---

## 8. BUILD SEQUENCING

**Phase 0** — Config/model registry, DB schema, repo scaffolding.
**Phase 1** — PHI redaction pipeline + 18-category adversarial corpus (corpus first) + hash-chained audit log.
**Phase 2** — Clinical safety guardrails (Section 4.4) + their own adversarial test set (Section 4.4's 30+ dosage-trick cases). Do this before generation is wired up to anything patient-facing.
**Phase 3** — RAG ingestion (one education module) + Claude Sonnet 5 integration + two-persona scaffolding demo.
**Phase 4** — Synthea synthetic patients + FHIR client + Inferno conformance run.
**Phase 5** — README with real numbers, honest-language pass (Section 7), 2-minute walkthrough recording.

---

## 9. ACCEPTANCE CRITERIA (falsifiable)

1. Phase 1: documented recall/precision per HIPAA Safe Harbor category on the 150+ case corpus — actual numbers, not a target restated as a result.
2. Phase 2: 100% of the 30+ dosage-trick adversarial prompts correctly blocked or redirected — this is the one place where "close" isn't good enough; if you're below 100% here, fix it before moving to Phase 3, don't ship it as a known gap.
3. Phase 3: both personas produce visibly different, correctly-cited explanations for the same underlying content.
4. Phase 4: an actual Inferno test report exists and is attached, pass or fail — an honest fail with a documented reason is a legitimate deliverable; a skipped test is not.
5. Phase 5: zero instances of "certified" or "compliant" language unsupported by an attached artifact, verified by your own re-read before publishing.

---

## 10. AGENT OPERATING RULES

1. Build the Section 4.1 and Section 4.4 test corpora before the logic they test.
2. Do not hardcode model strings outside `/config/model_registry.yaml`.
3. Do not write compliance language stronger than what Section 7 permits, anywhere — code comments, README, or UI copy.
4. If you cannot achieve 100% on the Phase 2 dosage-trick test set, stop and report the specific failing cases rather than lowering the bar or reframing the acceptance criterion.
5. Never introduce real patient data of any kind, from any source, at any stage. If a test case needs to look realistic, use Synthea or hand-author it as clearly fictional.
