"""Linux runtime bootstrap used before Qt is imported.

The source checkout keeps the downloaded package under ``.vendor``. A regular
pip installation has no checkout to use, so it falls back to the user's XDG
cache (or ``~/.cache``). No system package manager or global directory is
modified.
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import lzma
import os
import platform
import subprocess
import sys
import tarfile
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_ROOT = PACKAGE_ROOT.parents[1]
LIBRARY_NAME = "libxcb-cursor.so.0"
DEB_NAME = "libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb"
DEB_URL = (
    "https://archive.ubuntu.com/ubuntu/pool/universe/x/xcb-util-cursor/"
    + DEB_NAME
)
DEB_SHA256 = "c9b5d1ad4af57397b1bd77e0a92750e34419def134c0282a0836ae9efc07cf64"
BOOTSTRAP_ENV = "RANKEDDOJO_LINUX_BOOTSTRAPPED"


def running_on_linux() -> bool:
    return sys.platform.startswith("linux")


def system_library_available() -> bool:
    try:
        ctypes.CDLL(LIBRARY_NAME)
    except OSError:
        return False
    return True


def ubuntu_jammy_amd64() -> bool:
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        return False
    if not running_on_linux():
        return False
    release = Path("/etc/os-release")
    if not release.is_file():
        return False
    values: dict[str, str] = {}
    for line in release.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value.strip().strip('"')
    return values.get("ID") == "ubuntu" and values.get("VERSION_ID") == "22.04"


def _cache_root() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME")
    base = Path(configured) if configured else Path.home() / ".cache"
    return base / "rankeddojo" / "linux"


def _checkout_root() -> Path | None:
    if (CHECKOUT_ROOT / "pyproject.toml").is_file():
        return CHECKOUT_ROOT
    return None


def runtime_root() -> Path:
    checkout = _checkout_root()
    if checkout is not None:
        return checkout / ".vendor" / "linux"
    return _cache_root()


def vendor_lib() -> Path:
    return runtime_root() / "lib"


def cache_dir() -> Path:
    return runtime_root() / "cache"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_deb(path: Path) -> None:
    cache_dir().mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        last_error: Exception | None = None
        commands = (
            ["curl", "--fail", "--location", "--silent", "--show-error", "--connect-timeout", "30", DEB_URL, "--output", str(temporary)],
            ["wget", "--quiet", "--output-document", str(temporary), DEB_URL],
        )
        for command in commands:
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
                break
            except FileNotFoundError as error:
                last_error = error
            except subprocess.CalledProcessError as error:
                last_error = error
        else:
            raise RuntimeError("curl ou wget não está disponível para baixar o pacote Ubuntu oficial.") from last_error
        if sha256(temporary) != DEB_SHA256:
            raise RuntimeError("SHA-256 do pacote Ubuntu não corresponde ao valor fixado.")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def extract_library(deb_path: Path) -> Path:
    """Extract only libxcb-cursor from the data archive inside a .deb."""
    raw = deb_path.read_bytes()
    if not raw.startswith(b"!<arch>\n"):
        raise RuntimeError("pacote baixado não é um arquivo .deb válido.")
    offset = 8
    data_archive: bytes | None = None
    while offset + 60 <= len(raw):
        header = raw[offset : offset + 60]
        size = int(header[48:58].decode("ascii").strip())
        name = header[:16].decode("ascii").strip().rstrip("/")
        payload = raw[offset + 60 : offset + 60 + size]
        if name.startswith("data.tar"):
            data_archive = payload
            break
        offset += 60 + size + (size % 2)
    if data_archive is None:
        raise RuntimeError("pacote .deb não contém data.tar.")

    vendor_lib().mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data_archive), mode="r:*") as archive:
        member = next(
            (
                item
                for item in archive.getmembers()
                if item.name in {
                    "./usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0.0.0",
                    "usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0.0.0",
                }
            ),
            None,
        )
        if member is None or not member.isfile():
            raise RuntimeError("libxcb-cursor.so.0.0.0 não foi encontrada no pacote Ubuntu.")
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError("não foi possível ler libxcb-cursor.so.0.0.0.")
        target = vendor_lib() / LIBRARY_NAME
        target.write_bytes(source.read())
        return target


def prepare_dependency() -> bool:
    if not running_on_linux() or system_library_available():
        return True
    if (vendor_lib() / LIBRARY_NAME).is_file():
        return True
    if not ubuntu_jammy_amd64():
        print("libxcb-cursor.so.0 não está disponível e o bootstrap local só suporta Ubuntu 22.04 amd64.")
        print("Nenhuma biblioteca foi baixada; instale a dependência conforme a sua distribuição.")
        return False

    cached = cache_dir() / DEB_NAME
    if not cached.is_file() or sha256(cached) != DEB_SHA256:
        print("Preparando libxcb-cursor.so.0 no cache local...")
        try:
            download_deb(cached)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(f"Não foi possível obter o pacote Ubuntu oficial: {error}")
            return False
    try:
        library = vendor_lib() / LIBRARY_NAME
        if not library.is_file():
            extract_library(cached)
    except (OSError, RuntimeError, tarfile.TarError, lzma.LZMAError) as error:
        print(f"Não foi possível preparar a biblioteca Qt local: {error}")
        return False
    return (vendor_lib() / LIBRARY_NAME).is_file()


def _library_environment() -> dict[str, str]:
    environment = os.environ.copy()
    current = environment.get("LD_LIBRARY_PATH")
    path = str(vendor_lib())
    if current:
        path += os.pathsep + current
    environment["LD_LIBRARY_PATH"] = path
    return environment


def ensure_runtime_before_qt(argv: list[str] | None = None) -> bool:
    """Prepare Linux dependencies and re-exec before any Qt import."""
    if not running_on_linux() or system_library_available():
        return True
    if not prepare_dependency():
        return False
    if os.environ.get(BOOTSTRAP_ENV) == "1":
        return True

    if argv is None:
        argv = sys.argv[1:]
    environment = _library_environment()
    environment[BOOTSTRAP_ENV] = "1"
    os.execve(sys.executable, [sys.executable, "-m", "rankeddojo.main", *argv], environment)
    return False


def run_rankeddojo() -> int:
    if not prepare_dependency():
        return 1
    environment = (
        _library_environment()
        if running_on_linux() and (vendor_lib() / LIBRARY_NAME).is_file()
        else os.environ.copy()
    )
    return subprocess.run(["rankeddojo"], cwd=CHECKOUT_ROOT, env=environment).returncode
