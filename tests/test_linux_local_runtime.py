from __future__ import annotations

import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load_helper():
    from rankeddojo.infrastructure import linux_runtime

    return linux_runtime


class LinuxLocalRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = _load_helper()

    def test_pyproject_uses_valid_project_urls_and_runtime_metadata(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = data["project"]
        self.assertEqual(project["name"], "rankeddojo")
        self.assertEqual(project["urls"]["Repository"], "https://github.com/reginaldojr-dev/rankeddojo")
        self.assertNotIn("pyinstaller", " ".join(project["dependencies"]).lower())

    def test_existing_system_library_skips_download_and_vendor_write(self) -> None:
        with mock.patch.object(self.helper, "running_on_linux", return_value=True), \
             mock.patch.object(self.helper, "system_library_available", return_value=True), \
             mock.patch.object(self.helper, "download_deb") as download:
            self.assertTrue(self.helper.prepare_dependency())
        download.assert_not_called()

    def test_unsupported_platform_does_not_use_ubuntu_binary(self) -> None:
        with mock.patch.object(self.helper, "running_on_linux", return_value=True), \
             mock.patch.object(self.helper, "system_library_available", return_value=False), \
             mock.patch.object(self.helper, "ubuntu_jammy_amd64", return_value=False), \
             mock.patch.object(self.helper, "download_deb") as download:
            self.assertFalse(self.helper.prepare_dependency())
        download.assert_not_called()

    def test_prepared_local_library_skips_download(self) -> None:
        with mock.patch.object(self.helper, "running_on_linux", return_value=True), \
             mock.patch.object(self.helper, "system_library_available", return_value=False), \
             mock.patch.object(self.helper, "vendor_lib", return_value=Path("/tmp/rd/lib")), \
             mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(self.helper, "download_deb") as download:
            self.assertTrue(self.helper.prepare_dependency())
        download.assert_not_called()

    def test_installed_runtime_uses_user_cache_outside_checkout(self) -> None:
        with mock.patch.object(self.helper, "_checkout_root", return_value=None), \
             mock.patch.dict(self.helper.os.environ, {"XDG_CACHE_HOME": "/tmp/rd-cache"}, clear=True):
            self.assertEqual(self.helper.runtime_root(), Path("/tmp/rd-cache/rankeddojo/linux"))

    def test_direct_launcher_reexecutes_once_with_vendor_library(self) -> None:
        with mock.patch.object(self.helper, "running_on_linux", return_value=True), \
             mock.patch.object(self.helper, "system_library_available", return_value=False), \
             mock.patch.object(self.helper, "prepare_dependency", return_value=True), \
             mock.patch.object(self.helper.os, "execve") as execve, \
             mock.patch.dict(self.helper.os.environ, {}, clear=True):
            self.assertFalse(self.helper.ensure_runtime_before_qt(["--profile", "demo"]))
        execve.assert_called_once()
        executable, argv, environment = execve.call_args.args
        self.assertEqual(executable, self.helper.sys.executable)
        self.assertEqual(argv, [self.helper.sys.executable, "-m", "rankeddojo.main", "--profile", "demo"])
        self.assertEqual(environment[self.helper.BOOTSTRAP_ENV], "1")
        self.assertIn("LD_LIBRARY_PATH", environment)

    def test_direct_launcher_marker_prevents_reexec_loop(self) -> None:
        with mock.patch.object(self.helper, "running_on_linux", return_value=True), \
             mock.patch.object(self.helper, "system_library_available", return_value=False), \
             mock.patch.object(self.helper, "prepare_dependency", return_value=True), \
             mock.patch.object(self.helper.os, "execve") as execve, \
             mock.patch.dict(self.helper.os.environ, {self.helper.BOOTSTRAP_ENV: "1"}, clear=True):
            self.assertTrue(self.helper.ensure_runtime_before_qt([]))
        execve.assert_not_called()

    def test_direct_launcher_preserves_non_linux_behavior(self) -> None:
        with mock.patch.object(self.helper, "running_on_linux", return_value=False), \
             mock.patch.object(self.helper, "prepare_dependency") as prepare, \
             mock.patch.object(self.helper.os, "execve") as execve:
            self.assertTrue(self.helper.ensure_runtime_before_qt(["--arg"]))
        prepare.assert_not_called()
        execve.assert_not_called()

    def test_make_uses_local_editable_install_and_helper(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("python -m pip install -e .", makefile)
        self.assertIn("python scripts/prepare_linux.py --run", makefile)
        self.assertNotIn("pip install rankeddojo", makefile)

    def test_helper_contains_no_system_package_manager_or_fixed_user_path(self) -> None:
        source = (ROOT / "scripts" / "prepare_linux.py").read_text(encoding="utf-8")
        self.assertNotIn("sudo", source)
        self.assertNotIn("apt ", source)
        self.assertNotIn("/home/", source)
        self.assertIn("VENDOR_LIB", source)

    def test_main_launcher_imports_qt_only_after_bootstrap(self) -> None:
        source = (ROOT / "src" / "rankeddojo" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("from PySide6", source)
        self.assertIn("ensure_runtime_before_qt", source)

    def test_development_wrapper_reuses_installed_runtime_helper(self) -> None:
        source = (ROOT / "scripts" / "prepare_linux.py").read_text(encoding="utf-8")
        self.assertIn("rankeddojo.infrastructure.linux_runtime", source)
        self.assertNotIn("urllib.request", source)

    def test_runtime_bootstrap_does_not_import_http_client(self) -> None:
        source = (ROOT / "src" / "rankeddojo" / "infrastructure" / "linux_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("urllib.request", source)
        self.assertIn('"curl"', source)
        self.assertIn('"wget"', source)


if __name__ == "__main__":
    unittest.main()
