"""FHIR client — load Synthea bundles, extract Patient/Condition/Observation.

Spec §4.5: Generate 20 Synthea patients with Type 2 diabetes as a
Condition. Build a FHIR R4 client that pulls Patient, Condition, and
Observation (A1C value) resources.

This module:
1. Reads the 20 T2D patient bundles from /fhir/synthea_data/output/fhir/
2. Extracts Patient, Condition, and A1C Observation resources from each
3. Loads them into an in-memory store keyed by patient_id (the FHIR
   endpoint will serve these read-only)
4. Records a fhir_sync_log row per resource pulled (spec §5)

Per spec §4.5: we expose ONLY these three resource types (plus a
CapabilityStatement). Synthea bundles contain many other resource types
(Encounter, Organization, Practitioner, etc.) — we deliberately ignore
them for this scope.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

log = logging.getLogger(__name__)

SYNTHEA_DATA_DIR = (
    Path(__file__).resolve().parent.parent.parent / "fhir" / "synthea_data"
)
BUNDLE_DIR = SYNTHEA_DATA_DIR / "output" / "fhir"
INDEX_PATH = SYNTHEA_DATA_DIR / "t2d_patients_index.json"

# A1C LOINC code (Hemoglobin A1c / Hemoglobin.total in Blood)
A1C_LOINC = "4548-4"

# SNOMED code for Type 2 diabetes mellitus
T2D_SNOMED = "44054006"


@dataclass
class FhirStore:
    """In-memory store of FHIR resources, keyed by resource type + id.

    Lives in memory for the demo. A production version would persist
    to PostgreSQL with a FHIR-specific schema (e.g., HAPI FHIR JPA server
    in Java, or fhirbase in PostgreSQL).
    """
    patients: Dict[str, dict]          # patient_id -> Patient resource
    conditions: Dict[str, dict]        # condition_id -> Condition resource
    observations: Dict[str, dict]       # observation_id -> Observation resource
    conditions_by_patient: Dict[str, List[str]]   # patient_id -> [condition_id]
    observations_by_patient: Dict[str, List[str]] # patient_id -> [observation_id]


_store: FhirStore | None = None


def _empty_store() -> FhirStore:
    return FhirStore(
        patients={},
        conditions={},
        observations={},
        conditions_by_patient={},
        observations_by_patient={},
    )


async def load_all() -> tuple[int, int, int]:
    """Load all 20 T2D bundles into memory + record fhir_sync_log rows.

    Returns (n_patients, n_conditions, n_observations).
    Idempotent: re-running replaces the in-memory store.
    """
    global _store
    _store = _empty_store()

    if not INDEX_PATH.exists():
        raise FileNotFoundError(
            f"T2D patients index not found: {INDEX_PATH}. "
            f"Run Synthea generation per /docs/phase4_inferno_report.md."
        )
    index = json.loads(INDEX_PATH.read_text())
    log.info("Loading %d T2D patient bundles from %s", len(index), BUNDLE_DIR)

    for entry in index:
        bundle_path = BUNDLE_DIR / entry["file"]
        if not bundle_path.exists():
            log.warning("Bundle file missing: %s; skipping", bundle_path)
            continue
        bundle = json.loads(bundle_path.read_text())
        _extract_resources(bundle, entry["patient_id"])

    # Record fhir_sync_log rows in the DB (spec §5 schema).
    # Also create rows in the patients table (spec §5) for each T2D patient
    # — they're referenced by fhir_sync_log.patient_id (FK).
    from sqlalchemy import text
    from core.db import get_session_factory
    from models import FhirSyncLog, Patient
    factory = get_session_factory()
    async with factory() as session:
        # Clear previous sync log + patients for clean reload
        await session.execute(text("DELETE FROM fhir_sync_log"))
        await session.execute(text("DELETE FROM patients"))

        # Insert a patients row for each T2D patient (spec §5).
        # literacy_persona is not known from FHIR data; we'll set it to
        # "foundational" by default (Phase 3 patients are persona-agnostic
        # for the FHIR scope — Phase 1 would derive this from assessment).
        for pid in _store.patients:
            session.add(Patient(id=pid, literacy_persona="foundational"))
        await session.flush()  # so FK rows can reference them

        for pid in _store.patients:
            session.add(FhirSyncLog(patient_id=pid, resource_type="Patient"))
            for cid in _store.conditions_by_patient.get(pid, []):
                session.add(FhirSyncLog(patient_id=pid, resource_type="Condition"))
            for oid in _store.observations_by_patient.get(pid, []):
                session.add(FhirSyncLog(patient_id=pid, resource_type="Observation"))
        await session.commit()

    n_p = len(_store.patients)
    n_c = sum(len(v) for v in _store.conditions_by_patient.values())
    n_o = sum(len(v) for v in _store.observations_by_patient.values())
    log.info(
        "FHIR load complete: %d patients, %d conditions, %d A1C observations",
        n_p, n_c, n_o,
    )
    return n_p, n_c, n_o


def _extract_resources(bundle: dict, expected_patient_id: str | None = None) -> None:
    """Pull Patient, Condition, A1C-Observation resources from one bundle."""
    assert _store is not None
    for entry in bundle.get("entry", []):
        r = entry.get("resource", {})
        rt = r.get("resourceType")
        rid = r.get("id")
        if not rt or not rid:
            continue

        if rt == "Patient":
            _store.patients[rid] = r
            _store.conditions_by_patient.setdefault(rid, [])
            _store.observations_by_patient.setdefault(rid, [])

        elif rt == "Condition":
            # Get subject patient id. Synthea uses "urn:uuid:PatientID" format.
            subject_ref = r.get("subject", {}).get("reference", "")
            pid = _resolve_patient_ref(subject_ref)
            if pid and pid in _store.patients:
                _store.conditions[rid] = r
                _store.conditions_by_patient.setdefault(pid, []).append(rid)

        elif rt == "Observation":
            # Only keep A1C observations (LOINC 4548-4)
            is_a1c = any(
                c.get("code") == A1C_LOINC
                for c in r.get("code", {}).get("coding", [])
            )
            if not is_a1c:
                continue
            subject_ref = r.get("subject", {}).get("reference", "")
            pid = _resolve_patient_ref(subject_ref)
            if pid and pid in _store.patients:
                _store.observations[rid] = r
                _store.observations_by_patient.setdefault(pid, []).append(rid)


def _resolve_patient_ref(ref: str) -> str | None:
    """Resolve a FHIR reference to a patient id.

    Synthea bundles use both formats:
    - "Patient/<id>" — standard FHIR
    - "urn:uuid:<id>" — Synthea's bundle-internal reference style

    Both should resolve to the patient id we keyed on.
    """
    if not ref:
        return None
    if ref.startswith("urn:uuid:"):
        return ref[len("urn:uuid:"):]
    if ref.startswith("Patient/"):
        return ref[len("Patient/"):]
    return None


def get_store() -> FhirStore:
    """Return the loaded FHIR store. Raises if load_all() hasn't run."""
    if _store is None:
        raise RuntimeError(
            "FHIR store not loaded. Call load_all() first "
            "(normally happens at app startup)."
        )
    return _store


# ── Read accessors for the endpoint ──────────────────────────────

def list_patients() -> List[dict]:
    """Return all Patient resources (20 total per spec §4.5)."""
    return list(get_store().patients.values())


def get_patient(pid: str) -> dict | None:
    return get_store().patients.get(pid)


def list_conditions(patient_id: str | None = None) -> List[dict]:
    """List Conditions, optionally filtered by patient_id (spec §4.5 search)."""
    store = get_store()
    if patient_id is None:
        return list(store.conditions.values())
    cids = store.conditions_by_patient.get(patient_id, [])
    return [store.conditions[cid] for cid in cids]


def get_condition(cid: str) -> dict | None:
    return get_store().conditions.get(cid)


def list_observations(patient_id: str | None = None) -> List[dict]:
    """List A1C Observations, optionally filtered by patient_id."""
    store = get_store()
    if patient_id is None:
        return list(store.observations.values())
    oids = store.observations_by_patient.get(patient_id, [])
    return [store.observations[oid] for oid in oids]


def get_observation(oid: str) -> dict | None:
    return get_store().observations.get(oid)
