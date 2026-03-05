"""Google OAuth2 authentication helper (SaaS version — DB-backed per-user tokens)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_AUTH_ERROR_MSG = (
    "Google authentication token has expired. "
    "Please re-connect Google via the Integrations panel."
)

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/drive.file",
]


class GoogleAuth:
    """Handles loading credentials and maintaining an authenticated service.

    Supports two sources for the token:
    1. ``from_db(user_email, session_factory)`` — loads from ``UserCredential`` DB row (SaaS)
    2. ``__init__(credentials_file, token_file, scopes)`` — loads from files (legacy/local dev)
    """

    def __init__(
        self,
        credentials_file: str,
        token_file: str,
        scopes: list[str],
        _token_json: Optional[str] = None,
        _user_email: Optional[str] = None,
        _db_session_factory=None,
    ) -> None:
        self._credentials_file = Path(credentials_file)
        self._token_file = Path(token_file)
        self._scopes = scopes
        self._creds: Any = None
        self._auth_failed = False
        # DB-backed fields (set by from_db)
        self._token_json: Optional[str] = _token_json
        self._user_email: Optional[str] = _user_email
        self._db_session_factory = _db_session_factory
        self._use_db = _token_json is not None
        # Cached service clients: {f"{name}:{version}": service_obj}
        # build() fetches a discovery document and allocates an HTTP pool —
        # caching it saves ~100-300ms per tool call on repeated invocations.
        self._service_cache: dict[str, Any] = {}

    # ── Factory for DB-backed auth ────────────────────────────────────────────

    @classmethod
    async def from_db(cls, user_email: str, session_factory) -> "GoogleAuth":
        """Load Google credentials for *user_email* from the ``user_credentials`` DB table.

        Returns a ``GoogleAuth`` instance whose ``_save_token()`` writes back to the DB row.
        """
        from sqlalchemy import select
        from database.models import UserCredential

        async with session_factory() as session:
            row = await session.execute(
                select(UserCredential).where(
                    UserCredential.user_email == user_email,
                    UserCredential.provider == "google",
                )
            )
            cred = row.scalar_one_or_none()

        token_json = cred.token_json if cred else None
        instance = cls(
            credentials_file="integrations/google/credentials/google_credentials.json",
            token_file=f"/tmp/google_token_{user_email}.json",  # unused in DB mode
            scopes=GOOGLE_SCOPES,
            _token_json=token_json,
            _user_email=user_email,
            _db_session_factory=session_factory,
        )
        return instance

    # ── Credential loading ────────────────────────────────────────────────────

    def get_credentials(self) -> Any:
        """Load or refresh OAuth2 credentials."""
        from google.oauth2.credentials import Credentials

        if self._auth_failed:
            raise RuntimeError(_AUTH_ERROR_MSG)

        # Load from DB-supplied JSON or from file
        if self._creds is None:
            token_json = self._token_json if self._use_db else self._load_token_from_file()
            if token_json:
                import json as _json, tempfile, os
                try:
                    data = _json.loads(token_json)
                    if data.get("refresh_token"):
                        tmp = tempfile.NamedTemporaryFile(
                            mode="w", suffix=".json", delete=False
                        )
                        tmp.write(token_json)
                        tmp.close()
                        try:
                            self._creds = Credentials.from_authorized_user_file(
                                tmp.name, self._scopes
                            )
                        finally:
                            os.unlink(tmp.name)
                    else:
                        self._creds = Credentials(
                            token=data.get("token"),
                            token_uri=data.get("token_uri"),
                            client_id=data.get("client_id"),
                            client_secret=data.get("client_secret"),
                            scopes=data.get("scopes") or self._scopes,
                        )
                except Exception as exc:
                    logger.warning("Could not load token: %s — re-auth needed", exc)
                    self._creds = None

        if self._creds and self._creds.expired:
            if self._creds.refresh_token:
                try:
                    from google.auth.transport.requests import Request
                    self._creds.refresh(Request())
                    self._save_token()
                    logger.info("Google token refreshed for %s", self._user_email or "user")
                    return self._creds
                except Exception as exc:
                    logger.warning("Token refresh failed: %s", exc)
                    self._creds = None
                    self._auth_failed = True
                    self._service_cache.clear()
                    raise RuntimeError(_AUTH_ERROR_MSG) from exc
            else:
                self._creds = None
                self._auth_failed = True
                self._service_cache.clear()
                raise RuntimeError(_AUTH_ERROR_MSG)

        if self._creds and self._creds.valid:
            return self._creds

        # In SaaS/DB mode, never fall back to running a local server
        if self._use_db:
            raise RuntimeError(_AUTH_ERROR_MSG)

        # Legacy file-based flow (local dev only)
        if not self._credentials_file.exists():
            raise FileNotFoundError(
                f"Google credentials file not found: {self._credentials_file}"
            )

        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(
            str(self._credentials_file), self._scopes
        )
        logger.info("Starting Google OAuth flow (local dev). Opening browser on port 8085...")
        self._creds = flow.run_local_server(
            port=8085, access_type="offline", prompt="consent"
        )
        self._save_token()
        self._auth_failed = False
        return self._creds

    def _load_token_from_file(self) -> Optional[str]:
        if self._token_file.exists():
            try:
                return self._token_file.read_text()
            except Exception:
                pass
        return None

    def _save_token(self) -> None:
        """Persist refreshed credentials back to DB (SaaS) or file (legacy)."""
        if self._creds is None:
            return
        token_json = self._creds.to_json()
        if self._use_db and self._user_email and self._db_session_factory:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_save_to_db(token_json))
            except RuntimeError:
                pass  # no running loop — skip (refresh will re-save next time)
        else:
            self._token_file.parent.mkdir(parents=True, exist_ok=True)
            self._token_file.write_text(token_json)

    async def _async_save_to_db(self, token_json: str) -> None:
        from datetime import datetime, timezone
        from sqlalchemy import select
        from database.models import UserCredential

        try:
            async with self._db_session_factory() as session:
                row = await session.execute(
                    select(UserCredential).where(
                        UserCredential.user_email == self._user_email,
                        UserCredential.provider == "google",
                    )
                )
                cred = row.scalar_one_or_none()
                now = datetime.now(timezone.utc)
                if cred is None:
                    cred = UserCredential(
                        user_email=self._user_email,
                        provider="google",
                        token_json=token_json,
                        updated_at=now,
                    )
                    session.add(cred)
                else:
                    cred.token_json = token_json
                    cred.updated_at = now
                await session.commit()
            self._token_json = token_json  # update in-memory cache
        except Exception as exc:
            logger.warning("Failed to save refreshed Google token to DB: %s", exc)

    # ── Service helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _make_http():
        """httplib2.Http with certifi CA bundle (fixes macOS SSL errors)."""
        import httplib2
        try:
            import certifi
            return httplib2.Http(ca_certs=certifi.where())
        except ImportError:
            return httplib2.Http()

    def build_service(self, service_name: str, version: str) -> Any:
        """Return a cached Google API service client, building it on first use.

        Uses certifi CA bundle to avoid macOS SSL errors with httplib2.
        The Credentials object is shared by reference, so token auto-refresh
        continues to work with the cached service instance.
        """
        cache_key = f"{service_name}:{version}"
        if cache_key not in self._service_cache:
            from googleapiclient.discovery import build
            from google_auth_httplib2 import AuthorizedHttp
            creds = self.get_credentials()
            authed_http = AuthorizedHttp(creds, http=self._make_http())
            self._service_cache[cache_key] = build(
                service_name, version, http=authed_http,
                static_discovery=True,  # use bundled discovery doc — no network fetch
            )
            logger.debug("Built and cached Google service %s", cache_key)
        return self._service_cache[cache_key]

    def invalidate_service_cache(self) -> None:
        """Clear cached service clients (call after token revocation/refresh failure)."""
        self._service_cache.clear()

    def is_authenticated(self) -> bool:
        """Return True if credentials are currently valid."""
        if self._auth_failed:
            return False
        if self._creds and self._creds.valid:
            return True
        try:
            self.get_credentials()
            return True
        except Exception:
            return False
