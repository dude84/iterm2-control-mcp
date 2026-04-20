# iterm2-control-mcp

Give AI agents their own interactive terminal sessions via iTerm2. Visible, themed windows that persist across conversations. The daemon is the eyes and hands; the caller brings the brain.

User-facing documentation:
- `README.md` — install + quick start
- `docs/TOOLS.md` — MCP tool + CLI reference, file ops, env probe
- `SECURITY.md` — threat model + **transport policy (hard rule)**
- `docs/DEVELOPMENT.md` — setup, layout, releases

---

## Quick start

```bash
make dev-env                                 # create venv and install

./iterm2-control.sh new dev                  # new session in a new window
./iterm2-control.sh list                     # show sessions
./iterm2-control.sh send dev ls -la          # send a command
./iterm2-control.sh read dev                 # read output
./iterm2-control.sh stop dev                 # stop session
```

### Claude Code

Prod: `uv tool install git+https://github.com/dude84/iterm2-control-mcp@vX.Y.Z`, then
`claude mcp add --transport stdio iterm2-control-mcp -- iterm2-control-mcp`.

Dev (from source checkout): `claude mcp add --transport stdio iterm2-control-mcp -- "$(pwd)/.venv/bin/python" -m iterm2_control_mcp.mcp`.

### Claude Desktop

Build and install the `.mcpb` extension: `make mcpb` → Claude Desktop → Settings → Extensions → Install Extension → select `mcpb/dist/iterm2-control-mcp-<version>.mcpb`.

---

## Architecture

```
Claude Desktop → .mcpb extension → stdio MCP server → session.sock → daemon
Claude Code    → stdio MCP       → session.sock → daemon
local scripts  → session.sock    → daemon
```

One MCP transport (stdio, enforced by SECURITY.md transport policy). MCP server manages an in-memory session pool and routes tool calls to per-session Unix sockets. Each daemon owns one iTerm2 session.

---

## Tool schema

Session management:
- `iterm_list_sessions` — list all sessions with IDs, names, and status (active/stale)
- `iterm_new_session` — open a new iTerm2 terminal window and start a session (auto-connects)
- `iterm_connect_session` — make an existing session the active MCP target
- `iterm_detach_session` — detach terminal (daemon stays alive, reconnect later via `iterm_connect_session`)
- `iterm_rename_session` — rename an active session (updates metadata and window title)
- `iterm_focus_window` — bring session's iTerm2 window to front
- `iterm_split_pane` — split terminal into panes (creates a new session for the new pane)
- `iterm_stop_session` — stop and destroy a session; closes the iTerm2 pane by default (set `close_window=false` to keep with a `[stopped]` title)
- `iterm_clean_sessions` — remove stale session directories
- `iterm_status` — session info: ID, attached state, uptime, buffer size. No-arg form reports the active session or `{"active": false, "hint": ...}`

Terminal tools (all accept optional `session` param; defaults to active session):
- `iterm_send_command` — type and execute a shell command (fire-and-forget). Returns `{status, session, cursor}` — `cursor` is the source buffer position captured *before* the command was typed, for chaining into a subsequent `iterm_read_output(since=…)` call without racing the command's leading-edge output
- `iterm_send_and_read` — send a command and return its output in one call
- `iterm_send_keys` — send special keys: `ctrl-c`, `ctrl-d`, `ctrl-z`, `ctrl-l`, `enter`, `tab`, `escape`, `backspace`, `delete`, `up`, `down`, `left`, `right`, `home`, `end`
- `iterm_type` — type text into the prompt line without pressing Enter (stage for user review)
- `iterm_read_output` — read recent output (optional: `since` cursor, `wait_for`, `clear`)
- `iterm_capture_screen` — atomic snapshot of visible screen with cursor position and dimensions
- `iterm_probe_environment` — returns a read-only command sequence for the agent to run

File operations:
- `iterm_download_file` — download file from remote to Mac via iTerm2 OSC 1337
- `iterm_upload_file` — write file to remote via base64 here-doc. Exactly one of `local_path` (connector reads from Mac, recommended for binary) or `content` (inline, with optional `encoding="text"` (default) or `"base64"`)

Authoritative descriptions live in `src/iterm2_control_mcp/mcp/schemas.py`.

---

## Key conventions

- All Python in `src/iterm2_control_mcp/` with editable install
- `requires-python = ">=3.11"`; dependencies pinned `iterm2>=2.14,<3` and `mcp>=1.0,<2`
- Pure logic functions (no iTerm2 import) kept testable
- `ruff` for linting, `mypy --strict` for types
- No co-authored-by attribution in commits
- Session directory perms `0o700`; files inside `0o600` (umask at daemon start + explicit chmod)
- `session.log` rotates at `ITERM2_CONTROL_MCP_MAX_LOG_MB` (default 16 MB), keeping one backup

---

## Repository structure

```
iterm2-control.sh            # CLI entry point
Makefile                     # make dev-env | test | lint | mcpb | clean
manifest.json                # .mcpb extension manifest
mcpb/
├── build.sh                 # builds .mcpb extension
└── dist/                    # build output (gitignored)
scripts/
├── test-read-output.py      # direct socket test tool
└── test-send-command.py     # direct socket test tool
src/iterm2_control_mcp/
├── main.py                  # CLI + session daemon lifecycle
├── config.py                # Config, load_config, pid_looks_like_session_daemon,
│                            # cleanup_session_dir
├── terminal/
│   ├── session.py           # SessionObserver: buffer output, send_command,
│   │                        # send_and_read, upload/download
│   └── profile.py           # orange theme + save/restore
├── cowork/
│   ├── client.py            # RPC client (shared by CLI + MCP) with asyncio timeouts
│   ├── server.py            # socket server + DaemonHandler
│   └── protocol.py          # Request, Response, ProtocolError
└── mcp/
    ├── server.py            # stdio MCP server entry point
    ├── schemas.py           # Tool definitions (SESSION_TOOLS, FILE_TOOLS, _PROBE_SEQUENCE)
    ├── session_pool.py      # get_sessions, clean_sessions, stop_session,
    │                        # create_new_session, _create_split_session
    ├── handler.py           # ToolHandler: routes MCP tool calls
    └── tools.py             # thin re-export shim for legacy imports
```

---

## File structure

```
~/.iterm2-control-mcp/
└── sessions/<id>/
    ├── session.sock         # Unix socket (0o600)
    ├── session.log          # rotated at ITERM2_CONTROL_MCP_MAX_LOG_MB (default 16 MB)
    ├── session.log.1        # rotated backup, ≈ 2× cap total on disk per session
    ├── meta.json            # session_id, name, pid, started_at, iterm_session, attached
    └── activity             # ISO timestamp of last tool-call activity
```

---

## Desktop extension (.mcpb)

The project packages as a Claude Desktop extension using the MCPB format:

- `manifest.json` — extension manifest (uv server type, macOS only)
- `.mcpbignore` — excludes dev files from the bundle
- `make mcpb` (runs `mcpb/build.sh`) — builds `mcpb/dist/iterm2-control-mcp-<version>.mcpb`
- Version derived from git tags via `setuptools-scm`

The extension uses `uv` to manage the Python runtime automatically.

## Releases

Single-repo workflow: `origin` is `git@github.com:dude84/iterm2-control-mcp.git`. Work happens on `main` directly or on feature branches + PRs — whichever fits the size of the change.

### Cutting a release

```bash
make lint && make test
git add CHANGELOG.d/vX.Y.Z.md <other changes>
git commit -m "vX.Y.Z: <short summary>"
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
gh release create vX.Y.Z --notes-file CHANGELOG.d/vX.Y.Z.md --title vX.Y.Z
```

Release candidates: add `--prerelease` and use a `-rcN` suffix on the tag and CHANGELOG file.

Confirm before every `git push origin` of a tag or `gh release create` — these are the public-visible actions. Low risk once you decide, but the gate catches "did I remember to bump the version?" before the world sees it.

### Release notes

Hand-authored, one file per release, lives at `CHANGELOG.d/vX.Y.Z.md`. Keep it user-facing — what changed, what broke, what to migrate. Do not rely on `--generate-notes`; auto-generated "Full Changelog: compare/X...Y" tells users nothing.

### Private notes

`TODO.md` is gitignored. It holds internal roadmap / brainstorming that shouldn't land on GitHub. Edit freely; it never pushes. If `TODO.md` contents stabilize into something user-facing, move the relevant parts into `docs/` and commit.

### Behind the scenes

Tags drive everything. `mcpb/build.sh` reads `GITHUB_REF_NAME` in CI (or `git describe --tags` locally) and patches the version into a build-time copy of `manifest.json` under `mcpb/dist/` — the committed `manifest.json` is not mutated. The release workflow (`.github/workflows/release.yml`) fires on `release: published`, runs `make mcpb`, attaches the `.mcpb` asset, and signs it with GitHub build provenance.

See `docs/DEVELOPMENT.md` "Releases" section for the full flow.

---

## Session lifecycle

- `new` opens a new iTerm2 window with an orange-themed session
- `acquire` takes over the current terminal as a session
- Session names must be unique across active sessions
- Window title shows `iTerm2 Control: <id> (<name>)`
- `detach` detaches terminal (restores theme, title shows `[detached]`), daemon stays alive
- `connect` reattaches a detached session to the current terminal
- Closing the iTerm2 window auto-detaches the session via `SessionTerminationMonitor`
- On `stop`, theme is restored, window title changes to `[stopped]`, daemon exits

---

## Interactive prompts

Shell prompts (y/n, passwords, menus) are handled by the user typing directly in the iTerm2 window — not via MCP ElicitRequest. The terminal is visible and interactive precisely so the user can participate.

## Security

- Socket `0o600` — owner only. No network listeners. Local-only by design.
- Session dir `0o700`; `meta.json`, `session.log`, `activity` files are `0o600` (set via umask at daemon start and explicit chmod on the session dir).
- Visible terminal is the primary safeguard — you see everything the AI does.
- No regex-based command guard — a pattern matcher that an LLM can bypass with `bash -c`, aliases, or absolute paths is theatre. The human watching the terminal is the real safeguard.
- PID-liveness cross-check before any SIGTERM fallback: `config.pid_looks_like_session_daemon()` confirms the process comm contains `python` before signalling, so a PID reused by an unrelated process is never killed.
- RPC client wraps connect/drain/readline in `asyncio.wait_for` (60 s default, 300 s for upload/download) — a wedged daemon cannot hang the MCP host.
- Malformed JSON / missing fields on the Unix socket → `ProtocolError` → structured error Response (no silent close).
- No socket encryption or per-session auth — `0o600` is sufficient for local-only use. See SECURITY.md.
- **Transport policy (HARD RULE):** `mcp/server.py` MUST use `stdio_server()`. Adding SSE / StreamableHTTP / FastMCP HTTP requires auth + TLS + per-tool scoping. See `SECURITY.md` "Transport policy" — removing that section is itself a review-blocker.

---

## Scheduled maintenance

- **2026-06-02** — GitHub switches default Action runner to Node 24. Dependabot (configured in `.github/dependabot.yml`) should have already opened PRs bumping `actions/setup-node`, `actions/setup-python`, `actions/checkout`, `softprops/action-gh-release` to versions that support Node 24. Review and merge those before this date. If Dependabot hasn't, bump manually and re-pin to new commit SHAs (see `.github/workflows/release.yml` for the existing pinning pattern).
- **2026-09-16** — GitHub removes Node 20 from runners entirely. Any action still on Node 20 breaks CI. Hard deadline for the above.

---

## Known / deferred

- **AI command markers via iTerm2 Trigger API.** The `command_markers` config flag works today via text injection (echoes `# [ai]\n` before each AI command). Visually highlighting those lines with a background color via `HighlightLineTrigger` was attempted and reverted (`2107e73`) due to iterm2-library version compat issues. Not a bug; purely a UX enhancement.
