from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from client import McpClient
from protocol import Opcode, Target
from services import DiagnosticService


class FakeWorker(QObject):
    received = Signal(bytes)
    opened = Signal(str)
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.sent = []

    def send_data(self, data):
        self.sent.append(bytes(data))


def test_disconnect_does_not_leave_reentrant_requests_queued():
    app = QApplication.instance() or QApplication([])
    worker = FakeWorker()
    client = McpClient(worker)
    callbacks = []
    worker.opened.emit("COM5")
    client.submit(Target.SYSTEM, Opcode.PING, b"",
                  lambda _frame, error: callbacks.append(error))
    assert client.busy
    worker.closed.emit()
    app.processEvents()
    assert callbacks == ["串口已断开"]
    assert not client.busy
    assert not client.high and not client.normal


def test_stop_clears_both_side_busy_latches():
    app = QApplication.instance() or QApplication([])
    worker = FakeWorker()
    client = McpClient(worker)
    service = DiagnosticService(client)
    service._side_busy = [True, True]
    service.stop()
    assert service._side_busy == [False, False]
