from diagnostics import Severity, diagnose
from registers import Access, Group, reg


def test_error_counter_only_escalates_when_growing():
    spec = reg("x", "ERRORS", "错误", 0, Access.RO, Group.ERROR, "error_counter")
    assert diagnose(spec, 3, 3).severity == Severity.NOTICE
    assert diagnose(spec, 4, 3).severity == Severity.ERROR


def test_fatal_nonzero_is_red():
    spec = reg("x", "FATAL", "Fatal", 0, Access.RO, Group.ERROR, "fatal")
    assert diagnose(spec, 1).severity == Severity.ERROR


def test_tx_failure_stats_ignores_normal_remote_receive_field():
    spec = reg("tx_scheduler", "STAT_FAILURES", "失败统计", 0,
               Access.RO, Group.STATS, "tx_failure_stats")
    result = diagnose(spec, 0x5E000000)
    assert result.severity == Severity.NORMAL
    assert "反馈接收 94" in result.text


def test_tx_failure_stats_reports_real_terminal_failures():
    spec = reg("tx_scheduler", "STAT_FAILURES", "失败统计", 0,
               Access.RO, Group.STATS, "tx_failure_stats")
    assert diagnose(spec, 0x00000100).severity == Severity.ERROR
    assert diagnose(spec, 0x00000001).severity == Severity.ERROR


def test_activity_is_normal_operating_state():
    spec = reg("x", "PATH", "数据路径", 0, Access.RO, Group.STATUS, "activity")
    result = diagnose(spec, 0x1234)
    assert result.severity == Severity.NORMAL
    assert "正常" in result.text


def test_tail_last_len_ignores_normal_payload_length_change():
    spec = reg("rx_wrapper", "STAT_TAIL_LAST_LEN", "Tail错误/最近长度", 0,
               Access.RO, Group.ERROR, "tail_last_len")
    result = diagnose(spec, 0x000006D8, 0x00000000)
    assert result.severity == Severity.NORMAL
    assert result.text == "无Tail错误；最近Payload 1752字节"


def test_tail_last_len_reports_only_high_half_error_delta():
    spec = reg("rx_wrapper", "STAT_TAIL_LAST_LEN", "Tail错误/最近长度", 0,
               Access.RO, Group.ERROR, "tail_last_len")
    result = diagnose(spec, 0x000106D8, 0x00000000)
    assert result.severity == Severity.ERROR
    assert "Tail错误 +1" in result.text
    assert "最近Payload 1752字节" in result.text


def test_format_detail_compares_each_counter_independently():
    spec = reg("rx_wrapper", "STAT_FORMAT_DETAIL", "类型/长度错误", 0,
               Access.RO, Group.ERROR, "format_detail")
    length_result = diagnose(spec, 0x00010000, 0x00000000)
    type_result = diagnose(spec, 0x00000001, 0x00000000)
    assert length_result.severity == Severity.ERROR
    assert length_result.text == "长度错误 +1"
    assert type_result.severity == Severity.ERROR
    assert type_result.text == "类型错误 +1"


def test_tx_retx_stats_does_not_amplify_packed_byte_delta():
    spec = reg("tx_scheduler", "STAT_RETX", "重传统计", 0,
               Access.RO, Group.STATS, "tx_retx_stats")
    result = diagnose(spec, 0x00000100, 0x00000000)
    assert result.severity == Severity.NOTICE
    assert result.text == "重传命令 +1"
