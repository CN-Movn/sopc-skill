"""{{APP_NAME}} entry point."""
from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from hosttool.config import APP_NAME
from hosttool.main_window import MainWindow


def main() -> int:
    smoke_test = "--smoke-test" in sys.argv[1:]
    qt_argv = [value for value in sys.argv if value != "--smoke-test"]
    app = QApplication(qt_argv)
    app.setApplicationName(APP_NAME)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    window = MainWindow()
    window.show()
    if smoke_test:
        # Exercise the real packaged entry point and the normal close path.
        # A failed worker shutdown keeps the window open; the watchdog makes
        # that failure non-zero instead of hanging a build indefinitely.
        QTimer.singleShot(0, window.close)
        QTimer.singleShot(5000, lambda: app.exit(2))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
