from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from iterm2_control_mcp.cowork.protocol import Request
from iterm2_control_mcp.cowork.server import CoworkServer, DaemonHandler
from iterm2_control_mcp.terminal.session import SessionObserver


def _make_handler(
    buffer_lines: list[str] | None = None,
    detached: bool = False,
    name: str | None = None,
    activity_path: Path | None = None,
) -> DaemonHandler:
    buf = buffer_lines or []
    observer = MagicMock(spec=SessionObserver)
    observer.recent_output = buf
    observer.session_id = "test-iterm-session"
    observer.line_cursor = len(buf)
    observer.shell_integration = False
    observer.buffer_maxlen = 100
    observer.send_command = AsyncMock()
    observer.send_keys = AsyncMock()
    observer.type_text = AsyncMock()
    observer.send_and_read = AsyncMock(return_value={
        "stdout": "command output",
        "exit_code": 0,
        "cursor": len(buf) + 1,
        "start_cursor": len(buf),
    })

    def _read_since(since: int | None = None, lines: int = 50) -> dict[str, object]:
        cursor = len(buf)
        if since is not None:
            offset = max(since, 0)
            sliced = buf[offset:]
        else:
            sliced = buf[-lines:] if lines > 0 else buf
        return {"stdout": "\n".join(sliced), "cursor": cursor}

    observer.read_since = MagicMock(side_effect=_read_since)
    observer.refresh_from_screen = AsyncMock()
    observer.wait_for_text = AsyncMock(return_value="matched line")
    observer.clear_buffer = MagicMock()
    observer.capture_screen = AsyncMock(return_value={
        "lines": ["$ ls", "file1  file2"],
        "cursor": {"x": 0, "y": 2},
        "dimensions": {"rows": 24, "cols": 80},
        "scrollback_lines": 100,
    })

    handler = DaemonHandler(
        observer=observer,
        session_id="abc12345",
        start_time=datetime.now(UTC),
        name=name,
        activity_path=activity_path,
    )
    if detached:
        handler.set_observer(None)
    return handler


@pytest.mark.asyncio
async def test_read_output() -> None:
    handler = _make_handler(buffer_lines=["line1", "line2", "line3"])
    req = Request(method="iterm_read_output", params={"lines": 2}, id=1)
    resp = await handler.handle(req)
    assert isinstance(resp.result, dict)
    assert resp.result["stdout"] == "line2\nline3"
    assert resp.result["cursor"] == 3
    assert resp.result["session"] == {"id": "abc12345", "name": ""}


@pytest.mark.asyncio
async def test_read_output_empty() -> None:
    handler = _make_handler()
    req = Request(method="iterm_read_output", params={}, id=1)
    resp = await handler.handle(req)
    assert isinstance(resp.result, dict)
    assert resp.result["stdout"] == ""


@pytest.mark.asyncio
async def test_read_output_since_cursor() -> None:
    handler = _make_handler(buffer_lines=["a", "b", "c", "d"])
    req = Request(method="iterm_read_output", params={"since": 2}, id=1)
    resp = await handler.handle(req)
    assert isinstance(resp.result, dict)
    assert resp.result["stdout"] == "c\nd"
    assert resp.result["cursor"] == 4


@pytest.mark.asyncio
async def test_read_output_invalid_since() -> None:
    handler = _make_handler(buffer_lines=["a"])
    req = Request(
        method="iterm_read_output", params={"since": "not-an-int"}, id=1,
    )
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "integer" in resp.error


@pytest.mark.asyncio
async def test_send_command() -> None:
    handler = _make_handler(name="dev")
    # line_cursor mock is set to len(buffer)=0 in the fixture.
    req = Request(method="iterm_send_command", params={"command": "ls -la"}, id=1)
    resp = await handler.handle(req)
    assert isinstance(resp.result, dict)
    assert resp.result["status"] == "executed"
    assert resp.result["session"] == {"id": "abc12345", "name": "dev"}
    assert resp.result["cursor"] == 0
    handler._observer.send_command.assert_called_once_with("ls -la")
    handler._observer.refresh_from_screen.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_command_empty() -> None:
    handler = _make_handler()
    req = Request(method="iterm_send_command", params={"command": ""}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "required" in resp.error


@pytest.mark.asyncio
async def test_status() -> None:
    handler = _make_handler()
    req = Request(method="iterm_status", id=1)
    resp = await handler.handle(req)
    assert resp.result["session_id"] == "abc12345"
    assert resp.result["iterm_session"] == "test-iterm-session"
    assert "uptime_seconds" in resp.result


@pytest.mark.asyncio
async def test_unknown_method() -> None:
    handler = _make_handler()
    req = Request(method="terminal_explode", id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "unknown method" in resp.error


@pytest.mark.asyncio
async def test_send_keys() -> None:
    handler = _make_handler()
    req = Request(method="iterm_send_keys", params={"keys": "ctrl-c"}, id=1)
    resp = await handler.handle(req)
    assert resp.result == "sent"
    handler._observer.send_keys.assert_called_once_with("ctrl-c")


@pytest.mark.asyncio
async def test_send_keys_missing() -> None:
    handler = _make_handler()
    req = Request(method="iterm_send_keys", params={"keys": ""}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "required" in resp.error


@pytest.mark.asyncio
async def test_type_text() -> None:
    handler = _make_handler(name="dev")
    req = Request(
        method="iterm_type",
        params={"text": "rm -rf /tmp/staging"},
        id=1,
    )
    resp = await handler.handle(req)
    assert isinstance(resp.result, dict)
    assert resp.result["status"] == "typed"
    assert resp.result["session"] == {"id": "abc12345", "name": "dev"}
    handler._observer.type_text.assert_called_once_with("rm -rf /tmp/staging")


@pytest.mark.asyncio
async def test_type_text_missing() -> None:
    handler = _make_handler()
    req = Request(method="iterm_type", params={"text": ""}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "required" in resp.error


@pytest.mark.asyncio
async def test_type_text_preserves_whitespace() -> None:
    handler = _make_handler()
    req = Request(
        method="iterm_type",
        params={"text": "cd /var/lo"},
        id=1,
    )
    resp = await handler.handle(req)
    assert isinstance(resp.result, dict)
    handler._observer.type_text.assert_called_once_with("cd /var/lo")


@pytest.mark.asyncio
async def test_send_and_read() -> None:
    handler = _make_handler()
    req = Request(
        method="iterm_send_and_read",
        params={"command": "whoami", "timeout": 3},
        id=1,
    )
    resp = await handler.handle(req)
    assert isinstance(resp.result, dict)
    assert resp.result["stdout"] == "command output"
    assert resp.result["exit_code"] == 0
    assert resp.result["session"] == {"id": "abc12345", "name": ""}
    assert resp.result["shell_integration"] is False
    handler._observer.send_and_read.assert_called_once_with(
        "whoami", 3.0, quiet_ms=None, max_wait=None,
    )


@pytest.mark.asyncio
async def test_send_and_read_missing_command() -> None:
    handler = _make_handler()
    req = Request(method="iterm_send_and_read", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "required" in resp.error


@pytest.mark.asyncio
async def test_read_output_with_wait_for() -> None:
    handler = _make_handler(buffer_lines=["waiting", "prompt$"])
    req = Request(
        method="iterm_read_output",
        params={"wait_for": "prompt", "lines": 50},
        id=1,
    )
    resp = await handler.handle(req)
    handler._observer.wait_for_text.assert_called_once_with("prompt", timeout=10.0)
    assert resp.result is not None


@pytest.mark.asyncio
async def test_read_output_with_clear() -> None:
    handler = _make_handler(buffer_lines=["line1"])
    req = Request(
        method="iterm_read_output",
        params={"clear": True},
        id=1,
    )
    resp = await handler.handle(req)
    handler._observer.clear_buffer.assert_called_once()
    assert isinstance(resp.result, dict)
    assert resp.result["stdout"] == "line1"


@pytest.mark.asyncio
async def test_detached_read_output() -> None:
    handler = _make_handler(detached=True)
    req = Request(method="iterm_read_output", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "detached" in resp.error


@pytest.mark.asyncio
async def test_detached_send_command() -> None:
    handler = _make_handler(detached=True)
    req = Request(method="iterm_send_command", params={"command": "ls"}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "detached" in resp.error


@pytest.mark.asyncio
async def test_detached_send_keys() -> None:
    handler = _make_handler(detached=True)
    req = Request(method="iterm_send_keys", params={"keys": "ctrl-c"}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "detached" in resp.error


@pytest.mark.asyncio
async def test_detached_type_text() -> None:
    handler = _make_handler(detached=True)
    req = Request(method="iterm_type", params={"text": "hello"}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "detached" in resp.error


@pytest.mark.asyncio
async def test_daemon_stop_close_window() -> None:
    handler = _make_handler()
    stop_cb = AsyncMock(return_value="stopping")
    handler.set_stop_callback(stop_cb)
    req = Request(
        method="daemon_stop", params={"close_window": True}, id=1,
    )
    resp = await handler.handle(req)
    assert resp.result == "stopping"
    stop_cb.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_daemon_stop_keep_window() -> None:
    handler = _make_handler()
    stop_cb = AsyncMock(return_value="stopping")
    handler.set_stop_callback(stop_cb)
    req = Request(
        method="daemon_stop", params={"close_window": False}, id=1,
    )
    resp = await handler.handle(req)
    assert resp.result == "stopping"
    stop_cb.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_daemon_stop_default_closes_window() -> None:
    handler = _make_handler()
    stop_cb = AsyncMock(return_value="stopping")
    handler.set_stop_callback(stop_cb)
    req = Request(method="daemon_stop", params={}, id=1)
    await handler.handle(req)
    stop_cb.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_daemon_stop_not_registered() -> None:
    handler = _make_handler()
    req = Request(method="daemon_stop", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "not supported" in resp.error


@pytest.mark.asyncio
async def test_detached_send_and_read() -> None:
    handler = _make_handler(detached=True)
    req = Request(method="iterm_send_and_read", params={"command": "ls"}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "detached" in resp.error


@pytest.mark.asyncio
async def test_daemon_attach() -> None:
    handler = _make_handler()
    attach_cb = AsyncMock(return_value="attached")
    detach_cb = AsyncMock(return_value="detached")
    handler.set_lifecycle_callbacks(attach_cb, detach_cb)

    req = Request(
        method="daemon_attach",
        params={"iterm_session_id": "w0t1p0:sess-123"},
        id=1,
    )
    resp = await handler.handle(req)
    assert resp.result == "attached"
    attach_cb.assert_called_once_with("w0t1p0:sess-123")


@pytest.mark.asyncio
async def test_daemon_attach_missing_id() -> None:
    handler = _make_handler()
    attach_cb = AsyncMock(return_value="attached")
    detach_cb = AsyncMock(return_value="detached")
    handler.set_lifecycle_callbacks(attach_cb, detach_cb)

    req = Request(method="daemon_attach", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "required" in resp.error


@pytest.mark.asyncio
async def test_daemon_detach() -> None:
    handler = _make_handler()
    attach_cb = AsyncMock(return_value="attached")
    detach_cb = AsyncMock(return_value="detached")
    handler.set_lifecycle_callbacks(attach_cb, detach_cb)

    req = Request(method="daemon_detach", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.result == "detached"
    detach_cb.assert_called_once()


@pytest.mark.asyncio
async def test_daemon_detach_already_detached() -> None:
    handler = _make_handler(detached=True)
    attach_cb = AsyncMock(return_value="attached")
    detach_cb = AsyncMock(return_value="detached")
    handler.set_lifecycle_callbacks(attach_cb, detach_cb)

    req = Request(method="daemon_detach", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "detached" in resp.error


@pytest.mark.asyncio
async def test_daemon_attach_no_callback() -> None:
    handler = _make_handler()
    req = Request(
        method="daemon_attach",
        params={"iterm_session_id": "sess-123"},
        id=1,
    )
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "not supported" in resp.error


@pytest.mark.asyncio
async def test_status_detached() -> None:
    handler = _make_handler(detached=True)
    req = Request(method="iterm_status", id=1)
    resp = await handler.handle(req)
    assert resp.result["session_id"] == "abc12345"
    assert resp.result["attached"] is False
    assert "iterm_session" not in resp.result


@pytest.mark.asyncio
async def test_rename_session() -> None:
    handler = _make_handler()
    rename_cb = AsyncMock(return_value="renamed")
    handler.set_rename_callback(rename_cb)
    req = Request(method="iterm_rename_session", params={"name": "new-name"}, id=1)
    resp = await handler.handle(req)
    assert resp.result == "renamed"
    rename_cb.assert_called_once_with("new-name")


@pytest.mark.asyncio
async def test_rename_session_missing_name() -> None:
    handler = _make_handler()
    rename_cb = AsyncMock(return_value="renamed")
    handler.set_rename_callback(rename_cb)
    req = Request(method="iterm_rename_session", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "required" in resp.error


@pytest.mark.asyncio
async def test_rename_session_no_callback() -> None:
    handler = _make_handler()
    req = Request(method="iterm_rename_session", params={"name": "x"}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "not supported" in resp.error


@pytest.mark.asyncio
async def test_focus_window() -> None:
    handler = _make_handler()
    focus_cb = AsyncMock(return_value="focused")
    handler.set_focus_callback(focus_cb)
    req = Request(method="iterm_focus_window", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.result == "focused"
    focus_cb.assert_called_once()


@pytest.mark.asyncio
async def test_focus_window_detached() -> None:
    handler = _make_handler(detached=True)
    focus_cb = AsyncMock(return_value="focused")
    handler.set_focus_callback(focus_cb)
    req = Request(method="iterm_focus_window", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "detached" in resp.error


@pytest.mark.asyncio
async def test_split_pane() -> None:
    handler = _make_handler()
    split_cb = AsyncMock(return_value="w0t0p1:new-session-id")
    handler.set_split_callback(split_cb)
    req = Request(method="iterm_split_pane", params={"vertical": True}, id=1)
    resp = await handler.handle(req)
    assert resp.result == "w0t0p1:new-session-id"
    split_cb.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_split_pane_detached() -> None:
    handler = _make_handler(detached=True)
    split_cb = AsyncMock(return_value="new-id")
    handler.set_split_callback(split_cb)
    req = Request(method="iterm_split_pane", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "detached" in resp.error


@pytest.mark.asyncio
async def test_capture_screen() -> None:
    handler = _make_handler()
    req = Request(method="iterm_capture_screen", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.result["cursor"] == {"x": 0, "y": 2}
    assert resp.result["dimensions"]["rows"] == 24
    assert len(resp.result["lines"]) == 2


@pytest.mark.asyncio
async def test_capture_screen_detached() -> None:
    handler = _make_handler(detached=True)
    req = Request(method="iterm_capture_screen", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "detached" in resp.error


@pytest.mark.asyncio
async def test_handler_exception() -> None:
    handler = _make_handler()
    handler._observer.send_command = AsyncMock(side_effect=RuntimeError("boom"))
    req = Request(method="iterm_send_command", params={"command": "ls"}, id=1)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert "boom" in resp.error


@pytest.mark.asyncio
async def test_activity_stamped_on_rpc() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        activity_path = Path(tmpdir) / "activity"
        handler = _make_handler(
            buffer_lines=["x"], activity_path=activity_path,
        )
        assert not activity_path.exists()
        req = Request(method="iterm_read_output", params={}, id=1)
        await handler.handle(req)
        assert activity_path.exists()
        stamped = activity_path.read_text()
        assert "T" in stamped  # ISO timestamp


@pytest.mark.asyncio
async def test_activity_not_stamped_on_status() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        activity_path = Path(tmpdir) / "activity"
        handler = _make_handler(activity_path=activity_path)
        req = Request(method="iterm_status", params={}, id=1)
        await handler.handle(req)
        assert not activity_path.exists()


@pytest.mark.asyncio
async def test_socket_roundtrip() -> None:
    handler = _make_handler(buffer_lines=["hello world"])

    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = CoworkServer(sock_path, handler)
        await server.start()

        try:
            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            req = Request(method="iterm_read_output", params={"lines": 50}, id=1)
            writer.write(req.to_json().encode() + b"\n")
            await writer.drain()

            line = await reader.readline()
            data = json.loads(line.decode())
            assert data["result"]["stdout"] == "hello world"
            assert data["result"]["cursor"] == 1

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()
