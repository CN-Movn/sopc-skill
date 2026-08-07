"""Asynchronous, single-outstanding MCP transaction manager."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from protocol import FrameStream, McpFrame, MessageType, Opcode, Target, build_command, decode_frame
from serial_worker import SerialWorker


Callback = Callable[[McpFrame | None, str | None], None]


@dataclass
class Request:
    target: int
    opcode: int
    payload: bytes
    callback: Callback
    priority: int = 10
    timeout_ms: int = 450
    retries: int = 1


class McpClient(QObject):
    frame_received = Signal(object)
    transaction_changed = Signal(bool)
    protocol_online = Signal(bool)
    log = Signal(str)

    def __init__(self, worker: SerialWorker, parent=None) -> None:
        super().__init__(parent)
        self.worker = worker
        self.stream = FrameStream()
        self.high: deque[Request] = deque()
        self.normal: deque[Request] = deque()
        self.current: Request | None = None
        self.current_sequence = 0
        self.sequence = (time.monotonic_ns() ^ id(self)) & 0xFFFF
        self._online = False
        self._suspended = True
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self._timed_out)
        worker.received.connect(self._receive)
        worker.opened.connect(self.resume)
        worker.closed.connect(self.clear)

    @property
    def busy(self) -> bool:
        return self.current is not None

    def submit(self, target: Target | int, opcode: Opcode | int, payload: bytes,
               callback: Callback, *, priority: int = 10,
               timeout_ms: int = 450, retries: int = 1) -> None:
        if self._suspended:
            QTimer.singleShot(0, lambda: callback(None, "串口未连接"))
            return
        request = Request(int(target), int(opcode), bytes(payload), callback,
                          priority, timeout_ms, retries)
        (self.high if priority <= 2 else self.normal).append(request)
        self._dispatch()

    def clear(self) -> None:
        self._suspended = True
        self.timeout.stop()
        interrupted = self.current
        self.current = None
        self.high.clear()
        self.normal.clear()
        self.stream = FrameStream()
        self._set_online(False)
        self.transaction_changed.emit(False)
        if interrupted is not None:
            QTimer.singleShot(0,
                              lambda request=interrupted:
                              request.callback(None, "串口已断开"))

    def resume(self, _port: str = "") -> None:
        self.timeout.stop()
        self.current = None
        self.high.clear()
        self.normal.clear()
        self.stream = FrameStream()
        self._suspended = False
        self._set_online(False)

    def _dispatch(self) -> None:
        if self.current is not None:
            return
        queue = self.high if self.high else self.normal
        if not queue:
            self.transaction_changed.emit(False)
            return
        self.current = queue.popleft()
        self.sequence = (self.sequence + 1) & 0xFFFF or 1
        self.current_sequence = self.sequence
        self.worker.send_data(build_command(self.sequence, self.current.target,
                                            self.current.opcode, self.current.payload))
        self.timeout.start(self.current.timeout_ms)
        self.transaction_changed.emit(True)

    def _receive(self, chunk: bytes) -> None:
        for raw in self.stream.push(chunk):
            frame = decode_frame(raw)
            self.frame_received.emit(frame)
            if frame.message_type != MessageType.RESPONSE:
                continue
            if self.current is None or frame.sequence != self.current_sequence:
                self.log.emit(f"忽略非当前响应 seq={frame.sequence}")
                continue
            request, self.current = self.current, None
            self.timeout.stop()
            self._set_online(True)
            request.callback(frame, None)
            self._dispatch()

    def _timed_out(self) -> None:
        if self.current is None:
            return
        if self.current.retries:
            self.current.retries -= 1
            self.worker.send_data(build_command(self.current_sequence,
                                                self.current.target,
                                                self.current.opcode,
                                                self.current.payload))
            self.timeout.start(self.current.timeout_ms)
            return
        request, self.current = self.current, None
        self._set_online(False)
        request.callback(None, "MCP响应超时")
        self._dispatch()

    def _set_online(self, value: bool) -> None:
        if self._online != value:
            self._online = value
            self.protocol_online.emit(value)
