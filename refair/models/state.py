"""Mutable run state, kept apart from immutable budget configuration."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from refair.models.budget import RunUsage


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    STOPPED = "STOPPED"


class RunState(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: RunStatus = RunStatus.RUNNING
    usage: RunUsage = Field(default_factory=RunUsage)
