"""Backwards-compat shim for the old single-module layout.

`mcp/tools.py` used to hold everything — Tool definitions, session-pool
helpers, and the ToolHandler class. In v0.27.0 we split it into
`schemas.py`, `session_pool.py`, and `handler.py` (audit C-2). This
module re-exports the public names so existing imports keep working:

    from iterm2_control_mcp.mcp.tools import ToolHandler, SESSION_TOOLS

New code should import from the split modules directly.
"""

from __future__ import annotations

from iterm2_control_mcp.mcp.handler import ToolHandler
from iterm2_control_mcp.mcp.schemas import (
    _DESTRUCTIVE,
    _PROBE_SEQUENCE,
    _READONLY,
    FILE_TOOLS,
    SESSION_TOOLS,
)
from iterm2_control_mcp.mcp.session_pool import (
    _create_split_session,
    _parse_cli_session_json,
    clean_sessions,
    create_new_session,
    get_sessions,
    stop_session,
)

__all__ = [
    "FILE_TOOLS",
    "SESSION_TOOLS",
    "ToolHandler",
    "_DESTRUCTIVE",
    "_PROBE_SEQUENCE",
    "_READONLY",
    "_create_split_session",
    "_parse_cli_session_json",
    "clean_sessions",
    "create_new_session",
    "get_sessions",
    "stop_session",
]
