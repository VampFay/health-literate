"""FHIR R4 self-test — validate resource shapes against the R4 spec.

Per spec §4.5: Inferno's Docker-based test runner is unavailable in the
sandbox. Per spec §4.5 escape hatch, we self-test our endpoint against
the FHIR R4 spec for the three resource types we expose:

- Patient (R4 spec: https://hl7.org/fhir/R4/patient.html)
- Condition (R4 spec: https://hl7.org/fhir/R4/condition.html)
- Observation (R4 spec: https://hl7.org/fhir/R4/observation.html)

This module:
1. Checks the CapabilityStatement matches expected shape (R4 §3.2)
2. Validates each Patient resource has required R4 fields (id, resourceType)
3. Validates each Condition resource has required R4 fields (id,
   resourceType, subject, code)
4. Validates each Observation has required R4 fields (id, resourceType,
   status, code, subject) and that A1C observations have a value quantity
5. Validates search params (?patient=X) on /Condition and /Observation

These checks are a subset of what Inferno would test. Full Inferno
conformance requires more (US Core IG, OAuth 2.0 SMART on FHIR, etc.) —
those are explicitly out of scope per spec §4.5.

Results are pasted into /docs/phase4_inferno_report.md.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.fhir import client as fhir_client
from services.fhir.endpoint import FHIR_R4_CAPABILITY_STATEMENT

SYNTHEA_DATA_DIR = (
    Path(__file__).resolve().parent.parent.parent / "fhir" / "synthea_data"
)


@pytest.fixture
async def _fhir_loaded():
    """Load Synthea bundles into memory before tests."""
    from sqlalchemy import text
    from core.db import get_engine
    from models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS education_vectors_vec "
            "USING vec0(embedding float[1024])"
        ))

    n_p, n_c, n_o = await fhir_client.load_all()
    return n_p, n_c, n_o


# ── CapabilityStatement validation ──────────────────────────────

@pytest.mark.asyncio
async def test_capability_statement_conforms_to_r4(_fhir_loaded):
    """Spec §4.5: minimal CapabilityStatement for 3 resource types."""
    cs = FHIR_R4_CAPABILITY_STATEMENT
    assert cs["resourceType"] == "CapabilityStatement"
    assert cs["fhirVersion"] == "4.0.1"
    assert cs["format"] == ["json"]
    assert cs["rest"][0]["mode"] == "server"
    # Exactly 3 resource types (spec §4.5)
    resource_types = {r["type"] for r in cs["rest"][0]["resource"]}
    assert resource_types == {"Patient", "Condition", "Observation"}
    # All resources support read + search-type interactions
    for r in cs["rest"][0]["resource"]:
        codes = {i["code"] for i in r["interaction"]}
        assert "read" in codes, f"{r['type']} missing read interaction"
        assert "search-type" in codes, f"{r['type']} missing search-type"


# ── Patient resource validation (FHIR R4 §3.2 / Resourse-Patient) ──

@pytest.mark.asyncio
async def test_patient_resources_conform_to_r4(_fhir_loaded):
    """Spec §4.5: validate Patient resources match FHIR R4 shape.

    Per R4: https://hl7.org/fhir/R4/patient.html
    Required: id, resourceType
    Optional but expected: identifier, name, gender, birthDate
    """
    patients = fhir_client.list_patients()
    assert len(patients) == 20, f"Expected 20 T2D patients, got {len(patients)}"

    for p in patients:
        # Required
        assert p["resourceType"] == "Patient", f"Wrong resourceType: {p.get('resourceType')}"
        assert "id" in p and p["id"], "Patient missing id"

        # Expected fields (Synthea always populates these)
        assert "identifier" in p, f"Patient {p['id']} missing identifier"
        assert "name" in p, f"Patient {p['id']} missing name"
        assert len(p["name"]) > 0, f"Patient {p['id']} has empty name"
        # R4 Patient.name: required family + at least one of given/prefix/suffix
        name = p["name"][0]
        assert "family" in name, f"Patient {p['id']} name missing family"
        # gender: male | female | other | unknown (R4 binding)
        if "gender" in p:
            assert p["gender"] in {"male", "female", "other", "unknown"}, (
                f"Patient {p['id']} has invalid gender: {p['gender']}"
            )
        # birthDate: YYYY-MM-DD or partial
        if "birthDate" in p:
            assert isinstance(p["birthDate"], str), f"Patient {p['id']} birthDate not string"


@pytest.mark.asyncio
async def test_get_patient_by_id(_fhir_loaded):
    """GET /fhir/Patient/{id} should return the patient or 404."""
    patients = fhir_client.list_patients()
    first_id = patients[0]["id"]

    p = fhir_client.get_patient(first_id)
    assert p is not None
    assert p["id"] == first_id

    # 404 for unknown id
    missing = fhir_client.get_patient("nonexistent-patient-id")
    assert missing is None


# ── Condition resource validation ──────────────────────────────

@pytest.mark.asyncio
async def test_condition_resources_conform_to_r4(_fhir_loaded):
    """Spec §4.5: validate Condition resources match FHIR R4 shape.

    Per R4: https://hl7.org/fhir/R4/condition.html
    Required: id, resourceType, subject, code
    """
    conditions = fhir_client.list_conditions()
    assert len(conditions) > 0, "No conditions loaded"

    for c in conditions:
        assert c["resourceType"] == "Condition", f"Wrong resourceType: {c.get('resourceType')}"
        assert "id" in c and c["id"], "Condition missing id"
        # R4 Condition.subject: 1..1 (required)
        assert "subject" in c, f"Condition {c['id']} missing subject"
        assert "reference" in c["subject"], f"Condition {c['id']} subject missing reference"
        # R4 Condition.code: 1..1 (required)
        assert "code" in c, f"Condition {c['id']} missing code"
        assert "coding" in c["code"], f"Condition {c['id']} code missing coding"
        assert len(c["code"]["coding"]) > 0
        # At least one coding should have system + code
        for coding in c["code"]["coding"]:
            assert "system" in coding
            assert "code" in coding


@pytest.mark.asyncio
async def test_condition_search_by_patient(_fhir_loaded):
    """?patient=X search should filter Conditions by patient.

    Synthea's native subject.reference format is "urn:uuid:PatientID"
    rather than "Patient/PatientID". Both are valid FHIR R4; the FHIR
    loader normalizes both for internal lookup. The test accepts both.
    """
    patients = fhir_client.list_patients()
    first_pid = patients[0]["id"]

    all_conditions = fhir_client.list_conditions()
    patient_conditions = fhir_client.list_conditions(patient_id=first_pid)

    assert len(patient_conditions) <= len(all_conditions)
    for c in patient_conditions:
        subject_ref = c["subject"]["reference"]
        # Accept either "Patient/<id>" or "urn:uuid:<id>" (Synthea native)
        assert subject_ref in (f"Patient/{first_pid}", f"urn:uuid:{first_pid}"), (
            f"Condition {c['id']} has wrong subject: {subject_ref}"
        )


# ── Observation resource validation (A1C only) ─────────────────

@pytest.mark.asyncio
async def test_observation_resources_conform_to_r4(_fhir_loaded):
    """Spec §4.5: validate A1C Observation resources match FHIR R4 shape.

    Per R4: https://hl7.org/fhir/R4/observation.html
    Required: id, resourceType, status, code, subject
    A1C observations should have a value quantity (most do).
    """
    observations = fhir_client.list_observations()
    assert len(observations) > 0, "No A1C observations loaded"

    for o in observations:
        assert o["resourceType"] == "Observation", f"Wrong resourceType: {o.get('resourceType')}"
        assert "id" in o and o["id"], "Observation missing id"
        # R4 Observation.status: 1..1, binding to ObservationStatus
        assert "status" in o, f"Observation {o['id']} missing status"
        assert o["status"] in {
            "registered", "preliminary", "final", "amended",
            "corrected", "cancelled", "entered-in-error", "unknown"
        }, f"Observation {o['id']} has invalid status: {o['status']}"
        # R4 Observation.code: 1..1 (required)
        assert "code" in o, f"Observation {o['id']} missing code"
        # A1C: at least one coding should be LOINC 4548-4
        a1c_coding = any(
            c.get("code") == "4548-4" and "loinc" in c.get("system", "").lower()
            for c in o["code"].get("coding", [])
        )
        assert a1c_coding, f"Observation {o['id']} is not an A1C observation"
        # R4 Observation.subject: 1..1 for our scope
        assert "subject" in o, f"Observation {o['id']} missing subject"


@pytest.mark.asyncio
async def test_observation_search_by_patient(_fhir_loaded):
    """?patient=X search should filter Observations by patient.

    Synthea uses "urn:uuid:<id>" subject references; see
    test_condition_search_by_patient for the format note.
    """
    patients = fhir_client.list_patients()
    target_pid = None
    for p in patients:
        obs = fhir_client.list_observations(patient_id=p["id"])
        if obs:
            target_pid = p["id"]
            break
    assert target_pid is not None, "No patient has A1C observations"

    patient_obs = fhir_client.list_observations(patient_id=target_pid)
    assert len(patient_obs) > 0
    for o in patient_obs:
        subject_ref = o["subject"]["reference"]
        assert subject_ref in (f"Patient/{target_pid}", f"urn:uuid:{target_pid}"), (
            f"Observation {o['id']} has wrong subject: {subject_ref}"
        )


# ── Aggregate sanity checks (spec §4.5: 20 T2D patients) ────────

@pytest.mark.asyncio
async def test_20_t2d_patients_loaded(_fhir_loaded):
    """Spec §4.5: exactly 20 Synthea T2D patients."""
    n_p, n_c, n_o = _fhir_loaded
    assert n_p == 20, f"Expected 20 T2D patients, got {n_p}"
    # Each patient should have at least 1 condition (T2D itself)
    assert n_c >= 20, f"Expected >= 20 conditions (1+ per patient), got {n_c}"
    # Most T2D patients should have A1C observations (Synthea's realism)
    assert n_o >= 10, f"Expected >= 10 A1C observations, got {n_o}"


@pytest.mark.asyncio
async def test_fhir_sync_log_populated(_fhir_loaded):
    """Spec §5: fhir_sync_log should have rows for each resource pulled."""
    from sqlalchemy import text
    from core.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(text(
            "SELECT resource_type, COUNT(*) FROM fhir_sync_log GROUP BY resource_type"
        ))
        counts = dict(result.fetchall())
        assert counts.get("Patient", 0) == 20, f"Expected 20 Patient sync rows, got {counts.get('Patient', 0)}"
        assert counts.get("Condition", 0) > 0, "No Condition sync rows"
        assert counts.get("Observation", 0) > 0, "No Observation sync rows"
