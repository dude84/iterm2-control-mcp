# iterm2-control-mcp

> **⚠️ Disclaimer.** This software gives an AI agent **full shell access** to your terminal. It can execute any command your user account can run, including destructive ones. **There is no regex-based safety net** — the visible terminal and your attention *are* the safeguards. Do not run this on production systems unless you fully understand the risks and are actively watching the session. The authors are not liable for damages, data loss, or any other consequences. See [SECURITY.md](SECURITY.md).

Give AI agents their own interactive terminal sessions. iterm2-control-mcp opens a dedicated, themed iTerm2 window that Claude can see and type into — a live shell session the AI controls while you watch and can interact with. Unlike built-in terminal access, you see exactly what the AI sees and types; sessions persist across conversations and support any interactive program, not just one-shot commands.

**The daemon is the eyes and hands; the caller brings the brain.** Any MCP-compatible client (Claude Desktop, Claude Code, Cursor, Cowork) can connect.

## Why not just use Claude Code's terminal?

| | Claude Code built-in | iterm2-control-mcp |
|---|---|---|
| **Visibility** | Commands run in the background | Dedicated iTerm2 window — watch in real-time |
| **Interactive programs** | One-shot commands only | Full interactive shell — prompts, passwords, menus, REPLs |
| **Shared control** | AI only | AI and user share the same terminal — SSH, docker exec, prompts any time |
| **Session persistence** | Tied to conversation | Survive across conversations; detach and reattach |
| **Multiple sessions** | Single terminal context | Named sessions with a 16-session connection pool by default |
| **Remote + nested shells** | Requires separate SSH extension | User SSHes / `docker exec`s → AI takes over in that context |
| **Works with** | Claude Code only | Claude Desktop, Claude Code, Cursor, any MCP client |

### When it shines: shared interactive sessions

```
1. AI creates a session
   Claude: [calls iterm_new_session: "db-fix"]
   → Orange-themed iTerm2 window appears

2. YOU take control — SSH into the production server, authenticate
   ssh admin@prod-server.internal
   docker exec -it postgres-primary bash

3. You hand control back to the AI
   "I'm inside the postgres container. Find and remove duplicate orders."

4. AI operates inside your authenticated context
   Claude: [calls iterm_send_and_read: "psql -U app orders_db"]
   Claude: [calls iterm_send_command: "DELETE FROM orders WHERE ..."]
   → You watch every command execute in real-time, can Ctrl+C any time
```

This workflow is impossible with fire-and-forget terminal access. The AI doesn't have your credentials; you bring them, then hand the live shell over.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  MCP Clients                                                    │
│  Claude Desktop    Claude Code     Cursor / Cowork    Scripts   │
│  (.mcpb ext)       (stdio MCP)     (stdio MCP)        (socket)  │
└───────┬──────────────┬──────────────┬──────────────────┬────────┘
        │              │              │                  │
        └──────────────┴──────┬───────┘                  │
                    ┌─────────▼──────────┐               │
                    │  MCP Server        │               │
                    │  (stdio transport) │               │
                    │  Session pool (16) │               │
                    └────┬─────────┬─────┘               │
              ┌──────────▼──┐  ┌──▼──────────┐           │
              │   Daemon    │  │   Daemon    │           │
              │ session.sock│  │ session.sock│◄──────────┘
              │  (JSON-RPC, │  │  0o600)     │
              └──────┬──────┘  └──────┬──────┘
              ┌──────▼──────┐  ┌──────▼──────┐
              │  iTerm2     │  │  iTerm2     │
              │  Window     │  │  Split Pane │
              └─────────────┘  └─────────────┘
```

- One daemon per session, communicating via a 0o600 Unix socket.
- MCP server pool (default 16) targets any connected session via an optional `session` parameter.
- All clients talk to the same sockets — sessions are client-agnostic.

## Prerequisites

- macOS with iTerm2.
- iTerm2 Python API enabled: **iTerm2 → Settings → General → Magic → Enable Python API**.

## Install

### Claude Desktop (recommended)

1. Download the signed `.mcpb` from [GitHub Releases](../../releases) or build locally with `make mcpb` (see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)).
2. **Claude Desktop → Settings → Extensions → Install Extension** → select the `.mcpb`.

Claude Desktop uses [`uv`](https://github.com/astral-sh/uv) to manage the Python runtime automatically. No manual Python setup needed. Restart Claude Desktop after updating the extension — it caches tool definitions.

### Claude Code (prod)

```bash
uv tool install git+https://github.com/dude84/iterm2-control-mcp@main
claude mcp add --transport stdio iterm2-control-mcp -- iterm2-control-mcp
```

Pin to a tag (e.g. `@v0.27.0`) for reproducible installs. Upgrade with `uv tool install --force git+...@<new-tag>` — `uv tool upgrade` alone does not re-resolve a git ref.

### Shell CLI (dev / automation)

```bash
git clone git@github.com:dude84/iterm2-control-mcp.git
cd iterm2-control-mcp
make dev-env
./iterm2-control.sh new homelab            # create a session
./iterm2-control.sh send homelab uptime    # send a command
./iterm2-control.sh read homelab           # read output
./iterm2-control.sh stop homelab           # stop
```

### Extension settings

In Claude Desktop → Settings → Extensions → iterm2-control-mcp → **Configure**:

| Setting | Default | Description |
|---|---|---|
| **AI command markers** | On | Highlight AI-sent commands with a `# [ai]` marker line |
| **Cleanup on window close** | On | Auto-cleanup session when the iTerm2 window is closed |
| **iTerm2 profile name** | *(empty)* | Use a custom iTerm2 profile for session windows. Empty = default orange theme |
| **Max connected sessions** | 16 | Connection pool size. Oldest inactive session is dropped when full |
| **Max session log size (MB)** | 16 | Per-session `session.log` rotated at this size, one backup kept. Set 0 to disable rotation |

## Usage basics

Ask Claude in natural language:

> "Create a new terminal session called homelab, then check disk space"

Claude calls `iterm_new_session` (a themed window appears), then `iterm_send_and_read("df -h")`. For the full tool + CLI reference see **[docs/TOOLS.md](docs/TOOLS.md)**.

## Session files

```
~/.iterm2-control-mcp/
└── sessions/<id>/
    ├── session.sock        # Unix socket (JSON-RPC, 0o600)
    ├── session.log         # daemon log (rotated at ITERM2_CONTROL_MCP_MAX_LOG_MB, default 16 MB)
    ├── session.log.1       # rotated backup
    └── meta.json           # ID, name, PID, start time, attachment state
```

Directory perms `0o700`; file perms `0o600`. See [SECURITY.md](SECURITY.md).

## Cross-client

A session started via Claude Desktop can be controlled from the CLI and vice versa — the daemons don't care who created them. Sessions are Unix-socket processes, client-agnostic.

## More

- **[docs/TOOLS.md](docs/TOOLS.md)** — full MCP tool and CLI reference, including file ops and the environment probe.
- **[SECURITY.md](SECURITY.md)** — threat model, transport policy (hard rule: no network MCP without auth + TLS + per-tool scoping), supported versions, vulnerability reporting.
- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** — setup, layout, contributing, release flow.

## License

Apache License 2.0. See [LICENSE](LICENSE).
