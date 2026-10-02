from __future__ import annotations

import importlib.util
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load_helper():
    spec = importlib.util.spec_from_file_location("prepare_linux", ROOT / "scripts" / "prepare_linux.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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

    def test_linux_run_adds_vendor_library_path_only_when_library_exists(self) -> None:
        with mock.patch.object(self.helper, "running_on_linux", return_value=True), \
             mock.patch.object(self.helper, "VENDOR_LIB", Path("C:/vendor/lib")), \
             mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(self.helper.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(self.helper.run_rankeddojo(), 0)
        environment = run.call_args.kwargs["env"]
        self.assertIn("LD_LIBRARY_PATH", environment)

    def test_non_linux_run_does_not_add_linux_library_path(self) -> None:
        with mock.patch.object(self.helper, "running_on_linux", return_value=False), \
             mock.patch.dict(self.helper.os.environ, {}, clear=True), \
             mock.patch.object(self.helper.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(self.helper.run_rankeddojo(), 0)
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("LD_LIBRARY_PATH", environment)


if __name__ == "__main__":
    unittest.main()
