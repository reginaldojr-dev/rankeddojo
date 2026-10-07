from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from rankeddojo.adapters.pack.local_pack_catalog import LocalPackCatalog
from rankeddojo.adapters.pack.local_pack_importer import LocalPackImporter, PackImportError
from rankeddojo.adapters.pack.pack_security import PackSecurityError
from rankeddojo.domain.identifiers import (
    UnsafeValueError,
    parse_relative_path,
    validate_identifier,
    validate_simple_filename,
)


def write_pack(root: Path, pack_id: str = "safe_pack", level_path: str = "level0", fixture: str | None = None, reference: str | None = None) -> Path:
    exercise_dir = root / "level0" / "echo_args"
    exercise_dir.mkdir(parents=True, exist_ok=True)
    (root / "pack.json").write_text(
        json.dumps({"id": pack_id, "name": "Safe", "version": "1.0.0", "levels": [{"id": "level0", "path": level_path}]}),
        encoding="utf-8",
    )
    execution: dict[str, object] = {"type": "program_output"}
    if fixture is not None:
        execution = {"type": "function_with_main", "fixture": fixture}
    if reference is not None:
        execution = {"type": "reference_compare", "reference": reference}
    (exercise_dir / "exercise.json").write_text(
        json.dumps(
            {
                "id": "echo_args",
                "name": "Echo",
                "subject": "subject.md",
                "submission": {"filename": "echo_args.c"},
                "execution": execution,
                "tests": {"generator": "random_arguments", "expectation": "echo_arguments"},
            }
        ),
        encoding="utf-8",
    )
    (exercise_dir / "subject.md").write_text("subject", encoding="utf-8")
    return exercise_dir


class IdentifierRulesTest(unittest.TestCase):
    def test_accepts_existing_style_ids(self) -> None:
        for value in ("sample_rank", "rank02-practice", "level10", "echo_args", "Rank03"):
            self.assertEqual(validate_identifier(value), value)

    def test_rejects_unsafe_ids(self) -> None:
        for value in ("..", ".", "C:", "c:", "a.b", "a/b", "a\\b", " a", "", "-x", "CON", "nul", "x" * 65, "ação"):
            with self.subTest(value=value), self.assertRaises(UnsafeValueError):
                validate_identifier(value)

    def test_relative_paths(self) -> None:
        self.assertEqual(parse_relative_path("fixtures/main.c").as_posix(), "fixtures/main.c")
        self.assertEqual(parse_relative_path("fixtures\\main.c").as_posix(), "fixtures/main.c")
        for value in ("../x", "a/../../x", "/etc/passwd", "\\\\server\\share\\x", "C:\\x", "C:x", "a/b:stream", "", "  ", "con.txt"):
            with self.subTest(value=value), self.assertRaises(UnsafeValueError):
                parse_relative_path(value)

    def test_simple_filename(self) -> None:
        self.assertEqual(validate_simple_filename("ft_putstr.c"), "ft_putstr.c")
        for value in ("dir/file.c", "..", "C:file.c"):
            with self.subTest(value=value), self.assertRaises(UnsafeValueError):
                validate_simple_filename(value)


class ImporterSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.managed = self.root / "config" / "packs"
        self.managed.mkdir(parents=True)
        self.sentinel = self.root / "config" / "trainer.sqlite3"
        self.sentinel.write_text("user data", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _import(self, source: Path):
        return LocalPackImporter(self.managed).import_pack(source)

    def test_pack_id_dotdot_cannot_delete_config_folder(self) -> None:
        source = self.root / "evil"
        write_pack(source, pack_id="..")
        with self.assertRaises(PackImportError):
            self._import(source)
        self.assertTrue(self.sentinel.is_file())

    def test_pack_id_with_drive_is_rejected(self) -> None:
        source = self.root / "evil"
        write_pack(source, pack_id="C:")
        with self.assertRaises(PackImportError):
            self._import(source)

    def test_level_path_traversal_is_rejected(self) -> None:
        source = self.root / "evil"
        write_pack(source, level_path="../outside")
        with self.assertRaises(PackImportError):
            self._import(source)

    def test_fixture_traversal_is_rejected(self) -> None:
        source = self.root / "evil"
        write_pack(source, fixture="../../../../etc/passwd")
        with self.assertRaises(PackImportError):
            self._import(source)

    @unittest.skipIf(os.name == "nt", "creating symlinks on Windows requires privileges")
    def test_symlinks_are_rejected(self) -> None:
        source = self.root / "evil"
        exercise_dir = write_pack(source, fixture="fixtures/main.c")
        (exercise_dir / "fixtures").mkdir()
        secret = self.root / "secret.txt"
        secret.write_text("secret", encoding="utf-8")
        os.symlink(secret, exercise_dir / "fixtures" / "main.c")
        with self.assertRaises(PackImportError) as context:
            self._import(source)
        self.assertIn("Symbolic links", str(context.exception))
        self.assertFalse((self.managed / "safe_pack").exists())

    def test_zip_slip_is_rejected(self) -> None:
        zip_path = self.root / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("pack.json", "{}")
            archive.writestr("../../escaped.txt", "x")
        with self.assertRaises(PackImportError):
            self._import(zip_path)
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_zip_symlink_entry_is_rejected(self) -> None:
        zip_path = self.root / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, "/etc/passwd")
        with self.assertRaises(PackImportError):
            self._import(zip_path)

    def test_inspect_reports_executable_code_without_copying(self) -> None:
        source = self.root / "pack"
        exercise_dir = write_pack(source, reference="fixtures/reference.c")
        (exercise_dir / "fixtures").mkdir()
        (exercise_dir / "fixtures" / "reference.c").write_text("int main(void){return 0;}", encoding="utf-8")

        report = LocalPackImporter(self.managed).inspect_pack(source)

        self.assertTrue(report.has_executable_code)
        self.assertEqual(report.executable_files, ("level0/echo_args/fixtures/reference.c",))
        self.assertFalse((self.managed / "safe_pack").exists())

    def test_data_only_pack_has_no_executable_code(self) -> None:
        source = self.root / "pack"
        write_pack(source)
        self.assertFalse(LocalPackImporter(self.managed).inspect_pack(source).has_executable_code)

    def test_catalog_skips_installed_pack_that_is_now_invalid(self) -> None:
        bad = self.managed / "old"
        write_pack(bad, pack_id="old.pack")
        good = self.managed / "good"
        write_pack(good, pack_id="good")

        catalog = LocalPackCatalog(self.managed)

        self.assertEqual([pack.id for pack in catalog.list_packs()], ["good"])
            self.assertIn(str(bad), catalog.load_errors)


class ManagedPackRemovalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.managed = self.root / "managed"
        self.bundled = self.root / "bundled"
        self.managed.mkdir()
        self.bundled.mkdir()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_managed_pack_is_removed_without_touching_other_pack_or_history(self) -> None:
        write_pack(self.managed / "safe_pack")
        write_pack(self.managed / "other_pack", pack_id="other_pack")
        write_pack(self.bundled / "embedded", pack_id="embedded")
        history = self.root / "trainer.sqlite3"
        history.write_text("history", encoding="utf-8")

        catalog = LocalPackCatalog(self.managed, bundled_packs_dir=self.bundled)

        self.assertEqual(catalog.pack_origin("safe_pack"), "managed")
        self.assertEqual(catalog.pack_origin("embedded"), "embedded")
        catalog.remove_managed_pack("safe_pack")

        self.assertFalse((self.managed / "safe_pack").exists())
        self.assertTrue((self.managed / "other_pack").exists())
        self.assertTrue((self.bundled / "embedded").exists())
        self.assertEqual(history.read_text(encoding="utf-8"), "history")

    def test_embedded_and_unknown_packs_are_not_removable(self) -> None:
        write_pack(self.bundled / "embedded", pack_id="embedded")
        catalog = LocalPackCatalog(self.managed, bundled_packs_dir=self.bundled)

        with self.assertRaises(PackSecurityError):
            catalog.remove_managed_pack("embedded")
        with self.assertRaises(PackSecurityError):
            catalog.remove_managed_pack("local_dev")

    def test_path_traversal_is_rejected_before_filesystem_removal(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        catalog = LocalPackCatalog(self.managed)

        with self.assertRaises(PackSecurityError):
            catalog.remove_managed_pack("../outside")
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
