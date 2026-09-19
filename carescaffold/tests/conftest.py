"""Pytest configuration — async mode, sys.path, shared fixtures."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Make carescaffold/ importable as top-level package (api, core, etc.)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Tests should not hit the real LLM/embeddings APIs by default.
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("VOYAGEAI_API_KEY", "")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("AUDIT_LOG_HASH_SALT", "test-salt-not-for-prod")


@pytest_asyncio.fixture(autouse=True)
async def _isolate_db(tmp_path, monkeypatch):
    """Each test gets a fresh SQLite file DB in tmp_path."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    from core.config import get_settings
    get_settings.cache_clear()
    from core import db as db_module
    await db_module.dispose_engine()
    yield
    await db_module.dispose_engine()
    get_settings.cache_clear()
