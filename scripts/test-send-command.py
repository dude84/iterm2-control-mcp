#!/usr/bin/env python3
"""Send a command to a session via socket.

Usage:
    test-send-command.py ls -la                    # auto-select session
    test-send-command.py a1b2c3d4 ls -la           # specific session
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iterm2_control_mcp.cowork.protocol import Request

SESSIONS_DIR = Path.home() / ".iterm2-control-mcp" / "sessions"


def get_sock_path(session_id: str | None) -> Path:
    if session_id:
        return SESSIONS_DIR / session_id / "session.sock"
    active = [
        d.name for d in sorted(SESSIONS_DIR.iterdir())
        if d.is_dir() and (d / "session.sock").exists()
    ] if SESSIONS_DIR.exists() else []
    if len(active) == 1:
        return SESSIONS_DIR / active[0] / "session.sock"
    if not active:
        print("No active sessions", file=sys.stderr)
        sys.exit(1)
    print("Multiple sessions. Pass session ID as first arg:", file=sys.stderr)
    for s in active:
        print(f"  {s}", file=sys.stderr)
    sys.exit(1)


def parse_args() -> tuple[str | None, str]:
    args = sys.argv[1:]
    if not args:
        print("Usage: test-send-command.py [session-id] <command...>", file=sys.stderr)
        sys.exit(1)

    session_dir = SESSIONS_DIR / args[0]
    if session_dir.exists() and len(args) > 1:
        return args[0], " ".join(args[1:])

    return None, " ".join(args)


async def main() -> None:
    session_id, command = parse_args()
    sock = get_sock_path(session_id)
    if not sock.exists():
        print(f"Socket not found: {sock}", file=sys.stderr)
        sys.exit(1)

    reader, writer = await asyncio.open_unix_connection(str(sock))
    req = Request(method="iterm_send_command", params={"command": command}, id=1)
    writer.write(req.to_json().encode() + b"\n")
    await writer.drain()

    line = await reader.readline()
    data = json.loads(line)
    if "error" in data and data["error"]:
        err = data["error"]
        msg = err.get("message", err) if isinstance(err, dict) else err
        print(f"Error: {msg}", file=sys.stderr)
    else:
        print(data.get("result", ""))

    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
