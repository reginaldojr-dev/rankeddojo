"""Reusable components.

Screens create widgets through this module and only choose the role
(variant/role/status). Appearance comes from the active theme QSS.
"""

from __future__ import annotations

from collections.abc import Callable

from markdown_it import MarkdownIt

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QMouseEvent, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

BUTTON_VARIANTS = (
    "default",
    "primary",
    "start",
    "menu",
    "tab",
    "option",
    "danger",
    "small",
    "dojo-primary",
    "dojo-secondary",
    "cta",
)
STATUSES = ("pass", "fail", "pending", "muted", "")


def repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def button(text: str, handler: Callable[[], None] | None = None, variant: str = "default") -> QPushButton:
    if variant not in BUTTON_VARIANTS:
        raise ValueError(f"Unknown variant: {variant}")
    widget = QPushButton(text)
    widget.setProperty("variant", variant)
    widget.setProperty("baseText", text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if handler is not None:
        widget.clicked.connect(handler)
    return widget


def label(text: str = "", role: str | None = None, status: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    if status:
        widget.setProperty("status", status)
    widget.setWordWrap(wrap)
    return widget


def title_label(text: str = "") -> QLabel:
    widget = label(text, role="title")
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return widget


def section_label(text: str) -> QLabel:
    return label(text, role="section")


def logo_mark(glyph: str = "RD", size: int = 34) -> QLabel:
    """Small geometric RankedDojo brand mark.

    A generic, token-driven component: its look (fill, border, corner
    radius, title font) comes entirely from the active theme's `accent`,
    `accent_secondary`, `background` and `font_title` tokens through the
    `role="logo"` QSS rule -- there is no branching on theme id/name here or
    in the stylesheet, and no per-theme image asset. Swapping the theme
    restyles it exactly like every other widget.

    `glyph` stays a plain geometric mark (the default ``RD`` or a chevron)
    inspired by the product name/rank language -- never a samurai/katana/torii
    image.
    """
    widget = QLabel(glyph)
    widget.setProperty("role", "logo")
    widget.setFixedSize(size, size)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return widget


def pill(text: str, status: str = "ready") -> QLabel:
    """Small, discreet status chip (e.g. the Home screen's "READY" badge).

    Deliberately not a button: no hover/press affordance, just a label with
    `role="pill"` so QSS can give it a chip-like background/border purely
    from theme tokens.
    """
    widget = QLabel(text)
    widget.setProperty("role", "pill")
    widget.setProperty("status", status)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return widget


def keycap(key: str) -> QLabel:
    """Small keyboard-affordance glyph shown beside a secondary action
    (e.g. "N" next to "Study something new"). Purely visual -- it does not
    imply a bound shortcut; see the Home screen's secondary action row.
    """
    widget = QLabel(key)
    widget.setProperty("role", "keycap")
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return widget


def progress_bar(total: int, value: int) -> QProgressBar:
    """Slim, track+fill progress indicator (e.g. "LAST SESSION" completion).

    `role="progress"` keeps it token-driven (accent fill, discreet track) and
    text is hidden -- the numeric counter next to it is a separate label, so
    the bar itself stays a simple visual, not a mixed widget.
    """
    bar = QProgressBar()
    bar.setProperty("role", "progress")
    bar.setRange(0, max(total, 0))
    bar.setValue(max(0, min(value, total)) if total else 0)
    bar.setTextVisible(False)
    bar.setFixedHeight(6)
    return bar


def set_status(widget: QWidget, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")
    widget.setProperty("status", status or None)
    repolish(widget)


def card(status: str | None = None) -> QFrame:
    frame = QFrame()
    frame.setProperty("role", "card")
    if status:
        frame.setProperty("status", status)
    return frame


class OptionButton(QPushButton):
    """Full-line clickable option: `[x] level0`, `( ) All`.

    - kind="check": [x] / [ ] marker
    - kind="radio": (•) / ( ) marker; exclusivity comes from QButtonGroup
    `value` is the stable identifier; `label` is the visible text.
    """

    def __init__(
        self,
        value: str,
        kind: str = "check",
        checked: bool = False,
        caption: str = "",
        label: str | None = None,
    ) -> None:
        super().__init__()
        self._value = value
        self._label = label or value
        self._kind = kind
        self._caption = caption
        self.setProperty("variant", "option")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self._sync)
        self.setChecked(checked)
        self._sync()

    @property
    def value(self) -> str:
        return self._value

    def set_caption(self, caption: str) -> None:
        self._caption = caption
        self._sync()

    def set_label(self, label: str) -> None:
        self._label = label
        self._sync()

    def _sync(self, *_: object) -> None:
        on, off = ("[x]", "[ ]") if self._kind == "check" else ("(•)", "( )")
        text = f"{on if self.isChecked() else off} {self._label}"
        if self._caption:
            text += f"    {self._caption}"
        self.setProperty("baseText", text)
        self.setText(text)


class HintBar(QFrame):
    """Footer with keyboard shortcuts: [1-4] navigate, [Esc] back.

    `hints` is already-translated display text (key, text) pairs -- this
    widget never translates anything itself. `set_hints` updates the same
    labels in place (same length/order as construction) so a caller can
    retranslate on locale change without rebuilding the layout; see
    `MainWindow._footer`/`_retranslate_static_ui`.
    """

    def __init__(self, hints: list[tuple[str, str]]) -> None:
        super().__init__()
        self.setProperty("role", "hintbar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(18)
        self._hint_labels: list[QLabel] = []
        for key, text in hints:
            hint_label = label(f"[{key}] {text}", role="hint")
            self._hint_labels.append(hint_label)
            layout.addWidget(hint_label)
        layout.addStretch(1)

    def set_hints(self, hints: list[tuple[str, str]]) -> None:
        for (key, text), hint_label in zip(hints, self._hint_labels):
            hint_label.setText(f"[{key}] {text}")


class FeedbackBanner(QFrame):
    """Result strip (PASS/FAIL) with a short entry animation.

    Doubles as the compact, persistent status left after a FAIL's trace
    summary auto-collapses (see `TraceSummaryPanel`): the whole banner is
    clickable (`clicked`) so it can reopen that summary, and `show_result`
    always shows some explicit text -- clickability is never conveyed by
    cursor/hover alone.
    """

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "banner")
        self._icon = label("", role="headline")
        self._text = label("", wrap=True)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self._icon)
        top.addWidget(self._text, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        layout.addLayout(top)
        layout.addLayout(self._actions)
        self._animation: QPropertyAnimation | None = None
        self._clickable = False
        self.hide()

    def set_clickable(self, clickable: bool) -> None:
        self._clickable = clickable
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    @property
    def text(self) -> str:
        return self._text.text()

    @property
    def headline(self) -> str:
        return self._icon.text()

    def show_result(self, status: str, headline: str, text: str, actions: list[QPushButton] | None = None, animate: bool = True) -> None:
        self.setProperty("status", status)
        set_status(self._icon, status)
        self._icon.setText(headline)
        self._text.setText(text)
        while self._actions.count():
            item = self._actions.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for action in actions or []:
            self._actions.addWidget(action)
        self._actions.addStretch(1)
        repolish(self)
        self.show()
        if animate:
            self._fade_in()

    def clear(self) -> None:
        self.hide()
        self.setProperty("status", None)
        self.set_clickable(False)

    def _fade_in(self) -> None:
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(260)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: self.setGraphicsEffect(None))
        self._animation = animation
        animation.start()


class TraceSummaryPanel(QFrame):
    """The "trace resumido": a collapsible bottom drawer with only what's
    useful for a quick first look at a failed/content-error submission.

    Opens automatically after a FAIL (see `MainWindow._present_trace_summary`),
    then collapses itself after a short delay -- driven by the caller, not by
    this widget -- down to a single header row, so it never permanently
    shrinks the exercise screen. `FeedbackBanner`'s compact, persistent status
    is what reopens it afterwards; this widget only owns the expanded body
    and the collapse/expand animation itself.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "trace-summary")
        self._header_label = label("", role="section")
        self._toggle_button = button("[ − ]", None, "small")
        self._toggle_button.clicked.connect(self.toggle)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self._header_label, 1)
        header.addWidget(self._toggle_button)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 6, 0, 0)
        self._body_layout.setSpacing(10)

        self._open_full_button = button("", None, "small")
        actions = QHBoxLayout()
        actions.addWidget(self._open_full_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self._body)
        layout.addLayout(actions)

        self._expanded = True
        self._body_animation: QPropertyAnimation | None = None
        self.hide()

    def set_open_full_handler(self, handler: Callable[[], None]) -> None:
        self._open_full_button.clicked.connect(handler)

    def set_open_full_text(self, text: str) -> None:
        self._open_full_button.setText(text)

    def set_open_full_enabled(self, enabled: bool) -> None:
        self._open_full_button.setEnabled(enabled)

    def show_summary(self, title: str, rows: list[tuple[str, str]], animate: bool = True) -> None:
        self._header_label.setText(title)
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for field_label, value in rows:
            row = QVBoxLayout()
            row.setSpacing(2)
            row.addWidget(label(field_label, role="meta"))
            row.addWidget(label(value or "—", wrap=True, role="mono"))
            self._body_layout.addLayout(row)
        self._expanded = True
        self._body.setVisible(True)
        self._body.setGraphicsEffect(None)
        self._toggle_button.setText("[ − ]")
        self.show()
        if animate:
            self._fade_in()

    def expand(self, animate: bool = True) -> None:
        if self._expanded:
            return
        self._expanded = True
        self._toggle_button.setText("[ − ]")
        self._body.setVisible(True)
        self._body.setGraphicsEffect(None)
        if animate:
            self._fade_in()

    def collapse(self, animate: bool = True) -> None:
        if not self._expanded:
            return
        self._expanded = False
        self._toggle_button.setText("[ + ]")
        if not animate:
            self._body.setVisible(False)
            return
        effect = QGraphicsOpacityEffect(self._body)
        self._body.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self._body)
        animation.setDuration(180)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _finish() -> None:
            self._body.setVisible(False)
            self._body.setGraphicsEffect(None)

        animation.finished.connect(_finish)
        self._body_animation = animation
        animation.start()

    def toggle(self) -> None:
        if self._expanded:
            self.collapse()
        else:
            self.expand()

    def clear(self) -> None:
        self.hide()
        self._expanded = True

    def _fade_in(self) -> None:
        effect = QGraphicsOpacityEffect(self._body)
        self._body.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self._body)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: self._body.setGraphicsEffect(None))
        self._body_animation = animation
        animation.start()


class SubjectMarkdownView(QTextBrowser):
    """Read-only subject viewer.

    Pipeline: subject.md -> markdown-it-py (CommonMark, raw HTML disabled)
    -> sandboxed HTML -> QTextBrowser.setHtml() -> CSS centralized here.

    Raw HTML in the source Markdown is never parsed as HTML: markdown-it-py
    is configured with ``html=False``, so any literal "<", ">" or "&" the
    author typed (inside or outside fenced/inline code) is escaped by the
    Markdown renderer into HTML entities in the generated markup. Qt's HTML
    engine then decodes those entities back to the literal character when it
    displays the text -- the same two-step "escape for transport, decode for
    display" that any HTML consumer does. That is what makes both guarantees
    hold at once: a subject containing "<script>alert(1)</script>" shows up
    as plain, inert text (never becomes an active tag), and a fenced shell
    example containing "$> ./program" or C code containing "a < b && c > d"
    renders with the literal characters, not "&gt;"/"&lt;"/"&amp;".
    """

    EMPTY_MESSAGE = "_Subject vazio._"

    _renderer = MarkdownIt(
        "commonmark",
        {"html": False, "linkify": False, "typographer": False},
    )

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setProperty("role", "subject")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.document().setDocumentMargin(14)
        self._current_markdown = ""
        self.document().setDefaultStyleSheet(self._document_stylesheet(None))

    def set_subject_markdown(self, markdown: str) -> None:
        content = markdown if markdown.strip() else self.EMPTY_MESSAGE
        self._current_markdown = content
        self.setHtml(self._renderer.render(content))
        self.moveCursor(QTextCursor.MoveOperation.Start)

    def apply_theme(self, tokens: object) -> None:
        """Re-render with the active theme's colors/monospace font.

        `tokens` is a `ThemeTokens` (duck-typed here to avoid a
        components -> theme import); called from `MainWindow` on init and on
        every theme change, the same way table-item colors are refreshed
        elsewhere. Keeps the subject a plain, themed terminal buffer instead
        of the fixed, hardcoded-green "webview" look it had before.
        """
        self.document().setDefaultStyleSheet(self._document_stylesheet(tokens))
        if self._current_markdown:
            self.setHtml(self._renderer.render(self._current_markdown))

    @staticmethod
    def _document_stylesheet(tokens: object) -> str:
        if tokens is None:
            font_mono = 'Consolas, "Cascadia Mono", "JetBrains Mono", monospace'
            text_primary = "#e4ffe4"
            text_bright = "#b8ffb8"
            text_secondary = "#7fae7f"
            accent = "#a6f7a6"
            border = "#2f5f3b"
            surface_alt = "#102010"
        else:
            font_mono = tokens.font_mono
            text_primary = tokens.text_primary
            text_bright = tokens.text_bright
            text_secondary = tokens.text_secondary
            accent = tokens.accent
            border = tokens.border
            surface_alt = tokens.surface_alt
        return f"""
body {{
  margin: 0;
  font-family: {font_mono};
  font-size: 1.02em;
  line-height: 1.55;
  color: {text_primary};
}}
h1, h2, h3, h4 {{
  font-weight: 700;
  color: {accent};
}}
h1 {{
  margin: 2px 0 12px 0;
  font-size: 1.28em;
}}
h2 {{
  margin: 18px 0 8px 0;
  padding-bottom: 4px;
  border-bottom: 1px solid {border};
  font-size: 1.1em;
  letter-spacing: 0.03em;
}}
h3 {{
  margin: 14px 0 6px 0;
  font-size: 1.02em;
  color: {text_bright};
}}
h6 {{
  /* Demoted "Example(s)"/"Exemplo(s)" grouping heading only (see
     `subject_sections.demote_examples_heading`) -- a quiet label, not a
     section title, so it never outweighs the exercise's own instructions.
     Individual "Example 1"/"Example 2" sub-headings are untouched and keep
     the normal h2/h3 styling above. */
  margin: 16px 0 4px 0;
  font-size: 0.82em;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: {text_secondary};
}}
p {{
  margin: 6px 0 10px 0;
}}
ul, ol {{
  margin: 6px 0 10px 20px;
  padding: 0;
}}
li {{
  margin: 2px 0;
}}
code {{
  font-family: {font_mono};
  background-color: {surface_alt};
  color: {text_bright};
  border: 1px solid {border};
  padding: 1px 5px;
  white-space: pre;
}}
pre {{
  margin: 10px 0 14px 0;
  padding: 8px 10px;
  background-color: {surface_alt};
  border-left: 3px solid {accent};
  line-height: 1.4;
  white-space: pre;
}}
pre code {{
  background-color: transparent;
  border: none;
  padding: 0;
}}
blockquote {{
  margin: 8px 0 10px 8px;
  padding-left: 8px;
  border-left: 2px solid {border};
}}
hr {{
  margin: 14px 0;
  border: none;
  border-top: 1px solid {border};
}}
"""


def fade_to(stack: QStackedWidget, page: QWidget, animate: bool = True) -> None:
    """Switch screens with a short fade; remove the effect at the end to keep repaint light."""
    if stack.currentWidget() is page:
        return
    stack.setCurrentWidget(page)
    if not animate:
        return
    effect = QGraphicsOpacityEffect(page)
    page.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", page)
    animation.setDuration(150)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.finished.connect(lambda: page.setGraphicsEffect(None))
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
