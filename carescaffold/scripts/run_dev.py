#!/usr/bin/env python3
"""Tiny entry point: check Python version, then run uvicorn.

Usage:
    python3 scripts/run_dev.py
    # (run from the carescaffold/ project root)

This script checks that Python 3.11+ is installed before trying to
import app — gives a friendly error instead of the cryptic uvicorn
version-mismatch error from pip.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def check_python_version() -> None:
    """Spec §2 requires Python 3.11+. Fail clearly if older."""
    v = sys.version_info
    if v < (3, 11):
        print(f"ERROR: Python 3.11+ required (spec §2). You have {v.major}.{v.minor}.{v.micro}.")
        print()
        print("macOS fix:")
        print("  brew install python@3.11")
        print("  cd <path-to>/carescaffold")
        print("  python3.11 -m venv .venv")
        print("  source .venv/bin/activate")
        print("  pip install -r requirements.txt")
        print("  python3 scripts/run_dev.py")
        print()
        print("Linux (Ubuntu/Debian) fix:")
        print("  sudo apt install python3.11 python3.11-venv")
        print("  python3.11 -m venv .venv && source .venv/bin/activate")
        print("  pip install -r requirements.txt")
        print()
        print("Or use pyenv (any platform):")
        print("  brew install pyenv  # or: curl https://pyenv.run | bash")
        print("  pyenv install 3.11")
        print("  pyenv local 3.11")
        sys.exit(1)
    print(f"  ✓ Python {v.major}.{v.minor}.{v.micro} (>=3.11 required)")


def main() -> int:
    print("=== CareScaffold dev server ===")
    check_python_version()

    # Check we're being run from the project root (where app.py lives)
    project_dir = Path(__file__).resolve().parent.parent
    app_py = project_dir / "app.py"
    if not app_py.exists():
        print(f"ERROR: cannot find app.py — are you running from the project root?")
        print(f"  expected: {app_py}")
        sys.exit(1)

    # Start uvicorn
    print(f"  project: {project_dir}")
    print(f"  starting uvicorn on http://127.0.0.1:8000 ...")
    print()
    env = {
        **__import__("os").environ,
        # Don't let any inherited DATABASE_URL from a parent shell clobber our SQLite setup
        "DATABASE_URL": "",
    }
    env.pop("DATABASE_URL", None)
    return subprocess.call(
        [sys.executable, "-m", "uvicorn", "app:app", "--reload", "--port", "8000"],
        cwd=str(project_dir),
        env=env,
    )


if __name__ == "__main__":
    sys.exit(main())
