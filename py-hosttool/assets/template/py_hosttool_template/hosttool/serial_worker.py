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


COMMAND_QUEUE_SIZE = 256
SEND_BATCH_SIZE = 32
DEFAULT_WRITE_TIMEOUT = 0.5


@dataclass(frozen=True)
class SerialSettings:
    port: str
    baudrate: int
    bytesize: int = 8
    stopbits: float = 1.0
    parity: str = "N"
    rtscts: bool = False
    write_timeout: float | None = DEFAULT_WRITE_TIMEOUT


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
    command_rejected = Signal(str)
    sent = Signal(bytes)

    def __init__(self, parent=None, *, queue_size: int = COMMAND_QUEUE_SIZE) -> None:
        super().__init__(parent)
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self._controls: queue.SimpleQueue[tuple[str, object | None]] = queue.SimpleQueue()
        self._sends: queue.Queue[bytes] = queue.Queue(maxsize=queue_size)
        self._stop = Event()
        self._serial: serial.Serial | None = None

    def open_port(self, settings: SerialSettings) -> bool:
        self._controls.put(("open", settings))
        return True

    def close_port(self) -> bool:
        self._controls.put(("close", None))
        return True

    def send(self, data: bytes) -> bool:
        try:
            self._sends.put_nowait(bytes(data))
        except queue.Full:
            self.command_rejected.emit("串口发送队列已满，请稍后重试。")
            return False
        return True

    def shutdown(self, timeout_ms: int = 1500) -> bool:
        if timeout_ms < 0:
            raise ValueError("timeout_ms must be non-negative")
        self._stop.set()
        if not self.isRunning():
            return True
        return bool(self.wait(timeout_ms))

    def _close(self) -> None:
        serial_port = self._serial
        if serial_port is None:
            return
        self._serial = None
        try:
            serial_port.close()
        except Exception as exc:
            self.error.emit(f"关闭串口失败：{exc}")
        self.closed.emit()

    def _discard_pending_sends(self) -> None:
        while True:
            try:
                self._sends.get_nowait()
            except queue.Empty:
                return

    def _write(self, value: bytes) -> None:
        if self._serial is None or not self._serial.is_open:
            self.error.emit("串口未打开，请先打开串口。")
            self._discard_pending_sends()
            return
        try:
            written = self._serial.write(value)
            if written != len(value):
                raise serial.SerialException(f"只写入 {written}/{len(value)} 字节")
            self.sent.emit(value)
        except Exception as exc:
            self.error.emit(f"串口发送失败：{exc}")
            self._close()
            self._discard_pending_sends()

    def _process_commands(self) -> None:
        # Lifecycle controls cannot be starved by a burst of periodic sends.
        while not self._stop.is_set():
            try:
                kind, value = self._controls.get_nowait()
            except queue.Empty:
                break
            if kind == "close":
                self._discard_pending_sends()
                self._close()
            elif kind == "open":
                assert isinstance(value, SerialSettings)
                self._discard_pending_sends()
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
                        write_timeout=value.write_timeout,
                    )
                    self._serial.reset_input_buffer()
                    self.opened.emit(value.port)
                except Exception as exc:
                    self.error.emit(f"打开串口失败：{exc}")
                    self._close()

        for _ in range(SEND_BATCH_SIZE):
            if self._stop.is_set():
                return
            try:
                value = self._sends.get_nowait()
            except queue.Empty:
                break
            self._write(value)
            if self._serial is None:
                break

    def run(self) -> None:
        while not self._stop.is_set():
            self._process_commands()
            if self._serial is not None and self._serial.is_open:
                try:
                    data = self._serial.read(self._serial.in_waiting or 1)
                    if data:
                        self.received.emit(data)
                except Exception as exc:
                    self.error.emit(f"串口接收异常：{exc}")
                    self._close()
            else:
                time.sleep(0.03)
        self._close()
