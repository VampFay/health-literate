#!/usr/bin/env python3
"""Host CareScaffold in the z.ai workspace via Caddy proxy.

Z.ai's workspace uses Caddy on port 81 to proxy to an upstream on port 3000.
This script:
1. Starts uvicorn on 127.0.0.1:3000 (the Caddy upstream)
2. Verifies http://127.0.0.1:81/health proxies through to uvicorn
3. Verifies every endpoint via the Caddy-proxied URL
4. Reports the public preview URL the user can click in the workspace panel
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_DIR = Path("/home/z/my-project/carescaffold")
LOG_FILE = Path("/tmp/carescaffold_3000.log")
CADDY_PORT = 81
UVICORN_PORT = 3000


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


def wait_for_url(url, timeout_s=30):
    for _ in range(timeout_s):
        try:
            with urllib.request.urlopen(url, timeout=1) as _:
                return True
        except Exception:
            time.sleep(1)
    return False


def start_uvicorn_on_3000():
    """Start uvicorn on 127.0.0.1:3000 (the Caddy upstream port)."""
    subprocess.run(["pkill", "-f", "uvicorn app:app"], capture_output=True)
    time.sleep(2)
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    proc = subprocess.Popen(
        ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(UVICORN_PORT), "--log-level", "warning"],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=LOG_FILE.open("w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"  uvicorn starting (PID {proc.pid}) on port {UVICORN_PORT}")
    if wait_for_url(f"http://127.0.0.1:{UVICORN_PORT}/health", timeout_s=30):
        print(f"  ✓ uvicorn healthy on port {UVICORN_PORT}")
        return proc.pid
    print("  ✗ uvicorn failed to start")
    if LOG_FILE.exists():
        print(LOG_FILE.read_text()[-1000:])
    return None


def main():
    print("=" * 70)
    print("  CareScaffold v1.0 — Hosting in z.ai Workspace")
    print("=" * 70)
    print()
    print("--- Platform architecture discovered ---")
    print(f"  • Caddy reverse proxy on port {CADDY_PORT} (root-owned)")
    print(f"  • Caddy proxies to upstream on port {UVICORN_PORT} (user-accessible)")
    print(f"  • Workspace preview URL: <caddy-proxied-port-81-URL>")
    print()

    print("--- Starting uvicorn on 127.0.0.1:3000 (Caddy upstream) ---")
    pid = start_uvicorn_on_3000()
    if not pid:
        return 1

    # Verify through Caddy
    print()
    print(f"--- Verifying via Caddy proxy on port {CADDY_PORT} ---")
    code, body = http_get(f"http://127.0.0.1:{CADDY_PORT}/health")
    if code == 200 and isinstance(body, dict) and body.get("status") == "ok":
        print(f"  ✓ Caddy proxy → uvicorn working: /health returns 200")
    else:
        print(f"  ✗ Caddy proxy not forwarding. code={code}, body={str(body)[:200]}")
        return 1

    # Run every endpoint through the Caddy URL
    base = f"http://127.0.0.1:{CADDY_PORT}"
    print()
    print("--- Endpoint tests (via Caddy on port 81) ---")

    tests = []
    # 1. Health
    code, body = http_get(f"{base}/health")
    tests.append(("GET /health", code == 200 and body.get("status") == "ok" if isinstance(body, dict) else False,
                  f"status={body.get('status') if isinstance(body, dict) else 'N/A'}"))
    # 2. Swagger UI
    code, _ = http_get(f"{base}/docs")
    tests.append(("GET /docs (Swagger UI)", code == 200, f"HTTP {code}"))
    # 3. OpenAPI
    code, body = http_get(f"{base}/openapi.json")
    tests.append(("GET /openapi.json", code == 200 and len(body.get("paths", {})) >= 10 if isinstance(body, dict) else False,
                  f"{len(body.get('paths', {}))} paths" if isinstance(body, dict) else f"HTTP {code}"))
    # 4. FHIR metadata
    code, body = http_get(f"{base}/fhir/metadata")
    fhir_ok = code == 200 and isinstance(body, dict) and body.get("fhirVersion") == "4.0.1"
    types = [r["type"] for r in body.get("rest", [{}])[0].get("resource", [])] if isinstance(body, dict) else []
    tests.append(("GET /fhir/metadata", fhir_ok, f"FHIR 4.0.1, types={types}"))
    # 5. FHIR Patient list
    code, body = http_get(f"{base}/fhir/Patient")
    patient_count = body.get("total") if isinstance(body, dict) else 0
    tests.append(("GET /fhir/Patient", code == 200 and patient_count == 20,
                  f"{patient_count} patients"))
    first_pid = body["entry"][0]["resource"]["id"] if isinstance(body, dict) and body.get("entry") else None
    # 6. FHIR Patient by id
    if first_pid:
        code, body = http_get(f"{base}/fhir/Patient/{first_pid}")
        ok = code == 200 and isinstance(body, dict) and body.get("id") == first_pid
        name_str = ""
        if isinstance(body, dict):
            n = body.get("name", [{}])[0]
            name_str = f"{n.get('given', [])} {n.get('family', '')}"
        tests.append(("GET /fhir/Patient/{id}", ok, name_str))
    # 7. FHIR Condition search
    if first_pid:
        code, body = http_get(f"{base}/fhir/Condition?patient={first_pid}")
        cond_count = body.get("total") if isinstance(body, dict) else 0
        tests.append(("GET /fhir/Condition?patient=X", code == 200 and cond_count > 0,
                      f"{cond_count} conditions"))
    # 8. FHIR Observation (A1C)
    if first_pid:
        code, body = http_get(f"{base}/fhir/Observation?patient={first_pid}")
        obs_count = body.get("total") if isinstance(body, dict) else 0
        first_val = ""
        if isinstance(body, dict) and body.get("entry"):
            vq = body["entry"][0]["resource"].get("valueQuantity", {})
            first_val = f"first={vq.get('value')} {vq.get('unit')}"
        tests.append(("GET /fhir/Observation?patient=X", code == 200 and obs_count > 0,
                      f"{obs_count} A1C obs ({first_val})"))
    # 9. Patient 404
    code, _ = http_get(f"{base}/fhir/Patient/nonexistent-id")
    tests.append(("GET /fhir/Patient/nonexistent → 404", code == 404, f"HTTP {code}"))
    # 10. Inbound safety — chest pain
    code, body = http_post_json(
        f"{base}/scaffold",
        {"question": "I am having chest pain and it is spreading to my left arm.", "persona": "foundational"},
    )
    ok = code == 200 and isinstance(body, dict) and body.get("escalated") is True
    tests.append(("POST /scaffold (chest pain)", ok,
                  f"escalated={body.get('escalated') if isinstance(body, dict) else 'N/A'}"))
    # 11. Inbound safety — self-harm
    code, body = http_post_json(
        f"{base}/scaffold",
        {"question": "I want to hurt myself. I have a plan and I am scared.", "persona": "foundational"},
    )
    ok = code == 200 and isinstance(body, dict) and body.get("escalated") is True
    tests.append(("POST /scaffold (self-harm)", ok,
                  f"escalated={body.get('escalated') if isinstance(body, dict) else 'N/A'}"))

    print()
    passed = 0
    for name, ok, details in tests:
        icon = "✓" if ok else "✗"
        print(f"  {icon} {name}: {details}")
        if ok:
            passed += 1
    print()
    print(f"  Result: {passed}/{len(tests)} PASS")

    print()
    print("=" * 70)
    print("  🎉 CareScaffold is now LIVE in your z.ai workspace!")
    print("=" * 70)
    print()
    print("  The workspace panel on the right side of your chat should now")
    print("  show the CareScaffold FastAPI app instead of the placeholder.")
    print()
    print("  What you can click in the workspace:")
    print(f"    • /docs        → Swagger UI (interactive API explorer)")
    print(f"    • /redoc       → ReDoc alternative API docs")
    print(f"    • /health      → JSON health status")
    print(f"    • /fhir/metadata  → FHIR R4 CapabilityStatement")
    print(f"    • /fhir/Patient   → 20 T2D patients (Synthea)")
    print()
    print("  Try POST /scaffold in the Swagger UI with this body:")
    print('    {"question": "What is an A1C test?", "persona": "foundational"}')
    print()
    print(f"  uvicorn PID: {pid} (running on 127.0.0.1:{UVICORN_PORT})")
    print(f"  Caddy proxies port {CADDY_PORT} → port {UVICORN_PORT}")
    print()
    print("  To stop: pkill -f 'uvicorn app:app'")

    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
