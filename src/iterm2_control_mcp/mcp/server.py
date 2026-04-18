from __future__ import annotations

import json
import logging
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from iterm2_control_mcp.mcp.handler import ToolHandler
from iterm2_control_mcp.mcp.schemas import FILE_TOOLS, SESSION_TOOLS

logger = logging.getLogger(__name__)


def create_server() -> Server:
    server = Server("iterm2-control-mcp")
    handler = ToolHandler(server=server)

    # Backward compat: if --session is passed, auto-connect
    for i, arg in enumerate(sys.argv):
        if arg == "--session" and i + 1 < len(sys.argv):
            handler._add_session(sys.argv[i + 1])
        elif arg.startswith("--session="):
            handler._add_session(arg.split("=", 1)[1])

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [*SESSION_TOOLS, *FILE_TOOLS]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:  # type: ignore[type-arg]
        result = await handler.handle(name, arguments)
        if isinstance(result, str):
            text = result
        else:
            text = json.dumps(result, indent=2)
        return [TextContent(type="text", text=text)]

    return server


async def run_mcp() -> None:
    server = create_server()
    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options)


def main() -> None:
    import asyncio

    asyncio.run(run_mcp())
