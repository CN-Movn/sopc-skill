"""Single source of truth for every diagnostic register shown by the GUI."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from protocol import Target


class Access(StrEnum):
    RO = "RO"
    RW = "RW"
    WO = "WO/Pulse"
    W1C = "RO/W1C"


class Group(StrEnum):
    CONFIG = "配置寄存器"
    ACTIVE = "Active配置"
    STATUS = "状态寄存器"
    STATS = "统计寄存器"
    ERROR = "错误与诊断"
    SNAPSHOT = "Snapshot / Debug"
    PROTOCOL = "MCP通信统计"


@dataclass(frozen=True)
class RegisterSpec:
    ip_type: str
    name: str
    chinese: str
    key: int
    access: Access
    group: Group
    periodic: bool = True
    snapshot: bool = False
    rule: str = "default"
    expected: int | None = None

    @property
    def offset_text(self) -> str:
        if self.snapshot:
            select = (self.key & 0x03FC) >> 2
            return f"Snapshot Select 0x{select:02X}, Word {self.key & 3}"
        return f"Offset 0x{self.key:03X}"


@dataclass(frozen=True)
class InstanceSpec:
    target: int
    title: str
    ip_type: str
    registers: tuple[RegisterSpec, ...]


def reg(ip: str, name: str, chinese: str, offset: int, access: Access,
        group: Group, rule: str = "default", expected: int | None = None,
        periodic: bool = True) -> RegisterSpec:
    return RegisterSpec(ip, name, chinese, offset, access, group, periodic,
                        False, rule, expected)


def snap(ip: str, name: str, chinese: str, select: int, word: int,
         rule: str = "default") -> RegisterSpec:
    return RegisterSpec(ip, name, chinese,
                        0x8000 | ((select & 0xFF) << 2) | word,
                        Access.RO, Group.SNAPSHOT, True, True, rule)


def _scheduler_physical(tx: bool) -> list[RegisterSpec]:
    ip = "tx_scheduler" if tx else "rx_scheduler"
    expected = 0x54580500 if tx else 0x52580500
    rows = [
        reg(ip,"ID_VERSION","IP标识与版本",0x000,Access.RO,Group.STATUS,"id",expected),
        reg(ip,"SCRATCH","软件暂存",0x004,Access.RW,Group.CONFIG),
        reg(ip,"COMMAND","控制命令",0x008,Access.WO,Group.CONFIG,periodic=False),
        reg(ip,"STATUS","运行状态",0x00C,Access.W1C,Group.STATUS,"scheduler_status"),
        reg(ip,"RESOURCE_SUMMARY","资源摘要",0x010,Access.RO,Group.STATUS,"resource"),
        reg(ip,"CORE_STATS","核心统计摘要",0x014,Access.RO,Group.STATS),
        reg(ip,"ERROR_STATUS","错误状态",0x018,Access.W1C,Group.ERROR,"sticky_error"),
        reg(ip,"DEBUG_SELECT_LEGACY","旧调试选择",0x020,Access.RW,Group.SNAPSHOT),
        reg(ip,"SNAPSHOT_DATA0_LEGACY","旧快照数据0",0x024,Access.RO,Group.SNAPSHOT),
        reg(ip,"SNAPSHOT_DATA1_LEGACY","旧快照数据1",0x028,Access.RO,Group.SNAPSHOT),
        reg(ip,"SNAPSHOT_DATA2_LEGACY","旧快照数据2",0x02C,Access.RO,Group.SNAPSHOT),
        reg(ip,"SNAPSHOT_DATA3_LEGACY","旧快照数据3",0x030,Access.RO,Group.SNAPSHOT),
        reg(ip,"PATH_STATUS","数据路径状态",0x034,Access.RO,Group.STATUS,"activity"),
        reg(ip,"PROGRESS_STATUS","处理进度",0x038,Access.RO,Group.STATUS,"activity"),
        reg(ip,"LAST_EVENTS","最近事件",0x03C,Access.RO,Group.ERROR),
        reg(ip,"FIRST_FATAL_INFO","首次Fatal信息",0x040,Access.RO,Group.ERROR,"fatal"),
        reg(ip,"TIMEBASE_LO","时间基低32位",0x044,Access.RO,Group.STATUS),
        reg(ip,"TIMEBASE_HI","时间基高32位",0x048,Access.RO,Group.STATUS),
        reg(ip,"SHADOW_MAX_PAYLOAD","Payload Shadow",0x080,Access.RW,Group.CONFIG),
        reg(ip,"SHADOW_FRAME_BYTES","Physical Frame Shadow",0x084,Access.RW,Group.CONFIG),
        reg(ip,"APPLY_STATUS","配置Apply状态",0x088,Access.W1C,Group.CONFIG,"apply"),
        reg(ip,"ACTIVE_MAX_PAYLOAD","Active Payload",0x08C,Access.RO,Group.ACTIVE),
        reg(ip,"ACTIVE_FRAME_BYTES","Active Physical Frame",0x090,Access.RO,Group.ACTIVE),
        reg(ip,"SHADOW_ARQ_ENABLE","ARQ Enable Shadow",0x0A0,Access.RW,Group.CONFIG),
        reg(ip,"SHADOW_ARQ_TIMEOUT","ARQ Timeout Shadow",0x0A4,Access.RW if tx else Access.RO,Group.CONFIG),
        reg(ip,"SHADOW_ARQ_MAX_RETRY","Max Retry Shadow",0x0A8,Access.RW if tx else Access.RO,Group.CONFIG),
        reg(ip,"SHADOW_INITIAL_SEQUENCE","Initial Sequence Shadow",0x0AC,Access.RW,Group.CONFIG),
        reg(ip,"ARQ_APPLY_STATUS","ARQ配置状态",0x0B0,Access.RO,Group.CONFIG,"apply"),
        reg(ip,"ACTIVE_ARQ_ENABLE","Active ARQ Enable",0x0B4,Access.RO,Group.ACTIVE),
        reg(ip,"ACTIVE_ARQ_TIMEOUT","Active Timeout",0x0B8,Access.RO,Group.ACTIVE),
        reg(ip,"ACTIVE_ARQ_MAX_RETRY","Active Max Retry",0x0BC,Access.RO,Group.ACTIVE),
        reg(ip,"ACTIVE_INITIAL_SEQUENCE","Active Initial Sequence",0x0C0,Access.RO,Group.ACTIVE),
    ]
    if tx:
        rows += [
            reg(ip,"ARQ_STATUS","ARQ窗口与会话状态",0x0C4,Access.RO,Group.STATUS,"arq_status"),
            reg(ip,"OUTSTANDING_DESC_SLOT","Outstanding Descriptor/Slot",0x0C8,Access.RO,Group.STATUS,"activity"),
            reg(ip,"OUTSTANDING_SEQUENCE","Outstanding Sequence",0x0CC,Access.RO,Group.STATUS),
            reg(ip,"OUTSTANDING_PROCESS_RETRY","Outstanding Process/Retry",0x0D0,Access.RO,Group.STATUS),
            reg(ip,"ATTEMPT_TIMER","Attempt Timer",0x0D4,Access.RO,Group.STATUS,"activity"),
            reg(ip,"LAST_FEEDBACK_INFO","最近反馈信息",0x0D8,Access.RO,Group.ERROR),
            reg(ip,"LAST_FEEDBACK_SEQUENCE","最近反馈Sequence",0x0DC,Access.RO,Group.ERROR),
            reg(ip,"LAST_FAILURE_INFO","最近失败信息",0x0E0,Access.RO,Group.ERROR,"failure"),
            reg(ip,"LAST_FAILURE_SEQUENCE","最近失败Sequence",0x0E4,Access.RO,Group.ERROR),
            reg(ip,"STAT_ATTEMPTS","发送Attempt数",0x0E8,Access.RO,Group.STATS),
            reg(ip,"STAT_FEEDBACK","反馈统计",0x0EC,Access.RO,Group.STATS),
            reg(ip,"STAT_RETX","重传统计",0x0F0,Access.RO,Group.STATS,"tx_retx_stats"),
            reg(ip,"STAT_FAILURES","失败统计",0x0F4,Access.RO,Group.STATS,"tx_failure_stats"),
            reg(ip,"STAT_TERMINAL","终结事件统计",0x0F8,Access.RO,Group.STATS),
            reg(ip,"STAT_INGRESS","入口事件统计",0x0FC,Access.RO,Group.STATS),
        ]
    else:
        rows += [
            reg(ip,"ARQ_STATUS","ARQ接收状态",0x0C4,Access.RO,Group.STATUS,"arq_status"),
            reg(ip,"EXPECTED_SEQUENCE","Expected Sequence",0x0C8,Access.RO,Group.STATUS),
            reg(ip,"CONFIGURED_PROCESS","Configured Process",0x0CC,Access.RO,Group.ACTIVE),
            reg(ip,"LAST_DECISION_INFO","最近Sequence判决",0x0D0,Access.RO,Group.ERROR),
            reg(ip,"LAST_DECISION_SEQUENCE","最近判决Sequence",0x0D4,Access.RO,Group.ERROR),
            reg(ip,"LAST_DECISION_RETRY","最近判决Retry",0x0D8,Access.RO,Group.ERROR),
            reg(ip,"LOCAL_FEEDBACK_STATUS","Local Feedback队列",0x0DC,Access.RO,Group.STATUS,"activity"),
            reg(ip,"REMOTE_FEEDBACK_STATUS","Remote Feedback队列",0x0E0,Access.RO,Group.STATUS,"activity"),
            reg(ip,"STAT_NEW_ACCEPTED","Expected接收数",0x0E4,Access.RO,Group.STATS),
            reg(ip,"STAT_DUPLICATE","Duplicate数",0x0E8,Access.RO,Group.STATS,"recoverable_counter"),
            reg(ip,"STAT_UNEXPECTED","Unexpected数",0x0EC,Access.RO,Group.STATS,"recoverable_counter"),
            reg(ip,"STAT_PROCESS_MISMATCH","Process Mismatch数",0x0F0,Access.RO,Group.STATS,"error_counter"),
            reg(ip,"STAT_FEEDBACK_GENERATED","本地反馈生成数",0x0F4,Access.RO,Group.STATS),
            reg(ip,"STAT_FEEDBACK_ROUTED","远端反馈路由数",0x0F8,Access.RO,Group.STATS),
            reg(ip,"STAT_COMPAT_DROP","兼容路径Drop数",0x0FC,Access.RO,Group.STATS,"recoverable_counter"),
        ]
    rows += [
        reg(ip,"DEBUG_SELECT","调试页选择",0x100,Access.RW,Group.SNAPSHOT),
        reg(ip,"SNAPSHOT_DATA0","快照数据0",0x104,Access.RO,Group.SNAPSHOT),
        reg(ip,"SNAPSHOT_DATA1","快照数据1",0x108,Access.RO,Group.SNAPSHOT),
        reg(ip,"SNAPSHOT_DATA2","快照数据2",0x10C,Access.RO,Group.SNAPSHOT),
        reg(ip,"SNAPSHOT_DATA3","快照数据3",0x110,Access.RO,Group.SNAPSHOT),
    ]
    return rows


PERF_WORDS = {
    0:("GLOBAL_RESOURCE","GLOBAL_STATS","GLOBAL_PATH","GLOBAL_PROGRESS"),
    1:("CAPTURE_TIME_LO","CAPTURE_TIME_HI","PERF_SCHEMA","CAPTURE_SEQUENCE"),
    2:("INPUT_FIRE_BEATS","INPUT_FIRE_BYTES","INPUT_FIRE_FRAMES","INPUT_STALL_CYCLES"),
    3:("INPUT_READY_LOW","WRITE_AW_WAIT","WRITE_W_STALL","WRITE_B_WAIT"),
    4:("WRITE_FIRE_BEATS","WRITE_BURSTS","READ_AR_WAIT","READ_R_GAP"),
    5:("READ_R_STALL","READ_FIRE_BEATS","READ_BURSTS","OUTPUT_FIRE_BEATS"),
    6:("OUTPUT_FIRE_BYTES","OUTPUT_FIRE_FRAMES","OUTPUT_STALL","TX_CMD_FIRE"),
    7:("TX_CMD_STALL","PERF_TIME_LO","PERF_TIME_HI","PERF_CAPTURE_SEQUENCE"),
    8:("READ_AR_INTERNAL_GAP","READ_AR_WAIT_COPY","FIRST_LATENCY_SUM_LO","FIRST_LATENCY_SUM_HI"),
    9:("FIRST_LATENCY_COUNT","FIRST_LATENCY_MAX","INTER_GAP_TOTAL","INTER_GAP_COUNT"),
    10:("INTER_GAP_MAX","FRAME_SWITCH_GAP_TOTAL","FRAME_SWITCH_GAP_COUNT","FRAME_SWITCH_GAP_MAX"),
}


def _scheduler_snapshots(tx: bool) -> list[RegisterSpec]:
    ip = "tx_scheduler" if tx else "rx_scheduler"
    rows: list[RegisterSpec] = []
    for index in range(8):
        for word, name in enumerate(("META","PROCESS_RETRY","SEQUENCE","LENGTH")):
            rows.append(snap(ip,f"DESC{index}_{name}",f"Descriptor {index} {name}",index<<4,word,"descriptor"))
    for index in range(8):
        for word, name in enumerate(("OWNER","ADDRESS","SLOT_SIZE","OWNER_SEQUENCE")):
            rows.append(snap(ip,f"SLOT{index}_{name}",f"Slot {index} {name}",(index<<4)|1,word,"slot"))
    for page, prefix in ((2,"WRITE_ENGINE"),(3,"READ_ENGINE")):
        for index in range(2):
            for word in range(4):
                rows.append(snap(ip,f"{prefix}{index}_WORD{word}",f"{prefix} {index} 数据{word}",(index<<4)|page,word,"activity"))
    for page, names in (
        (4,("TIMEBASE_LO","TIMEBASE_HI","DESC_TIMESTAMP_LO","DESC_TIMESTAMP_HI")),
        (5,("FIRST_FATAL_INFO","ERROR_STATUS","FIRST_FATAL_TIME_LO","FIRST_FATAL_TIME_HI")),
    ):
        for word,name in enumerate(names):
            rows.append(snap(ip,name,name,page,word,"fatal" if page==5 else "default"))
    for index in range(8):
        for word,name in enumerate(("EVENT0","EVENT1","TIME_LO","TIME_HI")):
            rows.append(snap(ip,f"HISTORY{index}_{name}",f"历史事件{index} {name}",(index<<4)|6,word,"history"))
    for index,names in PERF_WORDS.items():
        for word,name in enumerate(names):
            rows.append(snap(ip,f"PERF_{name}",f"性能 {name}",(index<<4)|7,word,"activity"))
    extra = {
        11:("ARQ_STATUS","OUT_DESC_SLOT","OUT_SEQUENCE","OUT_PROCESS_RETRY"),
        12:("ARQ_TIMER","LAST_FB_INFO","LAST_FB_SEQUENCE","STAT_FEEDBACK"),
        13:("LAST_FAILURE_INFO","LAST_FAILURE_SEQUENCE","STAT_RETX","STAT_FAILURES"),
        14:("STAT_ATTEMPTS","STAT_TERMINAL","STAT_INGRESS","ARQ_SCHEMA"),
    } if tx else {
        11:("STAT_ACK","STAT_NACK","STAT_PROTOCOL_ERROR","LAST_ACK_NACK_HEAD"),
        12:("LAST_ACK_NACK_BODY","LAST_ACK_NACK_TAIL","LAST_ACK_NACK_TIME_LO","LAST_ACK_NACK_TIME_HI"),
        13:("ARQ_STATUS","EXPECTED_SEQUENCE","PROCESS_ID","INITIAL_SEQUENCE"),
        14:("LAST_DECISION_INFO","LAST_DECISION_SEQUENCE","LAST_DECISION_RETRY","LAST_DECISION_TIME_LO"),
        15:("LOCAL_FB_STATUS","REMOTE_FB_STATUS","LOCAL_FB_PUSH_POP","REMOTE_FB_PUSH_POP"),
    }
    for index,names in extra.items():
        for word,name in enumerate(names):
            if tx and name == "STAT_FAILURES":
                rule = "tx_failure_stats"
            elif name == "LAST_FAILURE_INFO":
                rule = "failure"
            elif name == "STAT_PROTOCOL_ERROR":
                rule = "error_counter"
            elif name == "STAT_RETX":
                rule = "tx_retx_stats"
            elif name == "ARQ_STATUS":
                rule = "arq_status"
            elif name in ("LAST_FAILURE_SEQUENCE", "ARQ_SCHEMA", "PROCESS_ID",
                          "INITIAL_SEQUENCE", "EXPECTED_SEQUENCE",
                          "LAST_DECISION_SEQUENCE", "LAST_DECISION_RETRY",
                          "LAST_DECISION_TIME_LO", "LAST_ACK_NACK_BODY",
                          "LAST_ACK_NACK_TAIL", "LAST_ACK_NACK_TIME_LO",
                          "LAST_ACK_NACK_TIME_HI", "LOCAL_FB_PUSH_POP",
                          "REMOTE_FB_PUSH_POP"):
                rule = "default"
            else:
                rule = "activity"
            rows.append(snap(ip,f"ARQ_{name}",f"ARQ {name}",(index<<4)|7,word,rule))
    return rows


def scheduler_registers(tx: bool) -> tuple[RegisterSpec, ...]:
    return tuple(_scheduler_physical(tx) + _scheduler_snapshots(tx))


def tx_wrapper_registers() -> tuple[RegisterSpec, ...]:
    ip="tx_wrapper"; rows=[
        reg(ip,"FRAME_BYTES_LEGACY","旧Frame Bytes Shadow",0x000,Access.RW,Group.CONFIG),
        reg(ip,"STAT_TX_FRAME_TOTAL","发送物理帧数",0x004,Access.RO,Group.STATS),
        reg(ip,"STAT_TX_TOTAL","发送命令数",0x008,Access.RO,Group.STATS),
        reg(ip,"STAT_ACK_TOTAL","ACK发送数",0x00C,Access.RO,Group.STATS),
        reg(ip,"STAT_NACK_TOTAL","NACK发送数",0x010,Access.RO,Group.STATS),
        reg(ip,"STAT_RETX_TOTAL","重传命令数",0x014,Access.RO,Group.STATS,"recoverable_counter"),
        reg(ip,"STAT_TX_PAYLOAD_BYTES","发送Payload字节",0x018,Access.RO,Group.STATS),
        reg(ip,"STAT_SHORT_DATA_TOTAL","短帧数",0x01C,Access.RO,Group.STATS),
        reg(ip,"STAT_PAD_BYTES","Padding字节",0x020,Access.RO,Group.STATS),
    ]
    debug_names=("IN_TLAST_FRAMES","IN_CUR_BEATS","IN_LAST_BEATS","IN_MIN_BEATS","IN_MAX_BEATS","IN_STALL","IN_LAST_PAYLOAD","IN_READY_LOW","FIFO_TLAST_FRAMES","FIFO_CUR_BEATS","FIFO_LAST_BEATS","FIFO_MIN_BEATS","FIFO_MAX_BEATS","FIFO_STALL","FIFO_LAST_PAYLOAD","OUT_FRAMES","OUT_CUR_BEATS","OUT_LAST_BEATS","OUT_MIN_BEATS","OUT_MAX_BEATS","OUT_STALL","OUT_READY_LOW")
    rows += [reg(ip,f"DBG_{name}",f"调试 {name}",0x024+i*4,Access.RO,Group.STATUS,"activity") for i,name in enumerate(debug_names)]
    rows += [
        reg(ip,"SHADOW_MAX_PAYLOAD","Payload Shadow",0x080,Access.RW,Group.CONFIG),
        reg(ip,"SHADOW_FRAME_BYTES","Frame Shadow",0x084,Access.RW,Group.CONFIG),
        reg(ip,"APPLY_STATUS","Apply状态",0x088,Access.W1C,Group.CONFIG,"apply"),
        reg(ip,"ACTIVE_MAX_PAYLOAD","Active Payload",0x08C,Access.RO,Group.ACTIVE),
        reg(ip,"ACTIVE_FRAME_BYTES","Active Frame",0x090,Access.RO,Group.ACTIVE),
    ]; return tuple(rows)


def rx_wrapper_registers() -> tuple[RegisterSpec, ...]:
    ip="rx_wrapper"; names=(
        ("STAT_RX_FRAME_TOTAL","接收物理帧数",Group.STATS,"default"),("STAT_RX_TOTAL","接收逻辑帧数",Group.STATS,"default"),
        ("STAT_ACK_TOTAL","ACK帧数",Group.STATS,"default"),("STAT_NACK_TOTAL","NACK帧数",Group.STATS,"default"),
        ("STAT_RX_PAYLOAD_BYTES","接收Payload字节",Group.STATS,"default"),("STAT_DATA_DROP_TOTAL","DATA Drop数",Group.STATS,"error_counter"),
        ("STAT_ACK_DROP_TOTAL","ACK Drop数",Group.STATS,"error_counter"),("STAT_NACK_DROP_TOTAL","NACK Drop数",Group.STATS,"error_counter"),
        ("STAT_HEAD_ERR_TOTAL","Header错误数",Group.ERROR,"error_counter"),("STAT_CHECKSUM_ERR_TOTAL","Checksum错误数",Group.ERROR,"error_counter"),
        ("DEBUG_STATUS","Parser状态",Group.STATUS,"parser"),("DEBUG_COUNTERS","Parser计数",Group.STATUS,"activity"),
        ("STAT_RECOVER_TOTAL","恢复同步数",Group.ERROR,"recoverable_counter"),("STAT_TLAST_ERR_TOTAL","TLAST错误数",Group.ERROR,"error_counter"),
        ("STAT_FORMAT_DETAIL","类型/长度错误",Group.ERROR,"format_detail"),("STAT_TAIL_LAST_LEN","Tail错误/最近长度",Group.ERROR,"tail_last_len"),
    )
    rows=[reg(ip,n,c,i*4,Access.RO,g,r) for i,(n,c,g,r) in enumerate(names)]
    rows += [reg(ip,"SHADOW_MAX_PAYLOAD","Payload Shadow",0x080,Access.RW,Group.CONFIG),reg(ip,"SHADOW_FRAME_BYTES","Frame Shadow",0x084,Access.RW,Group.CONFIG),reg(ip,"APPLY_STATUS","Apply状态",0x088,Access.W1C,Group.CONFIG,"apply"),reg(ip,"ACTIVE_MAX_PAYLOAD","Active Payload",0x08C,Access.RO,Group.ACTIVE),reg(ip,"ACTIVE_FRAME_BYTES","Active Frame",0x090,Access.RO,Group.ACTIVE)]
    return tuple(rows)


def channel_registers() -> tuple[RegisterSpec, ...]:
    ip="channel"; rows=[
        reg(ip,"ID_VERSION","IP标识与版本",0x000,Access.RO,Group.STATUS,"id",0x42450100),
        reg(ip,"CAPABILITY","能力",0x004,Access.RO,Group.STATUS),reg(ip,"SHADOW_CONTROL","控制Shadow",0x008,Access.RW,Group.CONFIG),
        reg(ip,"SHADOW_THRESHOLD","选择阈值Shadow",0x00C,Access.RW,Group.CONFIG),reg(ip,"SHADOW_MAX_FLIPS","Max Flips Shadow",0x010,Access.RW,Group.CONFIG),
        reg(ip,"SHADOW_SEED","随机Seed Shadow",0x014,Access.RW,Group.CONFIG),reg(ip,"COMMAND","控制命令",0x018,Access.WO,Group.CONFIG,periodic=False),
        reg(ip,"COMMAND_STATUS","命令与Snapshot状态",0x01C,Access.W1C,Group.STATUS,"apply"),reg(ip,"ACTIVE_CONTROL","Active控制",0x020,Access.RO,Group.ACTIVE),
        reg(ip,"ACTIVE_RANDOM","Active随机参数",0x024,Access.RO,Group.ACTIVE),reg(ip,"ACTIVE_SEED","Active Seed",0x028,Access.RO,Group.ACTIVE),
        reg(ip,"LFSR_STATE","LFSR状态",0x02C,Access.RO,Group.STATUS),reg(ip,"FRAMES_PASSED","通过帧数",0x030,Access.RO,Group.STATS),
        reg(ip,"FRAMES_SELECTED","选中帧数",0x034,Access.RO,Group.STATS),reg(ip,"ERROR_FRAMES","注错帧数",0x038,Access.RO,Group.STATS),
        reg(ip,"FLIPPED_BITS","累计翻转Bit",0x03C,Access.RO,Group.STATS),reg(ip,"LAST_FLIP_COUNT","最近翻转数量",0x040,Access.RO,Group.STATUS),
        reg(ip,"LAST_FLIP_LOCATION","最近翻转位置",0x044,Access.RO,Group.STATUS),reg(ip,"SCRATCH","软件暂存",0x048,Access.RW,Group.CONFIG),
    ]; return tuple(rows)


def source_registers() -> tuple[RegisterSpec, ...]:
    ip="source"; names=("ID_VERSION","CONTROL","MODE","FIXED_LENGTH","FRAME_GAP","FRAME_LIMIT","RANDOM_SEED","MIX_FULL_PERIOD","STATUS","ACTIVE_CONFIG","FRAME_LENGTHS","FRAMES_SENT","BYTES_SENT_LO","BYTES_SENT_HI","FULL_FRAMES","SHORT_FRAMES","STALL_CYCLES_LO","STALL_CYCLES_HI","SEQUENCE_STATE","CONFIG_APPLIED")
    groups=(Group.STATUS,Group.CONFIG,Group.CONFIG,Group.CONFIG,Group.CONFIG,Group.CONFIG,Group.CONFIG,Group.CONFIG,Group.STATUS,Group.ACTIVE,Group.STATUS,Group.STATS,Group.STATS,Group.STATS,Group.STATS,Group.STATS,Group.STATS,Group.STATS,Group.STATUS,Group.ACTIVE)
    rows=[]
    for i,(name,group) in enumerate(zip(names,groups,strict=True)):
        access=Access.RO if i==0 or i>=8 else (Access.WO if i==1 else Access.RW)
        periodic=i!=1; rule="id" if i==0 else ("activity" if name in ("STATUS","CONTROL") else "default")
        rows.append(reg(ip,name,name.replace("_"," "),i*4,access,group,rule,0x41580101 if i==0 else None,periodic))
    return tuple(rows)


PROTOCOL_REGISTERS = tuple(
    reg("protocol",name.upper(),chinese,index*4,Access.RO,Group.PROTOCOL,
        "error_counter" if name!="frames_ok" else "default")
    for index,(name,chinese) in enumerate((
        ("frames_ok","有效请求帧"),("crc_errors","CRC错误"),("version_errors","版本错误"),
        ("length_errors","长度错误"),("type_errors","类型错误"),("request_drops","请求丢弃"),
        ("uart_errors","UART错误"),("response_drops","响应丢弃"),("event_drops","事件丢弃"),
        ("report_drops","报告丢弃"),
    ))
)


SCHEDULER_CORE_OFFSETS = frozenset((
    0x000, 0x00C, 0x010, 0x018, 0x034, 0x038, 0x03C, 0x040,
    0x080, 0x084, 0x088, 0x08C, 0x090,
    0x0A0, 0x0A4, 0x0A8, 0x0AC, 0x0B0, 0x0B4, 0x0B8, 0x0BC, 0x0C0,
    0x0C4, 0x0C8, 0x0CC, 0x0D0, 0x0D4, 0x0D8, 0x0DC, 0x0E0,
    0x0E4, 0x0E8, 0x0EC, 0x0F0, 0x0F4, 0x0F8, 0x0FC,
))
TX_WRAPPER_CORE_OFFSETS = frozenset((
    0x000, 0x004, 0x008, 0x00C, 0x010, 0x014, 0x018, 0x01C,
    0x020, 0x024, 0x038, 0x03C, 0x040, 0x044, 0x058, 0x05C,
    0x060, 0x074, 0x078, 0x080, 0x084, 0x088, 0x08C, 0x090,
))


def _critical_scheduler(tx: bool) -> tuple[RegisterSpec, ...]:
    rows = scheduler_registers(tx)
    return tuple(item for item in rows if
                 (not item.snapshot and item.key in SCHEDULER_CORE_OFFSETS) or
                 (item.snapshot and (
                     # Vitis triggers Snapshot Select 0x17 before exporting
                     # 0x67, whose Word 0/1 are the final AXIS output
                     # byte/frame counters defined by the RTL.
                     item.key in (0x819C, 0x819D) or
                     ((item.key >> 2) & 0xFF) in
                     (0x02, 0x12, 0x03, 0x13, 0x05, 0x07) or
                     (((item.key >> 2) & 0x0F) == 0x07 and
                      11 <= (((item.key >> 2) & 0xFF) >> 4) <= (14 if tx else 15))
                 )))


TX_SCHEDULER_REGISTERS = _critical_scheduler(True)
RX_SCHEDULER_REGISTERS = _critical_scheduler(False)
TX_WRAPPER_REGISTERS = tuple(item for item in tx_wrapper_registers()
                             if item.key in TX_WRAPPER_CORE_OFFSETS)
RX_WRAPPER_REGISTERS = rx_wrapper_registers()
CHANNEL_REGISTERS = tuple(item for item in channel_registers()
                          if item.key != 0x018)
SOURCE_REGISTERS = tuple(item for item in source_registers()
                         if item.key != 0x004)

ALICE_INSTANCES = (
    InstanceSpec(Target.ALICE_TX_SCHEDULER,"TX Scheduler","tx_scheduler",TX_SCHEDULER_REGISTERS),
    InstanceSpec(Target.ALICE_TX_WRAPPER,"TX Wrapper","tx_wrapper",TX_WRAPPER_REGISTERS),
    InstanceSpec(Target.ALICE_RX_SCHEDULER,"RX Scheduler","rx_scheduler",RX_SCHEDULER_REGISTERS),
    InstanceSpec(Target.ALICE_RX_WRAPPER,"RX Wrapper","rx_wrapper",RX_WRAPPER_REGISTERS),
    InstanceSpec(Target.A2B_CHANNEL,"A2B Analog Channel","channel",CHANNEL_REGISTERS),
    InstanceSpec(Target.ALICE_SOURCE,"Test Source","source",SOURCE_REGISTERS),
)
BOB_INSTANCES = (
    InstanceSpec(Target.BOB_TX_SCHEDULER,"TX Scheduler","tx_scheduler",TX_SCHEDULER_REGISTERS),
    InstanceSpec(Target.BOB_TX_WRAPPER,"TX Wrapper","tx_wrapper",TX_WRAPPER_REGISTERS),
    InstanceSpec(Target.BOB_RX_SCHEDULER,"RX Scheduler","rx_scheduler",RX_SCHEDULER_REGISTERS),
    InstanceSpec(Target.BOB_RX_WRAPPER,"RX Wrapper","rx_wrapper",RX_WRAPPER_REGISTERS),
    InstanceSpec(Target.B2A_CHANNEL,"B2A Analog Channel","channel",CHANNEL_REGISTERS),
    InstanceSpec(Target.BOB_SOURCE,"Test Source","source",SOURCE_REGISTERS),
)
INSTANCES = ALICE_INSTANCES + BOB_INSTANCES

INSTANCE_BY_TARGET = {int(item.target): item for item in INSTANCES}
