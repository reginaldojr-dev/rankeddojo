"""Visual tokens.

This module is pure Python, with no PySide6: it describes what a theme is. QSS
and components only read these names, never literal colors.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    key: str
    name: str

    # surfaces
    background: str          # window background
    surface: str             # text panels (subject, trace, table)
    surface_alt: str         # cards / faixas de destaque
    hover_background: str    # hover/focus background
    selected_background: str # selected item background (tab, checked option)
    pressed_background: str  # pressed background

    # text
    accent: str              # titles, focus border, cursor, indicators
    accent_secondary: str    # second accent for components that need two; combine as they see fit
    text_primary: str        # body text
    text_bright: str         # texto em hover/selected
    text_secondary: str      # secondary labels, hints, IDs
    text_disabled: str

    # borders
    border: str              # subtle border (panels, idle buttons)
    border_strong: str       # emphasis border (hover/focus/selected)
    bevel: str               # lower button step (keycap look)
    border_disabled: str

    # semantic states, never used as the accent
    success: str             # PASS
    fail: str                # FAIL
    warning: str             # pending / in progress / attention
    fail_background: str     # destructive button hover / FAIL banner
    success_background: str  # banner de PASS
    warning_background: str = "#2a2010"  # compact warning cards / attention panels

    # typography and shape
    font_body: str = 'Consolas, "Cascadia Mono", "JetBrains Mono", "DejaVu Sans Mono", "Courier New", monospace'
    font_title: str = 'Consolas, "Cascadia Mono", "JetBrains Mono", "DejaVu Sans Mono", "Courier New", monospace'
    # Dedicated font for technical content: traces, commands, terminal panels.
    # Defaults to the same monospace stack `font_body`/`font_title` used before
    # this token existed, so themes that never set it explicitly keep looking
    # exactly as they did.
    font_mono: str = 'Consolas, "Cascadia Mono", "JetBrains Mono", "DejaVu Sans Mono", "Courier New", monospace'
    font_size: int = 13
    title_size: int = 28
    radius: int = 0
    bevel_width: int = 4

    # visual behavior
    blink_cursor: bool = True       # blinking "_" cursor in title/focus
    cursor_char: str = "_"
    animations: bool = True         # screen fade, typewriter, banner

    def get(self, token: str) -> str:
        value = getattr(self, token)
        if not isinstance(value, str):
            raise KeyError(token)
        return value
