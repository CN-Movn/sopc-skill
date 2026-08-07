from datetime import datetime

from performance import (LinkMetrics, Metric, PerformanceModel, StageMetrics,
                         _delta)
from protocol import Target
from registers import INSTANCE_BY_TARGET


def _values(target: Target, **named: int) -> dict[int, int]:
    specs = {spec.name: spec.key
             for spec in INSTANCE_BY_TARGET[int(target)].registers}
    values = {spec.key: 0
              for spec in INSTANCE_BY_TARGET[int(target)].registers}
    for name, value in named.items():
        values[specs[name]] = value
    return values


def test_unsigned_delta_distinguishes_wrap_and_reset():
    assert _delta(3, 0xFFFFFFFE, 32) == 5
    assert _delta(3, 100, 32) is None
    assert _delta(110, 100, 32) == 10


def test_a2b_rates_use_same_target_elapsed_time_and_existing_cache():
    model = PerformanceModel()
    first = {
        Target.ALICE_SOURCE: _values(Target.ALICE_SOURCE),
        Target.ALICE_TX_SCHEDULER: _values(Target.ALICE_TX_SCHEDULER),
        Target.ALICE_TX_WRAPPER: _values(
            Target.ALICE_TX_WRAPPER, ACTIVE_MAX_PAYLOAD=1752,
            ACTIVE_FRAME_BYTES=1776),
        Target.A2B_CHANNEL: _values(Target.A2B_CHANNEL),
        Target.BOB_RX_WRAPPER: _values(Target.BOB_RX_WRAPPER),
        Target.BOB_RX_SCHEDULER: _values(Target.BOB_RX_SCHEDULER),
        Target.ALICE_RX_SCHEDULER: _values(Target.ALICE_RX_SCHEDULER),
    }
    for target, values in first.items():
        model.ingest(target, values, 10.0, datetime(2026, 8, 3, 12, 0, 0))

    second = {
        Target.ALICE_SOURCE: _values(Target.ALICE_SOURCE, FRAMES_SENT=20,
                                     BYTES_SENT_LO=35040),
        Target.ALICE_TX_SCHEDULER: _values(
            Target.ALICE_TX_SCHEDULER, STAT_ATTEMPTS=20,
            STAT_INGRESS=20),
        Target.ALICE_TX_WRAPPER: _values(
            Target.ALICE_TX_WRAPPER, ACTIVE_MAX_PAYLOAD=1752,
            ACTIVE_FRAME_BYTES=1776, STAT_TX_FRAME_TOTAL=20,
            STAT_TX_TOTAL=20, STAT_TX_PAYLOAD_BYTES=35040),
        Target.A2B_CHANNEL: _values(Target.A2B_CHANNEL, FRAMES_PASSED=20),
        Target.BOB_RX_WRAPPER: _values(
            Target.BOB_RX_WRAPPER, STAT_RX_FRAME_TOTAL=20,
            STAT_RX_TOTAL=20, STAT_RX_PAYLOAD_BYTES=35040),
        Target.BOB_RX_SCHEDULER: _values(
            Target.BOB_RX_SCHEDULER, STAT_NEW_ACCEPTED=0,
            PERF_OUTPUT_FIRE_BYTES=35040, PERF_OUTPUT_FIRE_FRAMES=20,
            STAT_FEEDBACK_GENERATED=20),
        Target.ALICE_RX_SCHEDULER: _values(
            Target.ALICE_RX_SCHEDULER, STAT_FEEDBACK_ROUTED=20),
    }
    for target, values in second.items():
        model.ingest(target, values, 12.0, datetime(2026, 8, 3, 12, 0, 2))

    metrics = model.link_metrics("A2B", 12.0)
    summary = dict(metrics.summary)
    assert summary["业务源吞吐"].value == 140160.0
    assert summary["RX下游交付有效吞吐"].value == 140160.0
    assert summary["物理帧吞吐（含DATA/ACK/NACK）"].value == 142080.0
    assert not summary["RX下游交付有效吞吐"].estimated
    assert not metrics.stale
    model.invalidate_baselines()
    held = dict(model.link_metrics("A2B", 13.0).summary)
    assert held["RX下游交付有效吞吐"].value == 140160.0


def test_rx_delivery_uses_actual_short_frame_bytes_not_active_payload():
    model = PerformanceModel()
    target = Target.BOB_RX_SCHEDULER
    first = _values(target, PERF_OUTPUT_FIRE_BYTES=1000,
                    PERF_OUTPUT_FIRE_FRAMES=10)
    second = _values(target, PERF_OUTPUT_FIRE_BYTES=1600,
                     PERF_OUTPUT_FIRE_FRAMES=20)
    model.ingest(target, first, 1.0)
    model.ingest(target, second, 3.0)
    assert model._rate32(target, "PERF_OUTPUT_FIRE_BYTES") == 300.0
    assert model._rate32(target, "PERF_OUTPUT_FIRE_FRAMES") == 5.0


def test_rx_delivery_uses_same_counter_when_arq_is_enabled():
    model = PerformanceModel()
    target = Target.BOB_RX_SCHEDULER
    model.ingest(target, _values(target, STAT_NEW_ACCEPTED=0,
                                 PERF_OUTPUT_FIRE_BYTES=0,
                                 PERF_OUTPUT_FIRE_FRAMES=0), 1.0)
    model.ingest(target, _values(target, STAT_NEW_ACCEPTED=20,
                                 PERF_OUTPUT_FIRE_BYTES=35040,
                                 PERF_OUTPUT_FIRE_FRAMES=20), 2.0)
    assert model._rate32(target, "PERF_OUTPUT_FIRE_BYTES") == 35040.0
    assert model._rate32(target, "PERF_OUTPUT_FIRE_FRAMES") == 20.0


def test_missing_rx_delivery_counter_is_stale_not_zero():
    model = PerformanceModel()
    target = Target.BOB_RX_SCHEDULER
    values = _values(target)
    specs = {spec.name: spec.key
             for spec in INSTANCE_BY_TARGET[int(target)].registers}
    values.pop(specs["PERF_OUTPUT_FIRE_BYTES"])
    values.pop(specs["PERF_OUTPUT_FIRE_FRAMES"])
    model.ingest(target, values, 1.0)
    model.ingest(target, values, 2.0)
    assert model._rate32(target, "PERF_OUTPUT_FIRE_BYTES") is None
    assert model._rate32(target, "PERF_OUTPUT_FIRE_FRAMES") is None


def test_explicit_baseline_invalidation_suppresses_post_clear_spike():
    model = PerformanceModel()
    target = Target.BOB_RX_SCHEDULER
    model.ingest(target, _values(target, PERF_OUTPUT_FIRE_BYTES=0xFFFFFF00), 1.0)
    model.invalidate_baselines()
    model.ingest(target, _values(target, PERF_OUTPUT_FIRE_BYTES=32), 2.0)
    assert model._rate32(target, "PERF_OUTPUT_FIRE_BYTES") is None
    model.ingest(target, _values(target, PERF_OUTPUT_FIRE_BYTES=64), 3.0)
    assert model._rate32(target, "PERF_OUTPUT_FIRE_BYTES") == 32.0


def test_rate64_uses_low_word_then_high_word_order():
    model = PerformanceModel()
    target = Target.ALICE_SOURCE
    model.ingest(target, _values(target, BYTES_SENT_LO=0xFFFFFFF0,
                                 BYTES_SENT_HI=1), 1.0)
    model.ingest(target, _values(target, BYTES_SENT_LO=0x10,
                                 BYTES_SENT_HI=2), 2.0)
    assert model._rate64(target, "BYTES_SENT_LO", "BYTES_SENT_HI") == 32.0


def test_counter_reset_suppresses_false_rate_spike():
    model = PerformanceModel()
    target = Target.ALICE_SOURCE
    model.ingest(target, _values(target, FRAMES_SENT=100,
                                 BYTES_SENT_LO=100000), 1.0)
    model.ingest(target, _values(target, FRAMES_SENT=1,
                                 BYTES_SENT_LO=1000), 2.0)
    assert model._rate32(target, "FRAMES_SENT") is None
    assert model._rate64(target, "BYTES_SENT_LO", "BYTES_SENT_HI") is None


def test_estimated_values_keep_metadata_without_repeating_suffix():
    metric = PerformanceModel._throughput(100.0, estimated=True)
    assert metric.estimated
    assert "估算" not in metric.text


def test_recovery_rate_is_capped_and_no_event_is_zero():
    assert PerformanceModel._recovery_rate(3.0, 1.0) == 100.0
    assert PerformanceModel._recovery_rate(0.0, 0.0) == 0.0
    assert PerformanceModel._recovery_rate(2.0, 0.0) is None


def test_disconnect_reset_clears_baselines_and_history():
    model = PerformanceModel()
    model.history["A2B"].append((1.0, 2.0, 3.0))
    model.reset()
    assert model.history_points("A2B") == ()
    assert model.states[int(Target.ALICE_SOURCE)].timestamp is None


def test_mode_change_invalidates_baseline_but_keeps_trend_history():
    model = PerformanceModel()
    model.history["A2B"].append((1.0, 2.0, 3.0))
    model.ingest(Target.BOB_RX_SCHEDULER, _values(
        Target.BOB_RX_SCHEDULER, PERF_OUTPUT_FIRE_BYTES=100), 1.0)
    model.invalidate_baselines()
    model.ingest(Target.BOB_RX_SCHEDULER, _values(
        Target.BOB_RX_SCHEDULER, PERF_OUTPUT_FIRE_BYTES=10), 2.0)
    assert model.history_points("A2B") == ((1.0, 2.0, 3.0),)
    assert model._rate32(Target.BOB_RX_SCHEDULER,
                         "PERF_OUTPUT_FIRE_BYTES") is None


def test_baseline_pending_holds_last_metric_values_as_stale():
    model = PerformanceModel()
    stage = StageMetrics("RX Scheduler", Metric(1.0, "1 bps"),
                         Metric(2.0, "2 帧/秒"), Metric(None, "正常"),
                         Metric(None, "--"))
    model.held_metrics["A2B"] = LinkMetrics(
        "A→B", None,
        (("RX下游交付有效吞吐", Metric(1.0, "1 bps")),),
        (stage,), False, "")
    model.invalidate_baselines()
    metrics = model.link_metrics("A2B", 2.0)
    summary = dict(metrics.summary)
    assert metrics.stale
    assert summary["RX下游交付有效吞吐"].value == 1.0
    assert summary["RX下游交付有效吞吐"].severity.name == "STALE"


def _tx_stage(metrics: LinkMetrics) -> StageMetrics:
    return next(stage for stage in metrics.stages
               if stage.name == "TX Scheduler")


def test_tx_scheduler_uses_32bit_perf_output_when_attempts_are_zero():
    """ARQ off: STAT_ATTEMPTS stays 0 but the 32-bit PERF output counters
    grow, so the TX Scheduler throughput/frame rate must be non-zero.
    """
    model = PerformanceModel()
    tx = Target.ALICE_TX_SCHEDULER
    model.ingest(tx, _values(tx, STAT_ATTEMPTS=0,
                             PERF_OUTPUT_FIRE_FRAMES=0,
                             PERF_OUTPUT_FIRE_BYTES=0), 1.0)
    model.ingest(tx, _values(tx, STAT_ATTEMPTS=0,
                             PERF_OUTPUT_FIRE_FRAMES=20,
                             PERF_OUTPUT_FIRE_BYTES=35040), 2.0)
    stage = _tx_stage(model.link_metrics("A2B", 2.0))
    assert stage.frame_rate.value == 20.0
    assert stage.throughput.value == 35040.0 * 8.0


def test_tx_scheduler_keeps_value_when_attempts_wrap_over_65536():
    """Real output rate >65536 fps wraps the low-16 STAT_ATTEMPTS field
    (whose packed delta goes None -> gray "--"), while the 32-bit PERF
    counters still yield a live TX Scheduler reading.
    """
    model = PerformanceModel()
    tx = Target.ALICE_TX_SCHEDULER
    model.ingest(tx, _values(tx, STAT_ATTEMPTS=60000,
                             PERF_OUTPUT_FIRE_FRAMES=0,
                             PERF_OUTPUT_FIRE_BYTES=0), 1.0)
    # 94232 real frames/s -> low-16 attempts 28696 (wrap), PERF 32-bit intact.
    model.ingest(tx, _values(tx, STAT_ATTEMPTS=28696,
                             PERF_OUTPUT_FIRE_FRAMES=94232,
                             PERF_OUTPUT_FIRE_BYTES=94232 * 1752), 2.0)
    assert model._packed_rate(tx, "STAT_ATTEMPTS", 0, 16) is None
    stage = _tx_stage(model.link_metrics("A2B", 2.0))
    assert stage.frame_rate.value == 94232.0
    assert stage.throughput.value == 94232 * 1752 * 8.0
    assert not stage.throughput.estimated


def test_tx_scheduler_first_sample_is_baseline_and_invalidation_drops_one():
    """First sample only establishes the baseline; invalidate_baselines()
    drops exactly one interval for the new 32-bit TX output path.
    """
    model = PerformanceModel()
    tx = Target.ALICE_TX_SCHEDULER
    model.ingest(tx, _values(tx, PERF_OUTPUT_FIRE_FRAMES=0,
                             PERF_OUTPUT_FIRE_BYTES=0), 1.0)
    stage = _tx_stage(model.link_metrics("A2B", 1.0))
    assert stage.frame_rate.value is None
    assert stage.throughput.value is None

    model.ingest(tx, _values(tx, PERF_OUTPUT_FIRE_FRAMES=100,
                             PERF_OUTPUT_FIRE_BYTES=175200), 2.0)
    stage = _tx_stage(model.link_metrics("A2B", 2.0))
    assert stage.frame_rate.value == 100.0

    model.invalidate_baselines()
    model.ingest(tx, _values(tx, PERF_OUTPUT_FIRE_FRAMES=110,
                             PERF_OUTPUT_FIRE_BYTES=192720), 3.0)
    assert model._rate32(tx, "PERF_OUTPUT_FIRE_FRAMES") is None
    model.ingest(tx, _values(tx, PERF_OUTPUT_FIRE_FRAMES=130,
                             PERF_OUTPUT_FIRE_BYTES=227760), 4.0)
    assert model._rate32(tx, "PERF_OUTPUT_FIRE_FRAMES") == 20.0
