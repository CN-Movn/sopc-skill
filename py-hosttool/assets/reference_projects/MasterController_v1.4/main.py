"""MasterController v1.4 source entry point."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from gui import MasterControllerWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MasterController v1.4")
    window = MasterControllerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
