"""Session pool + lifecycle helpers.

Subprocess, filesystem, and RPC helpers that are called by ToolHandler
but aren't the handler itself. Separating them keeps handler.py focused
on routing MCP tool calls.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys

from iterm2_control_mcp.config import pid_looks_like_session_daemon
from iterm2_control_mcp.cowork.client import SESSIONS_DIR, call_session

logger = logging.getLogger(__name__)


def get_sessions() -> list[dict[str, object]]:
    if not SESSIONS_DIR.exists():
        return []
    sessions = []
    for d in sorted(SESSIONS_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        pid = meta.get("pid")
        alive = False
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                pass
        activity_path = d / "activity"
        last_activity_at: str | None = None
        if activity_path.exists():
            try:
                last_activity_at = activity_path.read_text().strip() or None
            except OSError:
                last_activity_at = None
        info: dict[str, object] = {
            "id": d.name,
            "name": meta.get("name", ""),
            "pid": pid,
            "started_at": meta.get("started_at"),
            "last_activity_at": last_activity_at,
            "status": "active" if alive else "stale",
        }
        sessions.append(info)
    return sessions


def clean_sessions() -> int:
    if not SESSIONS_DIR.exists():
        return 0
    import shutil
    cleaned = 0
    for d in sorted(SESSIONS_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            pid = meta.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    continue  # process alive, skip
                except ProcessLookupError:
                    pass  # process dead, clean up
        # rmtree by path (not via Config.cleanup_session_dir) because
        # SESSIONS_DIR may be patched in tests to a path that doesn't
        # match the Config-loaded sessions_dir.
        shutil.rmtree(d, ignore_errors=True)
        cleaned += 1
    return cleaned



def _parse_cli_session_json(stdout: str) -> str | None:
    """Pull the session_id from the CLI's --json output.

    The CLI prints a single JSON line; we scan from the last line
    backwards and return the first decodable object that has
    `session_id`. Older CLI versions that don't support --json print
    human-readable text, which won't decode — returns None and the
    caller logs.
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = obj.get("session_id")
        if isinstance(sid, str):
            return sid
    return None


def create_new_session(name: str | None = None) -> str | None:
    python_path = sys.executable
    args = [python_path, "-m", "iterm2_control_mcp", "new", "--json"]
    if name:
        args.append(name)

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return _parse_cli_session_json(result.stdout)
    except Exception:
        logger.exception("Failed to create session")
    return None


def _create_split_session(
    iterm_session_id: str, name: str | None = None,
) -> str | None:
    """Spawn a daemon for an already-existing iTerm2 pane (from split)."""
    python_path = sys.executable
    args = [python_path, "-m", "iterm2_control_mcp", "acquire", "--json"]
    if name:
        args.append(name)
    args.extend(["--iterm-session", iterm_session_id])

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return _parse_cli_session_json(result.stdout)
    except Exception:
        logger.exception("Failed to create split session")
    return None


async def stop_session(session_id: str, close_window: bool = True) -> str:
    meta_path = SESSIONS_DIR / session_id / "meta.json"
    if not meta_path.exists():
        return f"Session {session_id} not found"

    try:
        result = await call_session(
            session_id, "daemon_stop", {"close_window": close_window},
        )
    except Exception:
        result = {"error": "rpc failed"}

    if isinstance(result, dict) and "error" in result:
        meta = json.loads(meta_path.read_text())
        pid = meta.get("pid")
        if pid:
            if not pid_looks_like_session_daemon(pid):
                return (
                    f"PID {pid} is no longer our daemon (stale "
                    f"meta.json); not signalling"
                )
            try:
                os.kill(pid, signal.SIGTERM)
                return f"Stopped session {session_id} (SIGTERM fallback)"
            except ProcessLookupError:
                return f"Process {pid} not found"
        return f"No PID for session {session_id}"

    if close_window:
        return f"Stopped session {session_id}, closing pane"
    return f"Stopped session {session_id}"
