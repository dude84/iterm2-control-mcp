#!/usr/bin/env python3
"""Smoke-test iterm_pipe directly against two running session daemons.

Usage:
    .venv/bin/python scripts/test-pipe.py <from_session> <to_session> [--loop N]

One-shot: `scripts/test-pipe.py src dst`
Loop:     `scripts/test-pipe.py src dst --loop 8` — calls iterm_pipe 8 times,
          300 ms apart, passing the returned cursor back as `since` each time.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from iterm2_control_mcp.mcp.handler import ToolHandler


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("from_session")
    ap.add_argument("to_session")
    ap.add_argument("--loop", type=int, default=1)
    ap.add_argument("--sleep", type=float, default=0.3)
    args = ap.parse_args()

    handler = ToolHandler()
    cursor: int | None = None
    for i in range(args.loop):
        params: dict[str, object] = {
            "from_session": args.from_session,
            "to_session": args.to_session,
        }
        if cursor is not None:
            params["since"] = cursor
        result = await handler.handle("iterm_pipe", params)
        print(f"[{i}] {result}")
        if isinstance(result, dict):
            if "error" in result:
                return 1
            cursor = result.get("cursor") if isinstance(result.get("cursor"), int) else cursor
        if i + 1 < args.loop:
            await asyncio.sleep(args.sleep)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
