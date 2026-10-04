"""Compatibility wrapper for the local Linux development launcher."""

from __future__ import annotations

import sys

from rankeddojo.infrastructure.linux_runtime import (
    DEB_SHA256,
    DEB_URL,
    LIBRARY_NAME,
    cache_dir,
    download_deb,
    extract_library,
    prepare_dependency,
    run_rankeddojo,
    runtime_root,
    sha256,
    system_library_available,
    ubuntu_jammy_amd64,
    vendor_lib,
)

VENDOR_LIB = vendor_lib()


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "--run" in argv:
        return run_rankeddojo()
    return 0 if prepare_dependency() else 1


if __name__ == "__main__":
    raise SystemExit(main())
