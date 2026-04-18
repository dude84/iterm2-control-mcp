#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

source "$PROJECT_DIR/.venv/bin/activate"

# Use release tag from CI, fall back to git describe for local builds
if [ -n "${GITHUB_REF_NAME:-}" ]; then
    VERSION="${GITHUB_REF_NAME#v}"
else
    VERSION=$(cd "$PROJECT_DIR" && git describe --tags --always 2>/dev/null || echo "0.0.0")
    VERSION="${VERSION#v}"
fi
echo "Building iterm2-control-mcp $VERSION .mcpb extension"

# Patch manifest.json with git tag version
python -c "
import json, pathlib
m = pathlib.Path('$PROJECT_DIR/manifest.json')
d = json.loads(m.read_text())
d['version'] = '$VERSION'
m.write_text(json.dumps(d, indent=2) + '\n')
"

# Generate _version.py so the package builds without git inside .mcpb
python -c "
import pathlib
pathlib.Path('$PROJECT_DIR/src/iterm2_control_mcp/_version.py').write_text(
    'version = __version__ = \"$VERSION\"\n'
)
"

mkdir -p "$SCRIPT_DIR/dist"
npx @anthropic-ai/mcpb pack "$PROJECT_DIR" "$SCRIPT_DIR/dist/iterm2-control-mcp-${VERSION}.mcpb"
