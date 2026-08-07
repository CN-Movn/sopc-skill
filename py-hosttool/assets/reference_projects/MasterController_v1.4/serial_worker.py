"""A non-GUI-thread pySerial owner with a command queue."""
from __future__ import annotations
import queue
import time
from dataclasses import dataclass
from threading import Event
import serial
from PySide6.QtCore import QThread, Signal


@dataclass(frozen=True)
class SerialSettings:
    port: str; baudrate: int; bytesize: int = 8; stopbits: float = 1; parity: str = "N"; rtscts: bool = False


class SerialWorker(QThread):
    received = Signal(bytes)
    opened = Signal(str)
    closed = Signal()
    error = Signal(str)
    sent = Signal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent); self._commands = queue.Queue(); self._stop = Event(); self._serial = None

    def open(self, settings: SerialSettings): self._commands.put(("open", settings))
    def close_port(self): self._commands.put(("close", None))
    def send(self, data: bytes): self._commands.put(("send", bytes(data)))
    def shutdown(self): self._stop.set(); self._commands.put(("close", None)); self.wait(1500)

    def _close(self):
        if self._serial:
            try: self._serial.close()
            except serial.SerialException: pass
            self._serial = None; self.closed.emit()

    def run(self):
        while not self._stop.is_set():
            try:
                while True:
                    kind, value = self._commands.get_nowait()
                    if kind == "close": self._close()
                    elif kind == "open":
                        self._close()
                        try:
                            self._serial = serial.Serial(port=value.port, baudrate=value.baudrate, bytesize=value.bytesize, stopbits=value.stopbits, parity=value.parity, rtscts=value.rtscts, timeout=0.05)
                            self._serial.reset_input_buffer(); self.opened.emit(value.port)
                        except serial.SerialException as exc: self._serial = None; self.error.emit(f"打开串口失败：{exc}")
                    elif kind == "send":
                        if not self._serial or not self._serial.is_open: self.error.emit("串口未打开，请先打开串口。")
                        else:
                            try: self._serial.write(value); self.sent.emit(value)
                            except serial.SerialException as exc: self.error.emit(f"串口发送失败：{exc}")
            except queue.Empty: pass
            if self._serial and self._serial.is_open:
                try:
                    data = self._serial.read(self._serial.in_waiting or 1)
                    if data: self.received.emit(data)
                except serial.SerialException as exc: self.error.emit(f"串口接收异常：{exc}"); self._close()
            else: time.sleep(.03)
        self._close()
