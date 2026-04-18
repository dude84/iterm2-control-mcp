from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iterm2_control_mcp.cowork.protocol import ProtocolError, Request, Response
from iterm2_control_mcp.terminal.session import SessionObserver

# Methods that represent session activity; a timestamp is written to the
# session's activity file on every matching call so iterm_list_sessions can
# surface last_activity_at without polling each daemon.
_ACTIVITY_METHODS = frozenset({
    "iterm_send_command",
    "iterm_send_and_read",
    "iterm_send_keys",
    "iterm_type",
    "iterm_read_output",
    "iterm_capture_screen",
    "iterm_upload_file",
    "iterm_download_file",
})

logger = logging.getLogger(__name__)

# asyncio StreamReader default line buffer is 64 KB, which truncates file
# uploads (a 500 KB binary becomes ~700 KB of base64 in the JSON request
# line). Bump well above realistic single-file payloads.
RPC_LINE_LIMIT = 16 * 1024 * 1024

AttachCallback = Callable[[str], Coroutine[Any, Any, str]]
DetachCallback = Callable[[], Coroutine[Any, Any, str]]
RenameCallback = Callable[[str], Coroutine[Any, Any, str]]
FocusCallback = Callable[[], Coroutine[Any, Any, str]]
SplitCallback = Callable[[bool], Coroutine[Any, Any, str]]
StopCallback = Callable[[bool], Coroutine[Any, Any, str]]


class DaemonHandler:
    def __init__(
        self,
        observer: SessionObserver,
        session_id: str,
        start_time: datetime,
        name: str | None = None,
        activity_path: Path | None = None,
    ) -> None:
        self._observer: SessionObserver | None = observer
        self._session_id = session_id
        self._name = name
        self._activity_path = activity_path
        self._start_time = start_time
        self._attach_callback: AttachCallback | None = None
        self._detach_callback: DetachCallback | None = None
        self._rename_callback: RenameCallback | None = None
        self._focus_callback: FocusCallback | None = None
        self._split_callback: SplitCallback | None = None
        self._stop_callback: StopCallback | None = None

    def set_observer(self, observer: SessionObserver | None) -> None:
        self._observer = observer

    def set_name(self, name: str | None) -> None:
        self._name = name

    def _session_identity(self) -> dict[str, object]:
        return {"id": self._session_id, "name": self._name or ""}

    def _stamp_activity(self) -> None:
        if self._activity_path is None:
            return
        try:
            self._activity_path.write_text(
                datetime.now(UTC).isoformat(),
            )
            # Activity file may not exist yet on first stamp; chmod after write.
            try:
                self._activity_path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            logger.debug("failed to stamp activity", exc_info=True)

    def set_lifecycle_callbacks(
        self,
        attach_cb: AttachCallback,
        detach_cb: DetachCallback,
    ) -> None:
        self._attach_callback = attach_cb
        self._detach_callback = detach_cb

    def set_rename_callback(self, cb: RenameCallback) -> None:
        self._rename_callback = cb

    def set_focus_callback(self, cb: FocusCallback) -> None:
        self._focus_callback = cb

    def set_split_callback(self, cb: SplitCallback) -> None:
        self._split_callback = cb

    def set_stop_callback(self, cb: StopCallback) -> None:
        self._stop_callback = cb

    async def handle(self, request: Request) -> Response:
        method = request.method
        params = request.params
        req_id = request.id

        if method in _ACTIVITY_METHODS:
            self._stamp_activity()

        try:
            if method == "daemon_attach":
                if self._attach_callback is None:
                    return Response(id=req_id, error="attach not supported")
                iterm_session_id = str(params.get("iterm_session_id", ""))
                if not iterm_session_id:
                    return Response(id=req_id, error="iterm_session_id is required")
                attach_result = await self._attach_callback(iterm_session_id)
                return Response(id=req_id, result=attach_result)

            if method == "daemon_detach":
                if self._detach_callback is None:
                    return Response(id=req_id, error="detach not supported")
                if self._observer is None:
                    return Response(id=req_id, error="already detached")
                detach_result = await self._detach_callback()
                return Response(id=req_id, result=detach_result)

            if method == "iterm_read_output":
                if self._observer is None:
                    return Response(id=req_id, error="session is detached")
                wait_for = params.get("wait_for")
                if wait_for:
                    await self._observer.wait_for_text(
                        str(wait_for), timeout=10.0,
                    )
                lines = int(str(params.get("lines", 50)))
                since_raw = params.get("since")
                since_val: int | None = None
                if since_raw is not None:
                    try:
                        since_val = int(str(since_raw))
                    except (TypeError, ValueError):
                        return Response(
                            id=req_id,
                            error="since must be an integer cursor",
                        )
                # Force a pull-refresh of the observer's buffer from the
                # pane's current state. iTerm2's push-based screen
                # streamer can be quiet for seconds at a time on an
                # inactive pane — without this, read_since would return
                # stale data until the streamer next fires. The refresh
                # is cheap (one async_get_screen_contents) and runs
                # through the same dedup pipeline as the streamer.
                await self._observer.refresh_from_screen()
                read_result = self._observer.read_since(
                    since=since_val, lines=lines,
                )
                read_result["session"] = self._session_identity()
                if params.get("clear"):
                    self._observer.clear_buffer()
                return Response(id=req_id, result=read_result)

            if method == "iterm_send_command":
                if self._observer is None:
                    return Response(id=req_id, error="session is detached")
                command = str(params.get("command", ""))
                if not command:
                    return Response(id=req_id, error="command is required")
                logger.info("Command: %s", command)
                # Capture the cursor BEFORE sending so any output the
                # command produces is past this watermark. Callers that
                # want the command's output without racing its leading
                # edge pass this cursor as `since` to a subsequent
                # iterm_read_output call.
                await self._observer.refresh_from_screen()
                pre_send_cursor = self._observer.line_cursor
                await self._observer.send_command(command)
                return Response(id=req_id, result={
                    "status": "executed",
                    "session": self._session_identity(),
                    "cursor": pre_send_cursor,
                })

            if method == "iterm_send_and_read":
                if self._observer is None:
                    return Response(id=req_id, error="session is detached")
                command = str(params.get("command", ""))
                if not command:
                    return Response(id=req_id, error="command is required")
                timeout = float(str(params.get("timeout", 5)))
                quiet_raw = params.get("quiet_ms")
                max_wait_raw = params.get("max_wait")
                quiet_ms = (
                    float(str(quiet_raw)) if quiet_raw is not None else None
                )
                max_wait = (
                    float(str(max_wait_raw))
                    if max_wait_raw is not None
                    else None
                )
                logger.info("Command (send_and_read): %s", command)
                sar_output = await self._observer.send_and_read(
                    command, timeout, quiet_ms=quiet_ms, max_wait=max_wait,
                )
                sar_output["session"] = self._session_identity()
                sar_output["shell_integration"] = (
                    self._observer.shell_integration
                )
                return Response(id=req_id, result=sar_output)

            if method == "iterm_send_keys":
                if self._observer is None:
                    return Response(id=req_id, error="session is detached")
                keys = str(params.get("keys", ""))
                if not keys:
                    return Response(id=req_id, error="keys is required")
                logger.info("Keys: %s", keys)
                await self._observer.send_keys(keys)
                return Response(id=req_id, result="sent")

            if method == "iterm_type":
                if self._observer is None:
                    return Response(id=req_id, error="session is detached")
                text = params.get("text")
                if not isinstance(text, str) or text == "":
                    return Response(id=req_id, error="text is required")
                logger.info("Type: %r", text)
                await self._observer.type_text(text)
                return Response(id=req_id, result={
                    "status": "typed",
                    "session": self._session_identity(),
                })

            if method == "iterm_rename_session":
                if self._rename_callback is None:
                    return Response(id=req_id, error="rename not supported")
                new_name = str(params.get("name", ""))
                if not new_name:
                    return Response(id=req_id, error="name is required")
                rename_result = await self._rename_callback(new_name)
                return Response(id=req_id, result=rename_result)

            if method == "iterm_focus_window":
                if self._focus_callback is None:
                    return Response(id=req_id, error="focus not supported")
                if self._observer is None:
                    return Response(id=req_id, error="session is detached")
                focus_result = await self._focus_callback()
                return Response(id=req_id, result=focus_result)

            if method == "iterm_split_pane":
                if self._split_callback is None:
                    return Response(id=req_id, error="split not supported")
                if self._observer is None:
                    return Response(id=req_id, error="session is detached")
                vertical = bool(params.get("vertical", False))
                split_result = await self._split_callback(vertical)
                return Response(id=req_id, result=split_result)

            if method == "daemon_stop":
                if self._stop_callback is None:
                    return Response(id=req_id, error="stop not supported")
                close_window = bool(params.get("close_window", True))
                stop_result = await self._stop_callback(close_window)
                return Response(id=req_id, result=stop_result)

            if method == "iterm_download_file":
                if self._observer is None:
                    return Response(id=req_id, error="session is detached")
                path = str(params.get("path", ""))
                if not path:
                    return Response(id=req_id, error="path is required")
                filename = await self._observer.download_file(path)
                return Response(id=req_id, result={
                    "downloaded": filename,
                    "location": f"~/Downloads/{filename}",
                })

            if method == "iterm_upload_file":
                if self._observer is None:
                    return Response(id=req_id, error="session is detached")
                path = str(params.get("path", ""))
                content = str(params.get("content", ""))
                encoding = str(params.get("encoding", "text"))
                if not path:
                    return Response(id=req_id, error="path is required")
                if not content:
                    return Response(id=req_id, error="content is required")
                try:
                    await self._observer.upload_file(path, content, encoding)
                except ValueError as exc:
                    return Response(id=req_id, error=str(exc))
                return Response(id=req_id, result="uploaded")

            if method == "iterm_capture_screen":
                if self._observer is None:
                    return Response(id=req_id, error="session is detached")
                screen = await self._observer.capture_screen()
                return Response(id=req_id, result=screen)

            if method == "iterm_status":
                uptime = datetime.now(UTC) - self._start_time
                attached = self._observer is not None
                status_info: dict[str, object] = {
                    "session_id": self._session_id,
                    "attached": attached,
                    "uptime_seconds": int(uptime.total_seconds()),
                }
                if self._observer is not None:
                    status_info["iterm_session"] = self._observer.session_id
                    status_info["buffer_lines"] = len(self._observer.recent_output)
                return Response(id=req_id, result=status_info)

            return Response(id=req_id, error=f"unknown method: {method}")

        except Exception as e:
            logger.exception("Handler error for %s", method)
            return Response(id=req_id, error=str(e))


class CoworkServer:
    def __init__(self, sock_path: Path, handler: DaemonHandler) -> None:
        self._sock_path = sock_path
        self._handler = handler
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._sock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._sock_path.exists():
            self._sock_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._client_connected,
            path=str(self._sock_path),
            limit=RPC_LINE_LIMIT,
        )
        os.chmod(self._sock_path, 0o600)
        logger.info("Cowork server listening on %s", self._sock_path)

    async def _client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        logger.debug("Client connected")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    request = Request.from_json(line.decode())
                except ProtocolError as exc:
                    # Malformed wire message — write back a structured
                    # error so the client sees a clear reason instead of
                    # timing out on the read.
                    logger.warning("Protocol error: %s", exc)
                    err_resp = Response(id=None, error=f"protocol: {exc}")
                    try:
                        writer.write(err_resp.to_json().encode() + b"\n")
                        await writer.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    continue
                try:
                    response = await self._handler.handle(request)
                    writer.write(response.to_json().encode() + b"\n")
                    await writer.drain()
                except Exception:
                    logger.exception("Error handling request")
        finally:
            writer.close()
            await writer.wait_closed()
            logger.debug("Client disconnected")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._sock_path.exists():
            self._sock_path.unlink()
