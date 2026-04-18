import subprocess


def _get_version() -> str:
    # Try git tag first (live during development)
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=2,
            cwd=__file__.rsplit("/", 3)[0],  # project root
        )
        if result.returncode == 0:
            v = result.stdout.strip().lstrip("v")
            if v:
                return v
    except Exception:
        pass

    # Fall back to generated version file (inside .mcpb / installed package)
    try:
        from iterm2_control_mcp._version import __version__ as scm_version

        return scm_version
    except Exception:
        pass

    # Fall back to installed package metadata
    try:
        from importlib.metadata import version

        return version("iterm2-control-mcp")
    except Exception:
        return "0.0.0"


__version__ = _get_version()
