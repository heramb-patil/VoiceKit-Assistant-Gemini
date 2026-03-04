"""
Tests for:
1. Auth disconnect endpoints (Gap 2 fix)
2. Basecamp 401 clears DB credential (Gap 1 fix)
3. send_email with Drive attachment
4. drive.get_file_content helper
"""
import asyncio
import base64
import json
from email import message_from_bytes
from email.mime.multipart import MIMEMultipart
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── 1. Auth disconnect endpoints ─────────────────────────────────────────────

class TestDisconnectEndpoints:
    @pytest.mark.asyncio
    async def test_google_disconnect_returns_200(self, async_client):
        r = await async_client.delete("/gemini-live/auth/google/disconnect")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_google_disconnect_returns_disconnected_status(self, async_client):
        data = (await async_client.delete("/gemini-live/auth/google/disconnect")).json()
        assert data["status"] == "disconnected"

    @pytest.mark.asyncio
    async def test_basecamp_disconnect_returns_200(self, async_client):
        r = await async_client.delete("/gemini-live/auth/basecamp/disconnect")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_basecamp_disconnect_returns_disconnected_status(self, async_client):
        data = (await async_client.delete("/gemini-live/auth/basecamp/disconnect")).json()
        assert data["status"] == "disconnected"

    @pytest.mark.asyncio
    async def test_google_status_shows_disconnected_after_disconnect(self, async_client):
        # Connect first (fake it by setting in-memory state)
        import api as _api
        mgr = _api._get_auth_mgr("test@example.com")
        mgr.google.status = "connected"
        mgr.google.label = "test@example.com"

        await async_client.delete("/gemini-live/auth/google/disconnect")

        assert mgr.google.status == "disconnected"
        assert mgr.google.label is None

    @pytest.mark.asyncio
    async def test_basecamp_status_shows_disconnected_after_disconnect(self, async_client):
        import api as _api
        mgr = _api._get_auth_mgr("test@example.com")
        mgr.basecamp.status = "connected"
        mgr.basecamp.label = "My Company"

        await async_client.delete("/gemini-live/auth/basecamp/disconnect")

        assert mgr.basecamp.status == "disconnected"
        assert mgr.basecamp.label is None

    @pytest.mark.asyncio
    async def test_auth_status_reflects_disconnect(self, async_client):
        import api as _api
        mgr = _api._get_auth_mgr("test@example.com")
        mgr.google.status = "connected"
        mgr.google.label = "test@example.com"

        await async_client.delete("/gemini-live/auth/google/disconnect")

        r = await async_client.get("/gemini-live/auth/status")
        assert r.status_code == 200
        data = r.json()
        assert data["google"]["status"] == "disconnected"


# ── 2. Basecamp 401 clears DB credential ─────────────────────────────────────

class TestBasecampTokenRevocation:
    def _make_auth(self, token_data: dict | None = None):
        """Build a BasecampAuth instance in DB mode with a mock session factory.

        token_data includes 'accounts' so get_account_id() returns immediately
        without making an HTTP call — leaving urlopen free for test mocking.
        """
        from integrations.basecamp.auth import BasecampAuth

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_session)

        data = token_data or {
            "access_token": "valid_token",
            "accounts": [{"id": "12345", "name": "Test Co"}],
        }
        auth = BasecampAuth(
            client_id="test_id",
            client_secret="test_secret",
            token_file="/tmp/test_token.json",
            _token_data=data,
            _user_email="test@example.com",
            _db_session_factory=mock_factory,
        )
        return auth, mock_session

    def test_401_clears_token_data(self):
        """A 401 from Basecamp API should clear _token_data on the instance."""
        import urllib.error
        from integrations.basecamp.auth import BasecampAuth

        auth, _ = self._make_auth()
        assert auth._token_data is not None

        http_err = urllib.error.HTTPError(
            url="https://3.basecampapi.com/test",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(RuntimeError, match="expired or revoked"):
                auth.api_request("projects.json")

        assert auth._token_data is None

    def test_401_raises_runtime_error_with_reconnect_message(self):
        """401 error message should guide user to reconnect."""
        import urllib.error

        auth, _ = self._make_auth()
        http_err = urllib.error.HTTPError(
            url="https://3.basecampapi.com/test",
            code=401, msg="Unauthorized", hdrs=None, fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(RuntimeError) as exc_info:
                auth.api_request("projects.json")

        assert "Integrations panel" in str(exc_info.value)

    def test_non_401_http_error_is_reraised(self):
        """Non-401 HTTP errors should propagate unchanged."""
        import urllib.error

        auth, _ = self._make_auth()
        http_err = urllib.error.HTTPError(
            url="https://3.basecampapi.com/test",
            code=500, msg="Internal Server Error", hdrs=None, fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                auth.api_request("projects.json")

        assert exc_info.value.code == 500
        # token_data should NOT be cleared for non-401 errors
        assert auth._token_data is not None


# ── 3. send_email with Drive attachment ──────────────────────────────────────

class TestSendEmailWithAttachment:
    def _setup_gmail(self):
        """Inject a mock Gmail service into the gmail module."""
        import integrations.google.gmail as gmail_mod
        from integrations.google.auth import GoogleAuth

        mock_auth = MagicMock(spec=GoogleAuth)
        mock_service = MagicMock()
        mock_auth.build_service.return_value = mock_service
        gmail_mod._auth = mock_auth
        return gmail_mod, mock_service, mock_auth

    def _teardown_gmail(self):
        import integrations.google.gmail as gmail_mod
        gmail_mod._auth = None

    @pytest.mark.asyncio
    async def test_send_plain_email_no_attachment(self):
        """send_email without attach_drive_file sends a plain MIMEText message."""
        gmail_mod, mock_service, _ = self._setup_gmail()
        sent_raw = {}

        def capture_send(**kwargs):
            sent_raw["raw"] = kwargs["body"]["raw"]
            return MagicMock(execute=MagicMock(return_value={"id": "msg123"}))

        mock_service.users.return_value.messages.return_value.send.return_value = MagicMock(
            execute=MagicMock(return_value={"id": "msg123"})
        )
        # Capture the raw bytes passed
        mock_service.users.return_value.messages.return_value.send.side_effect = lambda **kw: (
            sent_raw.update({"raw": kw["body"]["raw"]}) or
            MagicMock(execute=MagicMock(return_value={"id": "msg123"}))
        )

        result = await gmail_mod.send_email(
            to="bob@example.com",
            subject="Hello",
            body="Test body",
        )

        assert "bob@example.com" in result
        assert "Hello" in result
        assert "attachment" not in result.lower()
        self._teardown_gmail()

    @pytest.mark.asyncio
    async def test_send_email_with_drive_attachment(self):
        """send_email with attach_drive_file fetches from Drive and attaches."""
        gmail_mod, mock_service, _ = self._setup_gmail()

        fake_content = b"# Research results\n\nAI is changing everything."
        fake_file_tuple = ("Research AI trends.md", fake_content, "text/markdown")

        captured = {}

        def capture_send(**kwargs):
            captured["raw"] = kwargs["body"]["raw"]
            m = MagicMock()
            m.execute.return_value = {"id": "msg456"}
            return m

        mock_service.users.return_value.messages.return_value.send.side_effect = capture_send

        with patch(
            "integrations.google.drive.get_file_content",
            new=AsyncMock(return_value=fake_file_tuple),
        ):
            result = await gmail_mod.send_email(
                to="alice@example.com",
                subject="Research Report",
                body="Please find the report attached.",
                attach_drive_file="AI trends",
            )

        assert "alice@example.com" in result
        assert "attachment" in result.lower()
        assert "Research AI trends.md" in result

        # Decode the raw MIME and verify it's multipart with the attachment
        assert "raw" in captured
        raw_bytes = base64.urlsafe_b64decode(captured["raw"] + "==")
        msg = message_from_bytes(raw_bytes)
        assert msg.is_multipart()
        attachments = [p for p in msg.walk() if p.get_filename()]
        assert len(attachments) == 1
        assert attachments[0].get_filename() == "Research AI trends.md"
        assert attachments[0].get_payload(decode=True) == fake_content

        self._teardown_gmail()

    @pytest.mark.asyncio
    async def test_send_email_returns_error_if_drive_file_not_found(self):
        """If Drive file not found, send_email returns an informative error (no email sent)."""
        gmail_mod, mock_service, _ = self._setup_gmail()

        with patch(
            "integrations.google.drive.get_file_content",
            new=AsyncMock(return_value=None),
        ):
            result = await gmail_mod.send_email(
                to="alice@example.com",
                subject="Report",
                body="See attached.",
                attach_drive_file="NonExistentFile.md",
            )

        assert "Could not find" in result
        assert "NonExistentFile.md" in result
        # Gmail send should NOT have been called
        mock_service.users.return_value.messages.return_value.send.assert_not_called()
        self._teardown_gmail()


# ── 4. drive.get_file_content ─────────────────────────────────────────────────

class TestDriveGetFileContent:
    def _setup_drive(self):
        import integrations.google.drive as drive_mod
        from integrations.google.auth import GoogleAuth

        mock_auth = MagicMock(spec=GoogleAuth)
        mock_service = MagicMock()
        mock_auth.build_service.return_value = mock_service
        drive_mod._auth = mock_auth
        return drive_mod, mock_service

    def _teardown_drive(self):
        import integrations.google.drive as drive_mod
        drive_mod._auth = None

    @pytest.mark.asyncio
    async def test_returns_none_if_not_authenticated(self):
        import integrations.google.drive as drive_mod
        drive_mod._auth = None
        result = await drive_mod.get_file_content("somefile.md")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_if_file_not_found(self):
        drive_mod, mock_service = self._setup_drive()

        # Folder search returns empty, broad search returns empty
        mock_service.files.return_value.list.return_value.execute.return_value = {"files": []}

        result = await drive_mod.get_file_content("ghost.md")
        assert result is None
        self._teardown_drive()

    @pytest.mark.asyncio
    async def test_returns_file_tuple_on_success(self):
        drive_mod, mock_service = self._setup_drive()

        fake_content = b"# Deep Research\n\nResults here."

        # Folder list returns one file
        mock_service.files.return_value.list.return_value.execute.return_value = {
            "files": [{"id": "file123", "name": "Research AI.md", "mimeType": "text/markdown"}]
        }

        # MediaIoBaseDownload writes to the buffer
        import io
        from unittest.mock import patch as _patch

        class FakeDownloader:
            def __init__(self, buf, request):
                self._buf = buf

            def next_chunk(self):
                self._buf.write(fake_content)
                return None, True

        with _patch("googleapiclient.http.MediaIoBaseDownload", FakeDownloader):
            mock_service.files.return_value.get_media.return_value = MagicMock()
            result = await drive_mod.get_file_content("Research AI")

        assert result is not None
        filename, content, mimetype = result
        assert filename == "Research AI.md"
        assert content == fake_content
        assert "text" in mimetype or "markdown" in mimetype
        self._teardown_drive()

    @pytest.mark.asyncio
    async def test_falls_back_to_broad_search_if_folder_empty(self):
        drive_mod, mock_service = self._setup_drive()

        fake_content = b"# Report"
        call_count = [0]

        def list_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call (folder search) — return folder
                return MagicMock(execute=MagicMock(return_value={"files": [{"id": "folder1"}]}))
            elif call_count[0] == 2:
                # Second call (files in folder) — empty
                return MagicMock(execute=MagicMock(return_value={"files": []}))
            else:
                # Third call (broad search) — found
                return MagicMock(execute=MagicMock(return_value={
                    "files": [{"id": "file999", "name": "report.md", "mimeType": "text/markdown"}]
                }))

        mock_service.files.return_value.list.side_effect = list_side_effect

        import io
        from unittest.mock import patch as _patch

        class FakeDownloader:
            def __init__(self, buf, request):
                self._buf = buf

            def next_chunk(self):
                self._buf.write(fake_content)
                return None, True

        with _patch("googleapiclient.http.MediaIoBaseDownload", FakeDownloader):
            mock_service.files.return_value.get_media.return_value = MagicMock()
            result = await drive_mod.get_file_content("report")

        assert result is not None
        assert result[0] == "report.md"
        self._teardown_drive()
