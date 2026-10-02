"""Prepare the minimal local Linux Qt dependency and start RankedDojo.

Only Ubuntu 22.04 amd64 uses the pinned Ubuntu package fallback. No system
package manager, sudo, or system directory is touched.
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import lzma
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = ROOT / ".vendor" / "linux"
VENDOR_LIB = VENDOR_ROOT / "lib"
CACHE_DIR = VENDOR_ROOT / "cache"
LIBRARY_NAME = "libxcb-cursor.so.0"
DEB_NAME = "libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb"
DEB_URL = (
    "https://archive.ubuntu.com/ubuntu/pool/universe/x/xcb-util-cursor/"
    + DEB_NAME
)
DEB_SHA256 = "c9b5d1ad4af57397b1bd77e0a92750e34419def134c0282a0836ae9efc07cf64"


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
    if not sys.platform.startswith("linux"):
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_deb(path: Path) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(DEB_URL, timeout=30) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
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

    VENDOR_LIB.mkdir(parents=True, exist_ok=True)
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
        target = VENDOR_LIB / LIBRARY_NAME
        target.write_bytes(source.read())
        return target


def prepare_dependency() -> bool:
    if not running_on_linux() or system_library_available():
        return True
    if not ubuntu_jammy_amd64():
        print("libxcb-cursor.so.0 não está disponível e o bootstrap local só suporta Ubuntu 22.04 amd64.")
        print("Nenhuma biblioteca foi baixada; instale a dependência conforme a sua distribuição.")
        return False

    cached = CACHE_DIR / DEB_NAME
    if not cached.is_file() or sha256(cached) != DEB_SHA256:
        print("Preparando libxcb-cursor.so.0 no cache local .vendor/...")
        try:
            download_deb(cached)
        except (OSError, RuntimeError, urllib.error.URLError) as error:
            print(f"Não foi possível obter o pacote Ubuntu oficial: {error}")
            return False
    try:
        library = VENDOR_LIB / LIBRARY_NAME
        if not library.is_file():
            extract_library(cached)
    except (OSError, RuntimeError, tarfile.TarError, lzma.LZMAError) as error:
        print(f"Não foi possível preparar a biblioteca Qt local: {error}")
        return False
    return (VENDOR_LIB / LIBRARY_NAME).is_file()


def run_rankeddojo() -> int:
    environment = os.environ.copy()
    if running_on_linux() and (VENDOR_LIB / LIBRARY_NAME).is_file():
        current = environment.get("LD_LIBRARY_PATH")
        environment["LD_LIBRARY_PATH"] = str(VENDOR_LIB) + (os.pathsep + current if current else "")
    return subprocess.run(["rankeddojo"], cwd=ROOT, env=environment).returncode


def main(argv: list[str] | None = None) -> int:
    if not prepare_dependency():
        return 1
    if argv is None:
        argv = sys.argv[1:]
    return run_rankeddojo() if "--run" in argv else 0


if __name__ == "__main__":
    raise SystemExit(main())
