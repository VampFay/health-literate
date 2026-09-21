#!/usr/bin/env python3
"""Test the new patient-facing UI: start server, hit /, verify HTML + a real scaffold call."""
import json, os, subprocess, time, urllib.request
from pathlib import Path

# Auto-detect project root (this script lives in <project_root>/scripts/)
# But this is in /home/z/my-project/scripts/, so go up 2 levels... actually no.
# The carescaffold dir is at /home/z/my-project/carescaffold/
# This script is at /home/z/my-project/scripts/ — so PROJECT_DIR is ../carescaffold
SCRIPT_DIR = Path(__file__).resolve().parent
# Try: this script might be in carescaffold/scripts/ OR /home/z/my-project/scripts/
candidates = [
    SCRIPT_DIR.parent,                              # if script is in carescaffold/scripts/
    SCRIPT_DIR.parent / "carescaffold",             # if script is in /home/z/my-project/scripts/
    Path("/home/z/my-project/carescaffold"),        # fallback
]
PROJECT_DIR = next((c for c in candidates if (c / "app.py").exists()), None)
if PROJECT_DIR is None:
    print(f"ERROR: cannot find app.py. Tried: {[str(c) for c in candidates]}")
    exit(1)
LOG = Path("/tmp/carescaffold_ui_test.log")
print(f"  project: {PROJECT_DIR}")

subprocess.run(["pkill", "-f", "uvicorn app:app"], capture_output=True)
time.sleep(1)
env = os.environ.copy()
env.pop("DATABASE_URL", None)
proc = subprocess.Popen(
    ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"],
    cwd=str(PROJECT_DIR), env=env,
    stdout=LOG.open("w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
    start_new_session=True,
)
for _ in range(30):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1):
            break
    except Exception:
        time.sleep(1)
else:
    print("server didn't come up"); print(LOG.read_text()); exit(1)

print("=== server up ===")

# 1. GET / should return the patient UI HTML
print("\n--- GET / (patient-facing UI) ---")
resp = urllib.request.urlopen("http://127.0.0.1:8000/")
html = resp.read().decode()
print(f"  HTTP {resp.status}, {len(html)} bytes")
checks = [
    ("CareScaffold" in html, "title 'CareScaffold' present"),
    ("Type 2 Diabetes Education Assistant" in html, "subtitle present"),
    ("portfolio demo" in html.lower() or "Portfolio demo" in html, "disclaimer present"),
    ("persona-foundational" in html, "persona toggle present"),
    ("askSuggested" in html, "suggested questions present"),
    ("fetch('/scaffold'" in html or "fetch(\"/scaffold\"" in html, "calls POST /scaffold"),
    ("tailwindcss" in html, "Tailwind via CDN"),
]
for ok, desc in checks:
    print(f"  {'✓' if ok else '✗'} {desc}")
all_ok = all(ok for ok, _ in checks)

# 2. POST /scaffold should return a real response (chest pain = instant escalation)
print("\n--- POST /scaffold (chest pain — should escalate, no LLM call) ---")
req = urllib.request.Request(
    "http://127.0.0.1:8000/scaffold",
    data=json.dumps({"question": "I am having chest pain spreading to my left arm.", "persona": "foundational"}).encode(),
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=10)
data = json.loads(resp.read().decode())
print(f"  escalated: {data.get('escalated')}")
print(f"  safety_layer: {data.get('safety_layer')}")
print(f"  response: {data.get('response', '')[:100]}...")
ok = data.get("escalated") is True
print(f"  {'✓' if ok else '✗'} inbound safety escalation works")

print("\n=== SUMMARY ===")
print(f"  UI: {'✓ all checks pass' if all_ok else '✗ some checks failed'}")
print(f"  Safety: {'✓ works' if ok else '✗ broken'}")
print(f"\n  Open http://localhost:8000/ in your browser to see the patient UI.")

subprocess.run(["pkill", "-f", "uvicorn app:app"], capture_output=True)
