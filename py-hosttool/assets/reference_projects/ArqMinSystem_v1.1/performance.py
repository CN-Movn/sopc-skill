"""Local link-performance calculations built from the existing register cache."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
import time

from diagnostics import Severity
from protocol import Target
from registers import INSTANCE_BY_TARGET


def _key(target: int, name: str) -> int:
    instance = INSTANCE_BY_TARGET[int(target)]
    for spec in instance.registers:
        if spec.name == name:
            return spec.key
    raise KeyError(f"{instance.ip_type}.{name} is not in the periodic cache")


def _delta(current: int, previous: int, bits: int) -> int | None:
    """Unsigned delta with wrap detection; a non-wrap regression is a reset."""
    modulus = 1 << bits
    current &= modulus - 1
    previous &= modulus - 1
    if current >= previous:
        return current - previous
    if previous >= (modulus * 3 // 4) and current <= (modulus // 4):
        return modulus - previous + current
    return None


def _field(value: int, shift: int, bits: int) -> int:
    return (value >> shift) & ((1 << bits) - 1)


def _field_delta(current: int, previous: int, shift: int, bits: int) -> int | None:
    return _delta(_field(current, shift, bits),
                  _field(previous, shift, bits), bits)


@dataclass(frozen=True)
class Metric:
    value: float | None
    text: str
    severity: Severity = Severity.NORMAL
    estimated: bool = False


@dataclass(frozen=True)
class StageMetrics:
    name: str
    throughput: Metric
    frame_rate: Metric
    quality: Metric
    pressure: Metric


@dataclass(frozen=True)
class LinkMetrics:
    name: str
    sampled_at: datetime | None
    summary: tuple[tuple[str, Metric], ...]
    stages: tuple[StageMetrics, ...]
    stale: bool
    note: str


@dataclass
class _Interval:
    timestamp: float
    wall_time: datetime
    elapsed: float
    values: dict[int, int]
    previous: dict[int, int]


@dataclass
class _TargetState:
    timestamp: float | None = None
    wall_time: datetime | None = None
    values: dict[int, int] = field(default_factory=dict)
    interval: _Interval | None = None
    failed: bool = False
    baseline_invalid: bool = False


@dataclass(frozen=True)
class _Link:
    name: str
    source: Target
    tx_scheduler: Target
    tx_wrapper: Target
    channel: Target
    rx_wrapper: Target
    rx_scheduler: Target
    feedback_rx_scheduler: Target


LINKS = {
    "A2B": _Link("A→B", Target.ALICE_SOURCE, Target.ALICE_TX_SCHEDULER,
                 Target.ALICE_TX_WRAPPER, Target.A2B_CHANNEL,
                 Target.BOB_RX_WRAPPER, Target.BOB_RX_SCHEDULER,
                 Target.ALICE_RX_SCHEDULER),
    "B2A": _Link("B→A", Target.BOB_SOURCE, Target.BOB_TX_SCHEDULER,
                 Target.BOB_TX_WRAPPER, Target.B2A_CHANNEL,
                 Target.ALICE_RX_WRAPPER, Target.ALICE_RX_SCHEDULER,
                 Target.BOB_RX_SCHEDULER),
}


RX_DELIVERY_LABEL = "RX下游交付有效吞吐"
PHYSICAL_LABEL = "物理帧吞吐（含DATA/ACK/NACK）"


class PerformanceModel:
    """Keeps one baseline per Target and never initiates a hardware request."""

    def __init__(self) -> None:
        self.states = {target: _TargetState() for target in INSTANCE_BY_TARGET}
        self.period_ms = 1000
        self.history: dict[str, deque[tuple[float, float, float]]] = {
            name: deque() for name in LINKS
        }
        self.held_metrics: dict[str, LinkMetrics] = {}

    def reset(self) -> None:
        self.states = {target: _TargetState() for target in INSTANCE_BY_TARGET}
        self.held_metrics.clear()
        for points in self.history.values():
            points.clear()

    def invalidate_baselines(self) -> None:
        """Discard one interval after a hardware counter reset/config change.

        The next sample is retained as the new baseline but does not produce a
        rate.  Existing valid metrics remain available as a stale display
        hold until the new interval is complete.
        """
        for state in self.states.values():
            state.interval = None
            state.baseline_invalid = True

    def set_period(self, period_ms: int) -> None:
        self.period_ms = max(500, int(period_ms))

    def ingest(self, target: int, values: dict[int, int],
               timestamp: float | None = None,
               wall_time: datetime | None = None) -> None:
        now = time.monotonic() if timestamp is None else float(timestamp)
        wall = datetime.now() if wall_time is None else wall_time
        state = self.states.setdefault(int(target), _TargetState())
        interval = None
        if (state.timestamp is not None and now > state.timestamp and
                not state.baseline_invalid):
            interval = _Interval(now, wall, now - state.timestamp,
                                 dict(values), state.values)
        if state.baseline_invalid:
            state.baseline_invalid = False
        state.timestamp = now
        state.wall_time = wall
        state.values = dict(values)
        state.interval = interval
        state.failed = False

        for name, link in LINKS.items():
            if int(link.rx_scheduler) == int(target):
                metrics = self.link_metrics(name, now)
                goodput = dict(metrics.summary)[RX_DELIVERY_LABEL].value
                physical = dict(metrics.summary)[PHYSICAL_LABEL].value
                if (not metrics.stale and goodput is not None and
                        physical is not None):
                    self.history[name].append((now, goodput, physical or 0.0))
                    while self.history[name] and now - self.history[name][0][0] > 60.0:
                        self.history[name].popleft()

    def mark_failed(self, target: int) -> None:
        self.states.setdefault(int(target), _TargetState()).failed = True

    def history_points(self, name: str) -> tuple[tuple[float, float, float], ...]:
        return tuple(self.history[name])

    def _interval(self, target: Target) -> _Interval | None:
        return self.states[int(target)].interval

    def _fresh(self, target: Target, now: float) -> bool:
        state = self.states[int(target)]
        limit = max(3.0, self.period_ms / 1000.0 * 2.8)
        return (not state.failed and state.timestamp is not None and
                now - state.timestamp <= limit)

    def _baseline_pending(self, targets: tuple[Target, ...]) -> bool:
        return any(self.states[int(target)].baseline_invalid or
                   self.states[int(target)].interval is None
                   for target in targets)

    @staticmethod
    def _stale_metric(metric: Metric) -> Metric:
        return replace(metric, severity=Severity.STALE)

    def _held_metrics(self, name: str, sampled: datetime | None) -> LinkMetrics | None:
        held = self.held_metrics.get(name)
        if held is None:
            return None
        summary = tuple((label, self._stale_metric(metric))
                        for label, metric in held.summary)
        stages = tuple(
            StageMetrics(stage.name,
                         self._stale_metric(stage.throughput),
                         self._stale_metric(stage.frame_rate),
                         self._stale_metric(stage.quality),
                         self._stale_metric(stage.pressure))
            for stage in held.stages)
        return replace(held, sampled_at=sampled, summary=summary,
                       stages=stages, stale=True)

    def _value(self, target: Target, name: str) -> int | None:
        state = self.states[int(target)]
        return state.values.get(_key(target, name))

    def _rate32(self, target: Target, name: str) -> float | None:
        interval = self._interval(target)
        key = _key(target, name)
        if interval is None or key not in interval.values or key not in interval.previous:
            return None
        change = _delta(interval.values[key], interval.previous[key], 32)
        return None if change is None else change / interval.elapsed

    def _rate64(self, target: Target, low: str, high: str) -> float | None:
        interval = self._interval(target)
        lo, hi = _key(target, low), _key(target, high)
        if (interval is None or lo not in interval.values or hi not in interval.values or
                lo not in interval.previous or hi not in interval.previous):
            return None
        current = (interval.values[hi] << 32) | interval.values[lo]
        previous = (interval.previous[hi] << 32) | interval.previous[lo]
        change = _delta(current, previous, 64)
        return None if change is None else change / interval.elapsed

    def _packed_rate(self, target: Target, name: str,
                     shift: int, bits: int) -> float | None:
        interval = self._interval(target)
        key = _key(target, name)
        if interval is None or key not in interval.values or key not in interval.previous:
            return None
        change = _field_delta(interval.values[key], interval.previous[key], shift, bits)
        return None if change is None else change / interval.elapsed

    @staticmethod
    def _throughput(byte_rate: float | None, estimated: bool = False) -> Metric:
        if byte_rate is None:
            return Metric(None, "--", Severity.STALE, estimated)
        bit_rate = max(0.0, byte_rate) * 8.0
        if bit_rate >= 1e9:
            text = f"{bit_rate / 1e9:.3f} Gbps"
        elif bit_rate >= 1e6:
            text = f"{bit_rate / 1e6:.3f} Mbps"
        elif bit_rate >= 1e3:
            text = f"{bit_rate / 1e3:.2f} kbps"
        else:
            text = f"{bit_rate:.0f} bps"
        return Metric(bit_rate, text, Severity.NORMAL, estimated)

    @staticmethod
    def _fps(rate: float | None, estimated: bool = False) -> Metric:
        if rate is None:
            return Metric(None, "--", Severity.STALE, estimated)
        return Metric(rate, f"{rate:.1f} 帧/秒", Severity.NORMAL, estimated)

    @staticmethod
    def _percent(value: float | None, severity: Severity = Severity.NORMAL,
                 estimated: bool = False) -> Metric:
        if value is None:
            return Metric(None, "--", Severity.STALE, estimated)
        value = max(0.0, value)
        return Metric(value, f"{value:.3f}%", severity, estimated)

    @staticmethod
    def _recovery_rate(retx_count: float | None,
                       data_drop: float | None) -> float | None:
        """保守估算丢帧恢复率，避免重复重传把比例推高到100%以上。"""
        if data_drop is None or retx_count is None:
            return None
        if data_drop > 0:
            return min(100.0, retx_count / data_drop * 100.0)
        return 0.0 if retx_count == 0 else None

    @staticmethod
    def _text(text: str, severity: Severity = Severity.NORMAL,
              estimated: bool = False) -> Metric:
        return Metric(None, text, severity, estimated)

    def link_metrics(self, name: str, now: float | None = None) -> LinkMetrics:
        now = time.monotonic() if now is None else now
        link = LINKS[name]
        targets = (link.source, link.tx_scheduler, link.tx_wrapper, link.channel,
                   link.rx_wrapper, link.rx_scheduler, link.feedback_rx_scheduler)
        baseline_pending = self._baseline_pending(targets)
        stale_targets = [target for target in targets if not self._fresh(target, now)]

        payload = self._value(link.tx_wrapper, "ACTIVE_MAX_PAYLOAD") or 0
        physical_bytes = self._value(link.tx_wrapper, "ACTIVE_FRAME_BYTES") or 0

        source_bytes = self._rate64(link.source, "BYTES_SENT_LO", "BYTES_SENT_HI")
        source_frames = self._rate32(link.source, "FRAMES_SENT")
        source_stall = self._rate64(link.source, "STALL_CYCLES_LO", "STALL_CYCLES_HI")

        tx_payload = self._rate32(link.tx_wrapper, "STAT_TX_PAYLOAD_BYTES")
        tx_frames = self._rate32(link.tx_wrapper, "STAT_TX_FRAME_TOTAL")
        tx_commands = self._rate32(link.tx_wrapper, "STAT_TX_TOTAL")
        tx_retx = self._rate32(link.tx_wrapper, "STAT_RETX_TOTAL")
        tx_stall = self._rate32(link.tx_wrapper, "DBG_OUT_STALL")

        channel_frames = self._rate32(link.channel, "FRAMES_PASSED")
        selected = self._rate32(link.channel, "FRAMES_SELECTED")
        error_frames = self._rate32(link.channel, "ERROR_FRAMES")
        flipped_bits = self._rate32(link.channel, "FLIPPED_BITS")

        rx_payload = self._rate32(link.rx_wrapper, "STAT_RX_PAYLOAD_BYTES")
        rx_physical_frames = self._rate32(link.rx_wrapper, "STAT_RX_FRAME_TOTAL")
        rx_logical_frames = self._rate32(link.rx_wrapper, "STAT_RX_TOTAL")
        data_drop = self._rate32(link.rx_wrapper, "STAT_DATA_DROP_TOTAL")
        ack_drop = self._rate32(link.rx_wrapper, "STAT_ACK_DROP_TOTAL")
        nack_drop = self._rate32(link.rx_wrapper, "STAT_NACK_DROP_TOTAL")

        # STAT_NEW_ACCEPTED is ARQ-only and intentionally not used for
        # delivery throughput.  The performance page counts the actual
        # downstream AXIS handshakes in both ARQ and compatibility modes.
        rx_output_bytes = self._rate32(link.rx_scheduler, "PERF_OUTPUT_FIRE_BYTES")
        rx_output_frames = self._rate32(link.rx_scheduler, "PERF_OUTPUT_FIRE_FRAMES")
        duplicate = self._rate32(link.rx_scheduler, "STAT_DUPLICATE")
        unexpected = self._rate32(link.rx_scheduler, "STAT_UNEXPECTED")
        mismatch = self._rate32(link.rx_scheduler, "STAT_PROCESS_MISMATCH")
        generated = self._rate32(link.rx_scheduler, "STAT_FEEDBACK_GENERATED")
        routed = self._rate32(link.feedback_rx_scheduler, "STAT_FEEDBACK_ROUTED")
        remote_receive = self._packed_rate(link.tx_scheduler, "STAT_INGRESS", 0, 16)

        attempts = self._packed_rate(link.tx_scheduler, "STAT_ATTEMPTS", 0, 16)
        # TX Scheduler actual AXIS output rate uses the 32-bit PERF snapshot
        # counters, which advance in both ARQ and compat modes. The low-16
        # STAT_ATTEMPTS field only advances in ARQ mode and wraps at rates
        # above 65536 fps, which _delta misreads as a reset (gray "--"). Keep
        # STAT_ATTEMPTS for the ARQ retry/quality semantics below.
        tx_output_bytes = self._rate32(link.tx_scheduler, "PERF_OUTPUT_FIRE_BYTES")
        tx_output_frames = self._rate32(link.tx_scheduler, "PERF_OUTPUT_FIRE_FRAMES")

        retx_issue = self._packed_rate(link.tx_scheduler, "STAT_RETX", 0, 8)
        retry_exhausted = self._packed_rate(link.tx_scheduler, "STAT_FAILURES", 0, 8)
        session_failed = self._packed_rate(link.tx_scheduler, "STAT_FAILURES", 8, 8)
        remote_stall = self._packed_rate(link.tx_scheduler, "STAT_INGRESS", 16, 16)
        failure_word = self._value(link.tx_scheduler, "STAT_FAILURES") or 0
        retry_total = _field(failure_word, 0, 8)
        session_total = _field(failure_word, 8, 8)
        last_failure = self._value(link.tx_scheduler, "LAST_FAILURE_INFO") or 0

        goodput_bytes = rx_output_bytes
        physical_rate = (channel_frames * physical_bytes
                         if channel_frames is not None and physical_bytes else None)
        # Scheduler统计是首选；若该Target本轮没有形成连续样本，使用TX Wrapper
        # 已发送重传命令计数补足吞吐显示，避免正常重传窗口被误显示为灰色。
        retx_count = retx_issue if retx_issue is not None else tx_retx
        retx_bytes = retx_count * payload if retx_count is not None and payload else None
        retx_rate = (retx_count / attempts * 100.0
                     if attempts and retx_count is not None else
                     (0.0 if attempts == 0 and retx_count == 0 else None))
        efficiency = (tx_payload / (tx_frames * physical_bytes) * 100.0
                      if tx_payload is not None and tx_frames and physical_bytes else None)
        injection_ber = (flipped_bits / (channel_frames * physical_bytes * 8.0) * 100.0
                         if flipped_bits is not None and channel_frames and physical_bytes else
                         (0.0 if flipped_bits == 0 and channel_frames else None))
        hit_rate = (data_drop / error_frames * 100.0
                    if data_drop is not None and error_frames else
                    (0.0 if data_drop == 0 and error_frames else None))
        drop_rate = (data_drop / rx_physical_frames * 100.0
                     if data_drop is not None and rx_physical_frames else
                     (0.0 if data_drop == 0 and rx_physical_frames else None))
        recovery = self._recovery_rate(retx_count, data_drop)

        terminal = bool(retry_total or session_total or last_failure)
        recoverable = bool((retx_issue or 0) > 0 or (data_drop or 0) > 0)
        abnormal_rx = bool((unexpected or 0) > 0 or (mismatch or 0) > 0)
        summary_severity = (Severity.ERROR if terminal or abnormal_rx else
                            Severity.NOTICE if recoverable else Severity.NORMAL)
        if data_drop == 0 and retx_count is not None and retx_count > 0:
            recovery_metric = self._text("无法关联", Severity.NOTICE, True)
        else:
            recovery_metric = self._percent(recovery, summary_severity, True)

        feedback_parts = (generated, routed, remote_receive)
        if any(item is None for item in feedback_parts):
            feedback_text = "生成 / 路由 / 接收：--"
            feedback_severity = Severity.STALE
        else:
            feedback_text = (f"生成 {generated:.1f} 帧/秒\n"
                             f"路由 {routed:.1f} / 接收 {remote_receive:.1f} 帧/秒")
            spread = max(feedback_parts) - min(feedback_parts)
            feedback_severity = (Severity.NOTICE if max(feedback_parts) > 1.0 and
                                 spread > max(feedback_parts) * 0.25 else Severity.NORMAL)

        local_balance = self._queue_balance(link.rx_scheduler, "ARQ_LOCAL_FB_PUSH_POP")
        remote_balance = self._queue_balance(link.feedback_rx_scheduler,
                                             "ARQ_REMOTE_FB_PUSH_POP")
        queue_text = f"本地反馈队列差 {local_balance}；远端反馈队列差 {remote_balance}"
        queue_severity = (Severity.NOTICE if local_balance not in ("0", "--") or
                          remote_balance not in ("0", "--") else Severity.NORMAL)

        terminal_text = (f"重试耗尽累计 {retry_total}；"
                         f"会话失败累计 {session_total}"
                         + (f"；最近失败 0x{last_failure:08X}" if last_failure else ""))
        stage_quality = (Severity.ERROR if terminal else
                         Severity.NOTICE if recoverable else Severity.NORMAL)
        sampled = max((self.states[int(target)].wall_time for target in targets
                       if self.states[int(target)].wall_time is not None), default=None)

        stages = (
            StageMetrics("测试源", self._throughput(source_bytes), self._fps(source_frames),
                         self._text("业务输入"),
                         self._text("源端阻塞：--" if source_stall is None else
                                    f"源端阻塞 {source_stall:.0f} 周期/秒",
                                    Severity.NOTICE if (source_stall or 0) > 0 else Severity.NORMAL)),
            StageMetrics("TX Scheduler",
                         self._throughput(tx_output_bytes), self._fps(tx_output_frames),
                         self._text("普通/可恢复重传" if recoverable else "发送正常", stage_quality),
                         self._text(terminal_text if terminal else
                                    self._resource_pressure(link.tx_scheduler, remote_stall),
                                    Severity.ERROR if terminal else
                                    Severity.NOTICE if (remote_stall or 0) > 0 else Severity.NORMAL)),
            StageMetrics("TX Wrapper", self._throughput(tx_payload), self._fps(tx_frames),
                         self._text(f"命令 {tx_commands:.1f}/秒\n重传 {tx_retx:.1f}/秒"
                                    if tx_commands is not None and tx_retx is not None else "--",
                                    Severity.NOTICE if (tx_retx or 0) > 0 else Severity.NORMAL),
                         self._text("输出阻塞：--" if tx_stall is None else
                                    f"输出阻塞 {tx_stall:.0f} 周期/秒",
                                    Severity.NOTICE if (tx_stall or 0) > 0 else Severity.NORMAL)),
            StageMetrics("Analog Channel", self._throughput(physical_rate, True),
                         self._fps(channel_frames),
                         self._text((f"选中 {selected:.1f}/秒；注错 {error_frames:.1f}/秒\n"
                                    f"翻转 {flipped_bits:.1f} bit/秒")
                                    if None not in (selected, error_frames, flipped_bits) else "--",
                                    Severity.NOTICE if (error_frames or 0) > 0 else Severity.NORMAL),
                         self._text("流式通过")),
            StageMetrics("RX Wrapper", self._throughput(rx_payload), self._fps(rx_physical_frames),
                         self._text((f"逻辑帧 {rx_logical_frames:.1f}/秒；数据丢弃 {data_drop:.1f}/秒\n"
                                    f"ACK/NACK丢弃 {(ack_drop or 0)+(nack_drop or 0):.1f}/秒")
                                    if None not in (rx_logical_frames, data_drop) else "--",
                                    Severity.NOTICE if (data_drop or 0) > 0 else Severity.NORMAL),
                         self._text("解析器已恢复" if (data_drop or 0) > 0 else "接收正常",
                                    Severity.NOTICE if (data_drop or 0) > 0 else Severity.NORMAL)),
            StageMetrics("RX Scheduler", self._throughput(goodput_bytes),
                         self._fps(rx_output_frames),
                         self._text((f"重复帧 {duplicate:.1f}/秒；非预期帧 {unexpected:.1f}/秒\n"
                                    f"进程不匹配 {mismatch:.1f}/秒")
                                    if None not in (duplicate, unexpected, mismatch) else "--",
                                    Severity.ERROR if abnormal_rx else
                                    Severity.NOTICE if (duplicate or 0) > 0 else Severity.NORMAL),
                         self._text(self._resource_pressure(link.rx_scheduler, None)
                                    + "；" + queue_text, queue_severity)),
        )

        summary = (
            ("业务源吞吐", self._throughput(source_bytes)),
            (RX_DELIVERY_LABEL, self._throughput(goodput_bytes)),
            (PHYSICAL_LABEL, self._throughput(physical_rate, True)),
            ("重传吞吐", self._throughput(retx_bytes, True)),
            ("重传率", self._percent(retx_rate,
                                    Severity.NOTICE if (retx_rate or 0) > 0 else Severity.NORMAL,
                                    True)),
            ("有效载荷效率", self._percent(efficiency, estimated=True)),
            ("人工注入误码率（BER）", self._percent(injection_ber,
                                         Severity.NOTICE if (injection_ber or 0) > 0 else Severity.NORMAL,
                                         True)),
            ("有效命中率", self._percent(hit_rate,
                                        Severity.NOTICE if (error_frames or 0) > 0 else Severity.NORMAL,
                                        True)),
            ("接收丢帧率", self._percent(drop_rate,
                                      Severity.NOTICE if (drop_rate or 0) > 0 else Severity.NORMAL,
                                      True)),
            ("注错恢复率", recovery_metric),
            ("反馈闭合", Metric(None, feedback_text, feedback_severity, True)),
            ("终结失败", self._text(terminal_text if terminal else "无", summary_severity)),
        )
        note = ("部分IP未采集或数据已过期；跨IP指标为非原子最近区间组合；"
                "RX下游交付有效吞吐直接来自RX Scheduler最终AXIS握手字节计数，"
                "物理帧吞吐由Analog Channel通过帧数×当前物理帧长度估算。")
        stale = bool(stale_targets) or goodput_bytes is None
        metrics = LinkMetrics(link.name, sampled, summary, stages, stale, note)
        if not stale:
            self.held_metrics[name] = metrics
        elif baseline_pending:
            held = self._held_metrics(name, sampled)
            if held is not None:
                return held
        return metrics

    def _queue_balance(self, target: Target, name: str) -> str:
        interval = self._interval(target)
        key = _key(target, name)
        if interval is None or key not in interval.values or key not in interval.previous:
            return "--"
        push = _field_delta(interval.values[key], interval.previous[key], 0, 16)
        pop = _field_delta(interval.values[key], interval.previous[key], 16, 16)
        if push is None or pop is None:
            return "--"
        return f"{push - pop:+d}" if push != pop else "0"

    def _resource_pressure(self, target: Target,
                           remote_stall: float | None) -> str:
        value = self._value(target, "RESOURCE_SUMMARY")
        if value is None:
            resource = "资源 --"
        else:
            resource = (f"空闲描述符/槽 {_field(value, 0, 4)}/{_field(value, 4, 4)}；"
                        f"就绪队列 {_field(value, 12, 4)}")
        if remote_stall is not None:
            resource += f"；反馈阻塞 {remote_stall:.1f}/秒"
        return resource

    def log_summary(self) -> str:
        lines = ["[链路性能与质量]",
                 "口径: 同一IP相邻采样按实际时间差计算；跨IP组合非原子；"
                 "换算指标的估算原因见每条链路说明。"]
        now = time.monotonic()
        for name in ("A2B", "B2A"):
            metrics = self.link_metrics(name, now)
            lines.append(f"{metrics.name}: {'数据过期/不完整' if metrics.stale else '有效'}")
            for title, metric in metrics.summary:
                lines.append(f"  {title}: {metric.text}")
            lines.append(f"  说明: {metrics.note}")
        return "\n".join(lines)
