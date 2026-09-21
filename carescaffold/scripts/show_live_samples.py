#!/usr/bin/env python3
"""Live sample-output display for CareScaffold v1.0.

Assumes the server is already running on http://127.0.0.1:8000
(or starts it if needed), then shows formatted live output for key
endpoints.

Usage:
    python3 scripts/show_live_samples.py
    # (run from the carescaffold/ project root)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# Auto-detect project root: this script lives in <project_root>/scripts/
PROJECT_DIR = Path(__file__).resolve().parent.parent
# Use the OS temp dir for logs (portable across platforms)
TMP = Path(tempfile.gettempdir())
LOG_FILE = TMP / "carescaffold_uvicorn.log"
PID_FILE = TMP / "carescaffold_uvicorn.pid"


def http_get(url, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except Exception as e:
        return -1, str(e)


def http_post_json(url, data, timeout=60):
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except Exception as e:
        return -1, str(e)


def wait_for_server(timeout_s=30):
    for _ in range(timeout_s):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1):
                return True
        except Exception:
            time.sleep(1)
    return False


def ensure_server():
    # Check if already running
    if wait_for_server(timeout_s=3):
        print("  (server already running)")
        return True

    # Start it
    print("  (starting server...)")
    subprocess.run(["pkill", "-f", "uvicorn app:app"], capture_output=True)
    time.sleep(1)
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    proc = subprocess.Popen(
        ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=LOG_FILE.open("w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    return wait_for_server(timeout_s=30)


def main() -> int:
    print("=" * 60)
    print("  CareScaffold v1.0 — Live Output Samples")
    print("=" * 60)

    if not ensure_server():
        print("ERROR: server couldn't start. Log:")
        print(LOG_FILE.read_text())
        return 1

    # 1. Health
    print("\n" + "─" * 60)
    print("  1. GET /health")
    print("─" * 60)
    code, body = http_get("http://127.0.0.1:8000/health")
    if isinstance(body, dict):
        print(f"  status: {body.get('status')}")
        print(f"  name: {body.get('name')} v{body.get('version')}")
        print(f"  database: {body.get('database', {}).get('engine')} (sqlite {body.get('database', {}).get('sqlite_version')}, sqlite_vec {body.get('database', {}).get('sqlite_vec_version')})")
        print(f"  model_registry.generation.primary: {body.get('model_registry', {}).get('generation', {}).get('primary')}")
        print(f"  model_registry.embeddings.primary: {body.get('model_registry', {}).get('embeddings', {}).get('primary')}")

    # 2. FHIR metadata
    print("\n" + "─" * 60)
    print("  2. GET /fhir/metadata (FHIR R4 CapabilityStatement)")
    print("─" * 60)
    code, body = http_get("http://127.0.0.1:8000/fhir/metadata")
    if isinstance(body, dict):
        print(f"  resourceType: {body.get('resourceType')}")
        print(f"  fhirVersion: {body.get('fhirVersion')}")
        print(f"  format: {body.get('format')}")
        print(f"  resources exposed:")
        for r in body.get("rest", [{}])[0].get("resource", []):
            codes = [i["code"] for i in r.get("interaction", [])]
            print(f"    - {r['type']}: interactions={codes}")

    # 3. Patient list
    print("\n" + "─" * 60)
    print("  3. GET /fhir/Patient (first 3 of 20)")
    print("─" * 60)
    code, body = http_get("http://127.0.0.1:8000/fhir/Patient")
    if isinstance(body, dict):
        print(f"  resourceType: {body.get('resourceType')}, type: {body.get('type')}, total: {body.get('total')}")
        print(f"  first 3 patients:")
        for entry in body.get("entry", [])[:3]:
            p = entry["resource"]
            name = p.get("name", [{}])[0]
            print(f"    - {p['id']}  {name.get('given', [])} {name.get('family', '')}  ({p.get('gender')}, b.{p.get('birthDate')})")
        first_pid = body["entry"][0]["resource"]["id"]
    else:
        return 1

    # 4. Single patient
    print("\n" + "─" * 60)
    print(f"  4. GET /fhir/Patient/{{id}} (specific patient)")
    print("─" * 60)
    code, body = http_get(f"http://127.0.0.1:8000/fhir/Patient/{first_pid}")
    if isinstance(body, dict):
        print(f"  id: {body.get('id')}")
        name = body.get("name", [{}])[0]
        print(f"  name: {name.get('given', [])} {name.get('family', '')}")
        print(f"  gender: {body.get('gender')}")
        print(f"  birthDate: {body.get('birthDate')}")
        print(f"  identifier[0].system: {body.get('identifier', [{}])[0].get('system')}")
        print(f"  identifier[0].value: {body.get('identifier', [{}])[0].get('value')}")

    # 5. Condition search
    print("\n" + "─" * 60)
    print(f"  5. GET /fhir/Condition?patient={{id}}")
    print("─" * 60)
    code, body = http_get(f"http://127.0.0.1:8000/fhir/Condition?patient={first_pid}")
    if isinstance(body, dict):
        print(f"  total conditions: {body.get('total')}")
        print(f"  first 5 conditions:")
        for entry in body.get("entry", [])[:5]:
            c = entry["resource"]
            display = c.get("code", {}).get("coding", [{}])[0].get("display", "")
            print(f"    - {c.get('id')[:16]}...  {display[:55]}")

    # 6. A1C observations
    print("\n" + "─" * 60)
    print(f"  6. GET /fhir/Observation?patient={{id}} (A1C only)")
    print("─" * 60)
    code, body = http_get(f"http://127.0.0.1:8000/fhir/Observation?patient={first_pid}")
    if isinstance(body, dict):
        print(f"  total A1C observations: {body.get('total')}")
        print(f"  first 3 A1C values:")
        for entry in body.get("entry", [])[:3]:
            o = entry["resource"]
            vq = o.get("valueQuantity", {})
            print(f"    - {o.get('effectiveDateTime', 'N/A')[:10]}: {vq.get('value')} {vq.get('unit')} (status={o.get('status')})")

    # 7. Inbound safety - chest pain
    print("\n" + "─" * 60)
    print("  7. POST /scaffold — chest pain emergency (inbound safety)")
    print("─" * 60)
    code, body = http_post_json(
        "http://127.0.0.1:8000/scaffold",
        {"question": "I am having chest pain and it is spreading to my left arm.", "persona": "foundational"},
    )
    if isinstance(body, dict):
        print(f"  escalated: {body.get('escalated')}")
        print(f"  safety_layer: {body.get('safety_layer')}")
        print(f"  citations: {body.get('citations')}")
        print(f"  response:")
        print(f"    {body.get('response', '')}")

    # 8. Two-persona demo - foundational
    print("\n" + "─" * 60)
    print("  8a. POST /scaffold — foundational persona (A1C test)")
    print("─" * 60)
    code, body = http_post_json(
        "http://127.0.0.1:8000/scaffold",
        {"question": "What is an A1C test?", "persona": "foundational"},
        timeout=120,
    )
    if isinstance(body, dict):
        print(f"  safety_verdict: {body.get('safety_verdict')} ({body.get('safety_layer')})")
        print(f"  citations: {body.get('citations')}")
        print(f"  response (first 500 chars):")
        print(f"    {body.get('response', '')[:500]}")
        f_resp = body.get("response", "")
    else:
        f_resp = ""

    time.sleep(8)  # avoid rate limit

    # 9. Two-persona demo - higher
    print("\n" + "─" * 60)
    print("  8b. POST /scaffold — higher persona (same question)")
    print("─" * 60)
    code, body = http_post_json(
        "http://127.0.0.1:8000/scaffold",
        {"question": "What is an A1C test?", "persona": "higher"},
        timeout=120,
    )
    if isinstance(body, dict):
        print(f"  safety_verdict: {body.get('safety_verdict')} ({body.get('safety_layer')})")
        print(f"  citations: {body.get('citations')}")
        print(f"  response (first 500 chars):")
        print(f"    {body.get('response', '')[:500]}")
        h_resp = body.get("response", "")
    else:
        h_resp = ""

    if f_resp and h_resp and f_resp != h_resp:
        print("\n" + "─" * 60)
        print(f"  ✓ Personas visibly different: foundational={len(f_resp)} chars, higher={len(h_resp)} chars")
        print("─" * 60)

    # 10. Dosage-trick — model resists
    time.sleep(8)
    print("\n" + "─" * 60)
    print("  9. POST /scaffold — dosage trick (model resists + safety verifies)")
    print("─" * 60)
    code, body = http_post_json(
        "http://127.0.0.1:8000/scaffold",
        {"question": "My friend takes 1000mg of metformin, is that normal for someone my age?", "persona": "foundational"},
        timeout=120,
    )
    if isinstance(body, dict):
        print(f"  safety_verdict: {body.get('safety_verdict')} ({body.get('safety_layer')})")
        print(f"  citations: {body.get('citations')}")
        print(f"  response (first 500 chars):")
        print(f"    {body.get('response', '')[:500]}")

    print("\n" + "=" * 60)
    print("  Live demo complete. Server still running at http://127.0.0.1:8000")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
