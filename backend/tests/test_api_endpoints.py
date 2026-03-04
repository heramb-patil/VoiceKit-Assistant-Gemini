"""
API endpoint tests (MCP disabled — tests baseline/static behaviour).

Uses httpx.AsyncClient with ASGITransport so no real server needed.
Auth is bypassed via dependency_overrides in conftest.py (no Bearer token required).
"""
import pytest
import pytest_asyncio


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_200(self, async_client):
        r = await async_client.get("/gemini-live/health")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_health_has_status_healthy(self, async_client):
        data = (await async_client.get("/gemini-live/health")).json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_reports_tool_count(self, async_client):
        data = (await async_client.get("/gemini-live/health")).json()
        assert data["tool_count"] > 0

    @pytest.mark.asyncio
    async def test_health_has_websocket_connections(self, async_client):
        data = (await async_client.get("/gemini-live/health")).json()
        assert "websocket_connections" in data


# ── GET /tools (static, MCP disabled) ────────────────────────────────────────

class TestGetToolsStatic:
    @pytest.mark.asyncio
    async def test_returns_200(self, async_client):
        r = await async_client.get("/gemini-live/tools")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_response_has_tools_key(self, async_client):
        data = (await async_client.get("/gemini-live/tools")).json()
        assert "tools" in data

    @pytest.mark.asyncio
    async def test_response_has_count(self, async_client):
        data = (await async_client.get("/gemini-live/tools")).json()
        assert data["count"] == len(data["tools"])

    @pytest.mark.asyncio
    async def test_response_has_tool_metadata(self, async_client):
        data = (await async_client.get("/gemini-live/tools")).json()
        assert "tool_metadata" in data
        assert isinstance(data["tool_metadata"], dict)

    @pytest.mark.asyncio
    async def test_basic_tools_present(self, async_client):
        data = (await async_client.get("/gemini-live/tools")).json()
        names = {t["name"] for t in data["tools"]}
        for expected in ["calculate", "get_current_time", "web_search", "deep_research"]:
            assert expected in names, f"Missing tool: {expected}"

    @pytest.mark.asyncio
    async def test_file_tools_present(self, async_client):
        data = (await async_client.get("/gemini-live/tools")).json()
        names = {t["name"] for t in data["tools"]}
        for expected in ["create_file", "read_file", "list_files", "append_to_file"]:
            assert expected in names

    @pytest.mark.asyncio
    async def test_all_tools_have_name_and_description(self, async_client):
        data = (await async_client.get("/gemini-live/tools")).json()
        for tool in data["tools"]:
            assert tool.get("name"), f"Tool missing name: {tool}"
            assert tool.get("description"), f"Tool {tool['name']} missing description"

    @pytest.mark.asyncio
    async def test_all_tools_have_parameters_object(self, async_client):
        data = (await async_client.get("/gemini-live/tools")).json()
        for tool in data["tools"]:
            params = tool.get("parameters", {})
            assert params.get("type") == "object", \
                f"Tool {tool['name']} parameters.type != 'object'"
            assert "properties" in params, \
                f"Tool {tool['name']} missing parameters.properties"

    @pytest.mark.asyncio
    async def test_tool_metadata_has_estimated_seconds(self, async_client):
        data = (await async_client.get("/gemini-live/tools")).json()
        meta = data["tool_metadata"]
        assert "web_search" in meta
        assert meta["web_search"]["estimated_seconds"] > 0

    @pytest.mark.asyncio
    async def test_tool_metadata_has_is_background_flag(self, async_client):
        data = (await async_client.get("/gemini-live/tools")).json()
        meta = data["tool_metadata"]
        # deep_research is a background tool
        assert meta.get("deep_research", {}).get("is_background") is True

    @pytest.mark.asyncio
    async def test_calculate_has_correct_schema(self, async_client):
        data = (await async_client.get("/gemini-live/tools")).json()
        calc = next(t for t in data["tools"] if t["name"] == "calculate")
        props = calc["parameters"]["properties"]
        assert "expression" in props
        assert props["expression"]["type"] == "string"
        assert "expression" in calc["parameters"]["required"]


# ── POST /tool-execute ────────────────────────────────────────────────────────

class TestToolExecute:
    @pytest.mark.asyncio
    async def test_calculate_returns_result(self, async_client):
        r = await async_client.post("/gemini-live/tool-execute", json={
            "tool_name": "calculate",
            "tool_args": {"expression": "6 * 7"},
        })
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert "42" in data["result"]
        assert data["error"] is None

    @pytest.mark.asyncio
    async def test_calculate_complex_expression(self, async_client):
        r = await async_client.post("/gemini-live/tool-execute", json={
            "tool_name": "calculate",
            "tool_args": {"expression": "2 ** 10"},
        })
        data = r.json()
        assert data["success"] is True
        assert "1024" in data["result"]

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, async_client):
        r = await async_client.post("/gemini-live/tool-execute", json={
            "tool_name": "no_such_tool",
            "tool_args": {},
        })
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is False
        assert data["error"] is not None

    @pytest.mark.asyncio
    async def test_response_has_required_fields(self, async_client):
        r = await async_client.post("/gemini-live/tool-execute", json={
            "tool_name": "calculate",
            "tool_args": {"expression": "1+1"},
        })
        data = r.json()
        assert "success" in data
        assert "result" in data
        assert "error" in data

    @pytest.mark.asyncio
    async def test_missing_tool_name_returns_422(self, async_client):
        # tool_name is required — omitting it must return 422
        r = await async_client.post("/gemini-live/tool-execute", json={
            "tool_args": {},
        })
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_user_identity_not_required_in_body(self, async_client):
        # SaaS: user identity comes from JWT, NOT request body
        r = await async_client.post("/gemini-live/tool-execute", json={
            "tool_name": "calculate",
            "tool_args": {"expression": "1"},
        })
        assert r.status_code == 200  # succeeds without user_identity in body


# ── POST /tool-submit (SJF background queue) ──────────────────────────────────

class TestToolSubmit:
    @pytest.mark.asyncio
    async def test_returns_task_id(self, async_client):
        r = await async_client.post("/gemini-live/tool-submit", json={
            "tool_name": "calculate",
            "tool_args": {"expression": "1+1"},
            "session_id": "sess-test",
        })
        assert r.status_code == 200
        data = r.json()
        assert "task_id" in data
        assert len(data["task_id"]) > 0

    @pytest.mark.asyncio
    async def test_returns_estimated_seconds(self, async_client):
        r = await async_client.post("/gemini-live/tool-submit", json={
            "tool_name": "web_search",
            "tool_args": {"query": "test"},
            "session_id": "sess-test",
        })
        data = r.json()
        assert data["estimated_seconds"] == 5  # from TOOL_METADATA

    @pytest.mark.asyncio
    async def test_unknown_tool_uses_default_estimate(self, async_client):
        r = await async_client.post("/gemini-live/tool-submit", json={
            "tool_name": "not_in_metadata",
            "tool_args": {},
            "session_id": "sess-test",
        })
        data = r.json()
        assert data["estimated_seconds"] == 10  # default fallback

    @pytest.mark.asyncio
    async def test_status_is_queued(self, async_client):
        r = await async_client.post("/gemini-live/tool-submit", json={
            "tool_name": "calculate",
            "tool_args": {"expression": "2+2"},
            "session_id": "sess-abc",
        })
        data = r.json()
        assert data["status"] == "queued"


# ── GET /tasks/{task_id} ──────────────────────────────────────────────────────

class TestGetTask:
    @pytest.mark.asyncio
    async def test_submitted_task_is_retrievable(self, async_client):
        # Submit
        submit_r = await async_client.post("/gemini-live/tool-submit", json={
            "tool_name": "calculate",
            "tool_args": {"expression": "3+3"},
            "session_id": "sess-task",
        })
        task_id = submit_r.json()["task_id"]

        # Poll until done (max 5s)
        import asyncio
        for _ in range(10):
            r = await async_client.get(f"/gemini-live/tasks/{task_id}")
            data = r.json()
            if data["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.5)

        assert r.status_code == 200
        assert data["task_id"] == task_id
        assert data["tool_name"] == "calculate"

    @pytest.mark.asyncio
    async def test_unknown_task_returns_404(self, async_client):
        r = await async_client.get("/gemini-live/tasks/does-not-exist")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_completed_task_has_result(self, async_client):
        import asyncio
        submit_r = await async_client.post("/gemini-live/tool-submit", json={
            "tool_name": "calculate",
            "tool_args": {"expression": "10*10"},
            "session_id": "sess-result",
        })
        task_id = submit_r.json()["task_id"]

        for _ in range(20):
            r = await async_client.get(f"/gemini-live/tasks/{task_id}")
            if r.json()["status"] == "done":
                break
            await asyncio.sleep(0.3)

        data = r.json()
        assert data["status"] == "done"
        assert "100" in (data["result"] or "")


# ── GET /tasks (database poll) ────────────────────────────────────────────────

class TestGetTasks:
    @pytest.mark.asyncio
    async def test_returns_pending_results_key(self, async_client):
        r = await async_client.get("/gemini-live/tasks")
        assert r.status_code == 200
        data = r.json()
        assert "pending_results" in data
        assert isinstance(data["pending_results"], list)


# ── POST /followup-response ───────────────────────────────────────────────────

class TestFollowupResponse:
    @pytest.mark.asyncio
    async def test_no_pending_returns_success_false(self, async_client):
        r = await async_client.post("/gemini-live/followup-response", json={
            "response_text": "yes",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is False
        assert "pending" in data.get("message", "").lower() or data["success"] is False
