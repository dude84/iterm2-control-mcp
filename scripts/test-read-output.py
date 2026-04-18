#!/usr/bin/env python3
"""Read recent terminal output from a session via socket.

Usage:
    test-read-output.py                      # auto-select session, 20 lines
    test-read-output.py 50                   # auto-select session, 50 lines
    test-read-output.py a1b2c3d4             # specific session, 20 lines
    test-read-output.py a1b2c3d4 50          # specific session, 50 lines
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
    print("Multiple sessions. Specify session ID:", file=sys.stderr)
    for s in active:
        print(f"  {s}", file=sys.stderr)
    sys.exit(1)


def parse_args() -> tuple[str | None, int]:
    args = sys.argv[1:]
    session_id = None
    lines = 20

    for arg in args:
        if arg.isdigit():
            lines = int(arg)
        elif arg.startswith("-"):
            print(f"Unknown flag: {arg}", file=sys.stderr)
            sys.exit(1)
        else:
            session_id = arg

    return session_id, lines


async def main() -> None:
    session_id, lines = parse_args()
    sock = get_sock_path(session_id)
    if not sock.exists():
        print(f"Socket not found: {sock}", file=sys.stderr)
        sys.exit(1)

    reader, writer = await asyncio.open_unix_connection(str(sock))
    req = Request(method="iterm_read_output", params={"lines": lines}, id=1)
    writer.write(req.to_json().encode() + b"\n")
    await writer.drain()

    line = await reader.readline()
    data = json.loads(line)
    if "error" in data and data["error"]:
        print(f"Error: {data['error']}", file=sys.stderr)
    else:
        print(data.get("result", ""))

    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
