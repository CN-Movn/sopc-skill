"""Agent 角色定义。

角色由选中的 Provider 构造（make_role），核心与平台层都不再硬编码
Provider id。当前为单角色（DeepSeek），后续可扩展
DeepSeekExplorer / DeepSeekWorker / DeepSeekTester / DeepSeekReviewer。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .provider import ProviderDefinition

DEFAULT_NAME = "DeepSeek"
DEFAULT_REASONING_EFFORT = "ultra"

DEVELOPER_INSTRUCTIONS = """\
You are a focused DeepSeek subagent.

Complete the bounded task assigned by the parent agent, use available tools when needed, and return a concise evidence-based result.
You are text-only. Do not claim to inspect images, videos, screenshots, or other visual inputs. If visual evidence is required and the parent did not provide a textual description, report that limitation clearly.
"""


@dataclass(frozen=True)
class AgentRoleDefinition:
    name: str
    description: str
    model: str
    provider_id: str
    reasoning_effort: str
    developer_instructions: str

    def summary(self) -> dict[str, str]:
        return {
            "agent_role": self.name,
            "model": self.model,
            "model_provider": self.provider_id,
            "reasoning_effort": self.reasoning_effort,
        }

    def with_overrides(self, **changes: Any) -> "AgentRoleDefinition":
        return replace(self, **changes)


def make_role(provider: ProviderDefinition, name: str = DEFAULT_NAME) -> AgentRoleDefinition:
    return AgentRoleDefinition(
        name=name,
        description=(
            f"Text-only {name} subagent for coding, repository research, review, and verification. "
            "Do not use it for image, video, screenshot, or other visual inspection; "
            "the parent agent must inspect visual inputs and pass the findings as text."
        ),
        model=provider.model,
        provider_id=provider.platform_id(),
        reasoning_effort=DEFAULT_REASONING_EFFORT,
        developer_instructions=DEVELOPER_INSTRUCTIONS,
    )
