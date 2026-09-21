#!/usr/bin/env python3
"""Generate 20 Synthea T2D patients and write the index file.

Per spec §4.5: 20 Synthea patients with Type 2 diabetes as a Condition.

The Synthea bundle files are too large for GitHub (30MB+ each, 305MB total)
and are .gitignored. This script regenerates them locally.

Usage:
    python3 scripts/generate_synthea_patients.py

Prereqs:
    - Java 21+ installed
    - Synthea jar downloaded to /tmp/synthea.jar
      (see https://github.com/synthetichealth/synthea/releases)

Output:
    - /fhir/synthea_data/output/fhir/*.json (20 T2D bundle files)
    - /fhir/synthea_data/t2d_patients_index.json (index of 20 T2D patients)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

SYNTHEA_JAR = "/tmp/synthea.jar"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "fhir" / "synthea_data" / "output"
INDEX_PATH = Path(__file__).resolve().parent.parent / "fhir" / "synthea_data" / "t2d_patients_index.json"

# SNOMED code for Type 2 diabetes mellitus
T2D_SNOMED = "44054006"
# LOINC code for A1C
A1C_LOINC = "4548-4"


def run_synthea(num_patients: int, seed: int) -> None:
    """Run Synthea to generate FHIR bundles."""
    OUTPUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "java", "-jar", SYNTHEA_JAR,
        "-p", str(num_patients),
        "--exporter.fhir.export=true",
        "--exporter.fhir.use_us_core_ig=false",
        "--exporter.hospital.fhir.export=false",
        "--exporter.practitioner.fhir.export=false",
        "--exporter.fhir.transaction_bundle=false",
        f"--exporter.baseDirectory={OUTPUT_DIR}",
        "--exporter.yearsOfHistory=5",
        f"--exporter.seed={seed}",
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: Synthea failed: {result.stderr[:500]}")
        sys.exit(1)
    print(result.stdout[-500:])


def find_t2d_patients() -> list[dict]:
    """Scan all bundle files in OUTPUT_DIR for T2D patients."""
    bundle_dir = OUTPUT_DIR / "fhir"
    if not bundle_dir.exists():
        return []

    t2d = []
    for bf in sorted(bundle_dir.glob("*.json")):
        try:
            bundle = json.loads(bf.read_text())
        except Exception:
            continue
        if bundle.get("resourceType") != "Bundle":
            continue
        patient_id = None
        has_t2d = False
        a1c_count = 0
        for entry in bundle.get("entry", []):
            r = entry.get("resource", {})
            rt = r.get("resourceType")
            if rt == "Patient":
                patient_id = r.get("id")
            elif rt == "Condition":
                for c in r.get("code", {}).get("coding", []):
                    if c.get("code") == T2D_SNOMED or "type 2 diabetes" in c.get("display", "").lower():
                        has_t2d = True
                        break
            elif rt == "Observation":
                for c in r.get("code", {}).get("coding", []):
                    if c.get("code") == A1C_LOINC:
                        a1c_count += 1
                        break
        if has_t2d:
            t2d.append({
                "patient_id": patient_id,
                "file": bf.name,
                "a1c_count": a1c_count,
            })
    return t2d


def main() -> None:
    if not Path(SYNTHEA_JAR).exists():
        print(f"ERROR: Synthea jar not found at {SYNTHEA_JAR}")
        print("Download from https://github.com/synthetichealth/synthea/releases")
        sys.exit(1)

    if not shutil.which("java"):
        print("ERROR: java not found in PATH. Install Java 21+.")
        sys.exit(1)

    print("=== Generating Synthea patients (will yield ~20 T2D after multiple batches) ===")
    # Strategy: generate in batches with different seeds until we have ≥ 20 T2D
    seeds = [None, 42, 99, 123, 777, 2026]
    found = []
    for i, seed in enumerate(seeds):
        print(f"\nBatch {i + 1}/{len(seeds)}, seed={seed}")
        n = 60 if i > 0 else 20
        run_synthea(n, seed or 0)
        found = find_t2d_patients()
        print(f"  Cumulative T2D patients: {len(found)}")
        if len(found) >= 20:
            break

    if len(found) < 20:
        print(f"\nERROR: Only found {len(found)} T2D patients after {len(seeds)} batches. Try more batches.")
        sys.exit(1)

    # Keep only the first 20; delete the rest to save disk space
    print(f"\n=== Keeping first 20 T2D patients, deleting the rest ===")
    keep_files = {p["file"] for p in found[:20]}
    bundle_dir = OUTPUT_DIR / "fhir"
    deleted = 0
    for bf in sorted(bundle_dir.glob("*.json")):
        if bf.name not in keep_files:
            bf.unlink()
            deleted += 1
    print(f"Deleted {deleted} non-T2D bundle files; kept {len(keep_files)}")

    # Write the index
    INDEX_PATH.write_text(json.dumps(found[:20], indent=2))
    print(f"\n✓ Wrote index: {INDEX_PATH}")
    print(f"✓ 20 T2D patients ready at {bundle_dir}")


if __name__ == "__main__":
    main()
