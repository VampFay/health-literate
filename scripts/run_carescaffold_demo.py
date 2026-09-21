#!/usr/bin/env python3
"""Live demo runner for CareScaffold v1.0.

Starts uvicorn in the background, runs all demo curl commands via Python
requests, captures output, keeps server running.

Usage:
    python3 /home/z/my-project/scripts/run_carescaffold_demo.py
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path("/home/z/my-project/carescaffold")
LOG_FILE = Path("/tmp/carescaffold_uvicorn.log")
OUTPUT_FILE = Path("/tmp/carescaffold_demo_output.txt")
PID_FILE = Path("/tmp/carescaffold_uvicorn.pid")


def log(msg: str) -> None:
    print(msg)
    with OUTPUT_FILE.open("a") as f:
        f.write(msg + "\n")


def run_cmd(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> str:
    """Run a command, return stdout."""
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        return f"[ERROR {result.returncode}] {result.stderr[:500]}"
    return result.stdout


def http_get(url: str, timeout: int = 10) -> dict | str:
    """HTTP GET, return parsed JSON or raw text."""
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return body
    except Exception as e:
        return f"[ERROR] {e}"


def http_post_json(url: str, data: dict, timeout: int = 30) -> dict | str:
    """HTTP POST JSON, return parsed JSON or raw text."""
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return body
    except Exception as e:
        return f"[ERROR] {e}"


def wait_for_server(url: str, timeout_s: int = 30) -> bool:
    """Poll url until it returns 200 or timeout."""
    for i in range(timeout_s):
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main() -> int:
    # Kill any stale uvicorn
    subprocess.run(["pkill", "-f", "uvicorn app:app"], capture_output=True)
    time.sleep(1)

    # Clear logs
    for f in [LOG_FILE, OUTPUT_FILE, PID_FILE]:
        if f.exists():
            f.unlink()

    # Start uvicorn detached
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    proc = subprocess.Popen(
        ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=LOG_FILE.open("w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # detach from this shell
    )
    PID_FILE.write_text(str(proc.pid))

    log(f"=== starting uvicorn (PID {proc.pid}) ===")

    if not wait_for_server("http://127.0.0.1:8000/health", timeout_s=30):
        log("ERROR: server didn't come up")
        log(LOG_FILE.read_text())
        return 1

    log("  server up — ready for demo")
    log("")
    log("==========================================")
    log("  CareScaffold v1.0 — Live Demo")
    log("  Server: http://127.0.0.1:8000")
    log("==========================================")

    # 1. Health
    log("")
    log("=== 1. GET /health ===")
    h = http_get("http://127.0.0.1:8000/health")
    log(json.dumps(h, indent=2) if isinstance(h, dict) else str(h))

    # 2. CapabilityStatement
    log("")
    log("=== 2. GET /fhir/metadata (FHIR R4 CapabilityStatement) ===")
    cs = http_get("http://127.0.0.1:8000/fhir/metadata")
    if isinstance(cs, dict):
        log(f"  resourceType: {cs.get('resourceType')}")
        log(f"  fhirVersion: {cs.get('fhirVersion')}")
        log(f"  format: {cs.get('format')}")
        log("  resources exposed:")
        for r in cs.get("rest", [{}])[0].get("resource", []):
            codes = [i["code"] for i in r.get("interaction", [])]
            log(f"    - {r['type']}: interactions={codes}")

    # 3. Patient list
    log("")
    log("=== 3. GET /fhir/Patient (all 20 T2D patients) ===")
    patients = http_get("http://127.0.0.1:8000/fhir/Patient")
    if isinstance(patients, dict):
        log(f"  resourceType: {patients.get('resourceType')}")
        log(f"  type: {patients.get('type')}")
        log(f"  total: {patients.get('total')}")
        first_pid = patients["entry"][0]["resource"]["id"]
        log(f"  first patient id: {first_pid}")
    else:
        log(f"  ERROR: {patients}")
        return 1

    # 4. Single patient
    log("")
    log(f"=== 4. GET /fhir/Patient/{first_pid} ===")
    p = http_get(f"http://127.0.0.1:8000/fhir/Patient/{first_pid}")
    if isinstance(p, dict):
        name = p.get("name", [{}])[0]
        given = name.get("given", [])
        family = name.get("family", "")
        log(f"  id: {p.get('id')}")
        log(f"  name: {given} {family}")
        log(f"  gender: {p.get('gender')}")
        log(f"  birthDate: {p.get('birthDate')}")

    # 5. Condition search
    log("")
    log(f"=== 5. GET /fhir/Condition?patient={first_pid} ===")
    conds = http_get(f"http://127.0.0.1:8000/fhir/Condition?patient={first_pid}")
    if isinstance(conds, dict):
        log(f"  total conditions: {conds.get('total')}")
        if conds.get("entry"):
            first_cond = conds["entry"][0]["resource"]
            display = first_cond["code"]["coding"][0]["display"]
            log(f"  first condition: {display[:60]}")

    # 6. A1C observations
    log("")
    log(f"=== 6. GET /fhir/Observation?patient={first_pid} (A1C only) ===")
    obs = http_get(f"http://127.0.0.1:8000/fhir/Observation?patient={first_pid}")
    if isinstance(obs, dict):
        log(f"  total A1C observations: {obs.get('total')}")
        if obs.get("entry"):
            first_obs = obs["entry"][0]["resource"]
            code = first_obs["code"]["coding"][0]["code"]
            vq = first_obs.get("valueQuantity", {})
            log(f"  first A1C: code={code} value={vq.get('value')} {vq.get('unit')}")

    # 7. 404 handling
    log("")
    log("=== 7. GET /fhir/Patient/nonexistent-id → 404 ===")
    import urllib.request, urllib.error
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/fhir/Patient/nonexistent-id", timeout=5)
        log("  ERROR: expected 404")
    except urllib.error.HTTPError as e:
        log(f"  HTTP {e.code} (expected 404)")
    except Exception as e:
        log(f"  ERROR: {e}")

    # 8. Inbound safety - chest pain emergency
    log("")
    log("=== 8. POST /scaffold — chest pain emergency (inbound safety bypasses LLM) ===")
    r = http_post_json(
        "http://127.0.0.1:8000/scaffold",
        {"question": "I am having chest pain and it is spreading to my left arm.", "persona": "foundational"},
    )
    if isinstance(r, dict):
        log(f"  escalated: {r.get('escalated')}")
        log(f"  safety_layer: {r.get('safety_layer')}")
        log(f"  response: {r.get('response')}")

    # 9. Inbound safety - self-harm
    log("")
    log("=== 9. POST /scaffold — self-harm indicator (inbound safety bypasses LLM) ===")
    r = http_post_json(
        "http://127.0.0.1:8000/scaffold",
        {"question": "I want to hurt myself. I have a plan and I am scared.", "persona": "foundational"},
    )
    if isinstance(r, dict):
        log(f"  escalated: {r.get('escalated')}")
        log(f"  safety_layer: {r.get('safety_layer')}")
        log(f"  response: {r.get('response')}")

    # 10. Severe hypoglycemia
    log("")
    log("=== 10. POST /scaffold — severe hypoglycemia (inbound safety) ===")
    r = http_post_json(
        "http://127.0.0.1:8000/scaffold",
        {"question": "My sugar is 38 and I cannot think straight or remember my own name.", "persona": "foundational"},
    )
    if isinstance(r, dict):
        log(f"  escalated: {r.get('escalated')}")
        log(f"  safety_layer: {r.get('safety_layer')}")
        log(f"  response: {r.get('response')}")

    log("")
    log("=== DONE — server still running on http://127.0.0.1:8000 ===")
    log(f"=== uvicorn PID: {proc.pid} ===")
    log("")
    log("To stop: pkill -f 'uvicorn app:app'")

    return 0


if __name__ == "__main__":
    sys.exit(main())
