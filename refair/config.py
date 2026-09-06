"""Validated configuration loading. Loading has no runtime side effects."""

from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from refair.models.budget import ApiBudgetLimits, HttpBudgetLimits, RunBudget


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    name: str = Field(min_length=1)


class ActorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    firefox_profile: str = Field(min_length=1)
    listener: int = Field(ge=1, le=65535)


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_concurrent_active_requests: int = Field(default=1, ge=1, le=1)
    requests_per_second: float = Field(gt=0)
    burst: int = Field(gt=0)


class BridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bind_address: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    sqlite_path: Path = Path("refair.sqlite3")

    @model_validator(mode="after")
    def bind_address_is_loopback(self) -> BridgeConfig:
        try:
            address = ip_address(self.bind_address)
        except ValueError as error:
            raise ValueError("bridge bind_address must be a loopback IP address") from error
        if not address.is_loopback:
            raise ValueError("bridge bind_address must be a loopback IP address")
        return self


class ReFairConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: ProjectConfig
    actors: dict[str, ActorConfig] = Field(min_length=1)
    bridge: BridgeConfig
    execution: ExecutionConfig
    budgets: RunBudget

    @model_validator(mode="after")
    def listeners_and_profiles_are_isolated(self) -> ReFairConfig:
        ports = [actor.listener for actor in self.actors.values()]
        if len(set(ports)) != len(ports):
            raise ValueError("actor listener ports must be unique")
        profiles = [actor.firefox_profile for actor in self.actors.values()]
        if len(set(profiles)) != len(profiles):
            raise ValueError("actor Firefox profiles must be unique")
        expected_prefix = f"{self.project.name}-ai-"
        if any(not profile.startswith(expected_prefix) for profile in profiles):
            raise ValueError(
                f"actor Firefox profiles must start with {expected_prefix!r}"
            )
        return self

    def actor_id_for_listener(self, listener_port: int) -> str | None:
        """Resolve attribution from trusted configuration, never bridge input."""

        return next(
            (
                actor_id
                for actor_id, actor in self.actors.items()
                if actor.listener == listener_port
            ),
            None,
        )


def load_config(path: str | Path) -> ReFairConfig:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw: Any = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    bridge = raw.get("bridge")
    if isinstance(bridge, dict) and "sqlite_path" in bridge:
        sqlite_path = Path(bridge["sqlite_path"])
        if not sqlite_path.is_absolute():
            bridge["sqlite_path"] = config_path.parent / sqlite_path
    return ReFairConfig.model_validate(raw)
