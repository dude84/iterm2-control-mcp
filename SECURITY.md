# Security Policy

[← back to README](README.md)

## Supported versions

Only the latest released version receives security fixes. See
[Releases](../../releases).

## Reporting a vulnerability

Please open a private security advisory:
https://github.com/dude84/iterm2-control-mcp/security/advisories/new

For non-sensitive questions, a regular GitHub issue is fine.

## Threat model

iterm2-control-mcp is designed for **single-user macOS machines** with
**local-only access**. The AI has full shell access to the user's
terminal — any guardrails in this project are convenience layers,
**not security boundaries**. Do not run this on machines where
untrusted users share your account, and do not expose the daemon
socket over a network.

## What we do

| Layer | Detail |
|---|---|
| **Socket permissions** | `session.sock` is `0o600` — only the owning user can read/write. Same model as SSH agent sockets. |
| **Session directory permissions** | `0o700` on `~/.iterm2-control-mcp/sessions/<id>/`; `0o600` on `meta.json`, `session.log`, and `activity` inside it. Set via umask at daemon start + explicit chmod. |
| **No network listeners** | Only stdio MCP + Unix socket. Nothing binds to a port. See [Transport policy](#transport-policy-hard-rule) below. |
| **Visible terminal** | You see every command the AI types and every response. This is the primary safeguard against destructive actions. |
| **Session logging** | All commands logged at INFO level in per-session `session.log`. Rotated at `ITERM2_CONTROL_MCP_MAX_LOG_MB` (default 16 MB) with one backup. |
| **Auto-cleanup** | Sessions clean up when the iTerm2 window closes (configurable). Stale sessions can be removed with `iterm_clean_sessions`. |
| **PID-liveness cross-check** | Before sending SIGTERM based on a PID read from `meta.json`, the code verifies the process comm contains `python`. Prevents killing an unrelated process that reused the PID. |
| **RPC timeouts** | The MCP↔daemon RPC client wraps connect / drain / readline in `asyncio.wait_for` (60 s default, 300 s for file uploads/downloads). A wedged daemon cannot hang the MCP host. |
| **Protocol validation** | Malformed JSON or missing `method` field on the Unix socket returns a structured error Response instead of silently closing. |
| **Supply chain** | CI actions pinned to commit SHAs. Released `.mcpb` is signed with GitHub build provenance — verify with `gh attestation verify --owner dude84 <file>.mcpb`. |

## What we don't do (and why)

| Not implemented | Reasoning |
|---|---|
| **Socket encryption / paired keys** | `0o600` already prevents non-owner access. Encryption would protect against a compromised user account, but at that point the attacker already has your shell. |
| **Per-session authentication** | Same reasoning — if you can reach the socket, you're already the owner. |
| **Network access / remote control** | Out of scope. If you need remote access, SSH-forward to the socket — SSH handles authentication and encryption. Never expose sockets directly over a network. See [Transport policy](#transport-policy-hard-rule). |
| **Regex-based "dangerous command" guard** | Removed in v0.26.0. A pattern matcher that an LLM can bypass with `bash -c`, aliases, variable substitution, or absolute paths (`/bin/rm` vs `rm`) is worse than no guard — it looks like a safety net but isn't one. The human watching the terminal is the real safeguard. |

## The real risk

The AI has full shell access. It can run any command you could type. **The visible terminal window is the safeguard** — you watch what happens and can intervene (Ctrl+C, close the window). If you're not watching, you have no guardrail. That's intentional.

## Transport policy (HARD RULE)

This project exposes **only** the following transports:

- **stdio MCP** via [`src/iterm2_control_mcp/mcp/server.py`](src/iterm2_control_mcp/mcp/server.py) (`stdio_server()` from the upstream `mcp` package). The caller's identity is the process identity — Claude Desktop / Claude Code / the CLI that launched the server.
- A **local Unix socket per session** (`~/.iterm2-control-mcp/sessions/<id>/session.sock`, `0o600`). Owner-only, never bound to a network.

**The project MUST NOT add a network MCP transport** — no SSE, no StreamableHTTP, no FastMCP HTTP — **without all of the following**:

1. Bearer / OAuth authentication on every request.
2. TLS termination, or an explicit requirement to run behind an SSH tunnel / WireGuard / similar.
3. Per-tool scoping so that remote callers cannot by default invoke `iterm_send_command`, `iterm_upload_file`, `iterm_send_keys`, or any other tool that mutates remote state. Read-only tools may be in a remotely-callable set; destructive tools must require an explicit scope grant.

Why this rule is hard: the daemon gives the caller full shell access. A single-line change in `mcp/server.py` (swapping `stdio_server()` for `streamable_http_server()` or similar) would expose every running session to the network with zero authentication. Any PR that adds such a transport without the three controls above must be rejected.

**Removing or weakening this section is itself a review-blocker.** If you genuinely need a network transport, add it with the controls, don't delete the rule.
