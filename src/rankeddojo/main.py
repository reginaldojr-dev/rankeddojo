from __future__ import annotations

import sys

from rankeddojo.infrastructure.linux_runtime import ensure_runtime_before_qt


def main() -> int:
    if not ensure_runtime_before_qt(sys.argv[1:]):
        return 1
    from rankeddojo.desktop_app import DesktopApp

    return DesktopApp().run()


if __name__ == "__main__":
    raise SystemExit(main())
