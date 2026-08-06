"""OpenCode Go → Codex 本地兼容桥。

方案 A：桥调用上游 /v1/responses（输出结构与 Codex 期望接近，响应转换最少；
上游约束已由最小探针验证）。仅监听 127.0.0.1 随机端口，进程退出即清理。
"""

__all__ = ["OpenCodeGoBridge", "BridgeHandle"]
