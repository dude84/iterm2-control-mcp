"""MCP tool dispatch.

Routes inbound MCP tool calls to the appropriate daemon / session-pool
helper, maintaining an in-memory pool of connected sessions.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from mcp.server.lowlevel.server import Server

from iterm2_control_mcp.config import load_config
from iterm2_control_mcp.cowork.client import call_session
from iterm2_control_mcp.mcp.schemas import _PROBE_SEQUENCE
from iterm2_control_mcp.mcp.session_pool import (
    _create_split_session,
    clean_sessions,
    create_new_session,
    get_sessions,
    stop_session,
)

logger = logging.getLogger(__name__)


class ToolHandler:
    def __init__(self, server: Server | None = None) -> None:
        self._connected_sessions: set[str] = set()
        self._active_session: str | None = None
        self._server = server
        self._config = load_config()

    @property
    def _max_sessions(self) -> int:
        return self._config.max_sessions

    def _add_session(self, session_id: str) -> None:
        """Add session to pool and set as active."""
        if len(self._connected_sessions) >= self._max_sessions:
            # Evict oldest (not active) — just drop the first one
            for sid in list(self._connected_sessions):
                if sid != self._active_session:
                    self._connected_sessions.discard(sid)
                    break
        self._connected_sessions.add(session_id)
        self._active_session = session_id

    def _remove_session(self, session_id: str) -> None:
        """Remove session from pool."""
        self._connected_sessions.discard(session_id)
        if self._active_session == session_id:
            self._active_session = (
                next(iter(self._connected_sessions), None)
            )

    def _resolve_session(self, arguments: dict[str, object]) -> str | None:
        """Resolve target session from arguments or active session."""
        session_id = str(arguments.get("session", ""))
        if session_id and session_id in self._connected_sessions:
            return session_id
        if session_id:
            # Not in pool but explicitly requested — try anyway
            return session_id
        return self._active_session

    async def handle(self, name: str, arguments: dict[str, object]) -> object:
        if name == "iterm_list_sessions":
            sessions = get_sessions()
            if not sessions:
                return "No active sessions. Use iterm_new_session to create one."
            return sessions

        if name == "iterm_new_session":
            session_name = arguments.get("name")
            sid = create_new_session(
                str(session_name) if session_name else None,
            )
            if sid:
                self._add_session(sid)
                return {
                    "created": sid,
                    "name": session_name or "",
                    "connected": True,
                }
            return {"error": "Failed to create session"}

        if name == "iterm_connect_session":
            session_id = str(arguments.get("session", ""))
            sessions = get_sessions()
            active = [s for s in sessions if s["status"] == "active"]
            active_ids = [s["id"] for s in active]

            if not active:
                return (
                    "No active sessions. "
                    "Use iterm_new_session to create one."
                )

            if session_id not in active_ids:
                return {
                    "error": (
                        f"Session {session_id} not found or not active"
                    ),
                    "available": active,
                }

            self._add_session(session_id)
            return {"connected": session_id}

        if name == "iterm_detach_session":
            session_id = str(arguments.get("session", ""))
            if not session_id:
                session_id = self._active_session or ""
            if not session_id:
                return "Not connected to any session"
            await call_session(session_id, "daemon_detach", {})
            self._remove_session(session_id)
            return {"detached": session_id}

        if name == "iterm_rename_session":
            session_id = str(arguments.get("session", ""))
            new_name = str(arguments.get("name", ""))
            if not session_id or not new_name:
                return {"error": "session and name are required"}
            return await call_session(
                session_id, "iterm_rename_session", {"name": new_name},
            )

        if name == "iterm_focus_window":
            focus_target = self._resolve_session(arguments)
            if not focus_target:
                return "Not connected to any session"
            return await call_session(
                focus_target, "iterm_focus_window", {},
            )

        if name == "iterm_split_pane":
            connected = self._resolve_session(arguments)
            if not connected:
                return (
                    "Not connected to a session. "
                    "Use iterm_connect_session first."
                )
            vertical = bool(arguments.get("vertical", False))
            split_result = await call_session(
                connected, "iterm_split_pane", {"vertical": vertical},
            )
            if isinstance(split_result, dict) and "error" in split_result:
                return split_result
            iterm_session_id = str(split_result)
            pane_name = arguments.get("name")
            sid = _create_split_session(
                iterm_session_id,
                str(pane_name) if pane_name else None,
            )
            if sid:
                self._add_session(sid)
                return {
                    "created": sid,
                    "name": pane_name or "",
                    "iterm_session": iterm_session_id,
                    "parent_session": connected,
                    "connected": True,
                }
            return {"error": "Failed to create session for new pane"}

        if name == "iterm_stop_session":
            session_id = str(arguments.get("session", ""))
            close_window = bool(arguments.get("close_window", True))
            self._remove_session(session_id)
            return await stop_session(session_id, close_window=close_window)

        if name == "iterm_clean_sessions":
            count = clean_sessions()
            if count == 0:
                return "No stale sessions to clean"
            return f"Cleaned {count} stale session(s)"

        if name == "iterm_probe_environment":
            return _PROBE_SEQUENCE

        if name == "iterm_status":
            # Special-case: iterm_status without a session arg is a query
            # for "which session is active?" — return a clean null-style
            # response rather than the generic "Not connected" error that
            # other terminal tools return.
            status_target = self._resolve_session(arguments)
            if not status_target:
                return {
                    "active": False,
                    "hint": (
                        "No session is active. Use iterm_new_session to "
                        "create one, or iterm_connect_session to attach "
                        "to an existing session."
                    ),
                }
            return await call_session(status_target, "iterm_status", {})

        if name == "iterm_pipe":
            from_session = str(arguments.get("from_session", ""))
            to_session = str(arguments.get("to_session", ""))
            if not from_session or not to_session:
                return {
                    "error": "from_session and to_session are required",
                }

            read_params: dict[str, object] = {}
            since = arguments.get("since")
            lines = arguments.get("lines")
            if since is not None:
                read_params["since"] = since
            if lines is not None:
                read_params["lines"] = lines
            elif since is None:
                # First call with no `since` and no explicit `lines`:
                # establish a from-now cursor instead of dumping the
                # tail-50 backfill (banner, prompts, prior output) into
                # the target pane. Caller sets lines=N explicitly if
                # they want backfill.
                read_params["lines"] = 0

            read_result = await call_session(
                from_session, "iterm_read_output", read_params,
            )
            if isinstance(read_result, dict) and "error" in read_result:
                return read_result
            if not isinstance(read_result, dict):
                return {"error": f"unexpected read result: {read_result!r}"}

            stdout = str(read_result.get("stdout", ""))
            response: dict[str, object] = {
                "piped": True,
                "from_session": from_session,
                "to_session": to_session,
                "bytes": len(stdout.encode()),
                "cursor": read_result.get("cursor"),
            }
            warning = read_result.get("warning")
            if warning is not None:
                response["warning"] = warning

            if stdout:
                # Deque lines are rstripped; joining with "\n" leaves no
                # trailing newline. Append one so the target's stdin
                # consumer (cat -, jq, while read …) sees the final line
                # as complete rather than holding it until more bytes
                # arrive next call.
                type_result = await call_session(
                    to_session,
                    "iterm_type",
                    {"text": stdout + "\n"},
                )
                if isinstance(type_result, dict) and "error" in type_result:
                    return type_result

            return response

        # Terminal tools — resolve target session
        target = self._resolve_session(arguments)
        if not target:
            sessions = get_sessions()
            active = [s for s in sessions if s["status"] == "active"]
            if not active:
                return (
                    "No active sessions. "
                    "Use iterm_new_session to create one."
                )
            session_list = ", ".join(
                f"{s['id']} ({s.get('name', '')})"
                if s.get("name") else str(s["id"])
                for s in active
            )
            return (
                f"Not connected to a session. "
                f"Use iterm_connect_session first. "
                f"Available: {session_list}"
            )

        if name == "iterm_upload_file":
            local_path_arg = arguments.get("local_path")
            content_arg = arguments.get("content")
            if local_path_arg and content_arg:
                return {
                    "error": "specify either local_path or content, not both",
                }
            if not local_path_arg and not content_arg:
                return {
                    "error": "either local_path or content is required",
                }
            if local_path_arg:
                local_path = str(local_path_arg)
                if not os.path.isabs(local_path):
                    return {
                        "error": f"local_path must be an absolute path: {local_path}",
                    }
                path_obj = Path(local_path)
                if not path_obj.exists():
                    return {"error": f"local_path not found: {local_path}"}
                if path_obj.is_dir():
                    return {"error": f"local_path is a directory: {local_path}"}
                if not path_obj.is_file():
                    return {
                        "error": f"local_path is not a regular file: {local_path}",
                    }
                try:
                    data = path_obj.read_bytes()
                except PermissionError:
                    return {
                        "error": f"permission denied reading local_path: {local_path}",
                    }
                except OSError as exc:
                    return {"error": f"failed to read local_path: {exc}"}

                forwarded = {
                    k: v for k, v in arguments.items() if k != "local_path"
                }
                forwarded["content"] = base64.b64encode(data).decode()
                forwarded["encoding"] = "base64"
                result = await call_session(target, name, forwarded)
                if isinstance(result, dict) and "error" in result:
                    return result
                return {
                    "uploaded": True,
                    "remote_path": str(arguments.get("path", "")),
                    "local_path": local_path,
                    "bytes": len(data),
                }

        return await call_session(target, name, arguments)
