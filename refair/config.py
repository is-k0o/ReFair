"""Validated configuration loading. Loading has no runtime side effects."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from refair.models.budget import ApiBudgetLimits, HttpBudgetLimits, RunBudget


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)


class ListenerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    human: int = Field(ge=1, le=65535)


class ActorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    firefox_profile: str = Field(min_length=1)
    listener: int = Field(ge=1, le=65535)


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_concurrent_active_requests: int = Field(default=1, ge=1, le=1)
    requests_per_second: float = Field(gt=0)
    burst: int = Field(gt=0)


class ReFairConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: ProjectConfig
    listeners: ListenerConfig
    actors: dict[str, ActorConfig]
    execution: ExecutionConfig
    budgets: RunBudget

    @model_validator(mode="after")
    def listeners_and_profiles_are_isolated(self) -> ReFairConfig:
        ports = [self.listeners.human, *(actor.listener for actor in self.actors.values())]
        if len(set(ports)) != len(ports):
            raise ValueError("human and actor listener ports must be unique")
        profiles = [actor.firefox_profile for actor in self.actors.values()]
        if len(set(profiles)) != len(profiles):
            raise ValueError("actor Firefox profiles must be unique")
        expected_prefix = f"{self.project.name}-ai-"
        if any(not profile.startswith(expected_prefix) for profile in profiles):
            raise ValueError(
                f"actor Firefox profiles must start with {expected_prefix!r}"
            )
        return self


def load_config(path: str | Path) -> ReFairConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw: Any = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    return ReFairConfig.model_validate(raw)
