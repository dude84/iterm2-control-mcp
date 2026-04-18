from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    context_lines: int = 1024
    cleanup_on_close: bool = True
    command_markers: bool = False
    theme_profile: str = ""
    max_sessions: int = 16
    # Per-session log cap in MB. session.log rotates to .1 at this size;
    # total on-disk size per session is roughly max_log_mb * 2.
    # Set 0 to disable rotation (unbounded).
    max_log_mb: int = 16
    sessions_dir: Path = Path.home() / ".iterm2-control-mcp" / "sessions"

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def session_sock(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.sock"

    def session_log(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.log"

    def session_meta(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "meta.json"

    def session_activity(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "activity"

    def active_session_ids(self) -> list[str]:
        if not self.sessions_dir.exists():
            return []
        ids = []
        for d in sorted(self.sessions_dir.iterdir()):
            if d.is_dir() and (d / "session.sock").exists():
                ids.append(d.name)
        return ids

    def cleanup_session_dir(self, session_id: str) -> None:
        """Remove the session's directory and all files in it.

        Single source of truth for "what belongs to a session on disk"
        (audit C-8). Using `shutil.rmtree` picks up rotated log backups
        (`session.log.1`, introduced in Phase 6) and any future files
        without each call-site needing to know the full list.
        """
        import shutil

        d = self.session_dir(session_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def pid_looks_like_session_daemon(pid: int) -> bool:
    """Return True iff `pid` is live and runs a python interpreter.

    Defends against PID reuse (audit S-4): `os.kill(pid, 0)` alone only
    says "something is alive at this PID" — it could be an unrelated
    process that reused the PID slot after our daemon exited. Before we
    signal based on a PID read from `meta.json`, verify the comm looks
    like the python daemon we spawned.

    The check is a strong hint, not a proof — a different python
    process with the same PID would still pass. Good enough given the
    tiny window and the local-only threat model.
    """
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False

    if sys.platform == "darwin":
        # macOS: `ps -p <pid> -o comm=` returns just the command basename.
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True, text=True, timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        comm = result.stdout.strip()
        return "python" in comm.lower()

    # Linux / other unix: prefer /proc/<pid>/comm when available.
    proc_comm = Path(f"/proc/{pid}/comm")
    if proc_comm.exists():
        try:
            return "python" in proc_comm.read_text().strip().lower()
        except OSError:
            return False

    # Fallback: ps as above.
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "python" in result.stdout.lower()


def load_config() -> Config:
    cleanup = (
        os.getenv("ITERM2_CONTROL_MCP_CLEANUP_ON_CLOSE", "1").strip()
        not in ("0", "false", "no")
    )
    theme = os.getenv("ITERM2_CONTROL_MCP_THEME_PROFILE", "").strip()
    markers = os.getenv("ITERM2_CONTROL_MCP_COMMAND_MARKERS", "0").strip() in ("1", "true", "yes")
    max_sess = int(os.getenv("ITERM2_CONTROL_MCP_MAX_SESSIONS", "16"))
    max_log = max(0, int(os.getenv("ITERM2_CONTROL_MCP_MAX_LOG_MB", "16")))
    return Config(
        cleanup_on_close=cleanup,
        command_markers=markers,
        theme_profile=theme,
        max_sessions=max_sess,
        max_log_mb=max_log,
    )
