# ARQ 寄存器调试指令协议

ARQ 寄存器调试使用与 `0x90`～`0x93` 相同的 22 Byte 测控指令帧，不定义额外外层或特殊校验规则。

## 公共帧结构

| 字节 | 含义 |
| --- | --- |
| 0～1 | 帧头 `EB 90` |
| 2 | InstructionCode，ARQ 固定为 `0x94` |
| 3～18 | 16 Byte 指令数据域 |
| 19 | Byte 2～18 累加和的低 8 位 |
| 20～21 | 帧尾 `55 AA` |

## 0x94 数据域

| 字节 | 含义 |
| --- | --- |
| 3 | MsgID；当前合法值为 `0x00` |
| 4 | DeviceID；当前设备为 `0x01` |
| 5 | Operation：`01` WRITE_REG，`02` READ_REG，`03` DUMP_ALL |
| 6 | TargetIP：`01` TX Wrapper，`02` RX Wrapper，`03` TX Scheduler，`04` RX Scheduler，`FF` ALL |
| 7～8 | RegisterOffset，16 位大端 |
| 9～12 | RegisterValue，32 位大端 |
| 13～18 | 未使用，固定填充 `0x55` |

## 操作约束

- WRITE_REG 使用 TargetIP、RegisterOffset 和 RegisterValue。
- READ_REG 使用 TargetIP 和 RegisterOffset，RegisterValue 必须为 0。
- DUMP_ALL 的 RegisterOffset 和 RegisterValue 必须为 0。
- TargetIP=`0xFF` 仅允许与 DUMP_ALL 组合。
- MsgID 不匹配的帧由目标设备静默忽略。
- DeviceID 不匹配的帧由目标设备静默忽略。
- 寄存器访问继续执行白名单、4 Byte 对齐、4 KB 范围、访问属性和写掩码检查。
- RW 写操作由 Vitis 软件自动回读并校验；W1C、PULSE 不执行等值回读；RO 拒绝写入。
- Scheduler Snapshot 与 Indexed Debug 访问仍遵循硬件规定的请求、完成、读取和 W1C 清除顺序。
