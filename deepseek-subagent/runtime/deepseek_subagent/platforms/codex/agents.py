"""Codex Agent 角色文件生成（CodexAgentInstaller）。

角色内容由 AgentRoleDefinition 驱动，不再硬编码 Provider id。
"""

from __future__ import annotations

from ...core.agent_role import AgentRoleDefinition


def expected_agent_text(role: AgentRoleDefinition) -> str:
    return f'''name = "{role.name}"
description = "{role.description}"
model = "{role.model}"
model_provider = "{role.provider_id}"
model_reasoning_effort = "{role.reasoning_effort}"
developer_instructions = """
{role.developer_instructions.strip()}
"""
'''
