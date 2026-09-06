"""Immutable budget configuration and separate mutable usage models."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiBudgetLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_cost_usd: Decimal = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    warning_thresholds: tuple[Decimal, ...] = (
        Decimal("0.50"),
        Decimal("0.80"),
        Decimal("0.95"),
    )

    @field_validator("warning_thresholds")
    @classmethod
    def valid_warning_thresholds(cls, value: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if any(threshold <= 0 or threshold >= 1 for threshold in value):
            raise ValueError("warning thresholds must be strictly between 0 and 1")
        if tuple(sorted(set(value))) != value:
            raise ValueError("warning thresholds must be unique and ascending")
        return value


class HttpBudgetLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_requests: int = Field(gt=0)
    max_mutations: int = Field(gt=0)
    max_per_endpoint: int = Field(gt=0)
    max_identical_replays: int = Field(gt=0)


class RunBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    api: ApiBudgetLimits
    http: HttpBudgetLimits


class ApiUsage(BaseModel):
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    model_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class HttpUsage(BaseModel):
    requests: int = Field(default=0, ge=0)
    mutations: int = Field(default=0, ge=0)
    per_endpoint: dict[str, int] = Field(default_factory=dict)
    identical_replays: dict[str, int] = Field(default_factory=dict)


class RunUsage(BaseModel):
    api: ApiUsage = Field(default_factory=ApiUsage)
    http: HttpUsage = Field(default_factory=HttpUsage)
