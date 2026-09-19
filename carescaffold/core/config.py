"""CareScaffold — configuration loader & model registry hot-reload.

Spec §3: /core/config.py
Spec §4.3: model identifiers live ONLY in /config/model_registry.yaml
Spec §1.2.5: never hardcode model identifiers in business logic.
"""
from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Project paths ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "config" / "model_registry.yaml"


class Settings(BaseSettings):
    """Runtime settings, populated from environment + .env file.

    Note on env-var precedence: pydantic-settings reads shell env vars
    BEFORE the .env file. If the parent shell sets DATABASE_URL (e.g. a
    leftover from another project), it will win. The carescaffold/.env
    file is the intended source of truth. Tests handle this via the
    _isolate_db fixture in conftest.py.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_api_key: str = ""
    voyageai_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./carescaffold.db"
    carescaffold_api_key: str = ""
    audit_log_hash_salt: str = ""
    model_registry_path: str = str(DEFAULT_REGISTRY_PATH)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide Settings singleton.

    Tests that need to override env vars should call `get_settings.cache_clear()`.
    """
    return Settings()


# ── Model registry: hot-reload via mtime check ───────────────────
class ModelRegistry:
    """Wraps /config/model_registry.yaml with hot-reload semantics.

    A model swap is a one-line YAML edit; the next call to `Registry.get()`
    picks up the change because we stat the file mtime on every access.
    Cost of the stat is sub-microsecond; no FS reads unless mtime changed.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or get_settings().model_registry_path)
        self._cached: dict[str, Any] | None = None
        self._cached_mtime: float = -1.0

    def _mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except FileNotFoundError as e:
            raise FileNotFoundError(
                f"Model registry not found at {self.path}. "
                f"Spec §4.3 requires this file to exist."
            ) from e

    def _load(self) -> dict[str, Any]:
        with self.path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or "roles" not in data:
            raise ValueError(
                f"Model registry {self.path} missing top-level 'roles' key "
                f"(spec §4.3)."
            )
        return data

    def get(self) -> dict[str, Any]:
        """Return current registry contents, reloading if file changed."""
        mtime = self._mtime()
        if self._cached is None or mtime != self._cached_mtime:
            self._cached = self._load()
            self._cached_mtime = mtime
        return self._cached

    def invalidate(self) -> None:
        """Force reload on next .get() — used by tests."""
        self._cached = None
        self._cached_mtime = -1.0


# ── Typed accessors ─────────────────────────────────────────────
class RoleConfig(BaseModel):
    primary: str
    fallback: str | None = None


class EmbeddingsConfig(BaseModel):
    primary: str
    output_dimension: int | str = 1024  # int for Voyage AI; "dynamic" for TF-IDF


def models_for_role(role: str) -> tuple[str, str | None]:
    """Return (primary, fallback) for a given role name."""
    roles = ModelRegistry().get()["roles"]
    if role not in roles:
        raise KeyError(f"Unknown role '{role}'. Defined: {list(roles)}")
    cfg = roles[role]
    return cfg["primary"], cfg.get("fallback")


def embeddings_config() -> EmbeddingsConfig:
    """Return typed embeddings config."""
    roles = ModelRegistry().get()["roles"]
    return EmbeddingsConfig(**roles["embeddings"])


def timeouts() -> dict[str, Any]:
    return ModelRegistry().get().get("timeouts", {})


def retry_config() -> dict[str, Any]:
    return ModelRegistry().get().get("retry", {})
