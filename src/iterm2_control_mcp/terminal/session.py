from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import shlex
import time
from collections import deque

import iterm2

logger = logging.getLogger(__name__)

# Default quiet period used by send_and_read's idle-detection fallback
# when shell integration is absent. The PTY is considered quiescent once
# this many milliseconds have elapsed without the screen streamer
# appending any new text. 800 ms is comfortably above iTerm2's adaptive
# streamer cadences (60 FPS foreground, 15 FPS high-throughput, 1 Hz
# background) while still feeling responsive on zero-output commands
# like `cd`.
_DEFAULT_QUIET_MS = 800

# Mapping of key names to escape sequences / text
_KEY_MAP: dict[str, str] = {
    "ctrl-c": "\x03",
    "ctrl-d": "\x04",
    "ctrl-z": "\x1a",
    "ctrl-l": "\x0c",
    "enter": "\r",
    "tab": "\t",
    "escape": "\x1b",
    "backspace": "\x7f",
    "delete": "\x1b[3~",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[H",
    "end": "\x1b[F",
}


AI_MARKER = "# [ai]"


def _compute_new_lines(
    last_visible: list[str],
    current_visible: list[str],
    scrollback_delta: int,
) -> list[str]:
    """Return lines appended to the pane between two streamer fires.

    iTerm2's screen_streamer delivers full visible-screen snapshots, not
    deltas — redraws (cursor blinks, prompt updates, escape processing)
    re-deliver lines we've already captured. This helper diffs the new
    snapshot against the previous one, accounting for:

      - Lines that scrolled off the top (count == scrollback_delta). They
        were at positions [0..scrollback_delta-1] of last_visible — still
        "new" from the observer's perspective because they passed
        through the pane and we need to record them.
      - Lines newly appeared below the overlap. We find the first row
        where the surviving-old and new-visible diverge; anything at or
        below that row in new_visible is new content.

    Mid-screen edits (a row's text changes in place) currently cause
    over-appending from the edit point down. Acceptable — it's a rare
    case in normal shell output, and the alternative (skip the edit)
    would under-report.
    """
    scrolled_off = last_visible[:scrollback_delta] if scrollback_delta > 0 else []
    remaining_old = last_visible[scrollback_delta:]
    common = 0
    for i in range(min(len(remaining_old), len(current_visible))):
        if remaining_old[i] != current_visible[i]:
            break
        common = i + 1
    new_below = current_visible[common:]
    return scrolled_off + new_below


class SessionObserver:
    def __init__(
        self,
        session: iterm2.Session,
        buffer_lines: int = 100,
        command_markers: bool = False,
    ) -> None:
        self._session = session
        self._buffer: deque[str] = deque(maxlen=buffer_lines)
        self._command_markers = command_markers
        # Monotonic line counter: increments on every line appended to the
        # buffer. Used as the `cursor` token for iterm_read_output's `since`
        # parameter so consumers can request only new lines since their last
        # read.
        self._line_cursor: int = 0
        # Set true once we see any iTerm2 PromptMonitor event, meaning OSC
        # 133 shell integration is active on the remote. Sticky for the
        # session's lifetime — once present, assumed present thereafter.
        self._shell_integration: bool = False
        # Signalled by notify_command_end; cleared by send_and_read before
        # each command so it waits only for that command's completion.
        self._command_end_event: asyncio.Event = asyncio.Event()
        self._last_exit_code: int | None = None
        # Dedup state for the screen-streamer loop — iTerm2 re-delivers
        # full visible-screen snapshots on every redraw, so we compare
        # against the previous snapshot and only append truly new lines.
        self._last_visible: list[str] = []
        self._last_scrollback: int = 0
        # Serialize buffer mutations between the streamer loop and any
        # pull-side refresh (e.g. from read_since) so both can safely
        # update _buffer / _line_cursor.
        self._buffer_lock: asyncio.Lock = asyncio.Lock()

    @property
    def session_id(self) -> str:
        return str(self._session.session_id)

    @property
    def recent_output(self) -> list[str]:
        return list(self._buffer)

    @property
    def line_cursor(self) -> int:
        return self._line_cursor

    @property
    def shell_integration(self) -> bool:
        return self._shell_integration

    @property
    def buffer_maxlen(self) -> int:
        return self._buffer.maxlen or 0

    def notify_prompt(self) -> None:
        """Called by the PromptMonitor task when a new shell prompt appears."""
        self._shell_integration = True

    def notify_command_start(self, _command: str) -> None:
        self._shell_integration = True

    def notify_command_end(self, exit_code: int | None) -> None:
        self._shell_integration = True
        self._last_exit_code = exit_code
        self._command_end_event.set()

    async def send_command(self, command: str) -> None:
        # Submit with CR, as the Return key does. Unix ttys map CR→NL
        # (ICRNL); Windows cmd.exe / ConPTY ignores a lone LF (#11).
        if self._command_markers:
            await self._session.async_send_text(AI_MARKER + "\r")
        await self._session.async_send_text(command + "\r")

    async def send_keys(self, keys: str) -> None:
        seq = _KEY_MAP.get(keys.lower())
        if seq is None:
            raise ValueError(f"Unknown key: {keys}")
        await self._session.async_send_text(seq)

    async def type_text(self, text: str) -> None:
        await self._session.async_send_text(text)

    async def send_and_read(
        self,
        command: str,
        timeout: float = 5.0,
        quiet_ms: float | None = None,
        max_wait: float | None = None,
    ) -> dict[str, object]:
        """Send a command and wait for completion, returning sliced output.

        Returns a dict with:
          - stdout: str — lines produced between command send and completion
          - exit_code: int | None — from shell-integration COMMAND_END, else None
          - cursor: int — line counter after the command completed
          - start_cursor: int — cursor value at the moment the command was sent
          - idle_reason: str — which completion path fired: "shell_integration",
            "idle", or "timeout"

        When the remote shell has iTerm2 shell integration (OSC 133), the
        fast path awaits COMMAND_END for an exit code and a clean boundary.
        Otherwise the fallback watches `_line_cursor` — a monotonic counter
        that the screen streamer advances each time a non-empty text line
        is appended — and returns once it has been stable for `quiet_ms`
        milliseconds (default 800, see `_DEFAULT_QUIET_MS`). This replaces
        the old "buffer grew + last-line prompt regex" heuristic, which
        truncated multi-chunk output and hung on zero-output commands.

        Parameters
        ----------
        timeout:
            Legacy aggregate budget. When neither `max_wait` nor `quiet_ms`
            is set, this is used as the max wait. Preserved for backward
            compatibility with callers that only know `timeout`.
        quiet_ms:
            Fallback path only. How long the cursor must be stable before
            we declare the PTY idle. Raise this (e.g. 2000) when the
            iTerm2 window may be backgrounded — iTerm2 throttles the
            streamer to 1 Hz for background windows, so sub-second
            thresholds will misfire.
        max_wait:
            Fallback path only. Hard cap on total wait time so a
            runaway command (`yes`, `tail -f`) eventually returns.
            Defaults to `timeout`.
        """
        quiet_s = (
            float(quiet_ms)
            if quiet_ms is not None
            else float(_DEFAULT_QUIET_MS)
        ) / 1000.0
        max_wait_s = float(max_wait) if max_wait is not None else float(timeout)

        mark_cursor = self._line_cursor
        mark_len = len(self._buffer)
        # Clear any stale end event (e.g. from a prior command) so we wait
        # only for this command's completion.
        self._command_end_event.clear()
        self._last_exit_code = None

        await self.send_command(command)

        exit_code: int | None = None
        idle_reason = "timeout"

        if self._shell_integration:
            try:
                await asyncio.wait_for(
                    self._command_end_event.wait(),
                    timeout=max_wait_s,
                )
                exit_code = self._last_exit_code
                idle_reason = "shell_integration"
            except TimeoutError:
                pass
        else:
            # Fallback: poll the monotonic cursor. Declare idle once it
            # has been stable for `quiet_s` seconds AND it has advanced
            # at least once since the command was sent (so we don't
            # falsely return before the echo arrives from a slow PTY).
            poll_interval = min(0.1, max(quiet_s / 4, 0.05))
            start = time.monotonic()
            last_cursor = mark_cursor
            last_change = start

            while True:
                now = time.monotonic()
                if now - start >= max_wait_s:
                    break
                # Sleep at most to either the next idle boundary or the
                # hard cap, whichever comes first.
                remaining_budget = max_wait_s - (now - start)
                await asyncio.sleep(min(poll_interval, remaining_budget))

                if self._line_cursor != last_cursor:
                    last_cursor = self._line_cursor
                    last_change = time.monotonic()
                    continue
                if (
                    self._line_cursor > mark_cursor
                    and time.monotonic() - last_change >= quiet_s
                ):
                    idle_reason = "idle"
                    break

        new_lines = list(self._buffer)[mark_len:]
        return {
            "stdout": "\n".join(new_lines),
            "exit_code": exit_code,
            "cursor": self._line_cursor,
            "start_cursor": mark_cursor,
            "idle_reason": idle_reason,
        }

    def read_since(
        self, since: int | None = None, lines: int = 50,
    ) -> dict[str, object]:
        """Return buffered output, optionally sliced to everything after `since`.

        Returns a dict with:
          - stdout: str
          - cursor: int — the current line cursor (pass back as `since` next call)
          - warning: str | None — set to "cursor_lost_due_to_scroll" if the
            caller's `since` is older than what the ring buffer still holds
        """
        buf = list(self._buffer)
        cursor = self._line_cursor
        warning: str | None = None
        oldest_cursor = cursor - len(buf)

        if since is not None:
            if since < oldest_cursor:
                warning = "cursor_lost_due_to_scroll"
                sliced = buf
            else:
                offset = max(since - oldest_cursor, 0)
                sliced = buf[offset:]
        else:
            # lines > 0: tail-N backfill.
            # lines == 0: "no backfill — just tell me the current cursor."
            # Callers that want "anything new past *now*" pass lines=0
            # to seed a since-cursor without pulling the current tail.
            sliced = buf[-lines:] if lines > 0 else []

        result: dict[str, object] = {
            "stdout": "\n".join(sliced),
            "cursor": cursor,
        }
        if warning is not None:
            result["warning"] = warning
        return result

    async def wait_for_text(self, text: str, timeout: float = 10.0) -> str | None:
        elapsed = 0.0
        interval = 0.3
        while elapsed < timeout:
            for line in self._buffer:
                if text in line:
                    return line
            await asyncio.sleep(interval)
            elapsed += interval
        return None

    def clear_buffer(self) -> None:
        self._buffer.clear()

    async def capture_screen(self) -> dict[str, object]:
        contents = await self._session.async_get_screen_contents()
        lines: list[str] = []
        for i in range(contents.number_of_lines):
            lines.append(contents.line(i).string.rstrip())
        cursor = contents.cursor_coord
        return {
            "lines": lines,
            "cursor": {"x": cursor.x, "y": cursor.y},
            "dimensions": {
                "rows": contents.number_of_lines,
                "cols": self._session.grid_size.width,
            },
            "scrollback_lines": contents.number_of_lines_above_screen,
        }

    async def download_file(self, remote_path: str) -> str:
        """Trigger iTerm2 OSC 1337 file download from the remote system."""
        safe_path = shlex.quote(remote_path)
        filename = remote_path.rsplit("/", 1)[-1]
        safe_name = shlex.quote(filename)
        # Shell one-liner: emit OSC 1337 escape sequence with base64 content
        cmd = (
            "printf '\\033]1337;File=name='"
            f"\"$(echo -n {safe_name} | base64)\""
            "';size='"
            f"\"$(wc -c < {safe_path} | tr -d ' ')\""
            "';inline=0:'"
            f"\"$(base64 < {safe_path})\""
            "'\\a'"
        )
        await self._session.async_send_text(cmd + "\r")
        return filename

    async def upload_file(
        self,
        remote_path: str,
        content: str,
        encoding: str = "text",
    ) -> None:
        """Write content to a file on the remote system via a base64 here-doc.

        encoding="text" (default): content is UTF-8 text; the server
            base64-encodes it before transport.
        encoding="base64": content is already base64-encoded bytes. Use this
            for binary files since JSON cannot carry raw bytes.
        """
        if encoding == "text":
            encoded = base64.b64encode(content.encode()).decode()
        elif encoding == "base64":
            # Strip whitespace the caller may have inserted (line wraps, etc.)
            cleaned = "".join(content.split())
            try:
                base64.b64decode(cleaned, validate=True)
            except ValueError as exc:
                raise ValueError(f"invalid base64 content: {exc}") from exc
            encoded = cleaned
        else:
            raise ValueError(f"unsupported encoding: {encoding!r}")

        # Wrap to 76-char lines so each here-doc line stays well below the
        # terminal canonical-mode line discipline limit (MAX_CANON: 1024 on
        # macOS, 255 on Linux).
        wrapped = "\n".join(
            encoded[i : i + 76] for i in range(0, len(encoded), 76)
        )
        safe_path = shlex.quote(remote_path)
        # Randomized sentinel avoids EOF collision if a payload happens to
        # contain a static marker, and prevents accidental termination by
        # user-typed content.
        sentinel = f"__ITERM_UL_{secrets.token_hex(8)}__"
        cmd = (
            f"base64 -d > {safe_path} <<'{sentinel}'\n"
            f"{wrapped}\n"
            f"{sentinel}\n"
        )
        # Chunk the send so a large payload doesn't arrive as a single
        # 700 KB+ burst. iTerm2's websocket and the PTY both handle streams
        # better than one giant message, and awaiting each chunk yields the
        # event loop so the shell can process input as it arrives.
        upload_chunk_size = 32 * 1024
        for start in range(0, len(cmd), upload_chunk_size):
            await self._session.async_send_text(
                cmd[start : start + upload_chunk_size],
            )

    async def _ingest_snapshot(
        self,
        current_visible: list[str],
        current_scrollback: int,
    ) -> None:
        """Fold a full-screen snapshot into the buffer, deduplicated.

        Used by both the push-side streamer loop and the pull-side
        refresh path. Guarded by a lock because both can run in the
        same event loop and mutate the buffer.
        """
        async with self._buffer_lock:
            scrollback_delta = max(0, current_scrollback - self._last_scrollback)
            new_lines = _compute_new_lines(
                self._last_visible, current_visible, scrollback_delta,
            )
            for line in new_lines:
                if line:
                    self._buffer.append(line)
                    self._line_cursor += 1
            self._last_visible = current_visible
            self._last_scrollback = current_scrollback

    async def refresh_from_screen(self) -> None:
        """Force-fold the pane's current state into the buffer.

        The streamer only fires on iTerm2 redraw events, which can be
        sparse (a quiet pane stays quiet). iterm_read_output triggers
        this before slicing so a read always sees what's actually on
        the pane, not what the streamer last happened to push.
        """
        contents = await self._session.async_get_screen_contents()
        current_visible = [
            contents.line(i).string.rstrip()
            for i in range(contents.number_of_lines)
        ]
        await self._ingest_snapshot(
            current_visible,
            contents.number_of_lines_above_screen,
        )

    async def start_observing(self) -> None:
        logger.info("Acquiring terminal: %s", self._session.session_id)
        async with self._session.get_screen_streamer() as streamer:
            while True:
                contents = await streamer.async_get()
                if contents is None:
                    break
                current_visible = [
                    contents.line(i).string.rstrip()
                    for i in range(contents.number_of_lines)
                ]
                await self._ingest_snapshot(
                    current_visible,
                    contents.number_of_lines_above_screen,
                )
