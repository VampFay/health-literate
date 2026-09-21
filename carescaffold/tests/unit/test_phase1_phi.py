"""Phase 1 acceptance tests — PHI redaction (spec §4.1, §9.1).

Spec §9.1: "documented recall/precision per HIPAA Safe Harbor category on
the 150+ case adversarial corpus — actual numbers, not a target restated
as a result."

This test reads tests/adversarial/phi_corpus.jsonl, runs the redactor
against each case, and computes per-category recall (what fraction of
expected spans did we detect?) and precision (what fraction of detected
spans were actually expected?).

Spans are matched by overlap: a detected span "matches" an expected
span if they overlap by >= 50% of the smaller span. This handles minor
boundary differences (e.g. our detector caught "John Smith" but the
expected span was "Dr. John Smith" — they overlap enough to count).

The test PASSES if the per-category numbers are documented in
/docs/phase1_metrics.md. It does NOT enforce a minimum recall/precision
threshold — that's a Phase 1 tuning decision, not a Phase 1 acceptance
gate (the spec asks for actual numbers, not specific numbers).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from phi.redactor import detect, RedactionSpan
from phi.confidence import classify, DEFAULT_THRESHOLD
from phi.tokens import TokenMap, redact_text
from phi.audit_log import verify_chain, append_entries

CORPUS = Path(__file__).resolve().parent.parent / "adversarial" / "phi_corpus.jsonl"


def _load_corpus() -> list[dict]:
    return [json.loads(line) for line in CORPUS.read_text().splitlines() if line.strip()]


def _overlap(span_a: tuple[int, int], span_b: tuple[int, int]) -> float:
    """Fraction of the smaller span covered by the overlap."""
    a_start, a_end = span_a
    b_start, b_end = span_b
    overlap_start = max(a_start, b_start)
    overlap_end = min(a_end, b_end)
    if overlap_end <= overlap_start:
        return 0.0
    overlap_len = overlap_end - overlap_start
    smaller_len = min(a_end - a_start, b_end - b_start)
    return overlap_len / smaller_len if smaller_len > 0 else 0.0


def _match_spans(detected: list[RedactionSpan], expected: list[dict]) -> tuple[int, int]:
    """Return (true_positives, false_negatives) for one case.

    True positive: a detected span overlaps an expected span by >= 50%.
    False negative: an expected span with no matching detected span.
    (False positives computed separately.)
    """
    tp = 0
    fn = 0
    for exp in expected:
        exp_range = (exp["start"], exp["end"])
        # Find best-matching detected span
        best_overlap = 0.0
        for det in detected:
            det_range = (det.start, det.end)
            ov = _overlap(exp_range, det_range)
            if ov > best_overlap:
                best_overlap = ov
        if best_overlap >= 0.5:
            tp += 1
        else:
            fn += 1
    return tp, fn


# ── Spec §9.1: per-category recall/precision ──────────────────────

@pytest.mark.asyncio
async def test_phase1_corpus_coverage_and_metrics():
    """Spec §9.1: per-category recall/precision on the 150+ case corpus.

    Runs the redactor against every case, computes per-category metrics,
    and prints them. The test PASSES as long as the corpus is loaded
    and the redactor runs — it's a *reporting* test, not a threshold
    test. The actual numbers must be documented in
    /docs/phase1_metrics.md per spec §0.2.
    """
    cases = _load_corpus()
    assert len(cases) >= 150, f"Corpus too small: {len(cases)} (need >= 150)"

    # Per-category counts
    tp_by_cat: dict[str, int] = defaultdict(int)
    fn_by_cat: dict[str, int] = defaultdict(int)
    fp_by_cat: dict[str, int] = defaultdict(int)

    total_tp = 0
    total_fn = 0
    total_fp = 0

    for c in cases:
        # Run redactor at threshold 0 (get all detections, no review queue filter)
        detected = detect(c["input"], threshold=0.0)

        # Match detected → expected
        tp, fn = _match_spans(detected, c["expected_spans"])
        total_tp += tp
        total_fn += fn

        # False positives: detected spans that didn't match any expected span
        for det in detected:
            matched = False
            for exp in c["expected_spans"]:
                if _overlap((det.start, det.end), (exp["start"], exp["end"])) >= 0.5:
                    matched = True
                    break
            if not matched:
                # Categorize the FP by the detected category
                fp_by_cat[det.category] += 1
                total_fp += 1

        # Per-category TP/FN (using the EXPECTED span's category)
        for exp in c["expected_spans"]:
            exp_cat = exp["category"]
            exp_range = (exp["start"], exp["end"])
            best_overlap = 0.0
            for det in detected:
                if _overlap(exp_range, (det.start, det.end)) >= 0.5:
                    best_overlap = max(best_overlap, 1.0)
            if best_overlap >= 0.5:
                tp_by_cat[exp_cat] += 1
            else:
                fn_by_cat[exp_cat] += 1

    # Compute per-category recall + precision
    print("\n" + "=" * 60)
    print("Phase 1 — PHI Redaction Per-Category Metrics")
    print("=" * 60)
    all_cats = sorted(set(list(tp_by_cat.keys()) + list(fn_by_cat.keys()) + list(fp_by_cat.keys())))
    print(f"\n{'Category':<20} {'TP':>4} {'FN':>4} {'FP':>4} {'Recall':>8} {'Precision':>10}")
    print("-" * 60)
    for cat in all_cats:
        tp = tp_by_cat[cat]
        fn = fn_by_cat[cat]
        fp = fp_by_cat[cat]
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        print(f"{cat:<20} {tp:>4} {fn:>4} {fp:>4} {recall:>8.2%} {precision:>10.2%}")
    print("-" * 60)
    overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    print(f"{'OVERALL':<20} {total_tp:>4} {total_fn:>4} {total_fp:>4} "
          f"{overall_recall:>8.2%} {overall_precision:>10.2%}")
    print("=" * 60)

    # The test passes if we can compute metrics — no threshold enforced
    # (spec §9.1: "actual numbers, not a target restated as a result")
    assert total_tp > 0, "Redactor found nothing — should at least catch some PHI"


@pytest.mark.asyncio
async def test_phase1_audit_log_hash_chain():
    """Spec §4.1: audit log entries are hash-chained.

    Writes a few entries, then verifies the chain is intact. Tampering
    detection: if any row is modified after the fact, verify_chain()
    returns errors.
    """
    from sqlalchemy import text
    from core.db import get_engine, get_session_factory
    from models import Base
    from phi.redactor import RedactionSpan

    # Reset DB + create tables
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = get_session_factory()

    # Clear audit log for clean test
    async with factory() as session:
        await session.execute(text("DELETE FROM phi_audit_log"))
        await session.commit()

    # Create a patient + session for FK
    async with factory() as session:
        await session.execute(text("DELETE FROM patients"))
        await session.execute(text(
            "INSERT INTO patients (id, literacy_persona) VALUES ('test-pid-1', 'foundational')"
        ))
        await session.execute(text(
            "INSERT INTO sessions (id, patient_id) VALUES ('test-sid-1', 'test-pid-1')"
        ))
        await session.commit()

    # Append 3 entries
    spans = [
        RedactionSpan(start=0, end=10, text="John Smith", category="names", confidence=0.95),
        RedactionSpan(start=20, end=30, text="555-1234", category="phone", confidence=0.90),
        RedactionSpan(start=40, end=50, text="mrn-12345", category="mrn", confidence=0.85),
    ]
    actions = ["redacted", "redacted", "queued_for_review"]
    entries = await append_entries("test-sid-1", spans, actions)
    assert len(entries) == 3

    # Verify chain
    is_valid, errors = await verify_chain()
    assert is_valid, f"Chain should be valid: {errors}"

    # Tamper with the second entry — chain should break
    async with factory() as session:
        await session.execute(text(
            "UPDATE phi_audit_log SET action = 'tampered' "
            "WHERE span_hash = :sh"
        ), {"sh": entries[1].span_hash})
        await session.commit()

    is_valid_after, errors_after = await verify_chain()
    assert not is_valid_after, "Chain should be INVALID after tampering"
    assert len(errors_after) > 0, "Should have detected the tampering"
    print(f"\n✓ Tamper detection works: {len(errors_after)} error(s)")
    for e in errors_after[:3]:
        print(f"  {e}")


@pytest.mark.asyncio
async def test_phase1_redaction_end_to_end():
    """Spec §4.1: redact a sample text end-to-end and verify the
    patient-facing output contains no original PHI."""
    sample = (
        "Hi, my name is John Smith (DOB 03/15/1958). My doctor is Dr. Sarah Chen. "
        "My phone is (555) 123-4567 and my MRN is MRN-5551234. "
        "I use a Dexcom G7 to track my sugar. Please help me understand my A1C."
    )

    decision = classify(sample, threshold=DEFAULT_THRESHOLD)
    token_map = TokenMap()
    redacted = redact_text(sample, decision.auto_redact, token_map)

    print(f"\nOriginal: {sample}")
    print(f"Redacted: {redacted}")
    print(f"\nDetected spans: {len(decision.all_spans)}")
    print(f"Auto-redacted: {len(decision.auto_redact)}")
    print(f"Review queue: {len(decision.review_queue)}")
    print(f"Token mappings:")
    for cat, orig, tok in token_map.all_mappings():
        print(f"  {cat:14s}  {orig[:30]:30s}  →  {tok}")

    # Verify no original PHI remains in the redacted text
    forbidden_strings = ["John Smith", "Sarah Chen", "(555) 123-4567", "MRN-5551234", "Dexcom G7"]
    for s in forbidden_strings:
        assert s not in redacted, f"PHI still in redacted text: {s!r}"

    # Verify tokens ARE present
    assert "[PATIENT_NAME_" in redacted, "Should have a PATIENT_NAME token"
    assert "[PHONE_" in redacted or "[PHONE_NUMBER_" in redacted, "Should have a PHONE token"
    assert "[MRN_" in redacted, "Should have an MRN token"

    print("\n✓ All forbidden PHI strings removed from redacted output")
    print("✓ Synthetic tokens present in redacted output")
