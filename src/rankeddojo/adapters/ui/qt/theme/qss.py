"""Generate the application's single QSS from tokens.

Screens do not write QSS. They mark widgets with properties (`variant`, `role`,
`status`), and this file decides appearance.
"""

from __future__ import annotations

from rankeddojo.adapters.ui.qt.theme.tokens import ThemeTokens


def build_stylesheet(t: ThemeTokens) -> str:
    r = t.radius
    # `t.bevel`/`t.bevel_width` are no longer used here (see the buttons
    # section below) -- left defined on ThemeTokens for now rather than
    # touching every theme's token set for an unrelated visual fix.
    return f"""
/* ---------- base ---------- */
QWidget {{ background: {t.background}; color: {t.text_primary}; font-family: {t.font_body}; font-size: {t.font_size}px; }}
QToolTip {{ background: {t.surface_alt}; color: {t.text_bright}; border: 1px solid {t.border_strong}; padding: 4px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t.background}; border: 0; }}

/* ---------- labels ---------- */
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ color: {t.accent}; font-family: {t.font_title}; font-size: {t.title_size}px; font-weight: 900; letter-spacing: 1px; }}
QLabel[role="title"][compact="true"] {{ font-size: {t.font_size + 5}px; letter-spacing: 0.4px; }}
QLabel[role="section"] {{ color: {t.accent}; font-size: {t.font_size}px; font-weight: 700; letter-spacing: 2px; padding-top: 6px; }}
QLabel[role="muted"] {{ color: {t.text_secondary}; }}
QLabel[role="prompt"] {{ color: {t.text_secondary}; }}
QLabel[role="meta"] {{ color: {t.text_secondary}; }}
QLabel[role="headline"] {{ color: {t.text_bright}; font-weight: 700; }}
QLabel[role="panel-caption"] {{ color: {t.text_secondary}; padding: 2px 0; }}
QLabel[role="hint"] {{ color: {t.text_secondary}; font-size: {t.font_size - 1}px; }}
QLabel[role="timer"] {{ color: {t.text_bright}; font-weight: 700; }}
QLabel[role="mono"] {{ color: {t.text_primary}; font-family: {t.font_mono}; }}
QLabel[role="activity-name"] {{ color: {t.text_bright}; font-family: {t.font_mono}; font-weight: 700; font-size: {t.font_size + 4}px; }}
QLabel[role="question"] {{ color: {t.text_secondary}; font-size: {t.font_size + 3}px; }}
QLabel[role="caption-muted"] {{ color: {t.text_secondary}; font-size: {t.font_size - 1}px; font-weight: 700; letter-spacing: 1.5px; }}
QLabel[role="pill"] {{
    background: {t.selected_background}; color: {t.success}; border: 1px solid {t.success};
    border-radius: {r + 8}px; padding: 3px 14px; font-size: {t.font_size - 1}px; font-weight: 700; letter-spacing: 1px;
}}
QLabel[role="keycap"] {{
    background: {t.surface_alt}; color: {t.text_secondary}; border: 1px solid {t.border};
    border-radius: 3px; padding: 1px 6px; font-size: {t.font_size - 1}px; font-weight: 700;
}}
QLabel[status="pass"] {{ color: {t.success}; font-weight: 700; }}
QLabel[status="fail"] {{ color: {t.fail}; font-weight: 700; }}
QLabel[status="pending"] {{ color: {t.warning}; font-weight: 700; }}
QLabel[status="muted"] {{ color: {t.text_secondary}; }}

/* ---------- containers ---------- */
QFrame[role="card"] {{ background: {t.surface_alt}; border: 1px solid {t.border}; border-radius: {r}px; }}
QFrame[role="card"][status="pending"] {{ background: {t.warning_background}; border: 1px solid {t.warning}; }}
QFrame[role="banner"] {{ border-radius: {r}px; }}
QFrame[role="banner"][status="pass"] {{ background: {t.success_background}; border: 2px solid {t.success}; }}
QFrame[role="banner"][status="fail"] {{ background: {t.fail_background}; border: 2px solid {t.fail}; }}
QFrame[role="hintbar"] {{ border-top: 1px solid {t.border}; }}
QFrame[role="trace-summary"] {{ background: {t.surface_alt}; border: 1px solid {t.border_strong}; border-radius: {r}px; }}
QWidget[role="exercise-sidebar"] {{ background: {t.surface_alt}; border-right: 1px solid {t.border}; }}
QWidget[role="exercise-footer"] {{ border-top: 1px solid {t.border}; }}
QWidget[role="dojo-header"] {{ border-bottom: 1px solid {t.border}; }}
QFrame[role="divider"] {{ background: {t.border}; border: none; }}
QProgressBar[role="progress"] {{ background: {t.border_disabled}; border: none; border-radius: 3px; }}
QProgressBar[role="progress"]::chunk {{ background: {t.accent}; border-radius: 3px; }}
QFrame[role="card"] QLabel, QFrame[role="banner"] QLabel, QFrame[role="hintbar"] QLabel, QFrame[role="trace-summary"] QLabel {{ background: transparent; }}

/* ---------- buttons ---------- */
/* Every side of a button's border uses the same color/width -- a previous
   "beveled keycap" look gave border-bottom its own, much darker/thicker
   color (`t.bevel`), which on several themes reads as a missing/clipped
   bottom edge rather than an intentional 3D effect, and was visually
   inconsistent with the flat dojo-primary/secondary/cta buttons introduced
   for the Home screen. Kept flat and uniform here instead -- across all
   states (hover/focus/pressed/checked/disabled) and all button variants. */
QPushButton {{
    background: {t.background}; color: {t.text_primary};
    border: 2px solid {t.border}; border-radius: {r}px;
    padding: 7px 12px; text-align: left; font-weight: 700;
    outline: none;
}}
QPushButton:hover, QPushButton:focus {{ background: {t.hover_background}; color: {t.text_bright}; border-color: {t.border_strong}; }}
QPushButton:pressed {{ background: {t.pressed_background}; color: {t.text_bright}; }}
QPushButton:checked {{ background: {t.selected_background}; color: {t.text_bright}; border-color: {t.border_strong}; }}
QPushButton:disabled {{ background: {t.background}; color: {t.text_disabled}; border: 2px dashed {t.border_disabled}; }}

QPushButton[variant="primary"] {{ color: {t.accent}; border: 2px solid {t.border_strong}; }}
QPushButton[variant="primary"]:hover, QPushButton[variant="primary"]:focus {{ background: {t.hover_background}; color: {t.text_bright}; }}
QPushButton[variant="primary"]:disabled {{ color: {t.text_disabled}; border: 2px dashed {t.border_disabled}; }}

QPushButton[variant="start"] {{
    color: {t.accent}; border: 2px solid {t.border_strong};
    font-family: {t.font_title}; font-size: {t.font_size + 4}px; font-weight: 900; letter-spacing: 2px;
    padding: 16px 28px; text-align: center; min-width: 280px;
}}
QPushButton[variant="start"]:hover, QPushButton[variant="start"]:focus {{ background: {t.hover_background}; color: {t.text_bright}; }}
QPushButton[variant="start"]:pressed {{ background: {t.pressed_background}; }}

QPushButton[variant="menu"] {{ min-height: 30px; padding: 10px 16px; font-size: {t.font_size + 1}px; }}

QPushButton[variant="tab"] {{
    color: {t.text_secondary}; border: 1px solid {t.border}; border-bottom: 2px solid {t.border}; padding: 6px 14px;
}}
QPushButton[variant="tab"]:hover, QPushButton[variant="tab"]:focus {{ background: {t.hover_background}; color: {t.text_bright}; border-color: {t.border_strong}; }}
QPushButton[variant="tab"]:checked {{ background: {t.selected_background}; color: {t.text_bright}; border: 1px solid {t.border_strong}; border-bottom: 2px solid {t.accent}; }}

QPushButton[variant="option"] {{
    background: transparent; color: {t.text_primary}; border: 1px solid transparent; border-radius: {r}px;
    padding: 5px 10px; font-weight: 400;
}}
QPushButton[variant="option"]:hover, QPushButton[variant="option"]:focus {{ background: {t.hover_background}; color: {t.text_bright}; border: 1px solid {t.border_strong}; }}
QPushButton[variant="option"]:checked {{ background: transparent; color: {t.text_bright}; border: 1px solid transparent; }}
QPushButton[variant="option"]:checked:hover, QPushButton[variant="option"]:checked:focus {{ background: {t.hover_background}; border: 1px solid {t.border_strong}; }}

QPushButton[variant="danger"] {{ color: {t.fail}; border: 2px solid {t.fail}; }}
QPushButton[variant="danger"]:hover, QPushButton[variant="danger"]:focus {{ background: {t.fail_background}; color: {t.fail}; border-color: {t.fail}; }}

QPushButton[variant="small"] {{ padding: 4px 10px; font-size: {t.font_size - 1}px; }}

QPushButton[variant="dojo-primary"] {{
    color: {t.text_bright}; background: {t.surface}; border: 1px solid {t.border};
    font-family: {t.font_mono}; font-weight: 700; font-size: {t.font_size + 1}px;
    text-align: center; min-width: 140px; max-width: 160px; min-height: 54px;
}}
QPushButton[variant="dojo-primary"]:hover, QPushButton[variant="dojo-primary"]:focus {{
    background: {t.hover_background}; border: 1px solid {t.border_strong}; color: {t.accent};
}}
QPushButton[variant="dojo-primary"]:pressed {{ background: {t.pressed_background}; }}

QPushButton[variant="dojo-secondary"] {{
    color: {t.text_primary}; background: {t.surface}; border: 1px solid {t.border};
    font-family: {t.font_mono}; font-weight: 700; font-size: {t.font_size}px;
    text-align: left; padding: 8px 14px; min-height: 38px; max-height: 44px;
}}
QPushButton[variant="dojo-secondary"]:hover, QPushButton[variant="dojo-secondary"]:focus {{
    background: {t.hover_background}; border: 1px solid {t.border_strong}; color: {t.text_bright};
}}
QPushButton[variant="dojo-secondary"]:pressed {{ background: {t.pressed_background}; }}

QPushButton[variant="cta"] {{
    color: {t.accent}; background: {t.surface}; border: 1px solid {t.accent};
    font-family: {t.font_mono}; font-weight: 700; font-size: {t.font_size + 1}px;
    text-align: center; min-width: 140px; max-width: 160px; min-height: 45px; max-height: 50px;
}}
QPushButton[variant="cta"]:hover, QPushButton[variant="cta"]:focus {{
    background: {t.hover_background}; border: 1px solid {t.accent}; color: {t.text_bright};
}}
QPushButton[variant="cta"]:pressed {{ background: {t.pressed_background}; color: {t.text_bright}; }}
QPushButton[variant="cta"]:disabled {{
    background: {t.background}; color: {t.text_disabled}; border: 1px dashed {t.border_disabled};
}}

/* ---------- inputs ---------- */
QComboBox, QLineEdit {{ background: {t.surface}; color: {t.text_primary}; border: 1px solid {t.border}; border-radius: {r}px; padding: 6px 8px; }}
QComboBox:hover, QComboBox:focus, QLineEdit:hover, QLineEdit:focus {{ background: {t.hover_background}; color: {t.text_bright}; border: 1px solid {t.border_strong}; }}
QComboBox QAbstractItemView {{ background: {t.surface}; color: {t.text_primary}; border: 1px solid {t.border_strong}; selection-background-color: {t.selected_background}; selection-color: {t.text_bright}; outline: 0; }}
QCheckBox, QRadioButton {{ background: transparent; color: {t.text_primary}; spacing: 8px; padding: 4px; outline: none; }}
QCheckBox:hover, QRadioButton:hover, QCheckBox:focus, QRadioButton:focus {{ color: {t.text_bright}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 12px; height: 12px; border: 1px solid {t.border_strong}; background: {t.surface}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {t.selected_background}; border: 2px solid {t.border_strong}; }}

/* ---------- text panels (subject, trace, docs) ---------- */
QTextEdit {{ background: {t.surface}; color: {t.text_primary}; border: 1px solid {t.border}; border-radius: {r}px; padding: 10px; selection-background-color: {t.selected_background}; selection-color: {t.text_bright}; }}
/* Terminal-role panels are the app's technical content: trace output,
   commands, exam-prep instructions, pack docs. They always use font_mono,
   never font_body -- readable code/commands matter more here than the
   theme's general UI typeface. */
QTextEdit[role="terminal"] {{ font-family: {t.font_mono}; }}

/* ---------- table ---------- */
QTableView, QTableWidget {{ background: {t.surface}; color: {t.text_primary}; gridline-color: {t.surface_alt}; border: 1px solid {t.border}; border-radius: {r}px; selection-background-color: {t.selected_background}; selection-color: {t.text_bright}; outline: 0; }}
QTableView::item, QTableWidget::item {{ padding: 4px 8px; border-bottom: 1px solid {t.surface_alt}; }}
QHeaderView {{ background: {t.surface}; border: 0; }}
QHeaderView::section {{ background: {t.surface_alt}; color: {t.accent}; border: 0; border-bottom: 1px solid {t.border}; padding: 7px 8px; font-weight: 700; letter-spacing: 1px; }}
QTableCornerButton::section {{ background: {t.surface_alt}; border: 0; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: {t.background}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.border}; min-height: 24px; border-radius: {min(r, 4)}px; }}
QScrollBar::handle:vertical:hover {{ background: {t.text_secondary}; }}
QScrollBar:horizontal {{ background: {t.background}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {t.border}; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- brand ---------- */
QLabel[role="logo"] {{
    background: {t.accent}; color: {t.background};
    border: 2px solid {t.accent_secondary}; border-radius: {r}px;
    font-family: {t.font_title}; font-weight: 900;
}}

/* ---------- dialogs ---------- */
QMessageBox QLabel {{ color: {t.text_primary}; }}
QMessageBox QPushButton {{ min-width: 90px; text-align: center; }}
"""
