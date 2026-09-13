# MASTER BUILD SPECIFICATION
## AI-Powered K–12 Adaptive Curriculum Platform (Gemini Backend)
### For execution by an autonomous coding agent (target: GLM-5.3)

---

## 0. HOW TO USE THIS DOCUMENT

You are an autonomous coding agent tasked with building this system. This document is both a functional spec and a set of hard constraints on your own behavior. Read Section 14 (Agent Operating Rules) before writing any code — it overrides your default instincts about how to satisfy acceptance criteria.

If any requirement in this document conflicts with your own capabilities, conflicts with another requirement, or is ambiguous enough that two reasonable implementations would diverge significantly, **stop and surface the conflict explicitly instead of silently picking one interpretation.** A wrong assumption here compounds across every module that depends on it.

---

## 1. MISSION & NON-NEGOTIABLE CONSTRAINTS

### 1.1 Mission
Build a privacy-compliant, AI-driven adaptive curriculum engine for 6th–12th grade students that personalizes instructional content in real time, integrates with school LMS platforms via LTI 1.3 / SCORM, and gives educators a live oversight and override layer.

### 1.2 Non-negotiable constraints (do not trade these away for velocity)
1. **No compliance claim without an evidence trail.** Never write an acceptance test, comment, or README line asserting "100% PII removal," "FERPA compliant," "COPPA compliant," or "IMS Global certified" unless the artifact backing that claim actually exists (a passed formal certification suite, a signed legal review, etc.). Where the real claim is "passes our internal test suite at X% recall," say exactly that.
2. **COPPA applies to this user base.** 6th graders are commonly 11 years old. Any student who may be under 13 requires a verifiable-parental-consent workflow before any personal data is processed or before any LLM call containing their inputs is made. This is a first-class module (Section 4.4), not an afterthought.
3. **Zero-trust is an enforced policy, not a name.** Every internal service-to-service call must be authenticated and authorized independently — no implicit trust because a request originated inside the VPC. Section 4.1 defines the actual policy enforcement points. If you can't implement a policy enforcement point for a given boundary, say so instead of leaving the boundary open and calling it zero-trust anyway.
4. **No hardcoded model version strings in business logic.** All LLM model identifiers live in an external, hot-reloadable config (Section 6.2). Google has deprecated or shut down a model roughly every 2–4 months over the past year; code that hardcodes `gemini-3.1-pro` inline in a service class will break on Google's schedule, not yours.
5. **Automated PII redaction is necessary but not sufficient.** Any redaction below a defined confidence threshold routes to a human review queue before the payload can reach an external LLM call. Section 4.2 defines the threshold and the escalation path.
6. **Build in the phased order in Section 11.** Do not attempt all modules in parallel. Each phase has a gate; do not start the next phase until the current phase's acceptance criteria (Section 12) are met.

---

## 2. SYSTEM ARCHITECTURE & TECH STACK

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2 | Async throughout; no sync DB calls in request handlers |
| Database | PostgreSQL 16+ with `pgvector` | Single source of truth for relational + vector data |
| Orchestration | LangChain or a thin custom router (your choice) — **do not** couple orchestration logic to a specific model string; route through the abstraction in Section 6 | |
| LLM Provider | **Google Gemini API only** (see Section 6 for full model registry and fallback design) | Single vendor by design — see Section 6.4 for the resilience trade-off this creates and how it's mitigated |
| Embeddings | `gemini-embedding-001` | 3072-dim default, MRL-scalable to 1536/768 for storage cost; 2048-token max input per call — chunk accordingly |
| Privacy Engine | Microsoft Presidio Analyzer/Anonymizer + spaCy NER + a domain-specific supplementary ruleset (Section 4.2) | Presidio/spaCy alone is not sufficient per Section 1.2.5 |
| Frontend | Next.js 14 (App Router), React, Tailwind CSS, TypeScript | |
| EdTech Protocols | LTI 1.3 Advantage (OIDC launch, AGS, NRPS), SCORM 1.2/2004 wrapper | "Conformant with," not "certified by," 1EdTech unless you actually run their formal conformance suite — see Section 8 |
| Secrets | External secrets manager (e.g., cloud KMS + secrets store) — never `.env` files in production paths | See Section 10 |

---

## 3. REPOSITORY STRUCTURE

```
/api            — FastAPI route definitions, request/response schemas
/core           — config loading, model registry, auth, zero-trust policy enforcement
/services       — business logic: personalization, RAG, moderation, LTI/SCORM, consent
/models         — SQLAlchemy/Pydantic models, DB schema
/privacy        — PII redaction pipeline, audit logging, consent workflow
/llm            — Gemini client wrapper, fallback router, prompt templates (versioned, not inline strings)
/tests
  /unit
  /integration
  /adversarial  — the expanded PII/adversarial test corpus (Section 12.2) lives here, separate from unit tests
/frontend       — Next.js app
/infra          — IaC, secrets manager config, deployment manifests
/config
  model_registry.yaml
  .env.example  (documented, not populated with real secrets)
```

---

## 4. CORE MODULES

### 4.1 Zero-Trust Policy Enforcement (operational definition)

Define and implement these concrete enforcement points — this replaces the vague "zero-trust" label from the original spec with testable behavior:

- **Service identity:** every internal service authenticates to every other internal service with short-lived signed tokens (not shared static API keys). No service call is trusted purely because it originates from inside the same network.
- **Per-request authorization:** every request to `/privacy`, `/llm`, and `/models` layers is checked against an explicit policy (who can call this, with what data scope) — not just "is this request authenticated."
- **Least-privilege DB roles:** the service account used by the RAG ingestion pipeline cannot write to the `consent_records` table; the service account used by the consent workflow cannot read curriculum vectors. Enforce this at the database role level, not just in application code.
- **Audit everything at the boundary**, not just at the LLM call — log every access to raw (pre-redaction) student data, who accessed it, and why, in the immutable log defined in 4.2.

### 4.2 PII Redaction & Audit Pipeline

**Pipeline:**
1. Intercept all student input server-side before it reaches any LLM call (internal or external).
2. Run Presidio + spaCy NER + a supplementary regex/dictionary layer tuned for K–12-specific identifiers (student ID formats, school-specific nicknames patterns, grade-level-appropriate slang that could leak identity).
3. **Confidence thresholding:** every detected-or-missed span gets a confidence score. Below a defined threshold (start at 0.85, tune against your adversarial corpus, document the chosen value and why), the entire input is routed to a **human review queue** rather than auto-redacted and sent onward. This is the mechanism that makes Section 1.2.5 real instead of aspirational.
4. Replace confirmed PII with synthetic tokens (`[STUDENT_NAME_1]`, `[LOCATION_A]`) using a session-scoped mapping table, never a global one.
5. Log every redaction decision (span, confidence, action taken) to an **append-only, hash-chained audit log** — each entry includes a hash of the previous entry so tampering is detectable, not just logged-and-hopeful.

**What "done" looks like (see Section 12.2 for the actual acceptance test):** a documented recall/precision rate on an adversarial test corpus of at least 200 cases (not 50) including edge cases the original spec didn't anticop for: nicknames, misspelled names, addresses embedded mid-sentence, non-US phone formats, and identifiers your students might use for each other (not just their own).

### 4.3 Adaptive Personalization & RAG Engine

- **Vector store:** Index state/national 6–12 curriculum benchmarks and lesson materials into `pgvector` using `gemini-embedding-001`. Chunk size must respect the 2048-token input ceiling per embedding call. Store the MRL truncation dimension used (768/1536/3072) as metadata alongside each vector, so you can re-embed consistently if you change dimension later.
- **Context ingestion:** combine retrieved curriculum chunks with the student's stored profile context (interests, reading level, past performance) — profile context must already be redacted/tokenized per Section 4.2 before it ever touches an LLM prompt.
- **Dynamic scaffolding:** generate personalized explanations/analogies keyed to student interest tags (e.g., sports, gaming, music) stored as enum values, not free text, to avoid re-introducing PII through the personalization layer itself.
- **Prompt templates live in `/llm/prompts/`, versioned by filename** (e.g., `scaffold_explain_v2.txt`), never inlined as Python string literals in service code — this is what makes prompt iteration reviewable and revertible.

### 4.4 COPPA Parental Consent Workflow (new module — not in the original spec)

- Age/grade capture at account creation determines whether a student is presumptively under 13.
- If so, **no personal data processing and no LLM call containing that student's inputs may occur** until a verifiable parental consent record exists (email verification is the floor; document if your district requires a stronger method — signed form, ID-verified, etc.).
- Consent state is a first-class field on the student record, checked by a policy enforcement point (Section 4.1) before the personalization pipeline runs — not a UI-only gate that the backend doesn't actually enforce.
- Consent revocation must trigger data deletion/anonymization within a defined SLA (document the SLA; don't leave it unspecified).

### 4.5 EdTech Interoperability

- **LTI 1.3 Advantage:** OIDC third-party launch, Assignment & Grade Services (AGS) for score sync to Canvas/Schoology/Google Classroom, Names & Role Provisioning Services (NRPS).
- **SCORM 1.2/2004** wrapper for non-LMS web runtime use.
- **Language discipline:** build against the 1EdTech conformance test suite and report the actual result. Do not describe the system as "IMS Global certified" unless certification was actually submitted and granted — that's a formal process with Google/1EdTech, not a byproduct of passing your own integration tests.

### 4.6 Safety Guardrails & Educator Oversight

- **Inbound moderation:** filter for inappropriate content, self-harm markers, cyberbullying, and prompt-injection attempts. **Do not rely on Gemini's built-in safety categories for this** — they're tuned for general harm categories, not classroom-specific behavioral signals. Build a supplementary classifier trained/tuned for this domain, and route anything it flags to human review, not just to a lower-confidence auto-response.
- **Outbound validation:** check LLM output against toxic language, hallucinated factual claims, and out-of-grade-level concepts before rendering to the student.
- **Teacher control panel:** real-time flagged-interaction feed, per-student difficulty override (grade 6–12 reading level), topic lock/override controls.
- **Escalation path:** define explicitly what happens when the inbound moderation layer flags a self-harm indicator — this needs a real escalation to a human (counselor/admin contact), not just a logged flag nobody reads in real time.

---

## 5. DATA MODEL (minimum viable schema)

```
students(id UUID PK, grade_level, reading_level, interest_tags[], consent_status, consent_verified_at, created_at)
consent_records(id UUID PK, student_id FK, method, verified_at, revoked_at NULLABLE)
sessions(id UUID PK, student_id FK, started_at, ended_at)
redaction_audit_log(id UUID PK, session_id FK, span_hash, confidence, action, prev_entry_hash, created_at)
curriculum_vectors(id UUID PK, embedding VECTOR, source_doc, grade_level, subject, embedding_dim)
flagged_interactions(id UUID PK, session_id FK, flag_type, confidence, reviewed_by NULLABLE, resolved_at NULLABLE)
lti_launches(id UUID PK, student_id FK, platform, launch_id, ags_score_synced BOOLEAN)
```

Every table touching student identity references `students.id` (a UUID) — never a name, email, or school-issued ID directly. If a service needs to resolve a UUID back to a real identity, that resolution must go through a single, heavily audited service, not ad hoc joins scattered across the codebase.

---

## 6. THE GEMINI LAYER

### 6.1 Model roles

| Role | Model (as of this writing — verify against 6.2 registry before deploying) | Rationale |
|---|---|---|
| Deep reasoning / narrative scaffolding | `gemini-3.1-pro` | Current top-tier Gemini reasoning model |
| Fast structured quiz generation, summaries | `gemini-3.7-flash` (or current equivalent Flash-tier model) | Cheapest tier with acceptable quality for structured, low-latency tasks |
| Embeddings | `gemini-embedding-001` | GA, MRL-scalable |

**[Flag to the human operator, not silently resolved by you as the agent]:** Google's Flash-tier naming has moved fast (3.5 → 3.6 → 3.7 within about 90 days as of mid-2026). Confirm the current recommended Flash model against the live API model list before your first production deploy, and again at every subsequent deploy — do not assume the model named above is still current.

### 6.2 Model registry (config-driven, not hardcoded)

```yaml
# /config/model_registry.yaml
roles:
  deep_reasoning:
    primary: gemini-3.1-pro
    fallback: gemini-3.7-flash
  fast_structured:
    primary: gemini-3.7-flash
    fallback: gemini-3.5-flash-lite
  embeddings:
    primary: gemini-embedding-001
    dimension: 1536
timeouts:
  latency_failover_ms: 2500
retry:
  max_attempts: 2
  on_status: [429, 500, 503]
```

All application code reads model identifiers from this file (or its runtime equivalent) via the router in `/llm/router.py`. Changing a model in response to a Google deprecation notice should be a one-line config change and a redeploy, never a code change scattered across service files.

### 6.3 Fallback design (intra-Gemini, since cross-vendor fallback no longer exists)

On timeout (>2.5s) or HTTP 429/500/503 from the primary model in a role, retry against that role's fallback model before surfacing an error to the user. Log every fallback event — a spike in fallback triggers is your leading indicator of a Google-side incident or an impending deprecation, and should page someone, not just increment a silent counter.

### 6.4 The trade-off you're accepting by going single-vendor

Going all-in on Gemini simplifies auth, billing, and schema handling (native structured output via `responseSchema`). The direct cost is that a Gemini-wide outage has no external fallback — only degradation within Gemini's own tiers. Mitigate this with: (a) the intra-tier fallback above, (b) a cached "last known good" response path for common personalization requests so a full outage degrades to stale-but-functional rather than fully down, and (c) an explicit runbook for what the product does during a sustained Gemini outage (read-only mode, cached content only, etc.) — write this runbook before you need it, not during an incident.

---

## 7. SECURITY & SECRETS

- API keys and DB credentials live in a secrets manager, retrieved at runtime, never committed or baked into images.
- Key rotation policy: document the rotation interval and automate it — a policy that exists only in a wiki page is not a policy.
- Encryption at rest for the database and at rest for the audit log specifically (it's the highest-value target in this system — it's the map from anonymized tokens back to real identities' behavior).
- Data residency: confirm with each pilot school district whether in-region or in-country data storage is required, and confirm Gemini API's regional processing options match that requirement before onboarding that district. Do not assume this is a non-issue.

---

## 8. HONEST LANGUAGE FOR CERTIFICATIONS

Anywhere the original spec or your own generated docs use "certified," replace with the actual verified status:
- "1EdTech LTI 1.3 conformant (self-tested against the official test suite; formal certification not yet submitted)" — unless certification was actually obtained.
- "FERPA-aligned architecture (internal compliance review passed on [date] by [reviewer]; not a substitute for district-level legal sign-off)."
- "COPPA consent workflow implemented per Section 4.4; verified-parental-consent method: [method]."

---

## 9. BUILD SEQUENCING (do not parallelize across phases)

**Phase 0 — Foundations.** Auth, DB schema, secrets manager wiring, model registry abstraction (Section 6.2), zero-trust policy enforcement points (Section 4.1). Nothing else starts until this phase's tests pass.

**Phase 1 — Privacy pipeline.** PII redaction, confidence thresholding, human review queue, hash-chained audit log. Build the adversarial test corpus (Section 12.2) *before* the redaction logic, not after.

**Phase 2 — RAG core.** Curriculum ingestion, `gemini-embedding-001` integration, retrieval demo across two student profiles on one 7th-grade science lesson (matches original spec's Section 4.3 deliverable).

**Phase 3 — LLM router & personalization.** Gemini role router with fallback (Section 6.3), dynamic scaffolding service.

**Phase 4 — EdTech interoperability.** LTI 1.3 launch flow, AGS, NRPS, SCORM wrapper, run against 1EdTech's actual test tools.

**Phase 5 — Safety guardrails & educator dashboard.** Inbound/outbound moderation, domain-specific classifier, teacher control panel, escalation path.

**Phase 6 — COPPA consent workflow.** Full consent capture, enforcement, revocation/deletion SLA.

**Phase 7 — Compliance review gate.** Human legal/compliance review of Phases 1 and 6 specifically, before any real student data touches the system. This phase cannot be automated away — it requires a human sign-off artifact.

---

## 10. ACCEPTANCE CRITERIA (revised — falsifiable, phase-gated)

1. Phase 0: all internal service calls fail closed (403) when the calling service's identity token is missing or invalid — demonstrated with a test, not asserted.
2. Phase 1: PII redaction pipeline achieves a documented recall/precision on the 200+-case adversarial corpus (target and actual both reported — do not round up); every sub-threshold case is demonstrably routed to the human review queue in a test.
3. Phase 2: retrieval demo produces visibly different scaffolded explanations for two student profiles on the same lesson, with citations back to source curriculum chunks.
4. Phase 3: fallback triggers correctly in a simulated primary-model timeout/error test for each role.
5. Phase 4: LTI 1.3 payload exchange validated against actual 1EdTech reference tooling, with the real pass/fail output attached — not a paraphrase of it.
6. Phase 5: a simulated self-harm-marker input reaches a human escalation contact in the test environment within the defined SLA.
7. Phase 6: a simulated under-13 student cannot trigger any LLM call until consent is recorded; consent revocation triggers deletion within the documented SLA, demonstrated with a test.
8. Phase 7: signed-off compliance review artifact exists for Phases 1 and 6 before Phase 7 is marked complete.

---

## 11. TESTING REQUIREMENTS

- Unit tests per module, per FastAPI convention.
- The adversarial PII corpus (Section 4.2, 200+ cases) lives separately from unit tests and is run as its own CI gate — a regression here should block deploy, not just log a warning.
- Integration tests for the LTI/SCORM flows against real (sandboxed) LMS test environments, not mocked responses only.
- Load/latency test for the Gemini fallback path specifically — confirm the 2.5s failover threshold behaves correctly under realistic latency variance, not just in a unit test with a mocked delay.

---

## 12. AGENT OPERATING RULES (meta — governs how you work, not what you build)

1. Do not mark an acceptance criterion "met" by writing a test that trivially passes against your own implementation's happy path. The adversarial corpus and the compliance-language rules in Section 8 exist specifically to prevent this failure mode.
2. Do not hardcode a Gemini model string anywhere outside `/config/model_registry.yaml` and the router that reads it.
3. When you hit an ambiguity this document doesn't resolve (e.g., what parental-consent verification method to use, what data-residency requirement a specific district has), stop and ask rather than picking a default and moving on.
4. Do not claim a compliance status ("FERPA compliant," "certified") anywhere in code comments, README, or user-facing copy unless the backing evidence specified in Section 8 actually exists in the repo.
5. Build the test corpus for Phase 1 before the redaction logic it tests. Building both simultaneously invites unconsciously tuning the corpus to the implementation.
6. Log every fallback event and every human-review-queue escalation with enough context that a human debugging six months from now can reconstruct what happened without re-reading this whole spec.

---

## APPENDIX A: `.env.example` (documented, not populated)

```
# Secrets are retrieved from the secrets manager at runtime.
# This file documents required keys only — do not populate with real values.
GEMINI_API_KEY=            # retrieved from secrets manager, not set here in production
DATABASE_URL=
MODEL_REGISTRY_PATH=./config/model_registry.yaml
AUDIT_LOG_HASH_SALT=       # retrieved from secrets manager
```

## APPENDIX B: Gemini client wrapper — expected shape (pseudocode, not literal implementation)

```python
# /llm/router.py
class GeminiRouter:
    def __init__(self, registry_path: str):
        self.registry = load_yaml(registry_path)  # hot-reloadable, not baked at import time

    async def call(self, role: str, prompt: str, schema: dict | None = None):
        primary, fallback = self._models_for(role)
        try:
            return await self._call_model(primary, prompt, schema, timeout=self.registry["timeouts"]["latency_failover_ms"])
        except (TimeoutError, RateLimitError, ServerError) as e:
            log_fallback_event(role, primary, fallback, reason=str(e))
            return await self._call_model(fallback, prompt, schema)
```
