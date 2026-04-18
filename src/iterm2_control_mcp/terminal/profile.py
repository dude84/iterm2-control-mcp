from __future__ import annotations

import logging

import iterm2

logger = logging.getLogger(__name__)

_original_profiles: dict[str, iterm2.Profile] = {}


def _color(r: float, g: float, b: float, a: float = 1.0) -> iterm2.Color:
    return iterm2.Color(int(r * 255), int(g * 255), int(b * 255), int(a * 255))


def _set_color_all_modes(
    profile: iterm2.LocalWriteOnlyProfile,
    fg: iterm2.Color,
    bg: iterm2.Color,
    cursor: iterm2.Color,
) -> None:
    """Set colors for both standard and light/dark mode profiles.

    iTerm2 ignores set_background_color() etc. when 'Use separate colors
    for light and dark mode' is enabled. Setting all variants covers both.
    """
    profile.set_foreground_color(fg)
    profile.set_background_color(bg)
    profile.set_cursor_color(cursor)
    # Light/dark mode variants
    profile.set_foreground_color_light(fg)
    profile.set_foreground_color_dark(fg)
    profile.set_background_color_light(bg)
    profile.set_background_color_dark(bg)
    profile.set_cursor_color_light(cursor)
    profile.set_cursor_color_dark(cursor)


AI_MARKER = "# [ai]"


def build_claude_profile() -> iterm2.LocalWriteOnlyProfile:
    profile = iterm2.LocalWriteOnlyProfile()
    _set_color_all_modes(
        profile,
        fg=_color(0.9, 0.85, 0.75),
        bg=_color(0.1, 0.06, 0.0),
        cursor=_color(1.0, 0.42, 0.0),
    )
    profile.set_tab_color(_color(0.8, 0.33, 0.0))
    profile.set_use_tab_color(True)
    profile.set_badge_text("")
    profile.set_transparency(0.08)
    profile.set_unlimited_scrollback(True)
    return profile


def _profile_from_full(full: iterm2.Profile) -> iterm2.LocalWriteOnlyProfile:
    """Extract visual properties from a full profile into a write-only profile."""
    profile = iterm2.LocalWriteOnlyProfile()
    fg = full.foreground_color
    bg = full.background_color
    cursor = full.cursor_color
    if fg is not None and bg is not None and cursor is not None:
        _set_color_all_modes(profile, fg, bg, cursor)
    if full.tab_color is not None:
        profile.set_tab_color(full.tab_color)
    if full.use_tab_color is not None:
        profile.set_use_tab_color(full.use_tab_color)
    if full.transparency is not None:
        profile.set_transparency(full.transparency)
    profile.set_badge_text(full.badge_text if full.badge_text else "")
    profile.set_unlimited_scrollback(True)
    return profile


async def _find_profile_by_name(
    connection: iterm2.Connection, name: str,
) -> iterm2.PartialProfile | None:
    profiles = await iterm2.PartialProfile.async_query(connection)
    for p in profiles:
        if p.name == name:
            return p
    return None


async def apply_theme(
    session: iterm2.Session,
    title: str,
    connection: iterm2.Connection | None = None,
    theme_profile: str = "",
) -> None:
    original = await session.async_get_profile()
    _original_profiles[session.session_id] = original

    if theme_profile and connection is not None:
        found = await _find_profile_by_name(connection, theme_profile)
        if found is not None:
            logger.info("Using iTerm2 profile: %s", theme_profile)
            full = await found.async_get_full_profile()
            profile = _profile_from_full(full)
            await session.async_set_profile_properties(profile)
            await session.async_set_name(title)
            return
        logger.warning(
            "iTerm2 profile '%s' not found, using default theme",
            theme_profile,
        )

    profile = build_claude_profile()
    await session.async_set_profile_properties(profile)
    await session.async_set_name(title)


async def restore_theme(session: iterm2.Session) -> None:
    original = _original_profiles.pop(session.session_id, None)
    if original is None:
        return

    restore = iterm2.LocalWriteOnlyProfile()
    fg = original.foreground_color
    bg = original.background_color
    cursor = original.cursor_color
    if fg is not None and bg is not None and cursor is not None:
        _set_color_all_modes(restore, fg, bg, cursor)
    if original.tab_color is not None:
        restore.set_tab_color(original.tab_color)
    if original.use_tab_color is not None:
        restore.set_use_tab_color(original.use_tab_color)
    restore.set_badge_text(original.badge_text if original.badge_text else "")
    if original.transparency is not None:
        restore.set_transparency(original.transparency)
    await session.async_set_profile_properties(restore)
    await session.async_set_name("")
