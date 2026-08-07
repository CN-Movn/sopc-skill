"""ARQ diagnostic instrument entry point."""
from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from gui import HostToolWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ARQ系统诊断工具")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    window = HostToolWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
