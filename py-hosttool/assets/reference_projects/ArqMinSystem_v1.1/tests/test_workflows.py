import struct

from protocol import Opcode, Target
from workflows import DelayStep, IdleStep, RequestStep, WorkflowService


class DummyClient:
    pass


def test_enable_arq_workflow_freezes_the_safe_command_order(monkeypatch):
    service = WorkflowService(DummyClient())
    monkeypatch.setattr(service, "_next", lambda _generation: None)
    service.enable_bidirectional_arq(100000, 3, 0x1234)
    labels = [step.label for step in service.steps
              if isinstance(step, RequestStep)]
    assert labels == [
        "停止Alice测试源", "停止Bob测试源",
        "四个Scheduler软复位", "应用A2B ARQ配置", "应用B2A ARQ配置",
        "启动Alice测试源", "启动Bob测试源",
    ]
    assert sum(isinstance(step, IdleStep) for step in service.steps) == 1
    assert sum(isinstance(step, DelayStep) for step in service.steps) == 3
    reset_index = next(index for index, step in enumerate(service.steps)
                       if isinstance(step, RequestStep) and
                       step.label == "四个Scheduler软复位")
    idle_index = next(index for index, step in enumerate(service.steps)
                      if isinstance(step, IdleStep))
    assert reset_index < idle_index
    assert service.steps[idle_index].targets == service.TX_SCHEDULERS
    a2b = next(step for step in service.steps
               if isinstance(step, RequestStep) and step.label == "应用A2B ARQ配置")
    assert a2b.target == Target.A2B_LINK
    assert a2b.opcode == Opcode.SET_CONFIG
    assert a2b.busy_retry_ms == 5000
    assert struct.unpack("<BBHII", a2b.payload) == (1, 0, 3, 100000, 0x1234)


def test_busy_detail_decoder_reports_rx_resource_reason():
    assert WorkflowService._busy_reason(0x0402) == \
        "BUSY detail=0x0402（RX侧：已绑定或就绪资源未释放）"


def test_clear_stats_and_errors_keeps_the_required_order(monkeypatch):
    service = WorkflowService(DummyClient())
    monkeypatch.setattr(service, "_next", lambda _generation: None)
    service.clear_stats_and_errors()
    assert [step.label for step in service.steps] == [
        "清空全部统计", "清空全部错误"]
    assert service.steps[0].opcode == Opcode.CLEAR_STATS
    assert service.steps[0].payload == b""
    assert service.steps[1].opcode == Opcode.CLEAR_ERRORS
    assert service.steps[1].payload == b"\xFF\xFF\xFF\xFF"


def test_single_fault_configures_then_arms_both_directions(monkeypatch):
    service = WorkflowService(DummyClient())
    monkeypatch.setattr(service, "_next", lambda _generation: None)
    service.arm_bidirectional_once(2, 1)
    labels = [step.label for step in service.steps]
    assert labels == ["配置A2B单次注错", "配置B2A单次注错",
                      "触发A2B下一帧注错", "触发B2A下一帧注错"]
    for step in service.steps[:2]:
        enable, bypass, continuous, force_one, threshold, flips, _, _seed = \
            struct.unpack("<BBBBHBBI", step.payload)
        assert (enable, bypass, continuous, force_one, threshold, flips) == \
               (1, 0, 0, 1, 0, 2)


def test_second_workflow_is_rejected_while_first_is_running(monkeypatch):
    service = WorkflowService(DummyClient())
    monkeypatch.setattr(service, "_next", lambda _generation: None)
    results = []
    service.finished.connect(lambda name, ok, detail:
                             results.append((name, ok, detail)))
    service.bypass_faults()
    service.enable_continuous_faults(66, 1, 1)
    assert service.name == "停止注错并恢复旁路"
    assert results and results[-1][1] is False
    assert "正在运行" in results[-1][2]
