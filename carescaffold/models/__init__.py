"""SQLAlchemy 2.0 models for CareScaffold.

Spec §5 data model (7 tables):
  patients, sessions, phi_audit_log, education_vectors,
  safety_events, judge_verdicts, fhir_sync_log.

Every table uses UUID PK (stored as 36-char string in SQLite; native UUID
in PostgreSQL). FK constraints are enforced at the DB layer (PRAGMA
foreign_keys=ON, set in /core/db.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Declarative base for all CareScaffold models."""

    type_annotation_map: dict[type, Any] = {}


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    literacy_persona: Mapped[str] = mapped_column(String(32), nullable=False)
    # Possible values: "foundational" | "higher" (spec §4.2 personas)
    created_at: Mapped[datetime] = mapped_column(
        default=_utcnow, server_default=func.current_timestamp(), nullable=False
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patient_id: Mapped[str] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        default=_utcnow, server_default=func.current_timestamp(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(default=None, nullable=True)


class PhiAuditLog(Base):
    """Hash-chained append-only audit log of every redaction decision.

    Each row's prev_entry_hash is the SHA-256 of the previous row's
    content hash, making tampering detectable.
    """

    __tablename__ = "phi_audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    span_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 of (redacted_span_text + salt from env AUDIT_LOG_HASH_SALT)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    # One of the 18 HIPAA Safe Harbor categories (spec §4.1)
    confidence: Mapped[float] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # "redacted" | "tokenized" | "queued_for_review" | "passed_through"
    prev_entry_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # NULL for the first entry in the chain
    created_at: Mapped[datetime] = mapped_column(
        default=_utcnow, server_default=func.current_timestamp(), nullable=False
    )


class EducationVector(Base):
    """One row per source markdown file in /content/education_module/.

    The actual vector is stored in a sqlite-vec `vec0` virtual table
    (see /services/rag/ingest.py) keyed by the same rowid; this relational
    row holds metadata + the citation identifier (filename).
    """

    __tablename__ = "education_vectors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_file: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    # e.g. "01_diagnosis_basics.md" — used as citation in generated responses
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Original chunk text — stored for retrieval-time display
    embedding_dim: Mapped[int] = mapped_column(default=1024, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=_utcnow, server_default=func.current_timestamp(), nullable=False
    )


class SafetyEvent(Base):
    """Every emergency escalation and every outbound scope-block lands here.

    Phase 2 acceptance requires verifying emergency cases by querying this
    table, not by inspecting response text (spec §9.3).
    """

    __tablename__ = "safety_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # "emergency_escalation" | "scope_block"
    rule_or_layer_triggered: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. "inbound:chest_pain", "outbound:layer1:dosage_pattern", "outbound:layer2:judge"
    created_at: Mapped[datetime] = mapped_column(
        default=_utcnow, server_default=func.current_timestamp(), nullable=False
    )


class JudgeVerdict(Base):
    """Audit of every Layer 2 Claude Haiku 4.5 judge call (spec §4.4.2)."""

    __tablename__ = "judge_verdicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 of the response_text being judged
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    # "SAFE" | "BLOCK_DIAGNOSIS" | "BLOCK_DOSAGE" | "BLOCK_DIRECTIVE"
    created_at: Mapped[datetime] = mapped_column(
        default=_utcnow, server_default=func.current_timestamp(), nullable=False
    )


class FhirSyncLog(Base):
    """One row per FHIR resource pulled from Synthea fixtures into the app."""

    __tablename__ = "fhir_sync_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patient_id: Mapped[str] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # "Patient" | "Condition" | "Observation"
    synced_at: Mapped[datetime] = mapped_column(
        default=_utcnow, server_default=func.current_timestamp(), nullable=False
    )


# ── Vector virtual table — created via raw SQL in /core/db.py ───
# sqlite-vec vec0 tables live outside SQLAlchemy's metadata; created with
# `CREATE VIRTUAL TABLE education_vectors_vec USING vec0(embedding float[1024])`.
# See /services/rag/ingest.py for creation + insertion.
