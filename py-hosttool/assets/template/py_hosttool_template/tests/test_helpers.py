import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hosttool.serial_console import (
    SerialConsolePanel,
    format_hex,
    parse_hex_text,
    status_color_for_line,
)
from hosttool.serial_worker import SerialSettings, SerialWorker, port_sort_key


def test_hex_helpers():
    assert parse_hex_text("AA 55\n01") == bytes.fromhex("AA 55 01")
    assert format_hex(bytes.fromhex("AA 55 01")) == "AA 55 01"


def test_port_order():
    assert port_sort_key("COM4") < port_sort_key("COM12") < port_sort_key("USB0")


def test_status_severity_priority():
    assert status_color_for_line("[OK] recovered [ERROR] fatal") == "#d93025"


def test_serial_worker_queue_is_bounded_and_reports_full():
    worker = SerialWorker(queue_size=1)
    rejected = []
    worker.command_rejected.connect(rejected.append)

    assert worker.send(b"first") is True
    assert worker.send(b"second") is False
    assert rejected and "队列已满" in rejected[-1]


def test_serial_worker_uses_write_timeout_and_rejects_partial_write(monkeypatch):
    class FakeSerial:
        is_open = True

        def __init__(self):
            self.closed = False

        def reset_input_buffer(self):
            return None

        def write(self, data):
            return len(data) - 1

        def close(self):
            self.closed = True
            self.is_open = False

    fake = FakeSerial()
    kwargs = {}
    monkeypatch.setattr(
        "hosttool.serial_worker.serial.Serial",
        lambda **values: (kwargs.update(values) or fake),
    )
    worker = SerialWorker()
    errors = []
    sent = []
    worker.error.connect(errors.append)
    worker.sent.connect(sent.append)

    assert worker.open_port(SerialSettings("COM_TEST", 115200, write_timeout=0.25))
    worker._process_commands()
    assert kwargs["write_timeout"] == 0.25

    assert worker.send(b"abc")
    worker._process_commands()
    assert not sent
    assert errors and "只写入" in errors[-1]
    assert fake.closed


def test_serial_worker_shutdown_reports_non_running_and_timeout(monkeypatch):
    worker = SerialWorker()
    assert worker.shutdown() is True

    monkeypatch.setattr(SerialWorker, "isRunning", lambda _self: True)
    monkeypatch.setattr(SerialWorker, "wait", lambda _self, _timeout: False)
    assert worker.shutdown(5) is False


def test_serial_worker_does_not_send_after_shutdown_requested():
    class FakeSerial:
        is_open = True

        def __init__(self):
            self.writes = []

        def write(self, data):
            self.writes.append(data)
            return len(data)

        def close(self):
            self.is_open = False

    worker = SerialWorker()
    fake = FakeSerial()
    worker._serial = fake
    assert worker.send(b"must-not-send") is True
    worker._stop.set()
    worker._process_commands()
    assert fake.writes == []


def test_close_control_discards_pending_sends_before_writing():
    class FakeSerial:
        is_open = True

        def __init__(self):
            self.closed = False
            self.writes = []

        def write(self, data):
            self.writes.append(data)
            return len(data)

        def close(self):
            self.closed = True
            self.is_open = False

    worker = SerialWorker()
    fake = FakeSerial()
    worker._serial = fake
    assert worker.send(b"queued") is True
    assert worker.close_port() is True
    worker._process_commands()
    assert fake.closed is True
    assert fake.writes == []


def test_ascii_receive_keeps_raw_bytes_for_log_export_state():
    app = QApplication.instance() or QApplication([])
    panel = SerialConsolePanel(allow_child=False)
    panel.rx_ascii.setChecked(True)
    raw = b"\xffA\x00"
    panel._received(raw)

    _stamp, kind, text, stored_raw, show_hex = panel._log_entries[-1]
    assert kind == "RX"
    assert text == raw.decode("utf-8", errors="replace")
    assert stored_raw == raw
    assert show_hex is False

    assert panel.shutdown() is True
    app.processEvents()
