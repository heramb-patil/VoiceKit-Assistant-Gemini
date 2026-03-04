"""Basecamp OAuth2 authentication helper (SaaS version — DB-backed per-user tokens)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("voicekit.integrations.basecamp.auth")

_AUTH_URL = "https://launchpad.37signals.com/authorization/new"
_TOKEN_URL = "https://launchpad.37signals.com/authorization/token"
_API_BASE = "https://3.basecampapi.com"


class BasecampAuth:
    """Handles Basecamp OAuth2 token acquisition and refresh.

    Supports two sources:
    1. ``from_db(user_email, session_factory)`` — loads from DB (SaaS)
    2. ``__init__(client_id, client_secret, token_file)`` — file-based (legacy/local dev)
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_file: str,
        _token_data: Optional[dict] = None,
        _user_email: Optional[str] = None,
        _db_session_factory=None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_file = Path(token_file)
        self._token_data: dict | None = _token_data
        self._user_email = _user_email
        self._db_session_factory = _db_session_factory
        self._use_db = _token_data is not None

    # ── Factory for DB-backed auth ────────────────────────────────────────────

    @classmethod
    async def from_db(cls, user_email: str, session_factory) -> "BasecampAuth":
        """Load Basecamp credentials for *user_email* from the ``user_credentials`` DB table."""
        from sqlalchemy import select
        from database.models import UserCredential

        client_id = os.environ.get("BASECAMP_CLIENT_ID", "")
        client_secret = os.environ.get("BASECAMP_CLIENT_SECRET", "")

        async with session_factory() as session:
            row = await session.execute(
                select(UserCredential).where(
                    UserCredential.user_email == user_email,
                    UserCredential.provider == "basecamp",
                )
            )
            cred = row.scalar_one_or_none()

        token_data: dict | None = None
        if cred:
            try:
                token_data = json.loads(cred.token_json)
            except Exception:
                pass

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            token_file=f"/tmp/basecamp_token_{user_email}.json",  # unused in DB mode
            _token_data=token_data if token_data else {},
            _user_email=user_email,
            _db_session_factory=session_factory,
        )

    # ── Token access ──────────────────────────────────────────────────────────

    def get_access_token(self) -> str:
        """Return a valid access token. Raises RuntimeError if not authenticated."""
        if self._use_db:
            token = (self._token_data or {}).get("access_token")
            if not token:
                raise RuntimeError(
                    "Basecamp token not found. "
                    "Please connect Basecamp via the Integrations panel."
                )
            return token

        # Legacy file-based flow
        if self._token_data is None and self._token_file.exists():
            self._token_data = json.loads(self._token_file.read_text())

        if self._token_data and self._token_data.get("access_token"):
            return self._token_data["access_token"]

        return self._run_oauth_flow()

    _REDIRECT_PORT = 8086
    _REDIRECT_URI = f"http://localhost:{_REDIRECT_PORT}/"

    def _run_oauth_flow(self) -> str:
        """Run OAuth2 flow using a local redirect server on port 8086 (local dev only)."""
        import urllib.parse
        import urllib.request
        import webbrowser
        from http.server import BaseHTTPRequestHandler, HTTPServer

        redirect_uri = self._REDIRECT_URI
        received_code: list[str] = []

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    received_code.append(params["code"][0])
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"<h2>Basecamp authorized! You can close this tab.</h2>")
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"<h2>Authorization failed - no code received.</h2>")

            def log_message(self, *args):
                pass

        params = {
            "type": "web_server",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
        }
        auth_url = f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"

        server = HTTPServer(("localhost", self._REDIRECT_PORT), _Handler)
        server.timeout = 120

        print(f"\nOpening Basecamp authorization in your browser...")
        print(f"If it doesn't open automatically, visit:\n{auth_url}\n")
        webbrowser.open(auth_url)

        server.handle_request()
        server.server_close()

        if not received_code:
            raise RuntimeError("No authorization code received from Basecamp.")

        code = received_code[0]
        payload = urllib.parse.urlencode({
            "type": "web_server",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }).encode()

        req = urllib.request.Request(
            f"{_TOKEN_URL}?type=web_server",
            data=payload,
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            self._token_data = json.loads(resp.read())

        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        self._token_file.write_text(json.dumps(self._token_data, indent=2))
        logger.info("Basecamp token saved to %s", self._token_file)
        return self._token_data["access_token"]

    def get_account_id(self) -> str | None:
        """Return the Basecamp account ID."""
        if self._token_data is None:
            self.get_access_token()

        accounts = (self._token_data or {}).get("accounts", [])
        if accounts:
            return str(accounts[0].get("id", ""))

        import urllib.request
        token = (self._token_data or {}).get("access_token")
        if not token:
            return None

        user_agent = os.environ.get("BASECAMP_USER_AGENT", "VoiceKit (voicekit@example.com)")
        req = urllib.request.Request(
            "https://launchpad.37signals.com/authorization.json",
            headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent},
        )
        with urllib.request.urlopen(req, timeout=10, context=self._ssl_context()) as resp:
            data = json.loads(resp.read())

        accounts = data.get("accounts", [])
        if accounts:
            if self._token_data:
                self._token_data["accounts"] = accounts
                if not self._use_db:
                    self._token_file.write_text(json.dumps(self._token_data, indent=2))
            return str(accounts[0].get("id", ""))

        return None

    def _ssl_context(self):
        """Return an SSL context that works on macOS (uses certifi if available)."""
        import ssl
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            return ssl.create_default_context()

    async def _async_clear_db_credential(self) -> None:
        """Delete this user's Basecamp credential from the DB after a 401."""
        from sqlalchemy import select
        from database.models import UserCredential
        try:
            async with self._db_session_factory() as session:
                row = await session.execute(
                    select(UserCredential).where(
                        UserCredential.user_email == self._user_email,
                        UserCredential.provider == "basecamp",
                    )
                )
                cred = row.scalar_one_or_none()
                if cred:
                    await session.delete(cred)
                    await session.commit()
            logger.info("Cleared stale Basecamp credential for %s", self._user_email)
            # Also update in-memory auth state so /auth/status reflects disconnected
            try:
                from api import _get_auth_mgr
                mgr = _get_auth_mgr(self._user_email)
                mgr.basecamp.status = "disconnected"
                mgr.basecamp.label = None
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Failed to clear Basecamp credential from DB: %s", exc)

    def api_request(self, path: str, method: str = "GET", body: Any = None) -> Any:
        """Make an authenticated Basecamp API request."""
        import urllib.error
        import urllib.request

        account_id = self.get_account_id()
        if not account_id:
            raise ValueError("Could not determine Basecamp account ID")

        url = f"{_API_BASE}/{account_id}/{path.lstrip('/')}"
        token = self.get_access_token()
        user_agent = os.environ.get("BASECAMP_USER_AGENT", "VoiceKit (voicekit@example.com)")

        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": user_agent,
            "Content-Type": "application/json",
        }
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15, context=self._ssl_context()) as resp:
                content = resp.read()
                if not content or not content.strip():
                    return {}
                return json.loads(content)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                self._token_data = None
                # Clear the DB credential so the panel shows "disconnected"
                # rather than staying stuck on "connected" with a dead token.
                if self._use_db and self._user_email and self._db_session_factory:
                    import asyncio as _aio
                    try:
                        loop = _aio.get_running_loop()
                        loop.create_task(self._async_clear_db_credential())
                    except RuntimeError:
                        pass
                raise RuntimeError(
                    "Basecamp token expired or revoked. Re-connect via the Integrations panel."
                ) from exc
            elif exc.code == 204:
                return {}
            raise
