import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hosttool.config import DEFAULT_LAYOUT
from hosttool.main_window import MainWindow


def _make_window():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    return app, window


def test_public_main_window_constructs_and_closes():
    app, window = _make_window()
    assert window.title_bar.maximize_button.toolTip() == "最大化"
    if DEFAULT_LAYOUT == "workbench":
        assert window.serial_panel.port is not None
        assert window.serial_panel.send_btn.text() == "发送"
    else:
        # Dashboard has 640 + 800 px minimum child widths; the window-level
        # minimum must not contradict those constraints.
        assert window.minimumWidth() >= 1440
    window.close()
    app.processEvents()


def test_workbench_serial_asset_regressions():
    if DEFAULT_LAYOUT != "workbench":
        return
    app, window = _make_window()
    panel = window.serial_panel

    # Mature MasterController color semantics: gray header, RX green, TX blue.
    panel._append_log_entry("RX", "", b"\x01\x02")
    panel._append_log_entry("TX", "", b"\x03\x04")
    html = panel.log_view.toHtml().lower()
    assert "#72777f" in html
    assert "#237a32" in html
    assert "#1565c0" in html

    # HEX rows must adapt to the actual viewport instead of a fixed 16-byte row.
    payload = bytes(range(64))
    panel.log_view.resize(180, 220)
    app.processEvents()
    narrow = panel._format_hex_frame(payload)
    panel.log_view.resize(760, 220)
    app.processEvents()
    wide = panel._format_hex_frame(payload)
    assert narrow.count("\n") > wide.count("\n")

    # Checkbox selects periodic mode; Send performs immediate first send and
    # becomes Stop until pressed again.
    sent = []
    panel.worker.send = lambda data: sent.append(bytes(data))
    panel._connected = True
    panel.send_text.setPlainText("AA55")
    panel.periodic.setChecked(True)
    assert not panel.timer.isActive()
    panel.send_input()
    assert sent == [bytes.fromhex("AA55")]
    assert panel.timer.isActive()
    assert panel.send_btn.text() == "停止发送"
    panel.send_input()
    assert not panel.timer.isActive()
    assert panel.send_btn.text() == "发送"

    window.close()
    app.processEvents()
