from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout

from rankeddojo.adapters.ui.qt.components import widgets as ui
from rankeddojo.adapters.ui.qt.components.cursor import CursorController
from rankeddojo.adapters.ui.qt.theme import THEMES, ThemeManager, build_stylesheet, get_theme
from rankeddojo.adapters.ui.qt.theme.registry import DuplicateThemeError, ThemeRegistry
from rankeddojo.adapters.ui.qt.theme.themes import DEFAULT_THEME_KEY, THEME_REGISTRY
from rankeddojo.adapters.ui.qt.theme.tokens import ThemeTokens
from rankeddojo.adapters.theme.user_theme_loader import UserThemeLoader

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _rules(qss: str) -> list[tuple[str, str]]:
    # Strip QSS comments first so a comment immediately preceding a
    # selector (e.g. "/* ---- buttons ---- */\nQPushButton {...}") never
    # gets swallowed into the parsed selector text.
    qss = COMMENT.sub("", qss)
    return [(selector.strip(), body) for selector, body in RULE.findall(qss)]


class ThemeTokensTest(unittest.TestCase):
    def test_every_theme_defines_every_color_token(self) -> None:
        color_fields = [name for name, field in ThemeTokens.__dataclass_fields__.items() if field.type == "str" and name not in ("key", "name", "font_body", "font_title", "font_mono", "cursor_char")]
        for theme in THEMES.values():
            for name in color_fields:
                value = getattr(theme, name)
                self.assertRegex(value, r"^#[0-9a-fA-F]{6}$", f"{theme.key}.{name}")

    def test_every_theme_defines_accent_secondary_and_font_mono(self) -> None:
        # Schema closure for Fase 3: every theme -- old and new -- must carry
        # a valid accent_secondary color and a non-empty font_mono stack.
        for theme in THEMES.values():
            self.assertRegex(theme.accent_secondary, r"^#[0-9a-fA-F]{6}$", theme.key)
            self.assertIsInstance(theme.font_mono, str)
            self.assertTrue(theme.font_mono.strip(), theme.key)

    def test_rankeddojo_official_themes_are_registered(self) -> None:
        # The three approved RankedDojo concepts must exist with stable,
        # English keys and the approved reference colors.
        self.assertEqual(THEMES["default"].background, "#0d0f12")
        self.assertEqual(THEMES["default"].surface_alt, "#14171b")
        self.assertEqual(THEMES["default"].border, "#262b31")
        self.assertEqual(THEMES["default"].accent, "#34e0a1")
        self.assertEqual(THEMES["default"].accent_secondary, "#8b5cf6")

        self.assertEqual(THEMES["gamified"].accent, "#b833ff")
        self.assertEqual(THEMES["gamified"].accent_secondary, "#2be0a0")
        self.assertRegex(THEMES["gamified"].background, r"^#(1c1330|0b0710)$")

        self.assertEqual(THEMES["retro"].background, "#0a0e0a")
        self.assertEqual(THEMES["retro"].accent, "#39ff14")

        self.assertEqual(get_theme("default").key, "default")
        self.assertEqual(get_theme("gamified").key, "gamified")
        self.assertEqual(get_theme("retro").key, "retro")

    def test_legacy_theme_ids_remain_available_after_rankeddojo_themes(self) -> None:
        # Adding the three new official themes must never drop or rename the
        # pre-existing ones, nor invalidate a config that persisted their key.
        for legacy_key in ("terminal", "amber", "gameboy", "neon", "minimal", "paper"):
            self.assertIn(legacy_key, THEMES)
            self.assertEqual(get_theme(legacy_key).key, legacy_key)

    def test_theme_collection_grew_without_losing_anything(self) -> None:
        # Capacity check instead of a fixed count: enough themes exist to
        # cover both the legacy set and the three new RankedDojo concepts,
        # without hardcoding a total that the next phase would need to bump.
        legacy = {"terminal", "amber", "gameboy", "neon", "minimal", "paper"}
        rankeddojo = {"default", "gamified", "retro"}
        self.assertTrue(legacy.issubset(THEMES.keys()))
        self.assertTrue(rankeddojo.issubset(THEMES.keys()))

    def test_terminal_role_text_uses_font_mono(self) -> None:
        # Technical panels (trace/terminal/commands/docs) must use font_mono,
        # never the theme's general-purpose font_body.
        for theme in THEMES.values():
            rules = {selector.strip(): body for selector, body in _rules(build_stylesheet(theme))}
            self.assertIn('QTextEdit[role="terminal"]', rules)
            self.assertIn(theme.font_mono, rules['QTextEdit[role="terminal"]'])

    def test_minimal_theme_keeps_sans_body_but_mono_terminal_text(self) -> None:
        # A theme whose general UI font is sans (e.g. Minimal) must still
        # render technical content in a monospace font_mono, not its sans
        # font_body -- font_mono defaults independently of font_body.
        minimal = THEMES["minimal"]
        self.assertNotIn("monospace", minimal.font_body.lower())
        self.assertIn("monospace", minimal.font_mono.lower())
        rules = {selector.strip(): body for selector, body in _rules(build_stylesheet(minimal))}
        terminal_rule = rules['QTextEdit[role="terminal"]']
        self.assertIn(minimal.font_mono, terminal_rule)
        self.assertNotIn(minimal.font_body, terminal_rule)

    def test_default_and_gamified_ui_is_not_forced_into_mono(self) -> None:
        # The new themes must not turn the whole Default/Gamified UI mono:
        # only the terminal-role technical panels get font_mono.
        for key in ("default", "gamified"):
            theme = THEMES[key]
            self.assertNotIn("monospace", theme.font_body.lower())
            rules = {selector.strip(): body for selector, body in _rules(build_stylesheet(theme))}
            self.assertNotIn(theme.font_mono, rules["QWidget"])

    def test_logo_role_is_driven_by_generic_tokens_per_theme(self) -> None:
        # The mini logo mark must read its look from accent/accent_secondary/
        # background/font_title -- proven here by checking that each theme's
        # own token values show up in its own generated rule, with no
        # hardcoded color or theme-name branching in the stylesheet.
        for theme in THEMES.values():
            rules = {selector.strip(): body for selector, body in _rules(build_stylesheet(theme))}
            self.assertIn('QLabel[role="logo"]', rules)
            logo_rule = rules['QLabel[role="logo"]']
            self.assertIn(f"background: {theme.accent};", logo_rule)
            self.assertIn(theme.accent_secondary, logo_rule)
            self.assertIn(f"color: {theme.background};", logo_rule)
            self.assertIn(theme.font_title, logo_rule)

    def test_logo_mark_has_rankeddojo_identity_without_theme_logic(self) -> None:
        logo = ui.logo_mark()
        self.assertEqual(logo.text(), "RD")
        self.assertEqual(logo.property("role"), "logo")
        self.assertEqual(logo.size().width(), 34)
        self.assertEqual(logo.size().height(), 34)

    def test_qss_source_has_no_conditional_theme_identity_branching(self) -> None:
        # Guard against the exact anti-pattern this phase forbids: no
        # `if`/branch inside build_stylesheet may key off a theme's identity
        # (key/name). Appearance must come only from token values plugged
        # into the same rules for every theme. (Role attributes such as
        # role="terminal" are unrelated component roles, not theme
        # branching, so this checks control flow, not arbitrary substrings.)
        import ast
        import inspect
        import textwrap

        from rankeddojo.adapters.ui.qt.theme import qss as qss_module

        source = textwrap.dedent(inspect.getsource(qss_module.build_stylesheet))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                branch_source = ast.get_source_segment(source, node.test) or ""
                self.assertNotIn(".key", branch_source)
                self.assertNotIn(".name", branch_source)

    def test_button_borders_never_use_the_bevel_tokens(self) -> None:
        # The previous "beveled keycap" look gave QPushButton's bottom
        # border its own darker/thicker color (`t.bevel`/`t.bevel_width`),
        # which read as a missing/clipped bottom edge on several dark
        # themes. The contract is structural: normal buttons must not emit
        # an asymmetric bottom border. Do not compare token hex values here:
        # `bevel` can legitimately match another token such as `border`.
        # The tab variant is allowed to keep its intentional underline.
        border_bottom_re = re.compile(r"border-bottom\s*:")
        for theme in THEMES.values():
            for selector, body in _rules(build_stylesheet(theme)):
                if "QPushButton" not in selector or "tab" in selector:
                    continue
                self.assertNotRegex(body, border_bottom_re, f"{theme.key}: {selector}")

    def test_button_rules_declare_no_asymmetric_border_bottom(self) -> None:
        # A `border-bottom` declared with its own width/color (distinct from
        # the rule's `border`) is exactly the shape of the clipped-bottom-
        # edge bug. The one intentional exception is the "tab" variant's
        # selected-tab underline indicator -- a different, pre-existing UI
        # pattern (a 2px accent underline on the checked tab), not a
        # 3D-bevel effect, and out of scope for this fix.
        border_bottom_re = re.compile(r"border-bottom\s*:")
        for theme in THEMES.values():
            for selector, body in _rules(build_stylesheet(theme)):
                if "QPushButton" not in selector or "tab" in selector:
                    continue
                self.assertNotRegex(body, border_bottom_re, f"{theme.key}: {selector}")

    def test_button_border_sides_are_uniform_within_each_variant_and_state(self) -> None:
        # Every `border: <width> <style> <color>` shorthand declared on a
        # QPushButton rule (any variant, any state) must apply to all four
        # sides -- i.e. there must be no separate per-side override sitting
        # alongside it. This is the general form of the two checks above.
        side_re = re.compile(r"border-(top|right|bottom|left)\s*:")
        for theme in THEMES.values():
            for selector, body in _rules(build_stylesheet(theme)):
                if "QPushButton" not in selector or "tab" in selector:
                    continue
                if "border:" not in body:
                    continue
                self.assertNotRegex(body, side_re, f"{theme.key}: {selector}")

    def test_accent_is_never_a_large_background(self) -> None:
        for theme in THEMES.values():
            for selector, body in _rules(build_stylesheet(theme)):
                if "QPushButton" in selector or "QHeaderView" in selector or "::item" in selector or "QFrame" in selector:
                    self.assertNotIn(f"background: {theme.accent.lower()}", body.lower(), f"{theme.key}: {selector}")
                    self.assertNotIn(f"background-color: {theme.accent.lower()}", body.lower(), f"{theme.key}: {selector}")

    def test_focusable_base_rules_suppress_native_outline(self) -> None:
        # Qt draws its own native focus rectangle over QSS unless `outline`
        # is explicitly suppressed. QComboBox's dropdown view and the table
        # views already do this; QPushButton/QCheckBox/QRadioButton need it
        # too so keyboard focus never shows the OS-native box instead of the
        # theme's own border-color feedback.
        for theme in THEMES.values():
            rules = {selector.strip(): body for selector, body in _rules(build_stylesheet(theme))}
            self.assertIn("QPushButton", rules)
            self.assertIn("outline: none", rules["QPushButton"])
            self.assertIn("QCheckBox, QRadioButton", rules)
            self.assertIn("outline: none", rules["QCheckBox, QRadioButton"])

    def test_focus_still_has_visible_theme_feedback(self) -> None:
        # Suppressing the native outline must not remove the theme's own
        # focus feedback: QPushButton:focus keeps changing border-color.
        for theme in THEMES.values():
            rules = {selector.strip(): body for selector, body in _rules(build_stylesheet(theme))}
            focus_rule = rules["QPushButton:hover, QPushButton:focus"]
            self.assertIn("border-color", focus_rule)

    def test_terminal_is_default_and_unknown_key_falls_back(self) -> None:
        self.assertEqual(get_theme(None).key, "terminal")
        self.assertEqual(get_theme("does-not-exist").key, "terminal")

    def test_terminal_keeps_spec_palette(self) -> None:
        terminal = THEMES["terminal"]
        self.assertEqual(terminal.background, "#0a0e0a")
        self.assertEqual(terminal.accent, "#39ff14")
        self.assertEqual(terminal.text_secondary, "#4a6b4a")
        self.assertEqual(terminal.success, "#50fa7b")
        self.assertEqual(terminal.fail, "#ff5555")
        self.assertEqual(terminal.warning, "#f1fa8c")
        self.assertEqual(terminal.hover_background, "#102010")


class ThemeRegistryTest(unittest.TestCase):
    """Fase 4: ThemeRegistry is the single source of truth THEMES/get_theme/
    ThemeManager are all derived from -- covered independently of the QSS/token
    tests above."""

    def test_registers_all_internal_themes(self) -> None:
        self.assertEqual(set(THEME_REGISTRY.keys()), set(THEMES.keys()))
        legacy = {"terminal", "amber", "gameboy", "neon", "minimal", "paper"}
        rankeddojo = {"default", "gamified", "retro"}
        self.assertTrue(legacy.issubset(THEME_REGISTRY.keys()))
        self.assertTrue(rankeddojo.issubset(THEME_REGISTRY.keys()))

    def test_lookup_by_id_returns_the_correct_theme(self) -> None:
        for key in THEME_REGISTRY.keys():
            theme = THEME_REGISTRY.get(key)
            self.assertIsNotNone(theme)
            self.assertEqual(theme.key, key)
            # THEMES stays a faithful, derived view of the same objects.
            self.assertIs(theme, THEMES[key])

    def test_lookup_of_unknown_id_returns_none(self) -> None:
        self.assertIsNone(THEME_REGISTRY.get("does-not-exist"))
        self.assertFalse(THEME_REGISTRY.has("does-not-exist"))
        self.assertTrue(THEME_REGISTRY.has("terminal"))

    def test_listing_is_deterministic(self) -> None:
        first = THEME_REGISTRY.keys()
        second = THEME_REGISTRY.keys()
        self.assertEqual(first, second)
        self.assertEqual(tuple(theme.key for theme in THEME_REGISTRY.themes()), first)

    def test_duplicate_id_is_rejected(self) -> None:
        registry = ThemeRegistry((THEMES["terminal"],))
        with self.assertRaises(DuplicateThemeError):
            registry.register(THEMES["terminal"])
        # The failed registration did not corrupt the existing entry.
        self.assertIs(registry.get("terminal"), THEMES["terminal"])

    def test_default_theme_still_exists_and_fallback_still_works(self) -> None:
        self.assertTrue(THEME_REGISTRY.has(DEFAULT_THEME_KEY))
        self.assertEqual(THEME_REGISTRY.resolve(None, DEFAULT_THEME_KEY).key, DEFAULT_THEME_KEY)
        self.assertEqual(THEME_REGISTRY.resolve("does-not-exist", DEFAULT_THEME_KEY).key, DEFAULT_THEME_KEY)
        self.assertEqual(get_theme(None).key, DEFAULT_THEME_KEY)
        self.assertEqual(get_theme("does-not-exist").key, DEFAULT_THEME_KEY)

    def test_theme_manager_available_sees_every_registry_theme(self) -> None:
        available_keys = {tokens.key for tokens in ThemeManager.available()}
        self.assertEqual(available_keys, set(THEME_REGISTRY.keys()))


class ThemePersistenceTest(unittest.TestCase):
    def test_config_repository_round_trips_theme(self) -> None:
        import tempfile

        from rankeddojo.adapters.persistence.json_app_config_repository import JsonAppConfigRepository

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = JsonAppConfigRepository(Path(temp_dir) / "config.json")
            self.assertIsNone(repository.load_theme())
            repository.save_theme("amber")
            self.assertEqual(JsonAppConfigRepository(Path(temp_dir) / "config.json").load_theme(), "amber")


class ThemeManagerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_set_theme_restyles_registered_widgets_and_emits(self) -> None:
        manager = ThemeManager("terminal")
        widget = QWidget()
        manager.apply(widget)
        received: list[str] = []
        manager.theme_changed.connect(lambda tokens: received.append(tokens.key))

        manager.set_theme("minimal")

        self.assertEqual(received, ["minimal"])
        self.assertIn(THEMES["minimal"].background, widget.styleSheet())
        self.assertEqual(manager.color("fail").name(), THEMES["minimal"].fail)


class ExternalThemeIntegrationTest(unittest.TestCase):
    """Fase 5: the loaded-theme.json -> THEME_REGISTRY -> ThemeManager path,
    exercised without ever mutating the real process-wide THEME_REGISTRY
    singleton (each test patches in its own isolated registry and restores
    the original afterward)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _isolated_registry_with(self, tmp_path: Path) -> ThemeRegistry:
        registry = ThemeRegistry(THEMES.values())
        loader = UserThemeLoader(base=THEMES["terminal"])
        loader.load_into(registry, tmp_path)
        self.assertEqual(loader.load_errors, {}, loader.load_errors)
        return registry

    def _write_theme(self, tmp_path: Path, folder: str, theme_id: str) -> None:
        import json

        theme_dir = tmp_path / folder
        theme_dir.mkdir(parents=True)
        (theme_dir / "theme.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": theme_id,
                    "name": "External " + theme_id,
                    "tokens": {"accent": "#00ffcc"},
                }
            ),
            encoding="utf-8",
        )

    def test_external_theme_appears_in_theme_manager_available(self) -> None:
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._write_theme(tmp_path, "my-ext-theme", "my-ext-theme")
            registry = self._isolated_registry_with(tmp_path)

            with patch("rankeddojo.adapters.ui.qt.theme.manager.THEME_REGISTRY", registry):
                available_keys = {tokens.key for tokens in ThemeManager.available()}

        self.assertIn("my-ext-theme", available_keys)
        # Built-ins are still all there alongside it.
        self.assertTrue(set(THEMES.keys()).issubset(available_keys))

    def test_external_saved_theme_can_be_restored(self) -> None:
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._write_theme(tmp_path, "restorable", "restorable")
            registry = self._isolated_registry_with(tmp_path)

            # Simulates config.json having "theme": "restorable" from a
            # previous run, resolved the same way ThemeManager resolves it.
            with patch("rankeddojo.adapters.ui.qt.theme.themes.THEME_REGISTRY", registry):
                restored = get_theme("restorable")

        self.assertEqual(restored.key, "restorable")
        self.assertEqual(restored.accent, "#00ffcc")

    def test_removed_or_invalid_saved_theme_falls_back_to_default(self) -> None:
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._write_theme(tmp_path, "temporary", "temporary")
            registry = self._isolated_registry_with(tmp_path)

            with patch("rankeddojo.adapters.ui.qt.theme.themes.THEME_REGISTRY", registry):
                # "temporary" was never persisted; config.json still points at
                # a theme id that simply does not exist in this registry
                # (e.g. the user removed the folder, or it failed to load).
                fallback = get_theme("uninstalled-external-theme")

        self.assertEqual(fallback.key, DEFAULT_THEME_KEY)


class CursorControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _page(self) -> tuple[QWidget, QLabel, list]:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel()
        layout.addWidget(title)
        buttons = [ui.button(f"> ITEM {index}", None, "menu") for index in range(3)]
        for button in buttons:
            layout.addWidget(button)
        return page, title, buttons

    def test_uses_a_single_timer(self) -> None:
        holder = QWidget()
        controller = CursorController(holder)
        timers = {id(timer) for timer in holder.findChildren(QTimer)}
        self.assertEqual(len(timers), 1)
        self.assertIs(controller.timer.parent(), controller)

    def test_only_one_item_blinks_besides_the_title(self) -> None:
        page, title, buttons = self._page()
        controller = CursorController(page)
        controller.configure(enabled=True, animations=False, cursor_char="_")
        controller.register_title(title, "TREINO")
        for button in buttons:
            controller.track(button)
        controller.set_idle_target(page, buttons[2])
        page.show()
        controller.enter_page(page)

        controller._focus = buttons[0]
        controller._on = True
        controller._render()

        lit = [button for button in buttons if button.text().endswith("_")]
        self.assertEqual(lit, [buttons[0]])
        self.assertEqual(title.text(), "TREINO _")

        controller._focus = None
        controller._render()
        lit = [button for button in buttons if button.text().endswith("_")]
        self.assertEqual(lit, [buttons[2]])

    def test_disabled_cursor_shows_static_texts(self) -> None:
        page, title, buttons = self._page()
        controller = CursorController(page)
        controller.configure(enabled=False, animations=False, cursor_char="_")
        controller.register_title(title, "HISTÓRICO")
        controller.set_idle_target(page, buttons[0])
        page.show()
        controller.enter_page(page)

        self.assertEqual(title.text(), "HISTÓRICO")
        self.assertEqual(buttons[0].text(), "> ITEM 0")


class OptionButtonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_check_and_radio_markers(self) -> None:
        check = ui.OptionButton("level0", kind="check", checked=True)
        radio = ui.OptionButton("Todos", kind="radio")
        self.assertEqual(check.text(), "[x] level0")
        self.assertEqual(radio.text(), "( ) Todos")
        check.setChecked(False)
        radio.setChecked(True)
        self.assertEqual(check.text(), "[ ] level0")
        self.assertEqual(radio.text(), "(•) Todos")
        self.assertEqual(check.value, "level0")


if __name__ == "__main__":
    unittest.main()
