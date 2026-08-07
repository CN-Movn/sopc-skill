"""Dedicated pySerial worker thread."""
from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event
import time

import serial
from PySide6.QtCore import QThread, Signal


@dataclass(frozen=True)
class SerialSettings:
    port: str
    baudrate: int = 115200


class SerialWorker(QThread):
    received = Signal(bytes)
    sent = Signal(bytes)
    opened = Signal(str)
    closed = Signal()
    error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._commands: Queue[tuple[str, object]] = Queue()
        self._stop = Event()
        self._serial: serial.Serial | None = None

    def open_port(self, settings: SerialSettings) -> None:
        self._commands.put(("open", settings))

    def close_port(self) -> None:
        self._commands.put(("close", None))

    def send_data(self, data: bytes) -> None:
        self._commands.put(("send", bytes(data)))

    def shutdown(self) -> None:
        self._stop.set()
        self._commands.put(("close", None))
        self.wait(1500)

    def _close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except serial.SerialException:
                pass
            self._serial = None
            self.closed.emit()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                while True:
                    kind, value = self._commands.get_nowait()
                    if kind == "close":
                        self._close()
                    elif kind == "open":
                        self._close()
                        try:
                            settings = value
                            self._serial = serial.Serial(settings.port, settings.baudrate,
                                                         bytesize=8, stopbits=1,
                                                         parity="N", timeout=0.03)
                            self._serial.reset_input_buffer()
                            self.opened.emit(settings.port)
                        except serial.SerialException as exc:
                            self.error.emit(f"打开串口失败：{exc}")
                    elif kind == "send":
                        if self._serial is None or not self._serial.is_open:
                            self.error.emit("串口未连接")
                        else:
                            self._serial.write(value)
                            self.sent.emit(value)
            except Empty:
                pass
            except serial.SerialException as exc:
                self.error.emit(f"串口发送异常：{exc}")
                self._close()
            if self._serial is not None and self._serial.is_open:
                try:
                    chunk = self._serial.read(self._serial.in_waiting or 1)
                    if chunk:
                        self.received.emit(chunk)
                except serial.SerialException as exc:
                    self.error.emit(f"串口接收异常：{exc}")
                    self._close()
            else:
                time.sleep(0.03)
        self._close()
