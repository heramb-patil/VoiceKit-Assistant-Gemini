"""Gmail integration tools (standalone version)."""

import asyncio
import base64
import logging
from typing import Any

logger = logging.getLogger(__name__)

_auth: Any = None


def _get_service():
    if _auth is None:
        raise RuntimeError("Google is not connected. The user must connect Google in the Integrations panel first.")
    return _auth.build_service("gmail", "v1")


async def search_emails(query: str, max_results: int = 10) -> str:
    """Search Gmail inbox with a query string."""
    # Convert to int if passed as string
    if isinstance(max_results, str):
        try:
            max_results = int(max_results)
        except (ValueError, TypeError):
            max_results = 10
    return await asyncio.to_thread(_search_emails_sync, query, max_results)


def _search_emails_sync(query: str, max_results: int) -> str:
    # Ensure max_results is an integer
    max_results = int(max_results) if not isinstance(max_results, int) else max_results
    try:
        service = _get_service()
        result = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        messages = result.get("messages", [])
        if not messages:
            return f"No emails found matching: {query}"

        summaries: list[str] = []
        for msg in messages[:max_results]:
            detail = service.users().messages().get(userId="me", id=msg["id"], format="metadata",
                                                    metadataHeaders=["Subject", "From", "Date"]).execute()
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            snippet = detail.get("snippet", "")[:150]
            summaries.append(
                f"From: {headers.get('From', 'Unknown')}\n"
                f"Subject: {headers.get('Subject', '(no subject)')}\n"
                f"Date: {headers.get('Date', '')}\n"
                f"Preview: {snippet}"
            )
        return f"Found {len(messages)} email(s):\n\n" + "\n\n---\n\n".join(summaries)
    except Exception as exc:
        logger.exception("search_emails failed")
        return f"Failed to search emails: {exc}"


async def get_recent_emails(count: int = 5) -> str:
    """Get the most recent emails from your Gmail inbox."""
    # Convert to int if passed as string
    if isinstance(count, str):
        try:
            count = int(count)
        except (ValueError, TypeError):
            count = 5
    return await asyncio.to_thread(_get_recent_emails_sync, count)


def _get_recent_emails_sync(count: int) -> str:
    # Ensure count is an integer
    count = int(count) if not isinstance(count, int) else count
    try:
        service = _get_service()
        result = service.users().messages().list(userId="me", maxResults=count, labelIds=["INBOX"]).execute()
        messages = result.get("messages", [])
        if not messages:
            return "No recent emails found."

        summaries: list[str] = []
        for msg in messages:
            detail = service.users().messages().get(userId="me", id=msg["id"], format="metadata",
                                                    metadataHeaders=["Subject", "From", "Date"]).execute()
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            snippet = detail.get("snippet", "")[:150]
            summaries.append(
                f"From: {headers.get('From', 'Unknown')}\n"
                f"Subject: {headers.get('Subject', '(no subject)')}\n"
                f"Date: {headers.get('Date', '')}\n"
                f"Preview: {snippet}"
            )
        return f"Your {len(summaries)} most recent email(s):\n\n" + "\n\n---\n\n".join(summaries)
    except Exception as exc:
        logger.exception("get_recent_emails failed")
        return f"Failed to get recent emails: {exc}"


async def send_email(to: str, subject: str, body: str, attach_drive_file: str = "") -> str:
    """Send an email via Gmail, optionally attaching a file from Google Drive.

    Args:
        to:                Recipient email address
        subject:           Email subject
        body:              Email body text
        attach_drive_file: Optional Drive file name to attach (e.g. 'Research: AI trends.md').
                           Searches the VoiceKit Research folder first.
    """
    attachment: tuple | None = None
    if attach_drive_file:
        try:
            from integrations.google import drive as _drive
            attachment = await _drive.get_file_content(attach_drive_file)
            if attachment is None:
                return (
                    f"Could not find '{attach_drive_file}' in Google Drive. "
                    "Check the file name and try again."
                )
        except Exception as exc:
            return f"Failed to fetch Drive file '{attach_drive_file}': {exc}"

    return await asyncio.to_thread(_send_email_sync, to, subject, body, attachment)


def _send_email_sync(
    to: str,
    subject: str,
    body: str,
    attachment: tuple | None = None,  # (filename, bytes, mimetype)
) -> str:
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    try:
        service = _get_service()

        if attachment:
            filename, content_bytes, mimetype = attachment
            msg = MIMEMultipart()
            msg["to"] = to
            msg["subject"] = subject
            msg.attach(MIMEText(body))

            part = MIMEBase(*mimetype.split("/", 1) if "/" in mimetype else ("application", "octet-stream"))
            part.set_payload(content_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            msg.attach(part)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return f"Email sent to {to} with subject '{subject}' and attachment '{filename}'."
        else:
            message = MIMEText(body)
            message["to"] = to
            message["subject"] = subject
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return f"Email sent to {to} with subject '{subject}'."

    except Exception as exc:
        logger.exception("send_email failed")
        return f"Failed to send email: {exc}"


async def get_email_details(message_id: str) -> str:
    """Get the full body of a specific email by its message ID."""
    return await asyncio.to_thread(_get_email_details_sync, message_id)


def _get_email_details_sync(message_id: str) -> str:
    try:
        service = _get_service()
        detail = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        body = ""
        parts = detail.get("payload", {}).get("parts", [])
        if parts:
            for part in parts:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                    break
        else:
            data = detail.get("payload", {}).get("body", {}).get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")

        return (
            f"From: {headers.get('From', 'Unknown')}\n"
            f"Subject: {headers.get('Subject', '(no subject)')}\n"
            f"Date: {headers.get('Date', '')}\n\n"
            f"{body[:2000]}"
        )
    except Exception as exc:
        logger.exception("get_email_details failed")
        return f"Failed to get email details: {exc}"
