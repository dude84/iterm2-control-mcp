from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from iterm2_control_mcp.mcp.tools import (
    ToolHandler,
    clean_sessions,
    get_sessions,
    stop_session,
)

# --- get_sessions / clean_sessions / stop_session ---


def _create_session_dir(
    base: Path,
    sid: str,
    pid: int | None = None,
    name: str = "",
) -> Path:
    d = base / sid
    d.mkdir(parents=True)
    meta: dict[str, object] = {
        "session_id": sid,
        "name": name,
        "started_at": "2026-01-01T00:00:00",
    }
    if pid is not None:
        meta["pid"] = pid
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def test_get_sessions_empty(tmp_path: Path) -> None:
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path):
        assert get_sessions() == []


def test_get_sessions_nonexistent() -> None:
    with patch(
        "iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR",
        Path("/tmp/nonexistent-iterm-test-dir"),
    ):
        assert get_sessions() == []


def test_get_sessions_stale(tmp_path: Path) -> None:
    _create_session_dir(tmp_path, "aaa", pid=999999999)
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path):
        sessions = get_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "aaa"
    assert sessions[0]["status"] == "stale"


def test_get_sessions_active(tmp_path: Path) -> None:
    _create_session_dir(tmp_path, "bbb", pid=os.getpid())
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path):
        sessions = get_sessions()
    assert len(sessions) == 1
    assert sessions[0]["status"] == "active"


def test_get_sessions_no_meta(tmp_path: Path) -> None:
    (tmp_path / "orphan").mkdir()
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path):
        assert get_sessions() == []


def test_clean_sessions_removes_stale(tmp_path: Path) -> None:
    d = _create_session_dir(tmp_path, "dead", pid=999999999)
    (d / "session.sock").touch()
    (d / "session.log").touch()
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path):
        count = clean_sessions()
    assert count == 1
    assert not d.exists()


def test_clean_sessions_keeps_active(tmp_path: Path) -> None:
    _create_session_dir(tmp_path, "alive", pid=os.getpid())
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path):
        count = clean_sessions()
    assert count == 0


def test_clean_sessions_empty(tmp_path: Path) -> None:
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path):
        assert clean_sessions() == 0


@pytest.mark.asyncio
async def test_stop_session_not_found(tmp_path: Path) -> None:
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path):
        result = await stop_session("nonexistent")
    assert "not found" in result


@pytest.mark.asyncio
async def test_stop_session_no_pid(tmp_path: Path) -> None:
    _create_session_dir(tmp_path, "nopid", pid=None)
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path):
        result = await stop_session("nopid")
    assert "No PID" in result


@pytest.mark.asyncio
async def test_stop_session_dead_process(tmp_path: Path) -> None:
    _create_session_dir(tmp_path, "dead", pid=999999999)
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path):
        result = await stop_session("dead")
    # PID 999999999 doesn't exist → pid_looks_like_session_daemon returns
    # False, so we short-circuit with a "stale meta.json" message rather
    # than sending a signal to a non-existent process.
    assert "stale" in result or "not found" in result


@pytest.mark.asyncio
async def test_stop_session_rpc_success_closes_pane(tmp_path: Path) -> None:
    _create_session_dir(tmp_path, "live", pid=1234)
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path), \
         patch(
             "iterm2_control_mcp.mcp.session_pool.call_session",
             new_callable=AsyncMock,
             return_value="stopping",
         ) as mock_call:
        result = await stop_session("live", close_window=True)
    mock_call.assert_awaited_once_with(
        "live", "daemon_stop", {"close_window": True},
    )
    assert "closing pane" in result


@pytest.mark.asyncio
async def test_stop_session_rpc_success_keeps_pane(tmp_path: Path) -> None:
    _create_session_dir(tmp_path, "live", pid=1234)
    with patch("iterm2_control_mcp.mcp.session_pool.SESSIONS_DIR", tmp_path), \
         patch(
             "iterm2_control_mcp.mcp.session_pool.call_session",
             new_callable=AsyncMock,
             return_value="stopping",
         ) as mock_call:
        result = await stop_session("live", close_window=False)
    mock_call.assert_awaited_once_with(
        "live", "daemon_stop", {"close_window": False},
    )
    assert "closing pane" not in result
    assert "Stopped" in result


# --- ToolHandler ---


@pytest.mark.asyncio
async def test_tool_handler_list_empty() -> None:
    handler = ToolHandler()
    with patch("iterm2_control_mcp.mcp.handler.get_sessions", return_value=[]):
        result = await handler.handle("iterm_list_sessions", {})
    assert "No active sessions" in str(result)


@pytest.mark.asyncio
async def test_tool_handler_list_sessions() -> None:
    sessions = [{"id": "aaa", "name": "dev", "status": "active", "pid": 123}]
    handler = ToolHandler()
    with patch("iterm2_control_mcp.mcp.handler.get_sessions", return_value=sessions):
        result = await handler.handle("iterm_list_sessions", {})
    assert result == sessions


@pytest.mark.asyncio
async def test_tool_handler_connect() -> None:
    sessions = [{"id": "aaa", "name": "dev", "status": "active"}]
    handler = ToolHandler()
    with patch("iterm2_control_mcp.mcp.handler.get_sessions", return_value=sessions):
        result = await handler.handle(
            "iterm_connect_session", {"session": "aaa"},
        )
    assert result == {"connected": "aaa"}
    assert "aaa" in handler._connected_sessions


@pytest.mark.asyncio
async def test_tool_handler_connect_not_found() -> None:
    sessions = [{"id": "aaa", "name": "dev", "status": "active"}]
    handler = ToolHandler()
    with patch("iterm2_control_mcp.mcp.handler.get_sessions", return_value=sessions):
        result = await handler.handle(
            "iterm_connect_session", {"session": "zzz"},
        )
    assert "error" in result


@pytest.mark.asyncio
async def test_tool_handler_connect_no_active() -> None:
    handler = ToolHandler()
    with patch("iterm2_control_mcp.mcp.handler.get_sessions", return_value=[]):
        result = await handler.handle(
            "iterm_connect_session", {"session": "aaa"},
        )
    assert "No active sessions" in str(result)


@pytest.mark.asyncio
async def test_tool_handler_detach() -> None:
    handler = ToolHandler()
    handler._add_session("aaa")
    with patch("iterm2_control_mcp.mcp.handler.call_session", new_callable=AsyncMock) as mock:
        mock.return_value = "detached"
        result = await handler.handle("iterm_detach_session", {})
    assert result == {"detached": "aaa"}
    assert "aaa" not in handler._connected_sessions
    mock.assert_called_once_with("aaa", "daemon_detach", {})


@pytest.mark.asyncio
async def test_tool_handler_detach_not_connected() -> None:
    handler = ToolHandler()
    result = await handler.handle("iterm_detach_session", {})
    assert "Not connected" in str(result)


@pytest.mark.asyncio
async def test_tool_handler_probe() -> None:
    handler = ToolHandler()
    result = await handler.handle("iterm_probe_environment", {})
    assert "instructions" in result
    assert "commands" in result


@pytest.mark.asyncio
async def test_tool_handler_not_connected_hint() -> None:
    sessions = [{"id": "aaa", "name": "dev", "status": "active"}]
    handler = ToolHandler()
    with patch("iterm2_control_mcp.mcp.handler.get_sessions", return_value=sessions):
        result = await handler.handle(
            "iterm_send_command", {"command": "ls"},
        )
    assert "Not connected" in str(result)
    assert "aaa" in str(result)


@pytest.mark.asyncio
async def test_tool_handler_forwards_to_session() -> None:
    handler = ToolHandler()
    handler._add_session("aaa")
    with patch("iterm2_control_mcp.mcp.handler.call_session", new_callable=AsyncMock) as mock:
        mock.return_value = "executed"
        result = await handler.handle(
            "iterm_send_command", {"command": "ls"},
        )
    assert result == "executed"
    mock.assert_called_once_with("aaa", "iterm_send_command", {"command": "ls"})


@pytest.mark.asyncio
async def test_tool_handler_clean_sessions() -> None:
    handler = ToolHandler()
    with patch("iterm2_control_mcp.mcp.handler.clean_sessions", return_value=3):
        result = await handler.handle("iterm_clean_sessions", {})
    assert "3" in str(result)


@pytest.mark.asyncio
async def test_tool_handler_clean_sessions_none() -> None:
    handler = ToolHandler()
    with patch("iterm2_control_mcp.mcp.handler.clean_sessions", return_value=0):
        result = await handler.handle("iterm_clean_sessions", {})
    assert "No stale" in str(result)


@pytest.mark.asyncio
async def test_tool_handler_stop_session() -> None:
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.stop_session",
        new_callable=AsyncMock,
        return_value="Stopped session aaa",
    ) as mock:
        result = await handler.handle(
            "iterm_stop_session", {"session": "aaa"},
        )
    assert "Stopped" in str(result)
    mock.assert_awaited_once_with("aaa", close_window=True)


@pytest.mark.asyncio
async def test_tool_handler_stop_session_no_close() -> None:
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.stop_session",
        new_callable=AsyncMock,
        return_value="Stopped session aaa",
    ) as mock:
        await handler.handle(
            "iterm_stop_session",
            {"session": "aaa", "close_window": False},
        )
    mock.assert_awaited_once_with("aaa", close_window=False)


@pytest.mark.asyncio
async def test_tool_handler_rename_session() -> None:
    handler = ToolHandler()
    with patch("iterm2_control_mcp.mcp.handler.call_session", new_callable=AsyncMock) as mock:
        mock.return_value = "renamed"
        result = await handler.handle(
            "iterm_rename_session", {"session": "aaa", "name": "new-name"},
        )
    assert result == "renamed"
    mock.assert_called_once_with("aaa", "iterm_rename_session", {"name": "new-name"})


@pytest.mark.asyncio
async def test_tool_handler_rename_missing_args() -> None:
    handler = ToolHandler()
    result = await handler.handle("iterm_rename_session", {"session": "aaa"})
    assert "error" in result


@pytest.mark.asyncio
async def test_tool_handler_focus_window_connected() -> None:
    handler = ToolHandler()
    handler._add_session("aaa")
    with patch("iterm2_control_mcp.mcp.handler.call_session", new_callable=AsyncMock) as mock:
        mock.return_value = "focused"
        result = await handler.handle("iterm_focus_window", {})
    assert result == "focused"
    mock.assert_called_once_with("aaa", "iterm_focus_window", {})


@pytest.mark.asyncio
async def test_tool_handler_focus_window_explicit() -> None:
    handler = ToolHandler()
    with patch("iterm2_control_mcp.mcp.handler.call_session", new_callable=AsyncMock) as mock:
        mock.return_value = "focused"
        result = await handler.handle("iterm_focus_window", {"session": "bbb"})
    mock.assert_called_once_with("bbb", "iterm_focus_window", {})


@pytest.mark.asyncio
async def test_tool_handler_focus_window_not_connected() -> None:
    handler = ToolHandler()
    result = await handler.handle("iterm_focus_window", {})
    assert "Not connected" in str(result)


@pytest.mark.asyncio
async def test_tool_handler_split_pane() -> None:
    handler = ToolHandler()
    handler._add_session("aaa")
    with (
        patch("iterm2_control_mcp.mcp.handler.call_session", new_callable=AsyncMock) as mock_call,
        patch("iterm2_control_mcp.mcp.handler._create_split_session", return_value="bbb") as mock_create,
    ):
        mock_call.return_value = "w0t0p1:new-sess"
        result = await handler.handle(
            "iterm_split_pane", {"name": "right-pane", "vertical": True},
        )
    assert result["created"] == "bbb"
    assert result["parent_session"] == "aaa"
    mock_call.assert_called_once_with("aaa", "iterm_split_pane", {"vertical": True})
    mock_create.assert_called_once_with("w0t0p1:new-sess", "right-pane")


@pytest.mark.asyncio
async def test_tool_handler_split_pane_not_connected() -> None:
    handler = ToolHandler()
    result = await handler.handle("iterm_split_pane", {})
    assert "Not connected" in str(result)


@pytest.mark.asyncio
async def test_tool_handler_multi_session_pool() -> None:
    handler = ToolHandler()
    handler._add_session("aaa")
    handler._add_session("bbb")
    assert handler._active_session == "bbb"
    assert "aaa" in handler._connected_sessions
    assert "bbb" in handler._connected_sessions
    # Can target non-active session directly
    with patch("iterm2_control_mcp.mcp.handler.call_session", new_callable=AsyncMock) as mock:
        mock.return_value = "executed"
        await handler.handle(
            "iterm_send_command", {"command": "ls", "session": "aaa"},
        )
    mock.assert_called_once_with("aaa", "iterm_send_command", {"command": "ls", "session": "aaa"})


@pytest.mark.asyncio
async def test_tool_handler_pool_eviction() -> None:
    handler = ToolHandler()
    handler._config = handler._config.__class__(max_sessions=2)
    handler._add_session("aaa")
    handler._add_session("bbb")
    handler._add_session("ccc")  # should evict aaa (oldest non-active)
    assert "aaa" not in handler._connected_sessions
    assert "bbb" in handler._connected_sessions
    assert "ccc" in handler._connected_sessions
    assert handler._active_session == "ccc"


# --- iterm_pipe ---


@pytest.mark.asyncio
async def test_tool_handler_pipe_happy_path() -> None:
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.call_session",
        new_callable=AsyncMock,
    ) as mock:
        mock.side_effect = [
            {"stdout": "line1\nline2", "cursor": 42, "session": "aaa"},
            {"typed": "line1\nline2\n", "session": "bbb"},
        ]
        result = await handler.handle(
            "iterm_pipe",
            {"from_session": "aaa", "to_session": "bbb", "since": 0},
        )
    assert result["piped"] is True
    assert result["from_session"] == "aaa"
    assert result["to_session"] == "bbb"
    assert result["cursor"] == 42
    assert result["bytes"] == len("line1\nline2".encode())
    assert "warning" not in result
    assert mock.await_count == 2
    mock.assert_any_await("aaa", "iterm_read_output", {"since": 0})
    mock.assert_any_await("bbb", "iterm_type", {"text": "line1\nline2\n"})


@pytest.mark.asyncio
async def test_tool_handler_pipe_first_call_from_now() -> None:
    # No `since`, no `lines` → establish starting cursor, type nothing.
    # The handler passes `lines=0` under the hood, which read_since
    # interprets as "give me the cursor, skip the backfill."
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.call_session",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = {"stdout": "", "cursor": 58, "session": "aaa"}
        result = await handler.handle(
            "iterm_pipe",
            {"from_session": "aaa", "to_session": "bbb"},
        )
    assert result["piped"] is True
    assert result["cursor"] == 58
    assert result["bytes"] == 0
    mock.assert_awaited_once_with("aaa", "iterm_read_output", {"lines": 0})


@pytest.mark.asyncio
async def test_tool_handler_pipe_explicit_backfill() -> None:
    # Caller opts into backfill with explicit `lines` — handler forwards
    # it verbatim and types whatever the source returns.
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.call_session",
        new_callable=AsyncMock,
    ) as mock:
        mock.side_effect = [
            {"stdout": "old", "cursor": 12, "session": "aaa"},
            {"typed": "old\n", "session": "bbb"},
        ]
        result = await handler.handle(
            "iterm_pipe",
            {"from_session": "aaa", "to_session": "bbb", "lines": 5},
        )
    assert result["bytes"] == len("old".encode())
    mock.assert_any_await("aaa", "iterm_read_output", {"lines": 5})
    mock.assert_any_await("bbb", "iterm_type", {"text": "old\n"})


@pytest.mark.asyncio
async def test_tool_handler_pipe_empty_source_skips_type() -> None:
    # Streaming call with `since` — source has nothing new past the
    # cursor, so the handler must skip the type RPC entirely.
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.call_session",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = {"stdout": "", "cursor": 7, "session": "aaa"}
        result = await handler.handle(
            "iterm_pipe",
            {"from_session": "aaa", "to_session": "bbb", "since": 7},
        )
    assert result["piped"] is True
    assert result["cursor"] == 7
    assert result["bytes"] == 0
    assert mock.await_count == 1
    mock.assert_awaited_once_with("aaa", "iterm_read_output", {"since": 7})


@pytest.mark.asyncio
async def test_tool_handler_pipe_cursor_lost_warning() -> None:
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.call_session",
        new_callable=AsyncMock,
    ) as mock:
        mock.side_effect = [
            {
                "stdout": "late",
                "cursor": 100,
                "warning": "cursor_lost_due_to_scroll",
                "session": "aaa",
            },
            {"typed": "late\n", "session": "bbb"},
        ]
        result = await handler.handle(
            "iterm_pipe",
            {"from_session": "aaa", "to_session": "bbb", "since": 5},
        )
    assert result["warning"] == "cursor_lost_due_to_scroll"
    mock.assert_any_await("aaa", "iterm_read_output", {"since": 5})


@pytest.mark.asyncio
async def test_tool_handler_pipe_source_error_propagates() -> None:
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.call_session",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = {"error": "session is detached"}
        result = await handler.handle(
            "iterm_pipe",
            {"from_session": "aaa", "to_session": "bbb", "since": 0},
        )
    assert result == {"error": "session is detached"}
    assert mock.await_count == 1


@pytest.mark.asyncio
async def test_tool_handler_pipe_target_error_propagates() -> None:
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.call_session",
        new_callable=AsyncMock,
    ) as mock:
        mock.side_effect = [
            {"stdout": "hi", "cursor": 1, "session": "aaa"},
            {"error": "session is detached"},
        ]
        result = await handler.handle(
            "iterm_pipe",
            {"from_session": "aaa", "to_session": "bbb", "since": 0},
        )
    assert result == {"error": "session is detached"}


@pytest.mark.asyncio
async def test_tool_handler_pipe_missing_sessions() -> None:
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.call_session",
        new_callable=AsyncMock,
    ) as mock:
        r1 = await handler.handle("iterm_pipe", {"to_session": "bbb"})
        r2 = await handler.handle("iterm_pipe", {"from_session": "aaa"})
        r3 = await handler.handle("iterm_pipe", {})
    for r in (r1, r2, r3):
        assert "error" in r
        assert "from_session" in r["error"]
    assert mock.await_count == 0


@pytest.mark.asyncio
async def test_tool_handler_pipe_forwards_since_and_lines() -> None:
    handler = ToolHandler()
    with patch(
        "iterm2_control_mcp.mcp.handler.call_session",
        new_callable=AsyncMock,
    ) as mock:
        mock.side_effect = [
            {"stdout": "x", "cursor": 99, "session": "aaa"},
            {"typed": "x\n", "session": "bbb"},
        ]
        await handler.handle(
            "iterm_pipe",
            {
                "from_session": "aaa",
                "to_session": "bbb",
                "since": 50,
                "lines": 10,
            },
        )
    mock.assert_any_await(
        "aaa", "iterm_read_output", {"since": 50, "lines": 10},
    )


