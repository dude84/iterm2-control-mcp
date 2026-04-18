from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import secrets
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import iterm2

from iterm2_control_mcp import __version__
from iterm2_control_mcp.config import load_config, pid_looks_like_session_daemon
from iterm2_control_mcp.cowork.client import call_session
from iterm2_control_mcp.cowork.server import CoworkServer, DaemonHandler
from iterm2_control_mcp.terminal.profile import apply_theme, restore_theme
from iterm2_control_mcp.terminal.session import SessionObserver

logger = logging.getLogger(__name__)


def _generate_session_id() -> str:
    return secrets.token_hex(4)


def _write_secure(path: Path, content: str) -> None:
    """Write text to `path` and chmod 0o600. Defensive in case the daemon
    umask was altered before the write.
    """
    path.write_text(content)
    path.chmod(0o600)


def _write_meta(  # type: ignore[no-untyped-def]
    config,
    session_id: str,
    iterm_session_id: str | None,
    pid: int,
    name: str | None = None,
) -> None:
    meta: dict[str, object] = {
        "session_id": session_id,
        "version": __version__,
        "iterm_session": iterm_session_id,
        "started_at": datetime.now(UTC).isoformat(),
        "pid": pid,
        "attached": iterm_session_id is not None,
    }
    if name:
        meta["name"] = name
    _write_secure(config.session_meta(session_id), json.dumps(meta, indent=2))


def _update_meta_attachment(config, session_id: str, iterm_session_id: str | None) -> None:  # type: ignore[no-untyped-def]
    meta_path = config.session_meta(session_id)
    if not meta_path.exists():
        return
    meta = json.loads(meta_path.read_text())
    meta["iterm_session"] = iterm_session_id
    meta["attached"] = iterm_session_id is not None
    _write_secure(meta_path, json.dumps(meta, indent=2))


def _cleanup_session(config, session_id: str) -> None:  # type: ignore[no-untyped-def]
    config.cleanup_session_dir(session_id)


async def _find_session(
    connection: iterm2.Connection,
    iterm_session_id: str | None,
) -> tuple[iterm2.Session | None, iterm2.Window | None]:
    app = await iterm2.async_get_app(connection)
    if iterm_session_id:
        # A specific iTerm2 session was requested — return it if found, or
        # nothing. Do NOT silently fall back to the current terminal window,
        # which would attach the daemon to the wrong terminal.
        for window in app.terminal_windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    if session.session_id == iterm_session_id:
                        return session, window
        return None, None
    if app.current_terminal_window:
        w = app.current_terminal_window
        return w.current_tab.current_session, w
    return None, None


async def _cancel_task(task: asyncio.Task[None] | None) -> None:
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _run_session(
    connection: iterm2.Connection,
    session_id: str,
    name: str | None = None,
    iterm_session_id: str | None = None,
) -> None:
    # Files created by this daemon should be owner-only (0o600), dirs 0o700.
    # `session.log` may contain command text including user-typed secrets, so
    # the default 0o644 from FileHandler is wrong.
    os.umask(0o077)

    config = load_config()
    start_time = datetime.now(UTC)

    # Tighten perms on parent dirs in case they were created with a looser
    # umask by an earlier version of this daemon.
    top_dir = config.sessions_dir.parent
    if top_dir.exists():
        try:
            top_dir.chmod(0o700)
        except OSError:
            pass
    if config.sessions_dir.exists():
        try:
            config.sessions_dir.chmod(0o700)
        except OSError:
            pass

    session_dir = config.session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    session_dir.chmod(0o700)

    log_path = config.session_log(session_id)
    # Rotate at config.max_log_mb to bound on-disk usage per session.
    # backupCount=1 keeps session.log + session.log.1 (≈ 2× cap total).
    # max_log_mb=0 means no rotation (legacy, unbounded).
    if config.max_log_mb > 0:
        file_handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=config.max_log_mb * 1024 * 1024,
            backupCount=1,
        )
    else:
        file_handler = logging.FileHandler(log_path)
    # FileHandler opens with O_APPEND + default mode; tighten post-hoc.
    try:
        log_path.chmod(0o600)
    except OSError:
        pass
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"),
    )
    logging.root.addHandler(file_handler)

    display_name = f"{session_id} ({name})" if name else session_id
    title = f"iTerm2 Control: {display_name}"
    logger.info(
        "iterm2-control-mcp %s — starting session %s (pid %d)",
        __version__, display_name, os.getpid(),
    )
    logger.info(
        "Config: markers=%s, theme=%s, cleanup=%s, max_log_mb=%s",
        config.command_markers, config.theme_profile,
        config.cleanup_on_close, config.max_log_mb,
    )

    # Mutable state for attach/detach
    current_session: iterm2.Session | None = None
    current_window: iterm2.Window | None = None
    observe_task: asyncio.Task[None] | None = None
    termination_task: asyncio.Task[None] | None = None
    prompt_task: asyncio.Task[None] | None = None

    stop_event = asyncio.Event()
    shutting_down = False
    close_on_stop = False

    def _start_termination_watch(watched_session: iterm2.Session) -> asyncio.Task[None]:
        watched_id = watched_session.session_id

        async def _watch() -> None:
            async with iterm2.SessionTerminationMonitor(connection) as mon:
                while True:
                    terminated_id = await mon.async_get()
                    if terminated_id == watched_id:
                        if not shutting_down:
                            logger.info("Terminal closed, detaching session %s", session_id)
                            await do_detach()
                        return

        return asyncio.create_task(_watch())

    async def _watch_prompts(observer: SessionObserver, iterm_session_id: str) -> None:
        """Feed shell-integration (OSC 133) events into the observer.

        If the remote has iTerm2 shell integration installed, events fire
        naturally. If not, this task blocks forever on async_get without
        consuming resources.
        """
        try:
            async with iterm2.PromptMonitor(
                connection,
                iterm_session_id,
                modes=[
                    iterm2.PromptMonitor.Mode.PROMPT,
                    iterm2.PromptMonitor.Mode.COMMAND_START,
                    iterm2.PromptMonitor.Mode.COMMAND_END,
                ],
            ) as mon:
                while True:
                    mode, payload = await mon.async_get()
                    if mode == iterm2.PromptMonitor.Mode.PROMPT:
                        observer.notify_prompt()
                    elif mode == iterm2.PromptMonitor.Mode.COMMAND_START:
                        observer.notify_command_start(
                            str(payload) if payload else "",
                        )
                    elif mode == iterm2.PromptMonitor.Mode.COMMAND_END:
                        rc: int | None = None
                        if isinstance(payload, int):
                            rc = payload
                        observer.notify_command_end(rc)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("PromptMonitor task ended", exc_info=True)

    async def do_attach(new_iterm_session_id: str) -> str:
        nonlocal current_session, current_window, observe_task
        nonlocal termination_task, prompt_task

        # Detach from current terminal first
        if current_session is not None:
            await do_detach()

        new_session, new_window = await _find_session(connection, new_iterm_session_id)
        if new_session is None:
            return f"iTerm2 session {new_iterm_session_id} not found"

        logger.info("Attaching to terminal: %s", new_session.session_id)
        await apply_theme(
            new_session, title, connection,
            config.theme_profile,
        )
        if new_window is not None:
            await new_window.async_set_title(title)

        current_session = new_session
        current_window = new_window

        observer = SessionObserver(
            new_session, config.context_lines, config.command_markers,
        )
        handler.set_observer(observer)
        observe_task = asyncio.create_task(observer.start_observing())
        termination_task = _start_termination_watch(new_session)
        prompt_task = asyncio.create_task(
            _watch_prompts(observer, new_session.session_id),
        )

        _update_meta_attachment(config, session_id, new_session.session_id)
        return "attached"

    async def do_detach() -> str:
        nonlocal current_session, current_window, observe_task
        nonlocal termination_task, prompt_task

        if current_session is None:
            return "already detached"

        logger.info("Detaching from terminal: %s", current_session.session_id)

        await _cancel_task(observe_task)
        await _cancel_task(termination_task)
        await _cancel_task(prompt_task)
        observe_task = None
        termination_task = None
        prompt_task = None

        try:
            await restore_theme(current_session)
            if current_window is not None:
                title_disc = f"iTerm2 Control: {display_name} [detached]"
                await current_window.async_set_title(title_disc)
        except Exception:
            logger.exception("Failed to restore theme on detach")

        handler.set_observer(None)
        current_session = None
        current_window = None

        _update_meta_attachment(config, session_id, None)
        return "detached"

    async def do_rename(new_name: str) -> str:
        nonlocal name, display_name, title
        name = new_name
        display_name = f"{session_id} ({name})"
        title = f"iTerm2 Control: {display_name}"
        handler.set_name(new_name)
        # Update meta.json
        meta_path = config.session_meta(session_id)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            meta["name"] = new_name
            _write_secure(meta_path, json.dumps(meta, indent=2))
        # Update window title if attached
        if current_window is not None:
            await current_window.async_set_title(title)
        if current_session is not None:
            await current_session.async_set_name(title)
        logger.info("Renamed session to: %s", new_name)
        return "renamed"

    async def do_focus() -> str:
        if current_window is None:
            return "no window to focus"
        await current_window.async_activate()
        return "focused"

    async def do_split(vertical: bool) -> str:
        if current_session is None:
            return "no session to split"
        new_session = await current_session.async_split_pane(vertical=vertical)
        return str(new_session.session_id)

    async def do_stop(close_window: bool) -> str:
        nonlocal shutting_down, close_on_stop
        close_on_stop = close_window
        if not shutting_down:
            shutting_down = True
            stop_event.set()
        return "stopping"

    # Initial setup
    handler = DaemonHandler(
        observer=SessionObserver.__new__(SessionObserver),  # placeholder, replaced by attach
        session_id=session_id,
        start_time=start_time,
        name=name,
        activity_path=config.session_activity(session_id),
    )
    handler.set_observer(None)  # start detached
    handler.set_lifecycle_callbacks(do_attach, do_detach)
    handler.set_rename_callback(do_rename)
    handler.set_focus_callback(do_focus)
    handler.set_split_callback(do_split)
    handler.set_stop_callback(do_stop)

    cowork = CoworkServer(
        sock_path=config.session_sock(session_id),
        handler=handler,
    )
    await cowork.start()

    _write_meta(config, session_id, None, os.getpid(), name)

    # Attach to initial terminal
    if iterm_session_id:
        result = await do_attach(iterm_session_id)
        if result != "attached":
            logger.error("Could not attach to iTerm2 session: %s", result)
            await cowork.stop()
            _cleanup_session(config, session_id)
            return

    logger.info("Session %s ready", session_id)

    loop = asyncio.get_event_loop()

    def handle_signal() -> None:
        nonlocal shutting_down
        if not shutting_down:
            shutting_down = True
            stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    await stop_event.wait()

    logger.info("Stopping session %s", session_id)

    # Detach cleanly if still attached
    if current_session is not None:
        try:
            await _cancel_task(observe_task)
            await _cancel_task(termination_task)
            await _cancel_task(prompt_task)
            if close_on_stop:
                # Closing the pane makes theme/title restore redundant.
                # If this is the only pane, iTerm2 closes the window too.
                try:
                    await current_session.async_close()
                except Exception:
                    logger.exception("Failed to close pane on stop")
            else:
                await restore_theme(current_session)
                if current_window is not None:
                    await current_window.async_set_title(
                        f"iTerm2 Control: {display_name} [stopped]"
                    )
        except Exception:
            logger.exception("Failed to restore theme on stop")

    await cowork.stop()
    _cleanup_session(config, session_id)
    logger.info("Session %s stopped", session_id)


def _parse_arg(args: list[str], flag: str) -> str | None:
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(f"{flag}="):
            return arg.split("=", 1)[1]
    return None


def _parse_name_arg(args: list[str]) -> str | None:
    return _parse_arg(args, "--name")


def _check_name_unique(config, name: str) -> None:  # type: ignore[no-untyped-def]
    for sid in config.active_session_ids():
        meta_path = config.session_meta(sid)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta.get("name") == name:
                msg = f"Error: session name '{name}' already in use (session {sid})"
                print(msg, file=sys.stderr)
                sys.exit(1)


def _resolve_session(config, identifier: str) -> str | None:  # type: ignore[no-untyped-def]
    """Resolve a session ID or friendly name to a session ID."""
    if config.session_meta(identifier).exists():
        return identifier
    for sid in config.active_session_ids():
        meta_path = config.session_meta(sid)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta.get("name") == identifier:
                return str(sid)
    return None


def _get_current_iterm_session_id() -> str | None:
    result: list[str] = []

    async def _get(connection: iterm2.Connection) -> None:
        app = await iterm2.async_get_app(connection)
        session = app.current_terminal_window.current_tab.current_session
        result.append(session.session_id)

    iterm2.run_until_complete(_get)
    return result[0] if result else None


def cmd_acquire(args: list[str]) -> None:
    config = load_config()

    # Check for --iterm-session flag (used by split pane)
    iterm_session_id = _parse_arg(args, "--iterm-session")
    json_output = "--json" in args
    # Filter out the flag and its value from args for name parsing
    filtered_args = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--iterm-session":
            skip_next = True
            continue
        if arg.startswith("--iterm-session="):
            continue
        if arg == "--json":
            continue
        filtered_args.append(arg)

    name = filtered_args[0] if filtered_args else None
    session_id = _generate_session_id()

    if name:
        _check_name_unique(config, name)

    if not iterm_session_id:
        iterm_session_id = _get_current_iterm_session_id()
    if not iterm_session_id:
        print("Error: could not detect iTerm2 session", file=sys.stderr)
        sys.exit(1)

    log_dir = config.session_dir(session_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.session_log(session_id)

    daemon_args = [
        sys.executable, "-m", "iterm2_control_mcp", "_daemon",
        session_id, "--iterm-session", iterm_session_id,
    ]
    if name:
        daemon_args.extend(["--name", name])

    with open(log_path, "w") as log_file:
        subprocess.Popen(
            daemon_args,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    if json_output:
        print(json.dumps({
            "session_id": session_id,
            "iterm_session": iterm_session_id,
            "name": name or "",
            "socket": str(config.session_sock(session_id)),
            "log": str(log_path),
        }))
        return

    display = f"{session_id} ({name})" if name else session_id
    print(f"iterm2-control-mcp session: {display}")
    print(f"iTerm2 session: {iterm_session_id}")
    print(f"Socket: {config.session_sock(session_id)}")
    print(f"Log: {log_path}")


def cmd_daemon(session_id: str, args: list[str]) -> None:
    name = _parse_arg(args, "--name")
    iterm_session_id = _parse_arg(args, "--iterm-session")

    level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    async def run_session(connection: iterm2.Connection) -> None:
        await _run_session(connection, session_id, name, iterm_session_id)

    iterm2.run_until_complete(run_session)


def cmd_list() -> None:
    config = load_config()
    active = config.active_session_ids()
    if not active:
        print("No active sessions")
        return
    for sid in active:
        meta_path = config.session_meta(sid)
        info = sid
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            name = meta.get("name", "")
            label = f"{sid} ({name})" if name else sid
            attached = meta.get("attached", True)
            state = "attached" if attached else "detached"
            info = (
                f"{label}  [{state}]  pid={meta.get('pid', '?')}"
                f"  started={meta.get('started_at', '?')}"
            )
        print(info)


def cmd_stop(args: list[str]) -> None:
    config = load_config()

    close_window = True
    positional: list[str] = []
    for arg in args:
        if arg == "--no-close-window":
            close_window = False
        elif arg == "--close-window":
            close_window = True
        else:
            positional.append(arg)

    if not positional:
        print(
            "Usage: iterm2-control-mcp stop <id|name> [--no-close-window]",
            file=sys.stderr,
        )
        sys.exit(1)

    identifier = positional[0]
    session_id = _resolve_session(config, identifier)
    if session_id is None:
        print(f"Session '{identifier}' not found")
        return

    meta_path = config.session_meta(session_id)
    if not meta_path.exists():
        print(f"Session {session_id} not found")
        return

    # Ask the daemon to stop cleanly via RPC so it can close the pane
    # before exiting. Fall back to SIGTERM if the socket is unreachable.
    try:
        result = asyncio.run(call_session(
            session_id, "daemon_stop", {"close_window": close_window},
        ))
    except Exception:
        result = {"error": "rpc failed"}

    if isinstance(result, dict) and "error" in result:
        meta = json.loads(meta_path.read_text())
        pid = meta.get("pid")
        if pid:
            if not pid_looks_like_session_daemon(pid):
                print(
                    f"PID {pid} is no longer our session daemon "
                    f"(stale meta.json); cleaning up without signal",
                )
                _cleanup_session(config, session_id)
                return
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"Stopped session {session_id} (pid {pid}, SIGTERM fallback)")
            except ProcessLookupError:
                print(f"Process {pid} not found, cleaning up")
                _cleanup_session(config, session_id)
        return

    if close_window:
        print(f"Stopped session {session_id}, closing pane")
    else:
        print(f"Stopped session {session_id}")


def cmd_connect(identifier: str | None = None) -> None:
    config = load_config()

    if identifier is None:
        print("Usage: iterm2-control-mcp connect <id|name>", file=sys.stderr)
        sys.exit(1)

    session_id = _resolve_session(config, identifier)
    if session_id is None:
        print(f"Session '{identifier}' not found")
        return

    iterm_session_id = _get_current_iterm_session_id()
    if not iterm_session_id:
        print("Error: could not detect iTerm2 session", file=sys.stderr)
        sys.exit(1)

    result = asyncio.run(
        call_session(session_id, "daemon_attach", {"iterm_session_id": iterm_session_id})
    )
    if isinstance(result, dict) and "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
    else:
        print(f"Connected session {session_id} to terminal {iterm_session_id}")


def cmd_detach(identifier: str | None = None) -> None:
    config = load_config()

    if identifier is None:
        print("Usage: iterm2-control-mcp detach <id|name>", file=sys.stderr)
        sys.exit(1)

    session_id = _resolve_session(config, identifier)
    if session_id is None:
        print(f"Session '{identifier}' not found")
        return

    result = asyncio.run(call_session(session_id, "daemon_detach", {}))
    if isinstance(result, dict) and "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
    else:
        print(f"Detached session {session_id}")


def cmd_rename(args: list[str]) -> None:
    config = load_config()

    if len(args) < 2:
        print("Usage: iterm2-control-mcp rename <id|name> <new-name>", file=sys.stderr)
        sys.exit(1)

    identifier = args[0]
    new_name = args[1]

    session_id = _resolve_session(config, identifier)
    if session_id is None:
        print(f"Session '{identifier}' not found")
        return

    result = asyncio.run(
        call_session(session_id, "iterm_rename_session", {"name": new_name})
    )
    if isinstance(result, dict) and "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
    else:
        print(f"Renamed session {session_id} to '{new_name}'")


def cmd_focus(identifier: str | None = None) -> None:
    config = load_config()

    if identifier is None:
        print("Usage: iterm2-control-mcp focus <id|name>", file=sys.stderr)
        sys.exit(1)

    session_id = _resolve_session(config, identifier)
    if session_id is None:
        print(f"Session '{identifier}' not found")
        return

    result = asyncio.run(call_session(session_id, "iterm_focus_window", {}))
    if isinstance(result, dict) and "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
    else:
        print(f"Focused session {session_id}")


def cmd_split(args: list[str]) -> None:
    config = load_config()

    if not args:
        print("Usage: iterm2-control-mcp split <id|name> [--vertical] [name]", file=sys.stderr)
        sys.exit(1)

    identifier = args[0]
    vertical = "--vertical" in args or "-v" in args
    # Filter out flags to find optional pane name
    remaining = [a for a in args[1:] if a not in ("--vertical", "-v")]
    pane_name = remaining[0] if remaining else None

    session_id = _resolve_session(config, identifier)
    if session_id is None:
        print(f"Session '{identifier}' not found")
        return

    # Ask daemon to split the pane
    split_result = asyncio.run(
        call_session(session_id, "iterm_split_pane", {"vertical": vertical})
    )
    if isinstance(split_result, dict) and "error" in split_result:
        print(f"Error: {split_result['error']}", file=sys.stderr)
        return

    # split_result is the new iTerm2 session_id
    iterm_session_id = str(split_result)

    # Spawn a daemon for the new pane
    new_session_id = _generate_session_id()
    if pane_name:
        _check_name_unique(config, pane_name)

    log_dir = config.session_dir(new_session_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.session_log(new_session_id)

    daemon_args = [
        sys.executable, "-m", "iterm2_control_mcp", "_daemon",
        new_session_id, "--iterm-session", iterm_session_id,
    ]
    if pane_name:
        daemon_args.extend(["--name", pane_name])

    with open(log_path, "w") as log_file:
        subprocess.Popen(
            daemon_args,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    display = f"{new_session_id} ({pane_name})" if pane_name else new_session_id
    direction = "vertically" if vertical else "horizontally"
    print(f"Split {session_id} {direction}")
    print(f"New pane session: {display}")
    print(f"iTerm2 session: {iterm_session_id}")


def cmd_send(args: list[str]) -> None:
    config = load_config()

    if len(args) < 2:
        print("Usage: iterm2-control-mcp send <id|name> <command>", file=sys.stderr)
        sys.exit(1)

    identifier = args[0]
    command = " ".join(args[1:])

    session_id = _resolve_session(config, identifier)
    if session_id is None:
        print(f"Session '{identifier}' not found")
        return

    result = asyncio.run(
        call_session(session_id, "iterm_send_command", {"command": command})
    )
    if isinstance(result, dict) and "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
    else:
        print(f"Sent to {session_id}: {command}")


def cmd_type(args: list[str]) -> None:
    config = load_config()

    if len(args) < 2:
        print(
            "Usage: iterm2-control-mcp type <id|name> <text>",
            file=sys.stderr,
        )
        sys.exit(1)

    identifier = args[0]
    text = " ".join(args[1:])

    session_id = _resolve_session(config, identifier)
    if session_id is None:
        print(f"Session '{identifier}' not found")
        return

    result = asyncio.run(
        call_session(session_id, "iterm_type", {"text": text})
    )
    if isinstance(result, dict) and "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
    else:
        print(f"Typed into {session_id}: {text}")


def cmd_read(args: list[str]) -> None:
    config = load_config()

    if not args:
        print("Usage: iterm2-control-mcp read <id|name> [lines]", file=sys.stderr)
        sys.exit(1)

    identifier = args[0]
    lines = int(args[1]) if len(args) >= 2 else 50

    session_id = _resolve_session(config, identifier)
    if session_id is None:
        print(f"Session '{identifier}' not found")
        return

    result = asyncio.run(
        call_session(session_id, "iterm_read_output", {"lines": lines})
    )
    if isinstance(result, dict) and "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
    elif isinstance(result, dict):
        # New shape: { stdout, cursor, session, ... }
        print(result.get("stdout", ""))
    else:
        print(result)


def cmd_new(args: list[str]) -> None:
    config = load_config()
    json_output = "--json" in args
    positional = [a for a in args if a != "--json"]
    name = positional[0] if positional else None
    session_id = _generate_session_id()

    if name:
        _check_name_unique(config, name)

    iterm_session_id: str | None = None

    async def _create_window(connection: iterm2.Connection) -> None:
        nonlocal iterm_session_id
        result = await iterm2.rpc.async_create_tab(
            connection, profile=None, window=None, profile_customizations=None)
        ctr = result.create_tab_response
        if ctr.status != iterm2.api_pb2.CreateTabResponse.Status.Value("OK"):
            logger.error("Could not create iTerm2 window: %s",
                         iterm2.api_pb2.CreateTabResponse.Status.Name(ctr.status))
            return
        iterm_session_id = ctr.session_id

    iterm2.run_until_complete(_create_window)

    if not iterm_session_id:
        print("Error: could not create iTerm2 window", file=sys.stderr)
        sys.exit(1)

    log_dir = config.session_dir(session_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.session_log(session_id)

    daemon_args = [
        sys.executable, "-m", "iterm2_control_mcp", "_daemon",
        session_id, "--iterm-session", iterm_session_id,
    ]
    if name:
        daemon_args.extend(["--name", name])

    with open(log_path, "w") as log_file:
        subprocess.Popen(
            daemon_args,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    if json_output:
        print(json.dumps({
            "session_id": session_id,
            "iterm_session": iterm_session_id,
            "name": name or "",
            "socket": str(config.session_sock(session_id)),
            "log": str(log_path),
        }))
        return

    display = f"{session_id} ({name})" if name else session_id
    print(f"Session: {display}")
    print(f"iTerm2 session: {iterm_session_id}")
    print(f"Socket: {config.session_sock(session_id)}")
    print(f"Log: {log_path}")


def cmd_clean(force: bool = False) -> None:
    config = load_config()
    if not config.sessions_dir.exists():
        print("No sessions directory")
        return

    cleaned = 0
    for d in sorted(config.sessions_dir.iterdir()):
        if not d.is_dir():
            continue
        sid = d.name
        sock = d / "session.sock"
        meta_path = d / "meta.json"

        is_running = sock.exists()
        if is_running and force:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                pid = meta.get("pid")
                if pid and pid_looks_like_session_daemon(pid):
                    try:
                        os.kill(pid, signal.SIGTERM)
                        print(f"Stopped session {sid} (pid {pid})")
                    except ProcessLookupError:
                        pass
                elif pid:
                    print(
                        f"PID {pid} for session {sid} is not our daemon "
                        f"(likely stale); skipping signal",
                    )
            is_running = False

        if not is_running:
            import shutil
            shutil.rmtree(d)
            cleaned += 1
            print(f"Cleaned session {sid}")

    if cleaned == 0:
        print("Nothing to clean")
    else:
        print(f"Cleaned {cleaned} session(s)")


def main() -> None:
    args = sys.argv[1:]

    if not args:
        print(f"iterm2-control-mcp {__version__}")
        cmds = ("acquire|new|connect|detach|rename|focus|split"
                "|send|type|read|list|stop|clean|clean-all")
        print(f"Usage: iterm2-control-mcp [{cmds}]")
        return

    if args[0] in ("--version", "-V"):
        print(f"iterm2-control-mcp {__version__}")
        return

    if args[0] == "acquire":
        cmd_acquire(args[1:])
    elif args[0] == "new":
        cmd_new(args[1:])
    elif args[0] == "connect":
        cmd_connect(args[1] if len(args) > 1 else None)
    elif args[0] == "detach":
        cmd_detach(args[1] if len(args) > 1 else None)
    elif args[0] == "rename":
        cmd_rename(args[1:])
    elif args[0] == "focus":
        cmd_focus(args[1] if len(args) > 1 else None)
    elif args[0] == "split":
        cmd_split(args[1:])
    elif args[0] == "send":
        cmd_send(args[1:])
    elif args[0] == "type":
        cmd_type(args[1:])
    elif args[0] == "read":
        cmd_read(args[1:])
    elif args[0] == "list":
        cmd_list()
    elif args[0] == "stop":
        cmd_stop(args[1:])
    elif args[0] == "clean":
        cmd_clean(force=False)
    elif args[0] == "clean-all":
        cmd_clean(force=True)
    elif args[0] == "_daemon":
        if len(args) < 2:
            sys.exit(1)
        cmd_daemon(args[1], args[2:])
    else:
        cmds = ("acquire|new|connect|detach|rename|focus|split"
                "|send|type|read|list|stop|clean|clean-all")
        print(f"iterm2-control-mcp {__version__}: unknown command: {args[0]}", file=sys.stderr)
        print(f"Usage: iterm2-control-mcp [{cmds}]", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
