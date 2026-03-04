"""Basecamp integration tools for Gemini Live backend.

Adapted from VoiceKit/src/integrations/basecamp/tools.py:
- Removed LiveKit @function_tool() decorators
- Removed context: RunContext parameters
- Plain async functions compatible with StandaloneOrchestration
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("gemini_live.integrations.basecamp")

_auth: Any = None  # set by _init_basecamp_tools()


def _api(path: str, method: str = "GET", body: Any = None) -> Any:
    if _auth is None:
        raise RuntimeError("Basecamp auth not initialised")
    return _auth.api_request(path, method=method, body=body)


async def list_basecamp_projects() -> str:
    """List all Basecamp projects you have access to."""
    return await asyncio.to_thread(_list_projects_sync)


def _list_projects_sync() -> str:
    try:
        data = _api("projects.json")
        if not data:
            return "No Basecamp projects found."
        lines = []
        for p in data:
            pid = p.get("id", "")
            name = p.get("name", "(unnamed)")
            # Keep name to 60 chars — no description, so all projects fit in one injection
            lines.append(f"• {name[:60]} (ID: {pid})")
        return f"Basecamp projects ({len(lines)} total):\n" + "\n".join(lines)
    except Exception as exc:
        logger.exception("list_basecamp_projects failed")
        return f"Failed to get projects: {exc}"


async def create_basecamp_todo(
    project_id: str,
    todolist_id: str,
    title: str,
    description: str = "",
) -> str:
    """Create a new todo item in a Basecamp project."""
    return await asyncio.to_thread(
        _create_todo_sync, project_id, todolist_id, title, description
    )


def _create_todo_sync(
    project_id: str, todolist_id: str, title: str, description: str
) -> str:
    try:
        body: dict = {"content": title}
        if description:
            body["description"] = f"<div>{description}</div>"
        result = _api(
            f"buckets/{project_id}/todolists/{todolist_id}/todos.json",
            method="POST",
            body=body,
        )
        return f"Todo created: '{title}' (ID: {result.get('id', '')})"
    except Exception as exc:
        logger.exception("create_basecamp_todo failed")
        return f"Failed to create todo: {exc}"


async def get_basecamp_messages(project_id: str) -> str:
    """Get recent messages from a Basecamp project's message board."""
    return await asyncio.to_thread(_get_messages_sync, project_id)


def _get_messages_sync(project_id: str) -> str:
    try:
        # Resolve message board ID from project dock
        project = _api(f"projects/{project_id}.json")
        board_id = None
        for item in project.get("dock", []):
            if item.get("name") == "message_board":
                url = item.get("url", "")
                parts = url.rstrip(".json").rsplit("/", 1)
                if len(parts) == 2:
                    board_id = parts[1]
                break
        if not board_id:
            return f"No message board found in project {project_id}."
        messages = _api(f"buckets/{project_id}/message_boards/{board_id}/messages.json")
        if not messages:
            return f"No messages found in project {project_id}."
        summaries: list[str] = []
        for m in messages[:10]:
            subject = m.get("subject", "(no subject)")
            author = m.get("creator", {}).get("name", "Unknown")
            created = m.get("created_at", "")[:10]
            summaries.append(f"• {subject} — by {author} on {created}")
        return "Recent messages:\n" + "\n".join(summaries)
    except Exception as exc:
        logger.exception("get_basecamp_messages failed for project_id=%s", project_id)
        return f"Failed to get messages: {exc}"


async def post_basecamp_message(
    project_id: str,
    subject: str,
    body: str,
) -> str:
    """Post a new message to a Basecamp project's message board."""
    return await asyncio.to_thread(_post_message_sync, project_id, subject, body)


def _post_message_sync(project_id: str, subject: str, body: str) -> str:
    try:
        # Resolve message board ID from project dock
        project = _api(f"projects/{project_id}.json")
        board_id = None
        for item in project.get("dock", []):
            if item.get("name") == "message_board":
                url = item.get("url", "")
                parts = url.rstrip(".json").rsplit("/", 1)
                if len(parts) == 2:
                    board_id = parts[1]
                break
        if not board_id:
            return f"No message board found in project {project_id}."
        result = _api(
            f"buckets/{project_id}/message_boards/{board_id}/messages.json",
            method="POST",
            body={"subject": subject, "content": f"<div>{body}</div>", "status": "active"},
        )
        msg_id = result.get("id", "")
        return f"Message posted: '{subject}' (ID: {msg_id})"
    except Exception as exc:
        logger.exception("post_basecamp_message failed for project_id=%s", project_id)
        return f"Failed to post message: {exc}"


# ── Check-ins (Automatic Check-ins / Questionnaire) ──────────────────────────


async def get_basecamp_checkins(project_id: str = "") -> str:
    """Get Automatic Check-in questions for a Basecamp project (or all projects if project_id is empty).
    Shows which questions are pending your answer today."""
    return await asyncio.to_thread(_get_checkins_sync, project_id)


def _get_questionnaire_id(project_id: str) -> str | None:
    """Return the questionnaire ID from a project's dock, or None if not found."""
    try:
        project = _api(f"projects/{project_id}.json")
        for item in project.get("dock", []):
            if item.get("name") == "questionnaire":
                url = item.get("url", "")
                # URL format: .../questionnaires/{id}.json
                parts = url.rstrip(".json").rsplit("/", 1)
                if len(parts) == 2:
                    return parts[1]
    except Exception:
        pass
    return None


def _get_checkins_sync(project_id: str) -> str:
    import datetime

    today = datetime.date.today().isoformat()

    try:
        if project_id and project_id.strip().lstrip("-").isdigit():
            # Numeric ID — fetch directly
            projects = [_api(f"projects/{project_id.strip()}.json")]
        elif project_id and project_id.strip():
            # Project name — search by name (case-insensitive partial match)
            name_query = project_id.strip().lower()
            all_projects = _api("projects.json") or []
            matched = [p for p in all_projects if name_query in p.get("name", "").lower()]
            if not matched:
                return (
                    f"No Basecamp project found matching '{project_id}'. "
                    f"Use list_basecamp_projects to see available projects with their IDs."
                )
            projects = matched
        else:
            projects = _api("projects.json") or []
    except Exception as exc:
        return f"Failed to fetch projects: {exc}"

    lines: list[str] = []

    for project in projects:
        pid = str(project.get("id", ""))
        pname = project.get("name", "(unnamed)")

        # Find questionnaire dock item
        questionnaire_id = None
        for item in project.get("dock", []):
            if item.get("name") == "questionnaire":
                url = item.get("url", "")
                parts = url.rstrip(".json").rsplit("/", 1)
                if len(parts) == 2:
                    questionnaire_id = parts[1]
                break

        if not questionnaire_id:
            continue

        try:
            questions = _api(f"questionnaires/{questionnaire_id}/questions.json") or []
        except Exception:
            continue

        for q in questions:
            if q.get("paused"):
                continue
            qid = str(q.get("id", ""))
            title = q.get("title", "(no title)")

            # Check if already answered today
            try:
                answers = _api(f"questions/{qid}/answers.json") or []
                answered_today = any(a.get("group_on", "") == today for a in answers)
            except Exception:
                answered_today = False

            status = "✓ answered" if answered_today else "⏳ pending"
            lines.append(
                f"• [{pname}] {title}\n"
                f"  question_id: {qid} | project_id: {pid} | {status}"
            )

    if not lines:
        return "No check-in questions found."
    return "Basecamp Check-ins:\n" + "\n".join(lines)


async def answer_basecamp_checkin(
    project_id: str,
    question_id: str,
    content: str,
) -> str:
    """Post your answer to a Basecamp Automatic Check-in question.
    Use get_basecamp_checkins first to find the question_id and project_id."""
    return await asyncio.to_thread(_answer_checkin_sync, project_id, question_id, content)


def _answer_checkin_sync(project_id: str, question_id: str, content: str) -> str:
    import urllib.error

    try:
        result = _api(
            f"buckets/{project_id}/questions/{question_id}/answers.json",
            method="POST",
            body={"content": f"<div>{content}</div>"},
        )
        answer_id = result.get("id", "")
        return f"Check-in answer posted (ID: {answer_id}). Your response has been submitted."
    except urllib.error.HTTPError as exc:
        if exc.code == 422:
            return (
                "Could not submit answer — the check-in question is not currently open. "
                "Basecamp check-ins can only be answered after they are issued (usually at 9 AM). "
                "Make sure you are subscribed to the check-in and the question period is active."
            )
        logger.exception(
            "answer_basecamp_checkin failed project_id=%s question_id=%s",
            project_id, question_id,
        )
        return f"Failed to post check-in answer: {exc}"
    except Exception as exc:
        logger.exception(
            "answer_basecamp_checkin failed project_id=%s question_id=%s",
            project_id, question_id,
        )
        return f"Failed to post check-in answer: {exc}"


def _init_basecamp_tools(auth: Any) -> dict:
    """Inject auth and return tool registry entries."""
    global _auth
    _auth = auth
    return {
        "list_basecamp_projects": list_basecamp_projects,
        "create_basecamp_todo": create_basecamp_todo,
        "get_basecamp_messages": get_basecamp_messages,
        "post_basecamp_message": post_basecamp_message,
        "get_basecamp_checkins": get_basecamp_checkins,
        "answer_basecamp_checkin": answer_basecamp_checkin,
    }
