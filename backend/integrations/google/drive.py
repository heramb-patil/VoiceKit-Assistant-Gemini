"""Google Drive tools — upload and list files in VoiceKit's research folder."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from integrations.google.auth import GoogleAuth

logger = logging.getLogger(__name__)

_auth: "GoogleAuth | None" = None  # injected by orchestration per-user

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
VOICEKIT_FOLDER = "VoiceKit Research"


def _get_or_create_folder(service, folder_name: str) -> str:
    """Return the Drive folder ID, creating it if it doesn't exist."""
    query = (
        f"mimeType='application/vnd.google-apps.folder' "
        f"and name='{folder_name}' and trashed=false"
    )
    results = service.files().list(q=query, fields="files(id,name)").execute()
    folders = results.get("files", [])
    if folders:
        return folders[0]["id"]

    folder_meta = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=folder_meta, fields="id").execute()
    logger.info("Created Drive folder '%s' (id=%s)", folder_name, folder["id"])
    return folder["id"]


async def upload_to_drive(filename: str, content: str, folder: str = VOICEKIT_FOLDER) -> str:
    """Upload a text/markdown file to Google Drive.

    Args:
        filename: Name for the file in Drive (e.g. 'Research: AI trends.md')
        content:  Text content to upload
        folder:   Drive folder name (default: 'VoiceKit Research')

    Returns:
        Confirmation message with the Drive file URL.
    """
    if _auth is None:
        return "Google Drive not connected. Please connect Google in the Integrations panel."
    try:
        import io
        from googleapiclient.http import MediaIoBaseUpload

        service = _auth.build_service("drive", "v3")
        folder_id = _get_or_create_folder(service, folder)

        # Sanitise filename
        safe_name = filename if filename.endswith(".md") else f"{filename}.md"

        file_meta = {"name": safe_name, "parents": [folder_id]}
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")),
            mimetype="text/markdown",
            resumable=False,
        )
        file = service.files().create(
            body=file_meta, media_body=media, fields="id,webViewLink"
        ).execute()

        url = file.get("webViewLink", "")
        logger.info("Uploaded '%s' to Drive folder '%s': %s", safe_name, folder, url)
        return f"Saved to Google Drive: {safe_name}\nLink: {url}"

    except Exception as exc:
        logger.warning("Drive upload failed: %s", exc)
        return f"Drive upload failed: {exc}"


async def get_file_content(file_name: str, folder: str = VOICEKIT_FOLDER) -> tuple[str, bytes, str] | None:
    """Download a file from Google Drive by name.

    Searches the VoiceKit Research folder first, then all Drive files.

    Args:
        file_name: File name to search for (partial match supported)
        folder:    Folder to search first (default: 'VoiceKit Research')

    Returns:
        (filename, content_bytes, mimetype) tuple, or None if not found.
    """
    if _auth is None:
        return None
    try:
        import io
        from googleapiclient.http import MediaIoBaseDownload

        service = _auth.build_service("drive", "v3")

        # Search in the VoiceKit folder first, fall back to all files
        folder_id = _get_or_create_folder(service, folder)
        query = f"name contains '{file_name}' and trashed=false and '{folder_id}' in parents"
        results = service.files().list(
            q=query,
            fields="files(id,name,mimeType)",
            orderBy="createdTime desc",
            pageSize=5,
        ).execute()
        files = results.get("files", [])

        if not files:
            # Broaden search to all of Drive
            query = f"name contains '{file_name}' and trashed=false"
            results = service.files().list(
                q=query,
                fields="files(id,name,mimeType)",
                orderBy="createdTime desc",
                pageSize=5,
            ).execute()
            files = results.get("files", [])

        if not files:
            return None

        file = files[0]
        file_id = file["id"]
        filename = file["name"]
        mimetype = file.get("mimeType", "application/octet-stream")

        # Google Docs/Sheets/Slides need export; uploaded files use get_media
        export_map = {
            "application/vnd.google-apps.document": ("text/plain", ".txt"),
            "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
            "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
        }
        if mimetype in export_map:
            export_mime, ext = export_map[mimetype]
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
            if not filename.endswith(ext):
                filename += ext
            mimetype = export_mime
        else:
            request = service.files().get_media(fileId=file_id)

        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        logger.info("Downloaded Drive file '%s' (%d bytes)", filename, buf.tell())
        return filename, buf.getvalue(), mimetype

    except Exception as exc:
        logger.warning("Drive get_file_content failed for '%s': %s", file_name, exc)
        return None


async def list_drive_files(folder: str = VOICEKIT_FOLDER, max_results: int = 10) -> str:
    """List files in the VoiceKit Research folder on Google Drive.

    Args:
        folder:      Folder name to list (default: 'VoiceKit Research')
        max_results: Max number of files to return (default 10)

    Returns:
        Formatted list of files with links.
    """
    if _auth is None:
        return "Google Drive not connected."
    try:
        service = _auth.build_service("drive", "v3")
        folder_id = _get_or_create_folder(service, folder)
        query = f"'{folder_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            fields="files(id,name,webViewLink,createdTime)",
            orderBy="createdTime desc",
            pageSize=max_results,
        ).execute()
        files = results.get("files", [])
        if not files:
            return f"No files found in '{folder}' on Google Drive."

        lines = [f"Files in '{folder}' ({len(files)} total):"]
        for f in files:
            created = f.get("createdTime", "")[:10]
            lines.append(f"  • {f['name']} ({created}) — {f.get('webViewLink','')}")
        return "\n".join(lines)

    except Exception as exc:
        logger.warning("Drive list failed: %s", exc)
        return f"Could not list Drive files: {exc}"
