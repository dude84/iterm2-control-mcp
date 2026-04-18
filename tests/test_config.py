from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from iterm2_control_mcp.config import Config, load_config


def test_default_config() -> None:
    cfg = Config()
    assert cfg.context_lines == 1024


def test_session_paths() -> None:
    cfg = Config()
    sid = "abc12345"
    assert cfg.session_sock(sid).name == "session.sock"
    assert cfg.session_log(sid).name == "session.log"
    assert cfg.session_meta(sid).name == "meta.json"
    assert sid in str(cfg.session_dir(sid))


def test_active_session_ids_empty() -> None:
    cfg = Config(sessions_dir=Path("/tmp/nonexistent-iterm2-control-mcp-test"))
    assert cfg.active_session_ids() == []


def test_active_session_ids() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        sessions = Path(tmpdir)
        s1 = sessions / "aaa"
        s1.mkdir()
        (s1 / "session.sock").touch()
        s2 = sessions / "bbb"
        s2.mkdir()

        cfg = Config(sessions_dir=sessions)
        assert cfg.active_session_ids() == ["aaa"]


def test_load_config() -> None:
    cfg = load_config()
    assert isinstance(cfg, Config)


def test_load_config_env_cleanup_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITERM2_CONTROL_MCP_CLEANUP_ON_CLOSE", "false")
    cfg = load_config()
    assert cfg.cleanup_on_close is False


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ITERM2_CONTROL_MCP_CLEANUP_ON_CLOSE", raising=False)
    monkeypatch.delenv("ITERM2_CONTROL_MCP_THEME_PROFILE", raising=False)
    cfg = load_config()
    assert cfg.cleanup_on_close is True
    assert cfg.theme_profile == ""


def test_load_config_theme_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITERM2_CONTROL_MCP_THEME_PROFILE", "My Custom Profile")
    cfg = load_config()
    assert cfg.theme_profile == "My Custom Profile"


def test_cleanup_session_dir_removes_all_files(tmp_path: Path) -> None:
    """Including rotated log backups (Phase 6) that the old unlink loop missed."""
    cfg = Config(sessions_dir=tmp_path)
    sid = "abc12345"
    d = cfg.session_dir(sid)
    d.mkdir(parents=True)
    (d / "session.sock").touch()
    (d / "meta.json").touch()
    (d / "session.log").touch()
    (d / "session.log.1").touch()  # rotated backup
    (d / "activity").touch()

    cfg.cleanup_session_dir(sid)

    assert not d.exists()


def test_cleanup_session_dir_idempotent(tmp_path: Path) -> None:
    cfg = Config(sessions_dir=tmp_path)
    cfg.cleanup_session_dir("nonexistent")  # should not raise
