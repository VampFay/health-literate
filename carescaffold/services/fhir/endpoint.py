"""FHIR R4 endpoint — read-only Patient/Condition/Observation + CapabilityStatement.

Spec §4.5: read-only endpoint exposing exactly three resource types
plus a minimal CapabilityStatement.

Endpoints:
  GET /fhir/metadata                       — CapabilityStatement
  GET /fhir/Patient                        — list all 20 T2D patients
  GET /fhir/Patient/{id}                   — single patient
  GET /fhir/Condition                      — list conditions (with ?patient= search)
  GET /fhir/Condition/{id}                 — single condition
  GET /fhir/Observation                    — list A1C observations (with ?patient= search)
  GET /fhir/Observation/{id}               — single observation

All responses are FHIR R4 conformant JSON. The endpoint is read-only;
no POST/PUT/DELETE.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from services.fhir import client as fhir_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/fhir", tags=["fhir"])


# ── CapabilityStatement (spec §4.5) ───────────────────────────────

FHIR_R4_CAPABILITY_STATEMENT = {
    "resourceType": "CapabilityStatement",
    "status": "active",
    "date": "2026-09-19",
    "kind": "instance",
    "fhirVersion": "4.0.1",
    "format": ["json"],
    "rest": [
        {
            "mode": "server",
            "resource": [
                {
                    "type": "Patient",
                    "interaction": [
                        {"code": "read"},
                        {"code": "search-type"},
                    ],
                    "searchParam": [
                        {"name": "_id", "type": "token"},
                    ],
                },
                {
                    "type": "Condition",
                    "interaction": [
                        {"code": "read"},
                        {"code": "search-type"},
                    ],
                    "searchParam": [
                        {"name": "_id", "type": "token"},
                        {"name": "patient", "type": "reference"},
                    ],
                },
                {
                    "type": "Observation",
                    "interaction": [
                        {"code": "read"},
                        {"code": "search-type"},
                    ],
                    "searchParam": [
                        {"name": "_id", "type": "token"},
                        {"name": "patient", "type": "reference"},
                        {"name": "code", "type": "token"},
                    ],
                },
            ],
        }
    ],
}


@router.get("/metadata")
async def get_metadata() -> dict:
    """FHIR R4 CapabilityStatement (read-only, 3 resource types)."""
    return FHIR_R4_CAPABILITY_STATEMENT


# ── Patient ───────────────────────────────────────────────────────

@router.get("/Patient")
async def list_patients() -> dict:
    """Return all 20 T2D Patient resources as a FHIR Bundle."""
    patients = fhir_client.list_patients()
    return _bundle(patients, "searchset")


@router.get("/Patient/{patient_id}")
async def get_patient(patient_id: str) -> dict:
    p = fhir_client.get_patient(patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Patient/{patient_id} not found")
    return p


# ── Condition ────────────────────────────────────────────────────

@router.get("/Condition")
async def list_conditions(patient: str | None = Query(default=None)) -> dict:
    """List Conditions, optionally filtered by ?patient=PatientID."""
    conditions = fhir_client.list_conditions(patient_id=patient)
    return _bundle(conditions, "searchset")


@router.get("/Condition/{condition_id}")
async def get_condition(condition_id: str) -> dict:
    c = fhir_client.get_condition(condition_id)
    if c is None:
        raise HTTPException(status_code=404, detail=f"Condition/{condition_id} not found")
    return c


# ── Observation (A1C only per spec §4.5) ─────────────────────────

@router.get("/Observation")
async def list_observations(patient: str | None = Query(default=None)) -> dict:
    """List A1C Observations, optionally filtered by ?patient=PatientID."""
    observations = fhir_client.list_observations(patient_id=patient)
    return _bundle(observations, "searchset")


@router.get("/Observation/{observation_id}")
async def get_observation(observation_id: str) -> dict:
    o = fhir_client.get_observation(observation_id)
    if o is None:
        raise HTTPException(status_code=404, detail=f"Observation/{observation_id} not found")
    return o


# ── Helpers ─────────────────────────────────────────────────────

def _bundle(resources: list[dict], bundle_type: str = "searchset") -> dict:
    """Wrap a list of FHIR resources in a FHIR R4 Bundle."""
    return {
        "resourceType": "Bundle",
        "type": bundle_type,
        "total": len(resources),
        "entry": [
            {"fullUrl": f"urn:uuid:{r.get('id', '')}", "resource": r}
            for r in resources
        ],
    }
