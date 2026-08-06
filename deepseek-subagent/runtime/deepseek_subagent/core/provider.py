"""固定 OpenCode Go 配置所需的最小数据结构。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderAuthSpec:
    kind: str
    installable: bool
    env_var: str | None = None
    keychain_service: str | None = None
    keychain_account: str | None = None


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    name: str
    model: str
    base_url: str
    wire_api: str
    config_id: str

    def platform_id(self) -> str:
        return self.config_id

    def validate(self) -> list[str]:
        return [] if all((self.id, self.name, self.model, self.base_url, self.wire_api, self.config_id)) else ["provider.identity"]
