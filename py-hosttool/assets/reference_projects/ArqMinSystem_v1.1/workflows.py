"""Asynchronous, logged workflows for common ARQ laboratory operations."""
from __future__ import annotations

from dataclasses import dataclass
import struct
import time

from PySide6.QtCore import QObject, QTimer, Signal

from client import McpClient
from protocol import Opcode, Status, Target


@dataclass(frozen=True)
class RequestStep:
    label: str
    target: int
    opcode: int
    payload: bytes = b""
    busy_retry_ms: int = 0


@dataclass(frozen=True)
class DelayStep:
    milliseconds: int
    reason: str


@dataclass(frozen=True)
class IdleStep:
    targets: tuple[int, ...]
    timeout_ms: int = 5000


class WorkflowService(QObject):
    """Run safety-critical command sequences without blocking the GUI thread."""

    log = Signal(str)
    finished = Signal(str, bool, str)
    running_changed = Signal(bool)

    SCHEDULERS = (
        int(Target.ALICE_TX_SCHEDULER), int(Target.ALICE_RX_SCHEDULER),
        int(Target.BOB_TX_SCHEDULER), int(Target.BOB_RX_SCHEDULER),
    )
    TX_SCHEDULERS = (
        int(Target.ALICE_TX_SCHEDULER), int(Target.BOB_TX_SCHEDULER),
    )

    def __init__(self, client: McpClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.steps: list[RequestStep | DelayStep | IdleStep] = []
        self.name = ""
        self.running = False
        self._generation = 0
        self._idle_deadline = 0.0

    @staticmethod
    def _control(label: str, target: Target, command: int) -> RequestStep:
        return RequestStep(label, int(target), int(Opcode.CONTROL),
                           struct.pack("<I", command))

    @staticmethod
    def _link(label: str, target: Target, enable: bool, timeout: int,
              retry: int, sequence: int) -> RequestStep:
        payload = struct.pack("<BBHII", int(enable), 0, retry, timeout, sequence)
        return RequestStep(label, int(target), int(Opcode.SET_CONFIG), payload,
                           busy_retry_ms=5000)

    @staticmethod
    def _channel(label: str, target: Target, *, enable: bool, bypass: bool,
                 continuous: bool, force_one: bool, threshold: int,
                 max_flips: int, seed: int) -> RequestStep:
        payload = struct.pack("<BBBBHBBI", int(enable), int(bypass),
                              int(continuous), int(force_one), threshold,
                              max_flips, 0, seed)
        return RequestStep(label, int(target), int(Opcode.SET_CONFIG), payload)

    def enable_bidirectional_arq(self, timeout: int, retry: int,
                                 sequence: int) -> None:
        self._start("启动双向ARQ", [
            self._control("停止Alice测试源", Target.ALICE_SOURCE, 2),
            self._control("停止Bob测试源", Target.BOB_SOURCE, 2),
            DelayStep(500, "等待测试源末帧离开入口"),
            self._control("四个Scheduler软复位", Target.SYSTEM, 1),
            DelayStep(200, "等待软复位及CDC收敛"),
            IdleStep(self.TX_SCHEDULERS),
            self._link("应用A2B ARQ配置", Target.A2B_LINK, True,
                       timeout, retry, sequence),
            self._link("应用B2A ARQ配置", Target.B2A_LINK, True,
                       timeout, retry, sequence),
            DelayStep(200, "等待双向ARQ生效配置回读稳定"),
            self._control("启动Alice测试源", Target.ALICE_SOURCE, 1),
            self._control("启动Bob测试源", Target.BOB_SOURCE, 1),
        ])

    def disable_bidirectional_arq(self, timeout: int, retry: int,
                                  sequence: int, seed: int = 1) -> None:
        self._start("关闭ARQ并恢复直通基线", [
            self._control("停止Alice测试源", Target.ALICE_SOURCE, 2),
            self._control("停止Bob测试源", Target.BOB_SOURCE, 2),
            DelayStep(500, "等待测试源末帧离开入口"),
            IdleStep(self.TX_SCHEDULERS),
            self._channel("A2B恢复旁路", Target.A2B_CHANNEL, enable=False,
                          bypass=True, continuous=False, force_one=True,
                          threshold=0, max_flips=1, seed=seed),
            self._channel("B2A恢复旁路", Target.B2A_CHANNEL, enable=False,
                          bypass=True, continuous=False, force_one=True,
                          threshold=0, max_flips=1, seed=seed),
            self._link("关闭A2B ARQ", Target.A2B_LINK, False,
                       timeout, retry, sequence),
            self._link("关闭B2A ARQ", Target.B2A_LINK, False,
                       timeout, retry, sequence),
            self._control("四个Scheduler软复位", Target.SYSTEM, 1),
            DelayStep(200, "等待直通基线稳定"),
            self._control("启动Alice测试源", Target.ALICE_SOURCE, 1),
            self._control("启动Bob测试源", Target.BOB_SOURCE, 1),
        ])

    def arm_bidirectional_once(self, max_flips: int, seed: int) -> None:
        self._start("双向单次注错", [
            self._channel("配置A2B单次注错", Target.A2B_CHANNEL, enable=True,
                          bypass=False, continuous=False, force_one=True,
                          threshold=0, max_flips=max_flips, seed=seed),
            self._channel("配置B2A单次注错", Target.B2A_CHANNEL, enable=True,
                          bypass=False, continuous=False, force_one=True,
                          threshold=0, max_flips=max_flips,
                          seed=(seed ^ 0xA5A55A5A) & 0xFFFFFFFF),
            self._control("触发A2B下一帧注错", Target.A2B_CHANNEL, 2),
            self._control("触发B2A下一帧注错", Target.B2A_CHANNEL, 2),
        ])

    def enable_continuous_faults(self, threshold: int, max_flips: int,
                                 seed: int) -> None:
        self._start("启动双向连续注错", [
            self._channel("应用A2B连续注错配置", Target.A2B_CHANNEL, enable=True,
                          bypass=False, continuous=True, force_one=True,
                          threshold=threshold, max_flips=max_flips, seed=seed),
            self._channel("应用B2A连续注错配置", Target.B2A_CHANNEL, enable=True,
                          bypass=False, continuous=True, force_one=True,
                          threshold=threshold, max_flips=max_flips,
                          seed=(seed ^ 0xA5A55A5A) & 0xFFFFFFFF),
        ])

    def bypass_faults(self, seed: int = 1) -> None:
        self._start("停止注错并恢复旁路", [
            self._channel("A2B恢复旁路", Target.A2B_CHANNEL, enable=False,
                          bypass=True, continuous=False, force_one=True,
                          threshold=0, max_flips=1, seed=seed),
            self._channel("B2A恢复旁路", Target.B2A_CHANNEL, enable=False,
                          bypass=True, continuous=False, force_one=True,
                          threshold=0, max_flips=1, seed=seed),
        ])

    def clear_stats_and_errors(self) -> None:
        self._start("清空统计和错误", [
            RequestStep("清空全部统计", int(Target.SYSTEM),
                        int(Opcode.CLEAR_STATS)),
            RequestStep("清空全部错误", int(Target.SYSTEM),
                        int(Opcode.CLEAR_ERRORS),
                        struct.pack("<I", 0xFFFFFFFF)),
        ])

    def cancel(self) -> None:
        if not self.running:
            return
        name = self.name
        self._generation += 1
        self.steps.clear()
        self.running = False
        self.log.emit(f"流程取消：{name}（已下发的硬件命令不会回滚）")
        self.running_changed.emit(False)
        self.finished.emit(name, False, "用户取消")

    def _start(self, name: str,
               steps: list[RequestStep | DelayStep | IdleStep]) -> None:
        if self.running:
            self.finished.emit(name, False, f"已有流程正在运行：{self.name}")
            return
        self._generation += 1
        self.name = name
        self.steps = list(steps)
        self.running = True
        self.log.emit(f"========== 开始流程：{name} ==========")
        self.running_changed.emit(True)
        self._next(self._generation)

    def _next(self, generation: int) -> None:
        if generation != self._generation or not self.running:
            return
        if not self.steps:
            name = self.name
            self.running = False
            self.log.emit(f"========== 流程完成：{name} ==========")
            self.running_changed.emit(False)
            self.finished.emit(name, True, "全部步骤完成")
            return
        step = self.steps.pop(0)
        if isinstance(step, DelayStep):
            self.log.emit(f"等待 {step.milliseconds} ms：{step.reason}")
            QTimer.singleShot(step.milliseconds,
                              lambda: self._next(generation))
            return
        if isinstance(step, IdleStep):
            self._idle_deadline = time.monotonic() + step.timeout_ms / 1000.0
            self.log.emit("检查TX发送事务是否已经排空")
            self._poll_idle(step, 0, generation)
            return
        retry_deadline = (time.monotonic() + step.busy_retry_ms / 1000.0
                          if step.busy_retry_ms else 0.0)
        self._request(step, generation, retry_deadline, False)

    def _request(self, step: RequestStep, generation: int,
                 retry_deadline: float, retrying: bool) -> None:
        if generation != self._generation or not self.running:
            return
        prefix = "RETRY" if retrying else "TX"
        self.log.emit(
            f"{prefix} {step.label} | target=0x{step.target:04X} "
            f"opcode=0x{step.opcode:04X} payload={step.payload.hex(' ').upper() or '--'}")

        def done(frame, error) -> None:
            if generation != self._generation or not self.running:
                return
            if error or frame is None:
                self._fail(step.label, error or "无响应")
                return
            if frame.status_code != Status.OK:
                status = frame.status.name if frame.status else f"0x{(frame.status_code or 0):04X}"
                if (frame.status_code == Status.BUSY and retry_deadline and
                        time.monotonic() < retry_deadline):
                    detail = frame.detail or 0
                    self.log.emit(
                        f"WAIT {step.label} | {self._busy_reason(detail)}；100 ms后重试")
                    QTimer.singleShot(
                        100, lambda: self._request(step, generation,
                                                  retry_deadline, True))
                    return
                self._fail(step.label,
                           f"{status}, detail=0x{(frame.detail or 0):04X}")
                return
            if step.opcode == int(Opcode.SET_CONFIG) and step.target in {
                int(Target.A2B_LINK), int(Target.B2A_LINK),
                int(Target.A2B_CHANNEL), int(Target.B2A_CHANNEL),
            }:
                self._verify_config(step, generation)
                return
            if step.opcode == int(Opcode.SET_CONFIG):
                self.log.emit(
                    f"RX {step.label} | 配置命令已接受；Active值由诊断采集确认")
            elif step.opcode == int(Opcode.CONTROL):
                self.log.emit(f"RX {step.label} | 控制命令已接受")
            else:
                self.log.emit(f"RX {step.label} | 命令已接受")
            self._next(generation)

        self.client.submit(step.target, step.opcode, step.payload, done,
                           priority=0, timeout_ms=900, retries=2)

    def _verify_config(self, step: RequestStep, generation: int) -> None:
        """Confirm link/channel Apply through the same target's GET_CONFIG."""
        def done(frame, error) -> None:
            if generation != self._generation or not self.running:
                return
            if error or frame is None:
                self._fail(step.label,
                           f"配置命令已接受，但回读失败：{error or '无响应'}")
            elif frame.status_code != Status.OK:
                status = frame.status.name if frame.status else f"0x{frame.status_code:04X}"
                self._fail(step.label, f"配置命令已接受，但回读失败：{status}")
            elif frame.data != step.payload:
                self._fail(step.label, "配置命令已接受，但配置回读不一致")
            else:
                self.log.emit(f"RX {step.label} | 配置已应用，回读一致")
                self._next(generation)
        self.client.submit(step.target, Opcode.GET_CONFIG, b"", done,
                           priority=0, timeout_ms=900, retries=1)

    @staticmethod
    def _busy_reason(detail: int) -> str:
        side = {1: "TX侧", 2: "RX侧"}.get(detail & 3, "Scheduler")
        reason = (detail >> 8) & 0x0F
        names = []
        if reason & 1:
            names.append("致命错误或复位未完成")
        if reason & 2:
            names.append("绑定、DDR引擎或就绪队列仍活动")
        if reason & 4:
            names.append("已绑定或就绪资源未释放")
        if reason & 8:
            names.append("反馈队列未排空")
        description = "、".join(names) if names else "等待安全配置应用窗口"
        return f"BUSY detail=0x{detail:04X}（{side}：{description}）"

    def _poll_idle(self, step: IdleStep, index: int, generation: int) -> None:
        if generation != self._generation or not self.running:
            return
        target = step.targets[index]

        def done(frame, error) -> None:
            if generation != self._generation or not self.running:
                return
            if error or frame is None:
                self._fail("Scheduler空闲检查", error or "无响应")
                return
            if frame.status_code != Status.OK or len(frame.data) < 4:
                self._fail("Scheduler空闲检查", "状态寄存器读取失败")
                return
            status = struct.unpack_from("<I", frame.data)[0]
            if status & 1:
                if time.monotonic() >= self._idle_deadline:
                    self._fail("Scheduler空闲检查",
                               f"target=0x{target:04X}持续BUSY，STATUS=0x{status:08X}")
                    return
                QTimer.singleShot(100,
                                  lambda: self._poll_idle(step, 0, generation))
                return
            if index + 1 < len(step.targets):
                self._poll_idle(step, index + 1, generation)
            else:
                self.log.emit("两个TX Scheduler均已空闲；RX安全窗口由Vitis精确门控")
                self._next(generation)

        self.client.submit(target, Opcode.READ_REGISTER,
                           struct.pack("<H", 0x00C), done,
                           priority=0, timeout_ms=700, retries=1)

    def _fail(self, step: str, detail: str) -> None:
        name = self.name
        self.steps.clear()
        self.running = False
        self.log.emit(f"流程失败：{step} | {detail}")
        self.running_changed.emit(False)
        self.finished.emit(name, False, f"{step}：{detail}")
