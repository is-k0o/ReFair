"""Deterministic budget accounting and hard-stop decisions."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from refair.models.budget import ApiUsage, HttpUsage, RunBudget, RunUsage


class BudgetExhaustedError(RuntimeError):
    """Raised when an operation is attempted after, or would cross, a hard limit."""


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    usage: RunUsage
    warning_thresholds_reached: tuple[Decimal, ...]
    exhausted: bool
    exhausted_reasons: tuple[str, ...]


class BudgetTracker:
    """Owns mutable counters while retaining immutable limit configuration."""

    def __init__(self, limits: RunBudget, usage: RunUsage | None = None) -> None:
        self.limits = limits
        self.usage = usage.model_copy(deep=True) if usage else RunUsage()

    def consume_api(
        self,
        *,
        cost_usd: Decimal | str | int = Decimal("0"),
        model_calls: int = 1,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> BudgetSnapshot:
        self._require_running_budget()
        increment_cost = Decimal(cost_usd)
        increments = (increment_cost, model_calls, input_tokens, output_tokens)
        if any(value < 0 for value in increments):
            raise ValueError("budget increments cannot be negative")

        candidate = self.usage.api.model_copy(
            update={
                "cost_usd": self.usage.api.cost_usd + increment_cost,
                "model_calls": self.usage.api.model_calls + model_calls,
                "input_tokens": self.usage.api.input_tokens + input_tokens,
                "output_tokens": self.usage.api.output_tokens + output_tokens,
            }
        )
        violations = self._api_over_limit(candidate)
        if violations:
            raise BudgetExhaustedError(", ".join(violations))
        self.usage.api = candidate
        return self.snapshot()

    def consume_http(
        self,
        *,
        endpoint_key: str,
        replay_key: str | None = None,
        is_mutation: bool = False,
    ) -> BudgetSnapshot:
        self._require_running_budget()
        if not endpoint_key:
            raise ValueError("endpoint_key is required")

        current = self.usage.http
        per_endpoint = dict(current.per_endpoint)
        per_endpoint[endpoint_key] = per_endpoint.get(endpoint_key, 0) + 1
        identical_replays = dict(current.identical_replays)
        if replay_key is not None:
            identical_replays[replay_key] = identical_replays.get(replay_key, 0) + 1

        candidate = HttpUsage(
            requests=current.requests + 1,
            mutations=current.mutations + int(is_mutation),
            per_endpoint=per_endpoint,
            identical_replays=identical_replays,
        )
        violations = self._http_over_limit(candidate)
        if violations:
            raise BudgetExhaustedError(", ".join(violations))
        self.usage.http = candidate
        return self.snapshot()

    def snapshot(self) -> BudgetSnapshot:
        exhausted_reasons = tuple(
            self._api_at_limit(self.usage.api) + self._http_at_limit(self.usage.http)
        )
        max_fraction = max(self._api_fractions())
        warnings = tuple(
            threshold
            for threshold in self.limits.api.warning_thresholds
            if max_fraction >= threshold
        )
        return BudgetSnapshot(
            usage=self.usage.model_copy(deep=True),
            warning_thresholds_reached=warnings,
            exhausted=bool(exhausted_reasons),
            exhausted_reasons=exhausted_reasons,
        )

    def _require_running_budget(self) -> None:
        snapshot = self.snapshot()
        if snapshot.exhausted:
            raise BudgetExhaustedError(
                "budget already exhausted: " + ", ".join(snapshot.exhausted_reasons)
            )

    def _api_over_limit(self, usage: ApiUsage) -> list[str]:
        limits = self.limits.api
        checks = {
            "api.cost_usd": usage.cost_usd > limits.max_cost_usd,
            "api.model_calls": usage.model_calls > limits.max_model_calls,
            "api.input_tokens": usage.input_tokens > limits.max_input_tokens,
            "api.output_tokens": usage.output_tokens > limits.max_output_tokens,
        }
        return [name for name, exceeded in checks.items() if exceeded]

    def _http_over_limit(self, usage: HttpUsage) -> list[str]:
        limits = self.limits.http
        violations: list[str] = []
        if usage.requests > limits.max_requests:
            violations.append("http.requests")
        if usage.mutations > limits.max_mutations:
            violations.append("http.mutations")
        if any(count > limits.max_per_endpoint for count in usage.per_endpoint.values()):
            violations.append("http.per_endpoint")
        if any(
            count > limits.max_identical_replays for count in usage.identical_replays.values()
        ):
            violations.append("http.identical_replays")
        return violations

    def _api_at_limit(self, usage: ApiUsage) -> list[str]:
        limits = self.limits.api
        checks = {
            "api.cost_usd": usage.cost_usd >= limits.max_cost_usd,
            "api.model_calls": usage.model_calls >= limits.max_model_calls,
            "api.input_tokens": usage.input_tokens >= limits.max_input_tokens,
            "api.output_tokens": usage.output_tokens >= limits.max_output_tokens,
        }
        return [name for name, reached in checks.items() if reached]

    def _http_at_limit(self, usage: HttpUsage) -> list[str]:
        limits = self.limits.http
        reached: list[str] = []
        if usage.requests >= limits.max_requests:
            reached.append("http.requests")
        if usage.mutations >= limits.max_mutations:
            reached.append("http.mutations")
        if any(count >= limits.max_per_endpoint for count in usage.per_endpoint.values()):
            reached.append("http.per_endpoint")
        if any(
            count >= limits.max_identical_replays for count in usage.identical_replays.values()
        ):
            reached.append("http.identical_replays")
        return reached

    def _api_fractions(self) -> list[Decimal]:
        usage, limits = self.usage.api, self.limits.api
        return [
            usage.cost_usd / limits.max_cost_usd,
            Decimal(usage.model_calls) / limits.max_model_calls,
            Decimal(usage.input_tokens) / limits.max_input_tokens,
            Decimal(usage.output_tokens) / limits.max_output_tokens,
        ]
