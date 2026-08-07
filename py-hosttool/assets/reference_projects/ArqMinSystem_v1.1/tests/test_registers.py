from registers import (ALICE_INSTANCES, BOB_INSTANCES, CHANNEL_REGISTERS,
                       RX_SCHEDULER_REGISTERS, SOURCE_REGISTERS,
                       RX_WRAPPER_REGISTERS, TX_SCHEDULER_REGISTERS,
                       TX_WRAPPER_REGISTERS)


def test_node_pages_are_structurally_symmetric():
    assert len(ALICE_INSTANCES) == len(BOB_INSTANCES) == 6
    assert [x.ip_type for x in ALICE_INSTANCES] == [x.ip_type for x in BOB_INSTANCES]


def test_scheduler_capture_includes_output_performance_snapshot_fields():
    assert len(TX_SCHEDULER_REGISTERS) == 79
    assert len(RX_SCHEDULER_REGISTERS) == 83
    assert max(len(TX_SCHEDULER_REGISTERS), len(RX_SCHEDULER_REGISTERS)) <= 2 * 82
    for collection in (TX_SCHEDULER_REGISTERS, RX_SCHEDULER_REGISTERS):
        fields = {item.name: item for item in collection}
        assert fields["PERF_OUTPUT_FIRE_BYTES"].key == 0x819C
        assert fields["PERF_OUTPUT_FIRE_FRAMES"].key == 0x819D


def test_write_only_registers_are_not_periodically_displayed():
    for collection in (TX_SCHEDULER_REGISTERS, RX_SCHEDULER_REGISTERS,
                       TX_WRAPPER_REGISTERS, CHANNEL_REGISTERS, SOURCE_REGISTERS):
        assert all(item.periodic for item in collection)


def test_tx_failure_registers_use_packed_field_rule():
    matches = [item for item in TX_SCHEDULER_REGISTERS
               if item.name in ("STAT_FAILURES", "ARQ_STAT_FAILURES")]
    assert len(matches) == 2
    assert all(item.rule == "tx_failure_stats" for item in matches)


def test_all_packed_diagnostic_registers_use_field_rules():
    tx_retx = [item for item in TX_SCHEDULER_REGISTERS
               if item.name in ("STAT_RETX", "ARQ_STAT_RETX")]
    assert len(tx_retx) == 2
    assert all(item.rule == "tx_retx_stats" for item in tx_retx)
    rules = {item.name: item.rule for item in RX_WRAPPER_REGISTERS}
    assert rules["STAT_FORMAT_DETAIL"] == "format_detail"
    assert rules["STAT_TAIL_LAST_LEN"] == "tail_last_len"
