"""Non-GUI-thread pySerial owner with a command queue."""
from __future__ import annotations

import queue
import re
import time
from dataclasses import dataclass
from threading import Event

import serial
from PySide6.QtCore import QThread, Signal
from serial.tools import list_ports


@dataclass(frozen=True)
class SerialSettings:
    port: str
    baudrate: int
    bytesize: int = 8
    stopbits: float = 1.0
    parity: str = "N"
    rtscts: bool = False


def port_sort_key(device: str) -> tuple[int, int | str]:
    match = re.fullmatch(r"COM(\d+)", device, flags=re.IGNORECASE)
    return (0, int(match.group(1))) if match else (1, device.casefold())


def available_ports() -> list[str]:
    return sorted((item.device for item in list_ports.comports()), key=port_sort_key)


class SerialWorker(QThread):
    received = Signal(bytes)
    opened = Signal(str)
    closed = Signal()
    error = Signal(str)
    sent = Signal(bytes)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._commands: queue.Queue[tuple[str, object | None]] = queue.Queue()
        self._stop = Event()
        self._serial: serial.Serial | None = None

    def open_port(self, settings: SerialSettings) -> None:
        self._commands.put(("open", settings))

    def close_port(self) -> None:
        self._commands.put(("close", None))

    def send(self, data: bytes) -> None:
        self._commands.put(("send", bytes(data)))

    def shutdown(self) -> None:
        self._stop.set()
        self._commands.put(("close", None))
        self.wait(1500)

    def _close(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.close()
        except serial.SerialException:
            pass
        self._serial = None
        self.closed.emit()

    def _process_commands(self) -> None:
        while True:
            try:
                kind, value = self._commands.get_nowait()
            except queue.Empty:
                return
            if kind == "close":
                self._close()
            elif kind == "open":
                assert isinstance(value, SerialSettings)
                self._close()
                try:
                    self._serial = serial.Serial(
                        port=value.port,
                        baudrate=value.baudrate,
                        bytesize=value.bytesize,
                        stopbits=value.stopbits,
                        parity=value.parity,
                        rtscts=value.rtscts,
                        timeout=0.05,
                    )
                    self._serial.reset_input_buffer()
                    self.opened.emit(value.port)
                except serial.SerialException as exc:
                    self._serial = None
                    self.error.emit(f"打开串口失败：{exc}")
            elif kind == "send":
                assert isinstance(value, bytes)
                if self._serial is None or not self._serial.is_open:
                    self.error.emit("串口未打开，请先打开串口。")
                    continue
                try:
                    self._serial.write(value)
                    self.sent.emit(value)
                except serial.SerialException as exc:
                    self.error.emit(f"串口发送失败：{exc}")

    def run(self) -> None:
        while not self._stop.is_set():
            self._process_commands()
            if self._serial is not None and self._serial.is_open:
                try:
                    data = self._serial.read(self._serial.in_waiting or 1)
                    if data:
                        self.received.emit(data)
                except serial.SerialException as exc:
                    self.error.emit(f"串口接收异常：{exc}")
                    self._close()
            else:
                time.sleep(0.03)
        self._close()
