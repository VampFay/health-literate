#!/usr/bin/env python3
"""Generate 20 Synthea T2D patients and write the index file.

Per spec §4.5: 20 Synthea patients with Type 2 diabetes as a Condition.

The Synthea bundle files are too large for GitHub (30MB+ each, 305MB total)
and are .gitignored. This script regenerates them locally.

Usage:
    python3 scripts/generate_synthea_patients.py
    # (run from the carescaffold/ project root)

Prereqs:
    - Java 21+ installed (verify with `java -version`)
    - Synthea jar (`synthea-with-dependencies.jar`) downloaded and either:
        - placed in the project root (next to README.md), OR
        - placed in any directory listed in $SYNTHEA_JAR_PATH, OR
        - the SYNTHEA_JAR env var set to the absolute path, OR
        - the script will offer to auto-download from
          https://github.com/synthetichealth/synthea/releases (~197MB)

Output:
    - fhir/synthea_data/output/fhir/*.json (20 T2D bundle files)
    - fhir/synthea_data/t2d_patients_index.json (index of 20 T2D patients)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# Project root (this script lives in <project_root>/scripts/)
PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "fhir" / "synthea_data" / "output"
INDEX_PATH = PROJECT_DIR / "fhir" / "synthea_data" / "t2d_patients_index.json"

# Synthea jar location: env var, then project root, then temp dir.
# Set SYNTHEA_JAR=<absolute path> to override.
SYNTHEA_JAR_DEFAULTS = [
    Path(os.environ.get("SYNTHEA_JAR", "")),  # explicit env var
    PROJECT_DIR / "synthea.jar",              # project root
    PROJECT_DIR / "synthea-with-dependencies.jar",
    Path(tempfile.gettempdir()) / "synthea.jar",  # OS temp dir
]

SYNTHEA_DOWNLOAD_URL = (
    "https://github.com/synthetichealth/synthea/releases/latest/download/"
    "synthea-with-dependencies.jar"
)

# SNOMED code for Type 2 diabetes mellitus
T2D_SNOMED = "44054006"
# LOINC code for A1C
A1C_LOINC = "4548-4"


def find_synthea_jar() -> Path | None:
    """Find the Synthea jar in the standard search locations."""
    for candidate in SYNTHEA_JAR_DEFAULTS:
        if candidate and candidate.exists() and candidate.stat().st_size > 1_000_000:
            return candidate
    return None


def download_synthea_jar(target: Path | None = None) -> Path:
    """Download the Synthea jar (~197MB) to the target location."""
    target = target or (Path(tempfile.gettempdir()) / "synthea.jar")
    print(f"  Downloading Synthea jar (~197MB) from {SYNTHEA_DOWNLOAD_URL}")
    print(f"  Saving to: {target}")
    urllib.request.urlretrieve(SYNTHEA_DOWNLOAD_URL, target)
    print(f"  ✓ Downloaded ({target.stat().st_size // (1024*1024)} MB)")
    return target


def run_synthea(num_patients: int, seed: int, jar_path: Path) -> None:
    """Run Synthea to generate FHIR bundles.

    Args:
        num_patients: how many patients to generate in this batch
        seed: random seed for Synthea's RNG
        jar_path: absolute path to the Synthea jar
    """
    OUTPUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "java", "-jar", str(jar_path),
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
    print("=== CareScaffold — Synthea T2D Patient Generator ===")
    print(f"  Project root: {PROJECT_DIR}")
    print(f"  Output dir:   {OUTPUT_DIR}")
    print(f"  Index file:    {INDEX_PATH}")
    print()

    if not shutil.which("java"):
        print("ERROR: java not found in PATH. Install Java 21+.")
        print("  Verify with: java -version")
        sys.exit(1)
    print(f"  ✓ Java available: {shutil.which('java')}")

    # Find or download the Synthea jar
    jar_path = find_synthea_jar()
    if jar_path is None:
        print()
        print("Synthea jar not found in any of these locations:")
        for cand in SYNTHEA_JAR_DEFAULTS:
            if cand:
                print(f"  - {cand}")
        print()
        answer = input("Auto-download Synthea jar (~197MB) to temp dir? [y/N] ").strip().lower()
        if answer != "y":
            print("Download manually from https://github.com/synthetichealth/synthea/releases")
            print(f"Then either:")
            print(f"  1. Save as {PROJECT_DIR / 'synthea.jar'}")
            print(f"  2. Set $env:SYNTHEA_JAR = '<path-to-jar>'")
            sys.exit(1)
        jar_path = download_synthea_jar()
    else:
        print(f"  ✓ Synthea jar found: {jar_path} ({jar_path.stat().st_size // (1024*1024)} MB)")

    print()
    print("=== Generating Synthea patients (will yield ~20 T2D after multiple batches) ===")
    # Strategy: generate in batches with different seeds until we have ≥ 20 T2D
    seeds = [0, 42, 99, 123, 777, 2026]
    found = []
    for i, seed in enumerate(seeds):
        print(f"\nBatch {i + 1}/{len(seeds)}, seed={seed}")
        n = 60 if i > 0 else 20
        run_synthea(n, seed, jar_path)
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
