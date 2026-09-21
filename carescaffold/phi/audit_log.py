"""Append-only, hash-chained audit log (spec §4.1).

Pipeline step 5: "Log every redaction decision (span, confidence, action
taken) to an append-only, hash-chained audit log — each entry includes
a hash of the previous entry so tampering is detectable."

Each row of `phi_audit_log` (spec §5 schema):
  id: UUID
  session_id: FK
  span_hash: SHA-256 of (redacted_span_text + salt from env AUDIT_LOG_HASH_SALT)
  category: short name (one of 18)
  confidence: float
  action: "redacted" | "tokenized" | "queued_for_review" | "passed_through"
  prev_entry_hash: SHA-256 of the previous row's content hash
  created_at: timestamp

Tampering detection: each row's prev_entry_hash = hash of the previous
row's (span_hash + category + confidence + action + prev_entry_hash +
created_at). If any row is modified after the fact, the chain breaks
at the next row, which surfaces in `verify_chain()`.

This module is async because the DB session is async (spec §3: async
throughout).
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import text
from core.db import get_session_factory
from core.config import get_settings
from phi.redactor import RedactionSpan

log = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    """Return current UTC time as ISO 8601 string (for hashing — must be stable)."""
    return datetime.now(timezone.utc).isoformat()


def _hash_span(span_text: str, salt: str) -> str:
    """Hash a span + salt for the audit log (one-way)."""
    h = hashlib.sha256()
    h.update((span_text + "|" + salt).encode("utf-8"))
    return h.hexdigest()


def _hash_entry(span_hash: str, category: str, confidence: float,
                action: str, prev_entry_hash: Optional[str], created_at: str) -> str:
    """Hash an entire audit log row (for the next row's prev_entry_hash)."""
    h = hashlib.sha256()
    parts = "|".join([
        span_hash,
        category,
        f"{confidence:.6f}",
        action,
        prev_entry_hash or "",
        created_at,
    ])
    h.update(parts.encode("utf-8"))
    return h.hexdigest()


@dataclass
class AuditEntry:
    """One audit log entry (in-memory before persistence)."""
    id: str
    session_id: str
    span_hash: str
    category: str
    confidence: float
    action: str
    prev_entry_hash: Optional[str]
    created_at: str
    content_hash: str  # = _hash_entry(...) — what the next row's prev_entry_hash should be


async def append_entries(
    session_id: str,
    spans: List[RedactionSpan],
    actions: List[str],   # one per span: "redacted" | "tokenized" | "queued_for_review" | "passed_through"
) -> List[AuditEntry]:
    """Append one audit log entry per span to the hash chain.

    Args:
        session_id: which session these redactions belong to
        spans: detected PHI spans
        actions: one action per span (parallel arrays)

    Returns: the persisted AuditEntry objects (with content_hash)
    """
    assert len(spans) == len(actions), f"spans ({len(spans)}) and actions ({len(actions)}) must be parallel"
    if not spans:
        return []

    settings = get_settings()
    salt = settings.audit_log_hash_salt or "default-salt-not-for-prod"

    factory = get_session_factory()
    entries: List[AuditEntry] = []

    async with factory() as session:
        # Get the last entry's content_hash to chain from
        last_hash = await _get_last_hash(session)

        for span, action in zip(spans, actions):
            span_hash = _hash_span(span.text, salt)
            created_at = _utcnow_iso()
            content_hash = _hash_entry(
                span_hash=span_hash,
                category=span.category,
                confidence=span.confidence,
                action=action,
                prev_entry_hash=last_hash,
                created_at=created_at,
            )
            entry_id = str(uuid.uuid4())

            # Insert into DB
            await session.execute(text(
                "INSERT INTO phi_audit_log "
                "(id, session_id, span_hash, category, confidence, action, prev_entry_hash, created_at) "
                "VALUES (:id, :sid, :sh, :cat, :conf, :act, :prev, :ts)"
            ), {
                "id": entry_id,
                "sid": session_id,
                "sh": span_hash,
                "cat": span.category,
                "conf": span.confidence,
                "act": action,
                "prev": last_hash,
                "ts": created_at,
            })

            entries.append(AuditEntry(
                id=entry_id, session_id=session_id, span_hash=span_hash,
                category=span.category, confidence=span.confidence,
                action=action, prev_entry_hash=last_hash,
                created_at=created_at, content_hash=content_hash,
            ))
            last_hash = content_hash  # next entry chains from this one

        await session.commit()

    return entries


async def _get_last_hash(session) -> Optional[str]:
    """Get the content_hash of the most recent audit log entry.

    For the hash chain to work, each new entry's prev_entry_hash must equal
    the previous entry's content_hash. We compute content_hash on the fly
    from the stored fields.

    Returns: the content_hash of the last row, or None if table is empty.
    """
    result = await session.execute(text(
        "SELECT span_hash, category, confidence, action, prev_entry_hash, created_at "
        "FROM phi_audit_log ORDER BY created_at DESC LIMIT 1"
    ))
    row = result.fetchone()
    if row is None:
        return None
    span_hash, category, confidence, action, prev_hash, created_at = row
    return _hash_entry(span_hash, category, float(confidence), action, prev_hash, created_at)


async def verify_chain() -> Tuple[bool, List[str]]:
    """Verify the hash chain integrity.

    Returns:
      (is_valid, errors) where errors is a list of broken-link descriptions
      (empty if is_valid is True)
    """
    factory = get_session_factory()
    errors: List[str] = []
    prev_expected: Optional[str] = None

    async with factory() as session:
        # Read in chronological order
        result = await session.execute(text(
            "SELECT id, span_hash, category, confidence, action, prev_entry_hash, created_at "
            "FROM phi_audit_log ORDER BY created_at ASC"
        ))
        rows = result.fetchall()
        for i, row in enumerate(rows):
            entry_id, span_hash, category, confidence, action, prev_hash, created_at = row
            # First entry: prev_hash should be NULL
            if i == 0:
                if prev_hash is not None:
                    errors.append(f"First entry {entry_id} has non-NULL prev_entry_hash: {prev_hash}")
                prev_expected = None
            else:
                # prev_hash should match what we computed for the previous entry
                if prev_hash != prev_expected:
                    errors.append(
                        f"Entry {entry_id} prev_entry_hash={prev_hash} != expected {prev_expected} "
                        f"(chain broken at row {i + 1})"
                    )
            # Compute this entry's content_hash and check it matches next row's prev
            computed = _hash_entry(span_hash, category, float(confidence), action, prev_hash, created_at)
            prev_expected = computed

    return (len(errors) == 0, errors)
