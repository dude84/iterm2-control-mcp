"""Tool and annotation schemas + probe sequence.

Pure data module. No subprocess, no socket, no session management —
just the Tool() definitions the MCP server advertises.
"""

from __future__ import annotations

from mcp.types import Tool, ToolAnnotations

_PROBE_SEQUENCE = {
    "instructions": (
        "Run these commands one at a time using iterm_send_and_read. "
        "All are passive and read-only. Parse the output to build "
        "a picture of the environment. Start with identity, then "
        "expand based on what you find."
    ),
    "commands": {
        "identity": [
            {"cmd": "uname -a", "purpose": "kernel, arch, OS type"},
            {"cmd": "hostname", "purpose": "machine name"},
            {
                "cmd": "cat /etc/os-release 2>/dev/null || sw_vers 2>/dev/null",
                "purpose": "distro (Linux) or macOS version",
            },
            {
                "cmd": "cat /proc/version 2>/dev/null",
                "purpose": "kernel detail, WSL detection",
            },
        ],
        "user": [
            {"cmd": "id", "purpose": "UID, GID, groups"},
            {
                "cmd": "sudo -n true 2>/dev/null && echo HAS_SUDO || echo NO_SUDO",
                "purpose": "passwordless sudo check (non-interactive)",
            },
            {"cmd": "echo $SHELL", "purpose": "current shell"},
            {"cmd": "echo $HOME && echo $PWD", "purpose": "home and cwd"},
        ],
        "system": [
            {"cmd": "uptime", "purpose": "uptime and load average"},
            {
                "cmd": "nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null",
                "purpose": "CPU count",
            },
            {
                "cmd": "free -h 2>/dev/null || vm_stat 2>/dev/null",
                "purpose": "memory",
            },
            {"cmd": "df -h / 2>/dev/null", "purpose": "root disk usage"},
        ],
        "network": [
            {
                "cmd": "ip addr 2>/dev/null || ifconfig 2>/dev/null",
                "purpose": "IP addresses and interfaces",
            },
            {
                "cmd": "ip route 2>/dev/null | grep default",
                "purpose": "default gateway",
            },
            {
                "cmd": "cat /etc/resolv.conf 2>/dev/null | grep nameserver",
                "purpose": "DNS servers",
            },
            {
                "cmd": "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null",
                "purpose": "listening ports",
            },
            {"cmd": "echo $SSH_CONNECTION", "purpose": "SSH session info"},
        ],
        "tools": [
            {
                "cmd": (
                    "which python3 python node java gcc docker"
                    " kubectl git ssh curl wget make 2>/dev/null"
                ),
                "purpose": "available tools",
            },
        ],
        "virtualization": [
            {
                "cmd": (
                    "systemd-detect-virt 2>/dev/null; "
                    "test -f /.dockerenv && echo docker; "
                    "test -f /run/.containerenv && echo podman"
                ),
                "purpose": "VM or container detection",
            },
        ],
        "security": [
            {"cmd": "getenforce 2>/dev/null", "purpose": "SELinux status"},
            {"cmd": "w 2>/dev/null | head -5", "purpose": "other logged-in users"},
        ],
    },
}

_READONLY = ToolAnnotations(readOnlyHint=True)
_DESTRUCTIVE = ToolAnnotations(destructiveHint=True)

SESSION_TOOLS = [
    Tool(
        name="iterm_list_sessions",
        description=(
            "List all terminal sessions on this machine. Returns an "
            "array of objects with keys: `id`, `name`, `pid`, "
            "`started_at`, `last_activity_at`, `status` "
            "(`active` | `stale`). Sessions are shown regardless of "
            "whether the MCP server has them in its connection pool — "
            "use this to discover sessions created by another client "
            "(CLI, Desktop, another Claude Code instance). "
            "This does NOT tell you which session is the *active* one "
            "for tool calls without an explicit `session` arg — use "
            "`iterm_status` for that."
        ),
        inputSchema={"type": "object", "properties": {}},
        annotations=_READONLY,
    ),
    Tool(
        name="iterm_new_session",
        description=(
            "Open a new iTerm2 terminal window and start a session. "
            "Auto-connects and sets the new session as the active "
            "target — do NOT call `iterm_connect_session` after this. "
            "A new session opens on your LOCAL machine (the Mac running "
            "iTerm2). The user can type directly in the terminal at any "
            "time — SSH into machines, run commands, answer prompts. "
            "Any SSH connections, docker exec, or navigation you see in "
            "the buffer was done by the user, not by session setup. "
            "Do not speculate about how the terminal reached its current "
            "state. Report what you observe and ask the user for context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Friendly name for the session (optional)",
                },
            },
        },
    ),
    Tool(
        name="iterm_connect_session",
        description=(
            "Attach the MCP connection pool to an existing session and "
            "make it the active target. "
            "\n\n"
            "WHEN TO USE:\n"
            "- Resume work on a session after `iterm_detach_session` "
            "or after a fresh Claude Desktop/Code restart.\n"
            "- Switch the active target between multiple running "
            "sessions.\n"
            "- Pick up a session another client (CLI, another agent) "
            "created.\n"
            "\n"
            "Not strictly required for every call — terminal tools "
            "accept an explicit `session` arg to target any session. "
            "But for multi-call workflows on one session, calling this "
            "once up front avoids passing `session` on every tool call."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "Session ID to connect to",
                },
            },
            "required": ["session"],
        },
    ),
    Tool(
        name="iterm_detach_session",
        description=(
            "Release the iTerm2 terminal from a session while keeping "
            "the session daemon alive. After detach, the terminal is "
            "freed for other use; the session can be reattached later "
            "via `iterm_connect_session`. "
            "\n\n"
            "WHEN TO USE A DIFFERENT TOOL:\n"
            "- Use `iterm_stop_session` if you want to fully destroy "
            "the session (daemon and all). Detach is the niche case "
            "where you plan to come back to this exact session later."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": (
                        "Session ID to detach (default: active session)"
                    ),
                },
            },
        },
    ),
    Tool(
        name="iterm_stop_session",
        description=(
            "Stop and destroy a session — kills the daemon, removes the "
            "socket and session dir. By default also closes the iTerm2 "
            "pane (if that pane was the only one in its window, the "
            "window closes too). Pass `close_window=false` to keep the "
            "pane open with a `[stopped]` title (useful when the user "
            "wants to inspect the final state). "
            "Note: the `close_window` flag is historical — it actually "
            "closes the pane, which may or may not close the window "
            "depending on whether other panes exist."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "Session ID to stop",
                },
                "close_window": {
                    "type": "boolean",
                    "description": (
                        "Close the iTerm2 pane when stopping. If this is "
                        "the only pane in the window, the window closes "
                        "too. Default: true."
                    ),
                    "default": True,
                },
            },
            "required": ["session"],
        },
        annotations=_DESTRUCTIVE,
    ),
    Tool(
        name="iterm_rename_session",
        description=(
            "Rename an active session. Updates the session's name in "
            "`meta.json` and sets the iTerm2 window title to match. "
            "Both `session` and `name` are mandatory (rename is an "
            "explicit operation — there is no default session for this "
            "tool)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "Session ID or name to rename",
                },
                "name": {
                    "type": "string",
                    "description": "New name for the session",
                },
            },
            "required": ["session", "name"],
        },
    ),
    Tool(
        name="iterm_focus_window",
        description=(
            "Bring a session's iTerm2 window to the front and give it "
            "keyboard focus. Use when you want the user to look at a "
            "specific session (e.g. after starting a long process)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "Target session ID (default: active session)",
                },
            },
        },
    ),
    Tool(
        name="iterm_split_pane",
        description=(
            "Split the active session's terminal into two panes. "
            "Creates a new iterm2-control-mcp session for the new pane "
            "and auto-connects it as the active target — do NOT call "
            "`iterm_connect_session` after this. The parent session "
            "stays running in its original pane."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Friendly name for the new pane session (optional)",
                },
                "vertical": {
                    "type": "boolean",
                    "description": (
                        "Split vertically (side-by-side) instead of "
                        "horizontally (top-bottom). Default: false"
                    ),
                    "default": False,
                },
            },
        },
    ),
    Tool(
        name="iterm_clean_sessions",
        description=(
            "Remove stale session directories where the daemon is no "
            "longer running. Safe to run at any time — live sessions "
            "are left alone, only ones with a dead PID are cleaned."
        ),
        inputSchema={"type": "object", "properties": {}},
        annotations=_DESTRUCTIVE,
    ),
    Tool(
        name="iterm_send_command",
        description=(
            "Type and press Enter for a shell command — fire-and-forget. "
            "Output is not returned from this call; read it with "
            "iterm_read_output or iterm_capture_screen afterwards. "
            "\n\n"
            "WHEN TO USE:\n"
            "- You are starting a long-running process (tail -f, server, "
            "watch loop) and will observe output with later reads.\n"
            "- You don't need the output at all.\n"
            "\n"
            "WHEN TO USE A DIFFERENT TOOL:\n"
            "- Use `iterm_send_and_read` for the common case — send a "
            "command AND get its output back in one call.\n"
            "- Use `iterm_type` to stage a command for the user to "
            "review without executing.\n"
            "\n"
            "If no new output appears after a send, the command may "
            "still be running — use iterm_capture_screen to check if "
            "the shell prompt has returned. "
            "DO NOT embed multi-line content, heredocs, or blobs "
            "larger than ~200 chars inside 'command' — the interactive "
            "shell line editor mangles them. Use iterm_upload_file with "
            "'content=' to stage a file on the remote first, then "
            "reference that file in the command."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to execute",
                },
                "session": {
                    "type": "string",
                    "description": "Target session ID (default: active session)",
                },
            },
            "required": ["command"],
        },
        annotations=_DESTRUCTIVE,
    ),
    Tool(
        name="iterm_send_and_read",
        description=(
            "Send a command and return its output in one call. This is "
            "the usual choice for running a command and seeing the "
            "result. Returns "
            "{ stdout, exit_code, cursor, session, shell_integration }. "
            "'exit_code' is populated when the remote has iTerm2 shell "
            "integration (OSC 133) installed — otherwise null. "
            "\n\n"
            "WHEN TO USE A DIFFERENT TOOL:\n"
            "- Use `iterm_send_command` only for fire-and-forget "
            "(starting a long-running process you'll tail later).\n"
            "- Use `iterm_type` to stage a command for user review "
            "without executing.\n"
            "\n"
            "IMPORTANT: This is a shared interactive terminal. The user "
            "types commands directly in the window at any time — SSH, "
            "docker exec, answer prompts, navigate directories. Output "
            "will contain their activity mixed with yours. When you see "
            "commands you didn't send, the user typed them. Report "
            "observations, don't speculate about how the terminal "
            "reached its current state. "
            "DO NOT embed multi-line content, heredocs, or blobs larger "
            "than ~200 chars inside 'command' — use iterm_upload_file "
            "with 'content=' to stage the file first."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to execute",
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        "Seconds to wait for output (default 5). Used as "
                        "max_wait when max_wait is not explicitly set."
                    ),
                    "default": 5,
                },
                "quiet_ms": {
                    "type": "number",
                    "description": (
                        "Fallback path only (no shell integration). "
                        "Milliseconds of PTY quiescence before we declare "
                        "the command complete. Default 800. Raise to "
                        "~2000 if the iTerm2 window may be backgrounded "
                        "(iTerm2 throttles its streamer to 1 Hz for "
                        "background windows)."
                    ),
                    "default": 800,
                },
                "max_wait": {
                    "type": "number",
                    "description": (
                        "Fallback path only. Hard cap on total wait "
                        "time, independent of quiet_ms. Prevents a "
                        "runaway command (yes, tail -f) from hanging "
                        "the call. Defaults to `timeout`."
                    ),
                },
                "session": {
                    "type": "string",
                    "description": "Target session ID (default: active session)",
                },
            },
            "required": ["command"],
        },
        annotations=_DESTRUCTIVE,
    ),
    Tool(
        name="iterm_send_keys",
        description=(
            "Send special keys — control characters and arrows. Use "
            "for interactive programs, REPLs, SSH sessions, or to "
            "interrupt running commands (ctrl-c). "
            "\n\n"
            "Supported keys (exact strings): ctrl-c, ctrl-d, ctrl-z, "
            "ctrl-l, enter, tab, escape, backspace, delete, up, down, "
            "left, right, home, end. Unknown keys return an error."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "keys": {
                    "type": "string",
                    "enum": [
                        "ctrl-c", "ctrl-d", "ctrl-z", "ctrl-l",
                        "enter", "tab", "escape", "backspace", "delete",
                        "up", "down", "left", "right", "home", "end",
                    ],
                    "description": "Key to send",
                },
                "session": {
                    "type": "string",
                    "description": "Target session ID (default: active session)",
                },
            },
            "required": ["keys"],
        },
        annotations=_DESTRUCTIVE,
    ),
    Tool(
        name="iterm_type",
        description=(
            "Type text into the terminal WITHOUT pressing Enter. The text "
            "lands on the prompt line and waits for the user to review "
            "and press Enter themselves. "
            "\n\n"
            "WHEN TO USE:\n"
            "- Stage a command for user review before any side effect.\n"
            "- Pre-fill partial input (e.g. a flag or filename) and let "
            "the user finish it.\n"
            "- Interact with TUIs that read single keystrokes or partial "
            "input before a confirmation step.\n"
            "\n"
            "WHEN TO USE A DIFFERENT TOOL:\n"
            "- Use `iterm_send_command` or `iterm_send_and_read` if you "
            "want the command to execute immediately.\n"
            "- Use `iterm_send_keys` for control characters (ctrl-c, "
            "tab, arrows, etc.).\n"
            "\n"
            "After typing you can follow up with `iterm_send_keys` "
            "using 'enter' to execute or 'tab' to trigger completion, "
            "or simply leave control with the user. "
            "Accepts any printable text, including whitespace."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": (
                        "Text to type into the terminal. No Enter is "
                        "appended; use iterm_send_keys('enter') to execute "
                        "if needed."
                    ),
                },
                "session": {
                    "type": "string",
                    "description": "Target session ID (default: active session)",
                },
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="iterm_read_output",
        description=(
            "Read recent terminal output from the connected session. "
            "Returns { stdout, cursor, session } and may include "
            "'warning' if a passed-in 'since' cursor is older than the "
            "ring buffer retains. Pass the returned 'cursor' back as "
            "'since' on the next call to get only new lines. "
            "\n\n"
            "WHEN TO USE:\n"
            "- Get the text of what happened in the terminal since a "
            "prior cursor (or last N lines).\n"
            "- Wait for a specific string to appear before acting.\n"
            "\n"
            "WHEN TO USE A DIFFERENT TOOL:\n"
            "- Use `iterm_capture_screen` when you need to know whether "
            "a command is still running (cursor position, whether the "
            "shell prompt is on the last line).\n"
            "- Use `iterm_capture_screen` for TUI apps where visual "
            "layout matters.\n"
            "\n"
            "IMPORTANT: This is a shared interactive terminal. The "
            "buffer contains ALL activity — yours and the user's. When "
            "you see commands or output you didn't produce, the user "
            "typed them. Report what you observe and ask for context if "
            "unclear. Optionally wait for specific text or clear the "
            "buffer."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "lines": {
                    "type": "integer",
                    "description": (
                        "Max number of trailing lines to return when "
                        "'since' is not provided (default 50)"
                    ),
                    "default": 50,
                },
                "since": {
                    "type": "integer",
                    "description": (
                        "Monotonic cursor returned by a prior "
                        "iterm_read_output or iterm_send_and_read call. "
                        "Only lines appended after that point are "
                        "returned. Overrides 'lines' when set."
                    ),
                },
                "wait_for": {
                    "type": "string",
                    "description": (
                        "Wait until this text appears before reading "
                        "(substring match, 10s timeout)"
                    ),
                },
                "clear": {
                    "type": "boolean",
                    "description": "Clear the buffer after reading",
                    "default": False,
                },
                "session": {
                    "type": "string",
                    "description": "Target session ID (default: active session)",
                },
            },
        },
        annotations=_READONLY,
    ),
    Tool(
        name="iterm_capture_screen",
        description=(
            "Atomic snapshot of the visible terminal screen with cursor "
            "position and dimensions. "
            "\n\n"
            "WHEN TO USE:\n"
            "- Check whether a command is still running (shell prompt "
            "on the last line = idle; otherwise something is running).\n"
            "- TUI apps where visual layout matters (vim, htop, menus).\n"
            "- Read cursor coordinates or screen dimensions.\n"
            "\n"
            "WHEN TO USE A DIFFERENT TOOL:\n"
            "- Use `iterm_read_output` to read scrollback / ring-buffer "
            "text with cursor-based slicing; `capture_screen` only "
            "returns what is currently visible.\n"
            "\n"
            "The screen may show user-typed activity — report "
            "observations, don't speculate about how the terminal "
            "reached its state."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "Target session ID (default: active session)",
                },
            },
        },
        annotations=_READONLY,
    ),
    Tool(
        name="iterm_probe_environment",
        description=(
            "Returns a list of passive, read-only commands for the agent "
            "to run via iterm_send_and_read — this tool does NOT run "
            "them itself, it just returns the sequence. The commands "
            "fingerprint OS, user, network, available tools, "
            "virtualization, and basic security posture. Use when "
            "connecting to an unknown machine and you want a consistent "
            "discovery pass. All returned commands are safe to execute "
            "(no writes, installs, or modifications)."
        ),
        inputSchema={"type": "object", "properties": {}},
        annotations=_READONLY,
    ),
    Tool(
        name="iterm_status",
        description=(
            "Return info about a session: ID, attached state, uptime, "
            "buffer size, iTerm2 session id. "
            "\n\n"
            "Called with no `session` argument, this reports on the "
            "*active session* — the one that terminal tools (send, "
            "read, capture, etc.) target by default. If no session is "
            "active, returns `{\"active\": false, ...}` with a hint on "
            "how to create or connect one. Use this to answer 'which "
            "session will my next call go to?' and to confirm the "
            "daemon is responsive after errors."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "Target session ID (default: active session)",
                },
            },
        },
        annotations=_READONLY,
    ),
]

FILE_TOOLS = [
    Tool(
        name="iterm_download_file",
        description=(
            "Download a file from the remote shell to your Mac via "
            "iTerm2's OSC 1337 escape sequence. "
            "\n\n"
            "**Always saves to `~/Downloads/<basename>`. The destination "
            "is not configurable** — iTerm2 decides. If a file with the "
            "same name already exists, iTerm2 appends a counter "
            "(`hosts`, `hosts 2`, `hosts 3`, …). Rename afterwards if "
            "you need a specific path. "
            "\n\n"
            "Remote must have `base64`, `wc`, `printf` on PATH — "
            "universal on Linux/macOS/busybox. Use for files too large "
            "to return via terminal output."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file on the remote system",
                },
                "session": {
                    "type": "string",
                    "description": "Target session ID (default: active session)",
                },
            },
            "required": ["path"],
        },
        annotations=_DESTRUCTIVE,
    ),
    Tool(
        name="iterm_upload_file",
        description=(
            "Write a file to the remote system via base64 here-doc. Use "
            "this for multi-line content, config blobs, scripts, or "
            "anything too large to paste into a command line — do NOT "
            "try to heredoc or echo it through iterm_send_command. "
            "\n\n"
            "Exactly one of `local_path` or `content` must be set "
            "(JSON schema enforces this). "
            "\n"
            "- `local_path`: absolute path to a file already on this "
            "Mac. Preferred for binary files — the connector reads the "
            "bytes and handles encoding.\n"
            "- `content` with `encoding='text'` (default): UTF-8 text "
            "payload. The usual choice for config files and scripts.\n"
            "- `content` with `encoding='base64'`: pre-encoded bytes. "
            "Needed for binary payloads passed inline, because JSON "
            "strings cannot carry raw (non-UTF-8) bytes.\n"
            "\n"
            "Max payload ~12 MB (limited by the RPC line buffer after "
            "base64 inflation). Remote must have `base64` on PATH."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path for the file on the remote system",
                },
                "local_path": {
                    "type": "string",
                    "description": (
                        "Absolute path to a file on this Mac. When set, "
                        "the connector reads the file and uploads its "
                        "bytes directly. Mutually exclusive with 'content'."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Inline file content. With encoding='text' "
                        "(default) this is UTF-8 text. With "
                        "encoding='base64' this is the base64-encoded "
                        "representation of arbitrary bytes (needed for "
                        "binary, since JSON strings can't carry raw "
                        "non-UTF-8 bytes). Mutually exclusive with "
                        "'local_path'."
                    ),
                },
                "encoding": {
                    "type": "string",
                    "enum": ["text", "base64"],
                    "default": "text",
                    "description": (
                        "How to interpret 'content'. Ignored when "
                        "'local_path' is set (the connector always uses "
                        "base64 internally)."
                    ),
                },
                "session": {
                    "type": "string",
                    "description": "Target session ID (default: active session)",
                },
            },
            "required": ["path"],
            "oneOf": [
                {"required": ["local_path"]},
                {"required": ["content"]},
            ],
        },
        annotations=_DESTRUCTIVE,
    ),
]
