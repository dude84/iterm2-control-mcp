# Tool and CLI reference

[← back to README](../README.md)

All MCP tools have a corresponding CLI command. Terminal tools accept an optional `session` parameter to target any connected session — if omitted, the active (most recently used) session is targeted. The authoritative descriptions live in [`src/iterm2_control_mcp/mcp/schemas.py`](../src/iterm2_control_mcp/mcp/schemas.py) — this page is a human-friendly summary.

## Session management

| MCP Tool | CLI Command | Parameters | Description |
|---|---|---|---|
| `iterm_list_sessions` | `list` | — | List all sessions on the machine with `id`, `name`, `pid`, `started_at`, `last_activity_at`, `status` |
| `iterm_new_session` | `new [name]` | `name` (opt) | Open a new iTerm2 window and start a session. Auto-connects |
| `iterm_connect_session` | `connect <id\|name>` | `session` | Make an existing session the active target |
| `iterm_detach_session` | `detach <id\|name>` | `session` (opt) | Release the terminal; daemon stays alive for reconnection |
| `iterm_rename_session` | `rename <id\|name> <new>` | `session`, `name` | Rename a session (metadata + window title) |
| `iterm_focus_window` | `focus <id\|name>` | `session` (opt) | Bring the session's iTerm2 window to front |
| `iterm_split_pane` | `split <id\|name> [-v] [name]` | `name` (opt), `vertical` (opt) | Split into two panes; new pane gets its own session, auto-connects |
| `iterm_stop_session` | `stop <id\|name> [--no-close-window]` | `session`, `close_window` (opt, default `true`) | Stop and destroy a session. Closes the pane by default; `close_window=false` keeps the pane with a `[stopped]` title |
| `iterm_clean_sessions` | `clean` | — | Remove stale session directories (daemon no longer running) |
| — | `acquire [name]` | — | Acquire the current terminal as a session (CLI only) |
| — | `clean-all` | — | Stop all sessions and remove everything (CLI only) |

### Multi-session pool

Sessions created with `iterm_new_session`, `iterm_split_pane`, or `iterm_connect_session` stay in the MCP server's connection pool (default size 16). Terminal tools can target any pooled session via the `session` parameter; if omitted, the most recently active session is used. When the pool fills, the oldest non-active session is dropped. Use `iterm_status` with no argument to ask "which session is active?".

## Terminal tools

All terminal tools accept an optional `session` parameter. If omitted, the active session is used.

| MCP Tool | CLI Command | Parameters | Description |
|---|---|---|---|
| `iterm_send_command` | `send <id\|name> <cmd>` | `command`, `session` (opt) | Type and press Enter for a shell command — fire-and-forget. Output is not returned; use `iterm_read_output` or `iterm_capture_screen` afterwards |
| `iterm_send_and_read` | *(MCP only)* | `command`, `timeout` (opt), `quiet_ms` (opt), `max_wait` (opt), `session` (opt) | Send a command and return its output. The usual choice. Returns `{ stdout, exit_code, cursor, session, shell_integration, idle_reason }`. Without shell integration, completion is determined by PTY quiescence — `quiet_ms` (default 800) milliseconds of no cursor advance. Raise to ~2000 if the iTerm2 window may be backgrounded |
| `iterm_send_keys` | *(MCP only)* | `keys` (enum), `session` (opt) | Send control characters / arrows: `ctrl-c`, `ctrl-d`, `ctrl-z`, `ctrl-l`, `enter`, `tab`, `escape`, `backspace`, `delete`, `up`, `down`, `left`, `right`, `home`, `end` |
| `iterm_type` | `type <id\|name> <text>` | `text`, `session` (opt) | Type text into the prompt line **without** pressing Enter. Use to stage a command for user review |
| `iterm_read_output` | `read <id\|name> [n]` | `lines` (opt), `since` (opt cursor), `wait_for` (opt), `clear` (opt), `session` (opt) | Read recent terminal output. Returns `{ stdout, cursor, session }` and optionally `warning: "cursor_lost_due_to_scroll"` |
| `iterm_capture_screen` | *(MCP only)* | `session` (opt) | Atomic snapshot of the visible screen with cursor position. Use to check whether a command is still running |
| `iterm_probe_environment` | *(MCP only)* | — | Returns a command sequence to run via `iterm_send_and_read`. See [Environment probe](#environment-probe) below |
| `iterm_status` | *(MCP only)* | `session` (opt) | Session info: ID, attached state, uptime, buffer size. Called with no args → info on the active session, or `{"active": false, "hint": ...}` if none |

### Shared terminal caveat

The terminal is shared between the AI and the user. Output buffers contain **all** activity — both yours and the user's (SSH sessions, docker exec, typed navigation). When you see commands you didn't send, the user typed them. Report what you observe; don't speculate about how the terminal reached its state.

## File operations

Both tools move files between the remote shell inside the iTerm2 session and your Mac. They share two hard requirements:
- The iTerm2 session must be directly connected to the remote shell. Multiplexers like `tmux`/`screen` without OSC passthrough can mangle the transfer.
- The remote must have `base64` on `PATH`.

| MCP Tool | Parameters | Description |
|---|---|---|
| `iterm_upload_file` | `path`, (`local_path` *xor* `content`), `encoding` (opt: `text` / `base64`), `session` (opt) | Push a file to the remote via a base64 here-doc |
| `iterm_download_file` | `path`, `session` (opt) | Pull a file from the remote into `~/Downloads` on your Mac via iTerm2 OSC 1337 |

The two tools take very different paths. **Upload** routes bytes through MCP server → session daemon socket → iTerm2 websocket → PTY → remote shell, bounded by the RPC line buffer. **Download** is emitted by the remote shell as an iTerm2 escape sequence that flows back up the PTY and is intercepted by iTerm2 locally — the bytes never traverse our MCP or RPC layer at all. So the two tools have very different size and failure characteristics.

### Upload — `iterm_upload_file`

With `local_path`, the MCP server reads the file from your Mac, base64-encodes it, and forwards it as a single JSON-RPC request over the session Unix socket. The daemon wraps the base64 to 76-char lines (well below the terminal canonical-mode line discipline limit) and builds a here-doc with a randomized sentinel: `base64 -d > <path> <<'__ITERM_UL_<nonce>__' … __ITERM_UL_<nonce>__`. The here-doc is streamed into the iTerm2 session in **32 KB chunks** so the PTY and the iTerm2 websocket don't get hit with a single massive burst. The remote shell decodes the base64 and writes the destination. Inline `content` mode works the same way but takes the payload directly from the tool parameters.

```python
iterm_upload_file(
    path="/tmp/random512_upload.bin",
    local_path="/Users/you/Downloads/random512.bin",
)
# → {"uploaded": true, "remote_path": "/tmp/random512_upload.bin",
#    "local_path": "/Users/you/Downloads/random512.bin", "bytes": 524288}
```

| Concern | Detail |
|---|---|
| **Max file size** | **~12 MB per call.** 16 MB RPC line buffer; base64 inflates binary by 4/3, so `(16 MB − JSON envelope) × 3/4 ≈ 12 MB`. Above the cap you get a clean `"RPC line buffer exceeded"` error — not a crash |
| **Streaming** | None. The whole file is held in memory at the MCP layer and the daemon layer simultaneously |
| **Retries** | None. Single attempt. Errors return as `{"error": ...}` dicts |
| **Verification** | Response echoes the source file size (`bytes`) but does not confirm the remote file landed correctly. Verify with `wc -c` or `md5sum` |
| **Atomic write** | None. Interrupted upload leaves a truncated file at `<path>` |
| **Terminal pollution** | The base64 here-doc **is** visible in the iTerm2 window — deliberate (visible terminal is the primary safeguard) but noticeable for larger files |
| **Remote requirements** | `base64 -d` on `PATH`. Present on Linux coreutils, macOS, busybox, alpine |
| **Binary via JSON** | `content` is a JSON string and cannot carry raw bytes. For binary, use `local_path` or pre-encode with `encoding="base64"`. JSON schema enforces `local_path` XOR `content` |

### Download — `iterm_download_file`

The daemon types a shell one-liner into the remote session that `printf`s an [iTerm2 OSC 1337 File](https://iterm2.com/documentation-escape-codes.html) escape sequence containing the base64-encoded file. iTerm2 intercepts the sequence locally and saves the file to `~/Downloads`.

```python
iterm_download_file(path="/etc/hosts")
# → {"downloaded": "hosts", "location": "~/Downloads/hosts"}
```

| Concern | Detail |
|---|---|
| **Destination** | Always `~/Downloads` on your Mac. **Not configurable.** iTerm2 handles filename collisions by appending a counter (`hosts`, `hosts 2`, …). Rename after download if you need a specific destination |
| **Filename** | The basename of the remote `path`. No way to rename on the Mac side from the tool call |
| **Max file size** | No fixed cap from our layer — the bytes do not pass through our RPC. Bounded in practice by remote shell memory for `$(base64 < <path>)` and iTerm2's OSC 1337 parser. Handles tens to hundreds of MB |
| **Streaming** | None. Remote allocates the full base64 string before emitting |
| **Retries** | None. Single attempt |
| **Verification** | **None.** Tool returns as soon as the shell command has been typed. Verify externally (`stat`, `md5sum`, `ls -l ~/Downloads/`) |
| **Asynchronous return** | Tool doesn't block on iTerm2 actually saving the file. If you need to act on the downloaded file immediately, poll `~/Downloads/<name>` until it exists |
| **Remote requirements** | `base64`, `wc -c`, `printf`, `echo`. Universal |
| **Terminal pollution** | The base64 payload is *not* visibly typed — it goes through `$(base64 < <path>)` command substitution. The short `printf` invocation is still echoed |
| **Path handling** | Remote `path` is `shlex.quote`d; spaces and shell metacharacters are safe |
| **Transport** | Requires iTerm2 to receive the raw OSC sequence directly. `tmux`/`screen` must forward OSC passthrough (`set -g allow-passthrough on` in tmux 3.3+). SSH-escape filtering can strip the sequence |

## Environment probe

`iterm_probe_environment` returns a structured list of passive, read-only commands for the agent to run via `iterm_send_and_read`. The tool itself does not execute anything — you see every command in the terminal because the agent calls them one at a time.

| Category | Commands | Purpose |
|---|---|---|
| **Identity** | `uname -a`, `hostname`, `cat /etc/os-release \|\| sw_vers`, `cat /proc/version` | Kernel, arch, OS, distro, WSL detection |
| **User** | `id`, `sudo -n true` (non-interactive), `echo $SHELL`, `echo $HOME && echo $PWD` | UID/GID/groups, sudo access, shell, home and working directory |
| **System** | `uptime`, `nproc \|\| sysctl -n hw.ncpu`, `free -h \|\| vm_stat`, `df -h /` | Load, CPU count, memory, root disk usage |
| **Network** | `ip addr \|\| ifconfig`, `ip route \| grep default`, `cat /etc/resolv.conf \| grep nameserver`, `ss -tlnp \|\| netstat -tlnp`, `echo $SSH_CONNECTION` | IP addresses, gateway, DNS, listening ports, SSH info |
| **Tools** | `which python3 python node java gcc docker kubectl git ssh curl wget make` | Installed tools |
| **Virtualization** | `systemd-detect-virt`, `test -f /.dockerenv`, `test -f /run/.containerenv` | VM / Docker / Podman detection |
| **Security** | `getenforce`, `w \| head -5` | SELinux status, other logged-in users |

All commands are safe to execute on any system — no writes, no installs, no modifications.
