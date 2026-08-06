"""Codex 当前运行链路使用的 OpenCode Go 模型服务定义。

``opencode-go`` 是上游模型服务标识；Codex 中使用独立的
``opencode-go-bridge`` 配置标识，确保请求始终进入本地桥。
"""

from __future__ import annotations

from ..core.provider import ProviderDefinition

OpenCodeGoProvider = ProviderDefinition(
    id="opencode-go",
    name="OpenCode Go",
    model="deepseek-v4-flash",
    base_url="https://opencode.ai/zen/go/v1",
    wire_api="responses",
    config_id="opencode-go-bridge",
)
