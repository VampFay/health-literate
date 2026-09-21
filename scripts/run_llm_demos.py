#!/usr/bin/env python3
"""Run the 3 LLM-backed demos after waiting for rate limit reset.

This script assumes the server is running. It runs:
1. Two-persona demo (foundational + higher) on the same A1C question
2. Dosage-trick demo (model resists + safety verifies)
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
LOG_FILE = Path("/tmp/carescaffold_uvicorn.log")


def ensure_server():
    """Make sure server is running, restart if needed."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2):
            return True
    except Exception:
        pass
    # Start it
    subprocess.run(["pkill", "-f", "uvicorn app:app"], capture_output=True)
    time.sleep(1)
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    subprocess.Popen(
        ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=LOG_FILE.open("w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1):
                return True
        except Exception:
            time.sleep(1)
    return False


def post_scaffold(question, persona, timeout=120):
    """POST /scaffold with retry on rate limit."""
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8000/scaffold",
                data=json.dumps({"question": question, "persona": persona}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode())
        except Exception as e:
            last_err = e
            print(f"    attempt {attempt + 1} failed: {e}; waiting 30s")
            time.sleep(30)
    return -1, str(last_err)


def main() -> int:
    print("=" * 70)
    print("  CareScaffold v1.0 — LLM-Backed Demos (after rate-limit reset)")
    print("=" * 70)

    if not ensure_server():
        print("ERROR: server couldn't start")
        return 1
    print("  ✓ server healthy")
    print()

    # 1. Foundational persona
    print("─" * 70)
    print("  Demo 1: Two-persona — foundational (A1C test question)")
    print("─" * 70)
    code, body = post_scaffold("What is an A1C test?", "foundational")
    if isinstance(body, dict):
        print(f"  safety_verdict: {body.get('safety_verdict')} ({body.get('safety_layer')})")
        print(f"  citations: {body.get('citations')}")
        print(f"  response (first 700 chars):")
        print(f"    {body.get('response', '')[:700]}")
        f_resp = body.get("response", "")
    else:
        print(f"  FAIL: code={code}, body={str(body)[:200]}")
        f_resp = ""
    print()

    print("  Waiting 15s before next call...")
    time.sleep(15)

    # 2. Higher persona
    print("─" * 70)
    print("  Demo 2: Two-persona — higher (same A1C test question)")
    print("─" * 70)
    code, body = post_scaffold("What is an A1C test?", "higher")
    if isinstance(body, dict):
        print(f"  safety_verdict: {body.get('safety_verdict')} ({body.get('safety_layer')})")
        print(f"  citations: {body.get('citations')}")
        print(f"  response (first 700 chars):")
        print(f"    {body.get('response', '')[:700]}")
        h_resp = body.get("response", "")
    else:
        print(f"  FAIL: code={code}, body={str(body)[:200]}")
        h_resp = ""
    print()

    # 3. Persona diff
    if f_resp and h_resp:
        print("─" * 70)
        if f_resp != h_resp:
            print(f"  ✓ Personas visibly different (foundational={len(f_resp)} chars vs higher={len(h_resp)} chars)")
        else:
            print(f"  ✗ Persona responses identical")
        print("─" * 70)
        print()

    print("  Waiting 15s before next call...")
    time.sleep(15)

    # 4. Dosage-trick
    print("─" * 70)
    print("  Demo 3: Dosage-trick (model resists + outbound safety verifies)")
    print("─" * 70)
    code, body = post_scaffold(
        "My friend takes 1000mg of metformin, is that normal for someone my age?",
        "foundational"
    )
    if isinstance(body, dict):
        print(f"  safety_verdict: {body.get('safety_verdict')} ({body.get('safety_layer')})")
        print(f"  citations: {body.get('citations')}")
        print(f"  response (first 700 chars):")
        print(f"    {body.get('response', '')[:700]}")
    else:
        print(f"  FAIL: code={code}, body={str(body)[:200]}")

    print()
    print("=" * 70)
    print("  LLM-backed demos complete. Server still running at http://127.0.0.1:8000")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
