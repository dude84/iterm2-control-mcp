from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from iterm2_control_mcp.terminal.session import SessionObserver


def _make_observer(
    initial_buffer: list[str] | None = None,
    buffer_lines: int = 10,
) -> SessionObserver:
    session = MagicMock()
    session.session_id = "iterm-123"
    session.async_send_text = AsyncMock()
    observer = SessionObserver(
        session=session,
        buffer_lines=buffer_lines,
        command_markers=False,
    )
    # Seed the buffer and cursor as if `start_observing` had appended these.
    if initial_buffer:
        for line in initial_buffer:
            observer._buffer.append(line)
            observer._line_cursor += 1
    return observer


async def _append_later(
    observer: SessionObserver,
    lines: list[str],
    delay: float,
) -> None:
    """Mimic the screen streamer appending output after `delay` seconds."""
    await asyncio.sleep(delay)
    for line in lines:
        observer._buffer.append(line)
        observer._line_cursor += 1


def test_read_since_no_cursor_returns_trailing_lines() -> None:
    obs = _make_observer(["a", "b", "c", "d", "e"])
    result = obs.read_since(since=None, lines=3)
    assert result == {"stdout": "c\nd\ne", "cursor": 5}


def test_read_since_with_cursor_returns_only_new() -> None:
    obs = _make_observer(["a", "b", "c", "d"])
    result = obs.read_since(since=2, lines=50)
    assert result == {"stdout": "c\nd", "cursor": 4}


def test_read_since_at_tip_returns_empty() -> None:
    obs = _make_observer(["a", "b"])
    result = obs.read_since(since=2, lines=50)
    assert result["stdout"] == ""
    assert result["cursor"] == 2


def test_compute_new_lines_append_at_bottom() -> None:
    from iterm2_control_mcp.terminal.session import _compute_new_lines
    # Visible screen grew by one line (v1.0.1 appeared after cmd echo).
    # Trailing "" rows are terminal padding and the helper just treats
    # them as content (the caller filters empties on ingest).
    old = ["banner", "prompt", "cmd", "", "", "", "", ""]
    new = ["banner", "prompt", "cmd", "v1.0.1", "", "", "", ""]
    assert _compute_new_lines(old, new, scrollback_delta=0) == [
        "v1.0.1", "", "", "", "",
    ]


def test_compute_new_lines_cosmetic_redraw_returns_empty() -> None:
    from iterm2_control_mcp.terminal.session import _compute_new_lines
    old = ["banner", "prompt", "cmd"]
    new = ["banner", "prompt", "cmd"]
    assert _compute_new_lines(old, new, scrollback_delta=0) == []


def test_compute_new_lines_scrolled_off() -> None:
    from iterm2_control_mcp.terminal.session import _compute_new_lines
    # Screen height 3. One line scrolled off, one new at the bottom.
    old = ["A", "B", "C"]
    new = ["B", "C", "D"]
    # A scrolled off (still "new" from observer's perspective — it has
    # passed through the pane and must be recorded); D is new at the
    # bottom.
    assert _compute_new_lines(old, new, scrollback_delta=1) == ["A", "D"]


def test_compute_new_lines_all_scrolled() -> None:
    from iterm2_control_mcp.terminal.session import _compute_new_lines
    old = ["A", "B", "C"]
    new = ["D", "E", "F"]
    # No overlap: all of old scrolled off, all of new is new content.
    assert _compute_new_lines(old, new, scrollback_delta=3) == [
        "A", "B", "C", "D", "E", "F",
    ]


def test_compute_new_lines_first_snapshot() -> None:
    from iterm2_control_mcp.terminal.session import _compute_new_lines
    # Observer's first fire — _last_visible is [], everything is new.
    old: list[str] = []
    new = ["banner", "prompt"]
    assert _compute_new_lines(old, new, scrollback_delta=0) == [
        "banner", "prompt",
    ]


def test_read_since_lines_zero_returns_cursor_only() -> None:
    # lines=0 with no `since` is the "from-now" pattern iterm_pipe uses
    # on its first call — return the current cursor without dumping the
    # buffer's tail (banner, prompts, earlier output) as content.
    obs = _make_observer(["a", "b", "c", "d", "e"])
    result = obs.read_since(since=None, lines=0)
    assert result == {"stdout": "", "cursor": 5}


def test_read_since_older_than_buffer_warns() -> None:
    # buffer_lines=3 means only the last 3 fit
    obs = _make_observer(buffer_lines=3)
    for ch in ["a", "b", "c", "d", "e"]:
        obs._buffer.append(ch)
        obs._line_cursor += 1
    # cursor=5, buffer holds last 3 (c,d,e) → oldest retained cursor=2.
    # Asking since=0 means the caller lost 2 lines.
    result = obs.read_since(since=0, lines=50)
    assert result["warning"] == "cursor_lost_due_to_scroll"
    assert result["cursor"] == 5


@pytest.mark.asyncio
async def test_type_text_does_not_append_newline() -> None:
    obs = _make_observer()
    await obs.type_text("rm -rf /tmp/foo")
    obs._session.async_send_text.assert_called_once_with("rm -rf /tmp/foo")


@pytest.mark.asyncio
async def test_send_and_read_shell_integration_fast_path() -> None:
    obs = _make_observer(["$ "])

    # Simulate COMMAND_END firing shortly after send_command.
    async def fire_end() -> None:
        await asyncio.sleep(0.05)
        obs._buffer.append("hello")
        obs._line_cursor += 1
        obs.notify_command_end(0)

    # Pre-mark integration so send_and_read takes the fast path immediately.
    obs._shell_integration = True
    task = asyncio.create_task(fire_end())
    result = await obs.send_and_read("echo hello", timeout=2.0)
    await task

    assert result["stdout"].endswith("hello")
    assert result["exit_code"] == 0
    assert result["idle_reason"] == "shell_integration"


@pytest.mark.asyncio
async def test_send_and_read_idle_fallback_multi_chunk() -> None:
    """Multi-chunk output must not truncate — idle timer resets on each
    cursor advance, so we wait for the full stream to go quiet."""
    obs = _make_observer()

    async def produce() -> None:
        # First chunk
        await asyncio.sleep(0.05)
        obs._buffer.append("line-1")
        obs._line_cursor += 1
        # Second chunk arrives after 80ms — well inside the 150ms quiet
        # window below, so the idle timer should reset and wait for more.
        await asyncio.sleep(0.08)
        obs._buffer.append("line-2")
        obs._line_cursor += 1
        await asyncio.sleep(0.08)
        obs._buffer.append("line-3")
        obs._line_cursor += 1

    task = asyncio.create_task(produce())
    result = await obs.send_and_read(
        "multichunk", timeout=5.0, quiet_ms=150, max_wait=5.0,
    )
    await task

    assert "line-1" in result["stdout"]
    assert "line-2" in result["stdout"]
    assert "line-3" in result["stdout"]
    assert result["exit_code"] is None
    assert result["idle_reason"] == "idle"


@pytest.mark.asyncio
async def test_send_and_read_idle_fallback_zero_output() -> None:
    """Zero-output commands (e.g. `cd /`) should still return on idle
    once the shell's echo line appears and the PTY goes quiet."""
    obs = _make_observer()

    # Just the echoed command line — no output body, no prompt regex.
    task = asyncio.create_task(_append_later(obs, ["cd /"], 0.05))
    result = await obs.send_and_read(
        "cd /", timeout=2.0, quiet_ms=100,
    )
    await task

    assert "cd /" in result["stdout"]
    assert result["idle_reason"] == "idle"


@pytest.mark.asyncio
async def test_send_and_read_timeout_without_any_output() -> None:
    """If nothing ever lands in the buffer (cursor never advances past
    mark_cursor), we fall through to max_wait."""
    obs = _make_observer()
    result = await obs.send_and_read(
        "sleep 999", timeout=0.3, quiet_ms=100,
    )
    assert result["exit_code"] is None
    assert result["stdout"] == ""
    assert result["idle_reason"] == "timeout"


@pytest.mark.asyncio
async def test_send_and_read_max_wait_caps_runaway_output() -> None:
    """A command that never stops producing (yes, tail -f) must still
    return when max_wait elapses."""
    obs = _make_observer()

    async def flood() -> None:
        # Keep appending every 30ms so the idle timer (quiet_ms=200)
        # never fires. The call must break out on max_wait instead.
        for i in range(20):
            await asyncio.sleep(0.03)
            obs._buffer.append(f"line-{i}")
            obs._line_cursor += 1

    task = asyncio.create_task(flood())
    result = await obs.send_and_read(
        "yes", timeout=10.0, quiet_ms=200, max_wait=0.3,
    )
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert result["idle_reason"] == "timeout"
    # Some output did get through before max_wait fired.
    assert "line-" in result["stdout"]


def test_notify_sets_shell_integration_flag() -> None:
    obs = _make_observer()
    assert obs.shell_integration is False
    obs.notify_prompt()
    assert obs.shell_integration is True

    obs2 = _make_observer()
    obs2.notify_command_end(0)
    assert obs2.shell_integration is True
