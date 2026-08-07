"""ARQ register whitelist mirrored from the Vitis ``arq_registers.c``.

This module is the single Python-side source for register names, offsets,
access attributes, sections and write masks.  Keep it synchronized with the Vitis tables;
the GUI and command analyzer both consume these definitions.
"""
from __future__ import annotations

from dataclasses import dataclass


ARQ_IP_TX_WRAPPER = 0x01
ARQ_IP_RX_WRAPPER = 0x02
ARQ_IP_TX_SCHEDULER = 0x03
ARQ_IP_RX_SCHEDULER = 0x04
ARQ_IP_ALL = 0xFF

ARQ_OP_WRITE_REG = 0x01
ARQ_OP_READ_REG = 0x02
ARQ_OP_DUMP_ALL = 0x03


@dataclass(frozen=True, slots=True)
class ArqRegister:
    offset: int
    name: str
    access: str
    section: str
    write_mask: int = 0

    @property
    def display_text(self) -> str:
        return (f"0x{self.offset:04X} - {self.name} "
                f"[{self.access} / {self.section}]")


def _reg(offset: int, name: str, access: str, section: str,
         write_mask: int = 0) -> ArqRegister:
    return ArqRegister(offset, name, access, section, write_mask)


# Vitis: tx_wrapper_regs
TX_WRAPPER_REGISTERS = (
    _reg(0x000, "LEGACY_FRAME_SHADOW_ALIAS", "RW", "Config", 0x0000FFFF),
    _reg(0x080, "CFG_PAYLOAD_SHADOW", "RW", "Config", 0x0000FFFF),
    _reg(0x084, "CFG_FRAME_SHADOW", "RW", "Config", 0x0000FFFF),
    _reg(0x088, "CFG_APPLY_STATUS", "CONTROL/STATUS", "Config", 0x00000001),
    _reg(0x08C, "CFG_ACTIVE_PAYLOAD", "RO", "Config"),
    _reg(0x090, "CFG_ACTIVE_FRAME", "RO", "Config"),
    _reg(0x004, "STAT_TX_FRAME_TOTAL", "RO", "Statistics"),
    _reg(0x008, "STAT_TX_TOTAL", "RO", "Statistics"),
    _reg(0x00C, "STAT_ACK_TOTAL", "RO", "Statistics"),
    _reg(0x010, "STAT_NACK_TOTAL", "RO", "Statistics"),
    _reg(0x014, "STAT_RETX_TOTAL", "RO", "Statistics"),
    _reg(0x018, "STAT_TX_PAYLOAD_BYTES", "RO", "Statistics"),
    _reg(0x01C, "STAT_TX_SHORT_DATA_TOTAL", "RO", "Statistics"),
    _reg(0x020, "STAT_TX_PAD_BYTES", "RO", "Statistics"),
    _reg(0x024, "DBG_IN_TLAST_FRAME_CNT", "RO", "Debug"),
    _reg(0x028, "DBG_IN_CUR_FIRE_BEATS", "RO", "Debug"),
    _reg(0x02C, "DBG_IN_LAST_FIRE_BEATS", "RO", "Debug"),
    _reg(0x030, "DBG_IN_MIN_FIRE_BEATS", "RO", "Debug"),
    _reg(0x034, "DBG_IN_MAX_FIRE_BEATS", "RO", "Debug"),
    _reg(0x038, "DBG_IN_STALL_CYCLES", "RO", "Debug"),
    _reg(0x03C, "DBG_LAST_PAYLOAD_BYTES", "RO", "Debug"),
    _reg(0x040, "DBG_IN_READY_LOW_CYCLES", "RO", "Debug"),
    _reg(0x044, "DBG_FIFO_OUT_TLAST_FRAME_CNT", "RO", "Debug"),
    _reg(0x048, "DBG_FIFO_OUT_CUR_FIRE_BEATS", "RO", "Debug"),
    _reg(0x04C, "DBG_FIFO_OUT_LAST_FIRE_BEATS", "RO", "Debug"),
    _reg(0x050, "DBG_FIFO_OUT_MIN_FIRE_BEATS", "RO", "Debug"),
    _reg(0x054, "DBG_FIFO_OUT_MAX_FIRE_BEATS", "RO", "Debug"),
    _reg(0x058, "DBG_FIFO_OUT_STALL_CYCLES", "RO", "Debug"),
    _reg(0x05C, "DBG_FIFO_OUT_LAST_PAYLOAD_BYTES", "RO", "Debug"),
    _reg(0x060, "DBG_OUT_FRAME_CNT", "RO", "Debug"),
    _reg(0x064, "DBG_OUT_CUR_FIRE_BEATS", "RO", "Debug"),
    _reg(0x068, "DBG_OUT_LAST_FIRE_BEATS", "RO", "Debug"),
    _reg(0x06C, "DBG_OUT_MIN_FIRE_BEATS", "RO", "Debug"),
    _reg(0x070, "DBG_OUT_MAX_FIRE_BEATS", "RO", "Debug"),
    _reg(0x074, "DBG_OUT_STALL_CYCLES", "RO", "Debug"),
    _reg(0x078, "DBG_OUT_READY_LOW_CYCLES", "RO", "Debug"),
)

# Vitis: rx_wrapper_regs
RX_WRAPPER_REGISTERS = (
    _reg(0x080, "CFG_PAYLOAD_SHADOW", "RW", "Config", 0x0000FFFF),
    _reg(0x084, "CFG_FRAME_SHADOW", "RW", "Config", 0x0000FFFF),
    _reg(0x088, "CFG_APPLY_STATUS", "CONTROL/STATUS", "Config", 0x00000001),
    _reg(0x08C, "CFG_ACTIVE_PAYLOAD", "RO", "Config"),
    _reg(0x090, "CFG_ACTIVE_FRAME", "RO", "Config"),
    _reg(0x000, "STAT_RX_FRAME_TOTAL", "RO", "Statistics"),
    _reg(0x004, "STAT_RX_TOTAL", "RO", "Statistics"),
    _reg(0x008, "STAT_ACK_TOTAL", "RO", "Statistics"),
    _reg(0x00C, "STAT_NACK_TOTAL", "RO", "Statistics"),
    _reg(0x010, "STAT_RX_PAYLOAD_BYTES", "RO", "Statistics"),
    _reg(0x014, "STAT_DATA_DROP_TOTAL", "RO", "Error"),
    _reg(0x018, "STAT_ACK_DROP_TOTAL", "RO", "Error"),
    _reg(0x01C, "STAT_NACK_DROP_TOTAL", "RO", "Error"),
    _reg(0x020, "STAT_HEAD_ERR_TOTAL", "RO", "Error"),
    _reg(0x024, "STAT_CHECKSUM_ERR_TOTAL", "RO", "Error"),
    _reg(0x030, "STAT_RECOVER_TOTAL", "RO", "Statistics"),
    _reg(0x034, "STAT_TLAST_ERR_TOTAL", "RO", "Error"),
    _reg(0x038, "STAT_FORMAT_DETAIL", "RO", "Error"),
    _reg(0x03C, "STAT_TAIL_LAST_LEN", "RO", "Error"),
    _reg(0x028, "DEBUG_STATUS", "RO", "Debug"),
    _reg(0x02C, "DEBUG_COUNTERS", "RO", "Debug"),
)

def _scheduler_registers(error_status_mask: int) -> tuple[ArqRegister, ...]:
    """Build one Scheduler table; RX additionally exposes protocol-error W1C."""
    return (
        _reg(0x000, "ID_VERSION", "RO", "Config"),
        _reg(0x004, "SCRATCH", "RW", "Config", 0xFFFFFFFF),
        _reg(0x008, "COMMAND", "PULSE", "Config", 0x00000007),
        _reg(0x020, "DEBUG_SELECT", "RW", "Config", 0x000000F7),
        _reg(0x080, "CFG_PAYLOAD_SHADOW", "RW", "Config", 0x0000FFFF),
        _reg(0x084, "CFG_FRAME_SHADOW", "RW", "Config", 0x0000FFFF),
        _reg(0x088, "CFG_APPLY_STATUS", "CONTROL/STATUS", "Config", 0x00000009),
        _reg(0x08C, "CFG_ACTIVE_PAYLOAD", "RO", "Config"),
        _reg(0x090, "CFG_ACTIVE_FRAME", "RO", "Config"),
        _reg(0x00C, "STATUS", "W1C", "Status", 0x00000020),
        _reg(0x034, "PATH_STATUS", "RO", "Status"),
        _reg(0x038, "PROGRESS_STATUS", "RO", "Status"),
        _reg(0x010, "RESOURCE_SUMMARY", "RO", "Resources"),
        _reg(0x014, "CORE_STATS", "RO", "Statistics"),
        _reg(0x018, "ERROR_STATUS", "W1C", "Error", error_status_mask),
        _reg(0x040, "FIRST_FATAL_INFO", "RO", "Error"),
        _reg(0x024, "DEBUG_DATA0", "RO", "Debug"),
        _reg(0x028, "DEBUG_DATA1", "RO", "Debug"),
        _reg(0x02C, "DEBUG_DATA2", "RO", "Debug"),
        _reg(0x030, "DEBUG_DATA3", "RO", "Debug"),
        _reg(0x03C, "LAST_EVENTS", "RO", "Debug"),
        _reg(0x044, "TIMEBASE_LO", "RO", "Debug"),
        _reg(0x048, "TIMEBASE_HI", "RO", "Debug"),
    )


TX_SCHEDULER_REGISTERS = _scheduler_registers(0x00000003)
RX_SCHEDULER_REGISTERS = _scheduler_registers(0x00000023)

REGISTERS_BY_TARGET = {
    ARQ_IP_TX_WRAPPER: TX_WRAPPER_REGISTERS,
    ARQ_IP_RX_WRAPPER: RX_WRAPPER_REGISTERS,
    ARQ_IP_TX_SCHEDULER: TX_SCHEDULER_REGISTERS,
    ARQ_IP_RX_SCHEDULER: RX_SCHEDULER_REGISTERS,
}


def registers_for_target(target_id: int) -> tuple[ArqRegister, ...]:
    """Return the complete Vitis whitelist for one concrete target."""
    return REGISTERS_BY_TARGET.get(target_id, ())


def register_is_readable(register: ArqRegister) -> bool:
    """Match Vitis ``arq_register_is_readable`` exactly."""
    return register.access not in ("WO", "PULSE")


def register_is_writable(register: ArqRegister) -> bool:
    """Match Vitis ``arq_register_is_writable`` exactly."""
    return register.access != "RO"


def registers_for_operation(target_id: int,
                            operation: int) -> tuple[ArqRegister, ...]:
    registers = registers_for_target(target_id)
    if operation == ARQ_OP_READ_REG:
        return tuple(reg for reg in registers if register_is_readable(reg))
    if operation == ARQ_OP_WRITE_REG:
        return tuple(reg for reg in registers if register_is_writable(reg))
    return ()


def find_register(target_id: int, offset: int) -> ArqRegister | None:
    return next((reg for reg in registers_for_target(target_id)
                 if reg.offset == offset), None)
