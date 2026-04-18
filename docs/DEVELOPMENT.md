# Development

[← back to README](../README.md)

## Setup

```bash
git clone git@github.com:dude84/iterm2-control-mcp.git
cd iterm2-control-mcp
make dev-env       # create .venv/ and install with dev dependencies
```

Python 3.11+ required.

## Workflow

```bash
make lint          # ruff + mypy --strict
make test          # pytest
make mcpb          # build the .mcpb Claude Desktop extension
make clean         # remove build artifacts
```

Source edits in a dev install apply immediately (editable install).

### Claude Code — dev install

For hacking on the server while using it in Claude Code:

```bash
claude mcp add --transport stdio iterm2-control-mcp -- \
  "$(pwd)/.venv/bin/python" -m iterm2_control_mcp.mcp
```

Caveats:
- If you rebuild `.venv` (`rm -rf .venv && make dev-env`), the MCP config keeps working — Python's shebang is updated in place.
- If you rename or move the project directory, the absolute path in the MCP config goes stale. Re-run `claude mcp add` with the new path.

### Local scripts (Unix socket)

For testing or automation, scripts can talk to session sockets directly:

```bash
./scripts/test-read-output.py a1b2c3d4 50
./scripts/test-send-command.py a1b2c3d4 docker ps
```

## Layout

```
src/iterm2_control_mcp/
├── main.py                   # CLI entry point + session daemon lifecycle
├── config.py                 # settings, session paths, PID helper
├── terminal/
│   ├── session.py            # acquire terminal, buffer output
│   └── profile.py            # theme, save/restore
├── cowork/
│   ├── client.py             # RPC client (shared by CLI + MCP)
│   ├── server.py             # socket server + handler
│   └── protocol.py           # JSON-RPC types, ProtocolError
└── mcp/
    ├── server.py             # stdio MCP server entry point
    ├── schemas.py            # Tool definitions (SESSION_TOOLS, FILE_TOOLS)
    ├── session_pool.py       # filesystem/subprocess helpers
    ├── handler.py            # ToolHandler — routes MCP tool calls
    └── tools.py              # thin re-export shim for legacy imports
```

## Contributing

Before opening a PR:

1. `make lint && make test` must be green.
2. Read [`SECURITY.md`](../SECURITY.md) — the **Transport policy** section is a hard rule. A PR that adds SSE / StreamableHTTP / FastMCP HTTP without bearer auth + TLS + per-tool scoping will be rejected.
3. Avoid introducing "safety theatre" — guardrails that an LLM can trivially bypass (regex command filters, alias traps) erode trust in the real safeguards. If a proposed check can be bypassed with `bash -c` or variable substitution, the change should probably not ship.

## Releases

Versions follow `vMAJOR.MINOR.PATCH` (or `vMAJOR.MINOR.PATCH-rcN` for release candidates) and are derived from git tags via `setuptools-scm`. The version is patched into `manifest.json` at build time by `mcpb/build.sh`.

The release flow uses GitHub Releases as the trigger: tag the commit, push the tag, then publish a Release. Publishing fires `.github/workflows/release.yml`, which:
- Builds the `.mcpb` extension (`make dev-env && make mcpb`).
- Signs it with [GitHub build provenance](https://docs.github.com/en/actions/security-guides/using-artifact-attestations). Verify with `gh attestation verify --owner dude84 <file>.mcpb`.
- Attaches the `.mcpb` to the Release page as a downloadable asset.

### Cutting a stable release

```bash
make lint && make test
git tag v0.27.0
git push origin v0.27.0
gh release create v0.27.0 --generate-notes --title "v0.27.0"
```

### Cutting a release candidate

```bash
git tag v0.27.0-rc1
git push origin v0.27.0-rc1
gh release create v0.27.0-rc1 --generate-notes --prerelease --title "v0.27.0-rc1"
```

After the workflow finishes (~1–2 min), the `.mcpb` is attached to the Release page.

## Dependency pinning

`.github/workflows/release.yml` pins GitHub Actions to commit SHAs (audit E-7). [Dependabot](../.github/dependabot.yml) bumps them weekly; review the PRs rather than merging blindly. `pyproject.toml` pins `iterm2>=2.14,<3` and `mcp>=1.0,<2` so a major-version upstream release doesn't break installs; no `uv.lock` is committed — the `.mcpb` resolves fresh on the end user's machine.
