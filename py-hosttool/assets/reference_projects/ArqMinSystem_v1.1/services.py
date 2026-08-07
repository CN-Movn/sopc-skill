"""Diagnostic refresh scheduler and hardware configuration façade."""
from __future__ import annotations

import struct

from PySide6.QtCore import QObject, QTimer, Signal

from client import McpClient
from protocol import Opcode, Status, Target, parse_protocol_stats, parse_register_fragment
from registers import ALICE_INSTANCES, BOB_INSTANCES, InstanceSpec


class DiagnosticService(QObject):
    instance_updated = Signal(int, object)
    instance_failed = Signal(int, str)
    protocol_updated = Signal(object)
    cycle_updated = Signal(str, int)

    def __init__(self, client: McpClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.period_ms = 1000
        self.side = 0
        self.capture_id = 0
        self.running = False
        self._side_busy = [False, False]
        self._generation = 0

    def start(self, period_ms: int = 1000) -> None:
        self._generation += 1
        self.period_ms = max(500, int(period_ms))
        self.running = True
        self.side = 0
        self._side_busy = [False, False]
        self.timer.start(max(200, self.period_ms // 2))
        self._tick()

    def stop(self) -> None:
        self.running = False
        self.timer.stop()
        self._generation += 1
        self._side_busy = [False, False]

    def refresh_instance(self, instance: InstanceSpec) -> None:
        self._capture(instance, lambda: None)

    def _tick(self) -> None:
        if not self.running:
            return
        side = self.side
        self.side ^= 1
        if self._side_busy[side]:
            return
        self._side_busy[side] = True
        generation = self._generation
        instances = ALICE_INSTANCES if side == 0 else BOB_INSTANCES
        started = self.capture_id
        self._capture_sequence(
            instances, 0,
            lambda: self._finish_side(side, started, generation), generation)

    def _finish_side(self, side: int, started: int, generation: int) -> None:
        if generation != self._generation or not self.running:
            return
        self._side_busy[side] = False
        self.cycle_updated.emit("Alice" if side == 0 else "Bob", started)
        if side == 1:
            self.client.submit(Target.SYSTEM, Opcode.GET_PROTOCOL_STATS, b"",
                               self._protocol_done, priority=11)

    def _capture_sequence(self, items: tuple[InstanceSpec, ...], index: int,
                          done, generation: int) -> None:
        if generation != self._generation or not self.running:
            return
        if index >= len(items):
            done()
            return
        self._capture(
            items[index],
            lambda: self._capture_sequence(items, index + 1, done, generation),
            generation)

    def _capture(self, instance: InstanceSpec, done,
                 generation: int | None = None) -> None:
        if generation is None:
            generation = self._generation
        self.capture_id = (self.capture_id + 1) & 0xFFFF or 1
        capture_id = self.capture_id
        values: dict[int, int] = {}

        def request_part(part: int) -> None:
            payload = struct.pack("<HBB", capture_id, part, 0)
            self.client.submit(instance.target, Opcode.READ_REG_BLOCK, payload,
                               response, priority=10, timeout_ms=700)

        def response(frame, error) -> None:
            if generation != self._generation:
                return
            if error or frame is None:
                self.instance_failed.emit(int(instance.target), error or "无响应")
                done()
                return
            try:
                fragment = parse_register_fragment(frame)
            except ValueError as exc:
                self.instance_failed.emit(int(instance.target), str(exc))
                done()
                return
            values.update(fragment.values)
            if fragment.index + 1 < fragment.count:
                request_part(fragment.index + 1)
            else:
                self.instance_updated.emit(int(instance.target), values)
                done()

        request_part(0)

    def _protocol_done(self, frame, error) -> None:
        if error or frame is None:
            return
        try:
            self.protocol_updated.emit(parse_protocol_stats(frame))
        except ValueError:
            pass


class ControlService(QObject):
    completed = Signal(str, bool, str)
    issued = Signal(str)

    def __init__(self, client: McpClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client

    def _send(self, label: str, target: int, opcode: Opcode,
              payload: bytes = b"") -> None:
        self.issued.emit(
            f"TX {label} | target=0x{int(target):04X} "
            f"opcode=0x{int(opcode):04X} payload={payload.hex(' ').upper() or '--'}")
        def done(frame, error) -> None:
            if error or frame is None:
                self.completed.emit(label, False, error or "无响应")
            elif frame.status_code != Status.OK:
                status = frame.status.name if frame.status else f"0x{frame.status_code:04X}"
                self.completed.emit(label, False,
                                    f"{status}, detail=0x{(frame.detail or 0):04X}")
            else:
                # SET_CONFIG is accepted first and, for link/channel/source,
                # verified with the matching GET_CONFIG response. The frame
                # bundle target has no GET_CONFIG endpoint, so its Active
                # values are confirmed by the diagnostic register stream.
                if opcode == Opcode.SET_CONFIG:
                    if int(target) in {
                        int(Target.A2B_LINK), int(Target.B2A_LINK),
                        int(Target.A2B_CHANNEL), int(Target.B2A_CHANNEL),
                        int(Target.ALICE_SOURCE), int(Target.BOB_SOURCE),
                    }:
                        self._verify_config(label, int(target), payload)
                    else:
                        self.completed.emit(
                            label, True,
                            "配置命令已接受；Active值将在诊断采集时确认")
                elif opcode == Opcode.CONTROL:
                    self.completed.emit(label, True, "控制命令已接受")
                else:
                    self.completed.emit(label, True, "命令已接受")
        self.client.submit(target, opcode, payload, done, priority=0, timeout_ms=900)

    def _verify_config(self, label: str, target: int, expected: bytes) -> None:
        def done(frame, error) -> None:
            if error or frame is None:
                self.completed.emit(label, False,
                                    f"配置命令已接受，但回读失败：{error or '无响应'}")
            elif frame.status_code != Status.OK:
                status = frame.status.name if frame.status else f"0x{frame.status_code:04X}"
                self.completed.emit(label, False,
                                    f"配置命令已接受，但回读失败：{status}")
            elif frame.data != expected:
                self.completed.emit(label, False,
                                    "配置命令已接受，但配置回读不一致")
            else:
                if target in {int(Target.ALICE_SOURCE), int(Target.BOB_SOURCE)}:
                    self.completed.emit(
                        label, True,
                        "配置已写入并回读一致；Active状态由诊断寄存器确认")
                else:
                    self.completed.emit(label, True, "配置已应用，回读一致")
        self.client.submit(target, Opcode.GET_CONFIG, b"", done,
                           priority=0, timeout_ms=900, retries=1)

    def set_frame(self, payload: int, physical: int) -> None:
        self._send("全局帧配置", Target.SYSTEM, Opcode.SET_CONFIG,
                   struct.pack("<HH", payload, physical))

    def set_link(self, target: Target, enable: bool, timeout: int,
                 retry: int, sequence: int) -> None:
        self._send("链路配置", target, Opcode.SET_CONFIG,
                   struct.pack("<BBHII", int(enable), 0, retry, timeout, sequence))

    def set_channel(self, target: Target, enable: bool, bypass: bool,
                    continuous: bool, force_one: bool, threshold: int,
                    max_flips: int, seed: int) -> None:
        self._send("故障注入配置", target, Opcode.SET_CONFIG,
                   struct.pack("<BBBBHBBI", int(enable), int(bypass),
                               int(continuous), int(force_one), threshold,
                               max_flips, 0, seed))

    def set_source(self, target: Target, mode: int, length: int, gap: int,
                   limit: int, seed: int, mix_period: int) -> None:
        self._send("测试源配置", target, Opcode.SET_CONFIG,
                   struct.pack("<B3xIIIII", mode, length, gap, limit, seed, mix_period))

    def control(self, label: str, target: Target | int, command: int) -> None:
        self._send(label, int(target), Opcode.CONTROL, struct.pack("<I", command))

    def clear_stats(self) -> None:
        self._send("清统计", Target.SYSTEM, Opcode.CLEAR_STATS)

    def clear_errors(self) -> None:
        self._send("清错误", Target.SYSTEM, Opcode.CLEAR_ERRORS,
                   struct.pack("<I", 0xFFFFFFFF))

    def soft_reset(self) -> None:
        self.control("Scheduler软复位", Target.SYSTEM, 1)
