"""
Authentication middleware for VoiceKit SaaS.

Validates Google Workspace ID tokens (Bearer header) and returns a verified
user dict. All API endpoints use the `CurrentUser` dependency.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency — validates Bearer Google ID token, returns user dict.

    Returns:
        {"email": str, "name": str, "picture": str}

    Raises:
        HTTPException 401 — missing or invalid token
        HTTPException 403 — token valid but not from allowed domain
    """
    from config import config  # imported here to avoid circular at module level

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed auth token")

    token = auth_header[7:]

    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            config.google_client_id,
        )
    except Exception as exc:
        logger.warning("Token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired auth token") from exc

    email: str = claims.get("email", "")
    if not email:
        raise HTTPException(status_code=401, detail="Token contains no email claim")

    if config.allowed_domain and claims.get("hd") != config.allowed_domain:
        raise HTTPException(
            status_code=403,
            detail=f"Account must belong to domain '{config.allowed_domain}'",
        )

    return {
        "email": email,
        "name": claims.get("name", ""),
        "picture": claims.get("picture", ""),
        "sub": claims.get("sub", ""),
    }


# Convenience alias for use as a FastAPI dependency annotation
CurrentUser = Annotated[dict, Depends(get_current_user)]
