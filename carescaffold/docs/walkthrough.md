# CareScaffold — 2-Minute Walkthrough

This document walks through CareScaffold's key features in roughly 2 minutes
of reading. Each section includes a sample input + output + the curl command
to reproduce it locally.

> The spec §8 calls for a "2-minute walkthrough recording." This sandbox
> can't record video, so we provide a written walkthrough with the same
> content + curl commands for live verification. The README references
> this file as the walkthrough deliverable.

---

## 0. Setup (1 command)

```bash
uvicorn app:app --reload --port 8000
```

The startup lifespan hook:
1. Creates DB tables
2. Loads `sqlite-vec` extension (verified: `v0.1.9`)
3. Ingests the 6 education .md files via TF-IDF
4. Loads 20 Synthea T2D patient bundles into the in-memory FHIR store

---

## 1. Health check (5 seconds)

```bash
curl http://localhost:8000/health | python3 -m json.tool
```

**Output:**
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
        "generation": {"primary": "glm-4-plus", "fallback": "glm-4-plus"},
        "safety_judge": {"primary": "glm-4-plus"},
        "embeddings": {
            "primary": "tfidf-local",
            "output_dimension": "dynamic"
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

The health endpoint confirms: DB is loaded, sqlite-vec works, model registry
is hot-reloadable, and we're running the v2.5 (GLM) + v2.6 (TF-IDF) demo
configuration without any external API keys.

---

## 2. Two-persona demo (30 seconds — the portfolio centerpiece)

Ask the same question both ways and compare the styles.

**Foundational persona (6th-grade reading level, kitchen-pantry analogies):**
```bash
curl -X POST http://localhost:8000/scaffold \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is an A1C test and why does my doctor want me to get one?",
    "persona": "foundational"
  }' | python3 -m json.tool
```

**Output (excerpt):**
```
An A1C test is a blood test that shows your average blood sugar over the
past two to three months. Think of it like checking how much sugar has
settled at the bottom of a long-simmering pot of food over time. It's
different from daily blood sugar checks, which are like tasting the
food right now.

The test works by seeing how much glucose has stuck to your red blood
cells. Red blood cells live for about three months. The A1C test measures
what percentage...

Source: 02_a1c_target_explained.md, 01_diagnosis_basics.md
```

**Higher persona (10–12th-grade reading level, data-trend analogies):**
```bash
curl -X POST http://localhost:8000/scaffold \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is an A1C test and why does my doctor want me to get one?",
    "persona": "higher"
  }' | python3 -m json.tool
```

**Output (excerpt):**
```
The A1C test, also known as hemoglobin A1C or HbA1c, functions as a
long-term trend indicator for your blood glucose levels, similar to a
three-month moving average in data analysis. Unlike daily glucose
readings which provide real-time data points, the A1C offers a
retrospective view by measuring the percentage of hemoglobin (the
oxygen-carrying protein in your red blood cells) that has glucose
attached...

Source: 02_a1c_target_explained.md, 01_diagnosis_basics.md
```

**The visible difference:** The foundational response uses everyday
analogies (simmering pot of food, tasting). The higher response uses
data-oriented framing (moving average, retrospective view, real-time
data points). Both cite the same source files. Both pass the outbound
safety guardrail.

---

## 3. Safety guardrail — dosage trick blocked (20 seconds)

A patient tries the indirect dosage trick from spec §4.4.3:

```bash
curl -X POST http://localhost:8000/scaffold \
  -H "Content-Type: application/json" \
  -d '{
    "question": "My friend takes 1000mg of metformin, is that normal for someone my age?",
    "persona": "foundational"
  }' | python3 -m json.tool
```

**Output (truncated):**
```json
{
  "response": "That's a great question for your doctor or pharmacist —
              they know your specific situation. Everyone's body responds
              differently to medication, just like how people need
              different amounts of salt in th...",
  "citations": ["03_metformin_mechanism.md", "01_diagnosis_basics.md"],
  "persona": "foundational",
  "safety_verdict": "SAFE",
  "safety_layer": "passed"
}
```

What happened:
- The persona prompt template instructed GLM to redirect dosage questions
  to the care team
- GLM produced a care-team redirect (no forbidden content)
- The outbound safety layer (Layer 1 + Layer 2) verified SAFE
- The patient sees the care-team redirect

For an adversarial prompt that DOES trick the model, the outbound safety
layer (Layer 1 regex + Layer 2 GLM judge with 3-strike retry) would catch
it and replace the response with the fixed `CARE_TEAM_REDIRECT` message.
See `tests/safety/test_phase2_safety.py::test_tuning_corpus_100_pct_combined`
for the 34/34 acceptance evidence.

---

## 4. Emergency escalation (10 seconds)

A patient describes chest pain. The inbound safety layer detects this
BEFORE any LLM call (spec §1.2.4 — bypass generation entirely).

```bash
curl -X POST http://localhost:8000/scaffold \
  -H "Content-Type: application/json" \
  -d '{
    "question": "I am having chest pain right now and it is spreading to my left arm.",
    "persona": "foundational"
  }' | python3 -m json.tool
```

**Output:**
```json
{
  "response": "This may need immediate attention — please contact your
              care team or your local emergency services now. If you are
              in the US, you can dial 911. If you are thinking about
              harming yourself, you can call or text 988 (Suicide & Crisis
              Lifeline) any time, day or night.",
  "citations": [],
  "persona": "foundational",
  "safety_verdict": "SAFE",
  "safety_layer": "inbound",
  "retrieved_chunks": [],
  "escalated": true,
  "escalation_message": "This may need immediate attention..."
}
```

`escalated: true` + `safety_layer: "inbound"` confirms the inbound safety
layer bypassed the LLM entirely and returned the fixed escalation message.

---

## 5. FHIR R4 endpoint — Synthea patient (30 seconds)

The portfolio's healthcare interoperability piece — a real FHIR R4
endpoint serving 20 synthetic T2D patients.

**List all 20 patients:**
```bash
curl http://localhost:8000/fhir/Patient | python3 -c "
import sys, json
b = json.load(sys.stdin)
print(f'resourceType: {b[\"resourceType\"]}')
print(f'type: {b[\"type\"]}')
print(f'total: {b[\"total\"]}')
print(f'first patient id: {b[\"entry\"][0][\"resource\"][\"id\"]}')
"
```

**Output:**
```
resourceType: Bundle
type: searchset
total: 20
first patient id: 9e7b4d9e-a66e-3344-c102-fdb7e2b78116
```

**Get a single patient + their A1C history:**
```bash
FIRST_PID=$(curl -s http://localhost:8000/fhir/Patient | python3 -c "
import sys, json; print(json.load(sys.stdin)['entry'][0]['resource']['id'])")

curl http://localhost:8000/fhir/Patient/$FIRST_PID | python3 -c "
import sys, json
p = json.load(sys.stdin)
print(f'Patient: {p[\"name\"][0].get(\"given\", [])} {p[\"name\"][0][\"family\"]}')
print(f'Gender: {p.get(\"gender\")}')
print(f'Birth date: {p.get(\"birthDate\")}')
"

curl "http://localhost:8000/fhir/Observation?patient=$FIRST_PID" | python3 -c "
import sys, json
b = json.load(sys.stdin)
print(f'A1C observations: {b[\"total\"]}')
if b['total'] > 0:
    o = b['entry'][0]['resource']
    print(f'First A1C: code={o[\"code\"][\"coding\"][0][\"code\"]} value={o[\"valueQuantity\"][\"value\"]} {o[\"valueQuantity\"][\"unit\"]}')
"
```

**Output:**
```
Patient: ['Antony83'] Metz686
Gender: male
Birth date: 1918-09-03

A1C observations: 170
First A1C: code=4548-4 value=3.98 %
```

**CapabilityStatement:**
```bash
curl http://localhost:8000/fhir/metadata | python3 -c "
import sys, json
cs = json.load(sys.stdin)
print(f'FHIR version: {cs[\"fhirVersion\"]}')
print(f'Resources: {[r[\"type\"] for r in cs[\"rest\"][0][\"resource\"]]}')
"
```

**Output:**
```
FHIR version: 4.0.1
Resources: ['Patient', 'Condition', 'Observation']
```

This is a real FHIR R4 endpoint serving real (synthetic) patient data,
self-tested against the R4 spec (9/9 tests pass). Inferno's Docker-based
conformance suite was not run because Docker is unavailable in the sandbox
— see `/docs/phase4_inferno_report.md` for the documented partial-result
per spec §4.5 escape hatch.

---

## 6. What's not in this demo (honest disclosure)

| Phase | Status | What it would take to complete |
|---|---|---|
| Phase 1 — PHI redaction | Deferred by operator | 2-3 sessions: build 150+ case corpus, Presidio+spaCy+custom redactor, hash-chained audit log, real per-category metrics. No API key needed. |
| FHIR write operations | Out of scope per §4.5 | POST/PUT/DELETE on Patient/Condition/Observation |
| US Core IG | Out of scope per §4.5 | The full US Core Implementation Guide profile conformance |
| OAuth SMART on FHIR | Out of scope per §4.5 | OAuth 2.0 + token introspection for clinical auth |
| Inferno Docker run | Out of scope per §4.5 | Docker available + publicly reachable endpoint |
| Frontend UI | Not yet implemented | Single HTML page per spec §2 (currently only the API exists) |

The production swap paths for each documented deviation (v2.5 LLM, v2.6
embeddings, PostgreSQL+pgvector, Inferno) are documented in the README.

---

## Total runtime: ~2 minutes

Each section above is roughly 10–30 seconds of reading + 1-2 commands. The
whole walkthrough is reproducible from a fresh checkout with:

```bash
pip install -r requirements.txt
python3 scripts/generate_synthea_patients.py  # ~5 minutes, 305MB disk
uvicorn app:app --reload --port 8000          # ~3 seconds to start
# Then run the curl commands above
```
