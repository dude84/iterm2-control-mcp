from __future__ import annotations

import asyncio
import json
from pathlib import Path

from iterm2_control_mcp.cowork.protocol import Request as RpcRequest

SESSIONS_DIR = Path.home() / ".iterm2-control-mcp" / "sessions"

# Must match cowork.server.RPC_LINE_LIMIT. Large enough to carry a file
# upload in a single JSON request line without hitting the asyncio
# StreamReader default 64 KB limit.
RPC_LINE_LIMIT = 16 * 1024 * 1024

# Default timeout for a full RPC round-trip (connect + write + read). File
# uploads/downloads type multi-megabyte payloads into the PTY and need
# longer; everything else is effectively instant.
_DEFAULT_RPC_TIMEOUT = 60.0
_LONG_RPC_METHODS = frozenset({"iterm_upload_file", "iterm_download_file"})
_LONG_RPC_TIMEOUT = 300.0


async def call_session(
    session_id: str,
    method: str,
    params: dict[str, object],
    timeout: float | None = None,
) -> object:
    sock_path = SESSIONS_DIR / session_id / "session.sock"
    if not sock_path.exists():
        return {"error": f"Session {session_id} not found or not running"}

    if timeout is None:
        timeout = (
            _LONG_RPC_TIMEOUT
            if method in _LONG_RPC_METHODS
            else _DEFAULT_RPC_TIMEOUT
        )

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(sock_path), limit=RPC_LINE_LIMIT),
            timeout=timeout,
        )
    except TimeoutError:
        return {"error": f"timed out connecting to session daemon ({timeout}s)"}

    try:
        try:
            req = RpcRequest(method=method, params=params, id=1)
            writer.write(req.to_json().encode() + b"\n")
            await asyncio.wait_for(writer.drain(), timeout=timeout)

            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except TimeoutError:
            return {
                "error": (
                    f"timed out waiting for session daemon response "
                    f"({timeout}s). The daemon may be wedged; try "
                    f"`iterm2-control-mcp stop <id>` and re-create."
                ),
            }
        except (BrokenPipeError, ConnectionResetError) as exc:
            return {
                "error": (
                    f"session daemon closed the connection before "
                    f"responding ({exc}). For uploads, this usually "
                    f"means the payload exceeded the RPC line buffer."
                ),
            }
        except ValueError as exc:
            # asyncio.StreamReader raises ValueError when readline's
            # limit is exceeded — surface as a clear error instead of
            # propagating a raw exception to the MCP host.
            return {"error": f"RPC line buffer exceeded: {exc}"}

        if not line:
            return {"error": "Session closed connection"}

        data = json.loads(line.decode())
        if "error" in data and data["error"]:
            err = data["error"]
            if isinstance(err, dict):
                return {"error": err.get("message", str(err))}
            return {"error": str(err)}
        return data.get("result", {})
    finally:
        writer.close()
        await writer.wait_closed()
