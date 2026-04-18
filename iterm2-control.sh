#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
    source "$PROJECT_DIR/.venv/bin/activate"
    VERSION=$(python -c "from iterm2_control_mcp import __version__; print(__version__)" 2>/dev/null || echo "?")
    echo "iterm2-control-mcp $VERSION"
    echo ""
    echo "Usage: $(basename "$0") <command>"
    echo ""
    echo "Session management:"
    echo "  new [name]                    Create a new iTerm2 window with a session"
    echo "  acquire [name]                Acquire the current terminal as a session"
    echo "  connect <id|name>             Connect current terminal to an existing session"
    echo "  detach <id|name>              Detach terminal from session (daemon stays running)"
    echo "  rename <id|name> <new-name>   Rename a session"
    echo "  focus <id|name>               Bring session window to front"
    echo "  split <id|name> [-v] [name]   Split terminal into panes"
    echo "  stop <id|name>                Stop a session"
    echo "  list                          Show active sessions"
    echo "  clean                         Remove stale session directories"
    echo "  clean-all                     Stop all sessions and remove everything"
    echo ""
    echo "Terminal:"
    echo "  send <id|name> <command>      Send a command to a session (with Enter)"
    echo "  type <id|name> <text>         Type text into session without pressing Enter"
    echo "  read <id|name> [lines]        Read recent output from a session"
    exit 1
}

[ $# -lt 1 ] && usage

source "$PROJECT_DIR/.venv/bin/activate"

case "$1" in
    --version|-V) python -m iterm2_control_mcp --version ;;
    new)         shift; python -m iterm2_control_mcp new "$@" ;;
    acquire)     shift; python -m iterm2_control_mcp acquire "$@" ;;
    connect)     shift; python -m iterm2_control_mcp connect "$@" ;;
    detach)      shift; python -m iterm2_control_mcp detach "$@" ;;
    rename)      shift; python -m iterm2_control_mcp rename "$@" ;;
    focus)       shift; python -m iterm2_control_mcp focus "$@" ;;
    split)       shift; python -m iterm2_control_mcp split "$@" ;;
    send)        shift; python -m iterm2_control_mcp send "$@" ;;
    type)        shift; python -m iterm2_control_mcp type "$@" ;;
    read)        shift; python -m iterm2_control_mcp read "$@" ;;
    list)        python -m iterm2_control_mcp list ;;
    stop)        shift; python -m iterm2_control_mcp stop "$@" ;;
    clean)       python -m iterm2_control_mcp clean ;;
    clean-all)   python -m iterm2_control_mcp clean-all ;;
    *)           usage ;;
esac
