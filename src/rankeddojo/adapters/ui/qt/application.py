from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication


def create_application() -> QApplication:
    return QApplication(sys.argv)
