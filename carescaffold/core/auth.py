"""Minimal auth for the CareScaffold demo.

Spec §3 note on /core: 'minimal auth (this is a demo, not multi-tenant SaaS)'.

Approach: single API key in env (CARESCAFFOLD_API_KEY). If unset, auth is
disabled (development mode). If set, every request must include
`Authorization: Bearer <key>`. This is documented as a known simplification,
not a production pattern.

Production would need: per-service JWT issuer + JWKS verifier, per-request
policy enforcement, least-privilege DB roles — out of scope for this demo.
"""
from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)


async def verify_api_key(
    creds: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> str:
    """Verify the incoming Bearer token matches CARESCAFFOLD_API_KEY.

    If CARESCAFFOLD_API_KEY is unset (dev mode), accept any token (or none).
    If set, reject 401 on mismatch — do not leak whether the issue is
    a missing token vs. a wrong token (timing-safe compare).
    """
    settings = get_settings()
    expected = settings.carescaffold_api_key

    if not expected:
        # Dev mode: auth disabled. Return a synthetic subject.
        return "dev"

    if creds is None or creds.credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return "service"


# Dependency shortcut for FastAPI routes
AuthSubject = Annotated[str, Depends(verify_api_key)]
