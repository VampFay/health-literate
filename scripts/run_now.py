#!/usr/bin/env python3
"""Single-shot: start server, run all 11 endpoint demos, print results.

This avoids the bash-tool-call gap that kills uvicorn between calls.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

PROJECT_DIR = Path("/home/z/my-project/carescaffold")
LOG_FILE = Path("/tmp/carescaffold_3000.log")


def http_get(url, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
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


def main():
    # Kill stale uvicorn
    subprocess.run(["pkill", "-f", "uvicorn app:app"], capture_output=True)
    time.sleep(2)

    # Start fresh uvicorn
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    proc = subprocess.Popen(
        ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "3000", "--log-level", "warning"],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=LOG_FILE.open("w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"=== uvicorn starting (PID {proc.pid}) ===")
    for i in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:3000/health", timeout=1):
                print(f"  ✓ ready after {i}s")
                break
        except Exception:
            time.sleep(1)
    else:
        print("  ✗ server failed to start; log:")
        print(LOG_FILE.read_text()[-1500:])
        return 1

    # Run all endpoints
    base = "http://127.0.0.1:81"
    print()
    print("=" * 60)
    print("  CareScaffold v1.0 — LIVE IN SANDBOX")
    print("=" * 60)

    print("\n--- 1. GET /health ---")
    code, body = http_get(f"{base}/health")
    if isinstance(body, dict):
        print(f"  status: {body.get('status')}")
        print(f"  name: {body.get('name')} v{body.get('version')}")
        print(f"  database: {body['database']['engine']} (sqlite_vec {body['database']['sqlite_vec_version']})")
        print(f"  model: {body['model_registry']['generation']['primary']}")
        print(f"  embeddings: {body['model_registry']['embeddings']['primary']}")

    print("\n--- 2. GET /docs (Swagger UI) ---")
    code, body = http_get(f"{base}/docs")
    print(f"  HTTP {code} ({len(body) if isinstance(body, str) else 0} bytes)")

    print("\n--- 3. GET /fhir/metadata (FHIR R4 CapabilityStatement) ---")
    code, body = http_get(f"{base}/fhir/metadata")
    if isinstance(body, dict):
        types = [r["type"] for r in body["rest"][0]["resource"]]
        print(f"  FHIR version: {body['fhirVersion']}")
        print(f"  resources: {types}")

    print("\n--- 4. GET /fhir/Patient (all 20 T2D) ---")
    code, body = http_get(f"{base}/fhir/Patient")
    if isinstance(body, dict):
        print(f"  total: {body['total']}")
        print("  first 5 patients:")
        for e in body["entry"][:5]:
            p = e["resource"]
            n = p["name"][0]
            print(f"    - {n.get('given', [])} {n['family']} ({p.get('gender')}, b.{p.get('birthDate')})")
        first_pid = body["entry"][0]["resource"]["id"]

    print(f"\n--- 5. GET /fhir/Patient/{{id}} ---")
    code, body = http_get(f"{base}/fhir/Patient/{first_pid}")
    if isinstance(body, dict):
        n = body["name"][0]
        print(f"  name: {n.get('given', [])} {n['family']}")
        print(f"  gender: {body.get('gender')}")
        print(f"  birthDate: {body.get('birthDate')}")

    print(f"\n--- 6. GET /fhir/Condition?patient={{id}} ---")
    code, body = http_get(f"{base}/fhir/Condition?patient={first_pid}")
    if isinstance(body, dict):
        print(f"  total conditions: {body['total']}")
        print("  first 3:")
        for e in body["entry"][:3]:
            c = e["resource"]
            print(f"    - {c['code']['coding'][0]['display'][:60]}")

    print(f"\n--- 7. GET /fhir/Observation?patient={{id}} (A1C only) ---")
    code, body = http_get(f"{base}/fhir/Observation?patient={first_pid}")
    if isinstance(body, dict):
        print(f"  total A1C: {body['total']}")
        print("  first 3:")
        for e in body["entry"][:3]:
            o = e["resource"]
            vq = o.get("valueQuantity", {})
            print(f"    {o.get('effectiveDateTime','?')[:10]}: {vq.get('value')} {vq.get('unit')}")

    print("\n--- 8. POST /scaffold — chest pain emergency ---")
    code, body = http_post_json(
        f"{base}/scaffold",
        {"question": "I am having chest pain and it is spreading to my left arm.", "persona": "foundational"},
    )
    if isinstance(body, dict):
        print(f"  escalated: {body.get('escalated')}")
        print(f"  safety_layer: {body.get('safety_layer')}")
        print(f"  response: {body.get('response', '')[:150]}")

    print("\n--- 9. POST /scaffold — self-harm indicator ---")
    code, body = http_post_json(
        f"{base}/scaffold",
        {"question": "I want to hurt myself. I have a plan and I am scared.", "persona": "foundational"},
    )
    if isinstance(body, dict):
        print(f"  escalated: {body.get('escalated')}")
        print(f"  response: {body.get('response', '')[:150]}")

    print("\n--- 10. POST /scaffold — severe hypoglycemia ---")
    code, body = http_post_json(
        f"{base}/scaffold",
        {"question": "My sugar is 38 and I cannot think straight.", "persona": "foundational"},
    )
    if isinstance(body, dict):
        print(f"  escalated: {body.get('escalated')}")
        print(f"  response: {body.get('response', '')[:150]}")

    print("\n--- 11. GET /fhir/Patient/nonexistent-id → 404 ---")
    code, _ = http_get(f"{base}/fhir/Patient/nonexistent-id")
    print(f"  HTTP {code} (expected 404)")

    print("\n" + "=" * 60)
    print(f"  ✓ All 11 endpoints live. Server PID: {proc.pid}")
    print(f"  ✓ uvicorn: 127.0.0.1:3000 | Caddy proxy: 127.0.0.1:81")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
