from decimal import Decimal

import pytest
from pydantic import ValidationError

from refair.models import ApiBudgetLimits, HttpBudgetLimits, RunBudget
from refair.policy import BudgetExhaustedError, BudgetTracker


def limits() -> RunBudget:
    return RunBudget(
        api=ApiBudgetLimits(
            max_cost_usd=Decimal("2.00"),
            max_model_calls=10,
            max_input_tokens=100,
            max_output_tokens=20,
            warning_thresholds=(Decimal("0.50"), Decimal("0.80"), Decimal("0.95")),
        ),
        http=HttpBudgetLimits(
            max_requests=10,
            max_mutations=3,
            max_per_endpoint=2,
            max_identical_replays=2,
        ),
    )


def test_api_budget_accounts_each_dimension_and_warns_at_threshold() -> None:
    tracker = BudgetTracker(limits())

    snapshot = tracker.consume_api(
        cost_usd="1.00", model_calls=2, input_tokens=40, output_tokens=4
    )

    assert snapshot.usage.api.cost_usd == Decimal("1.00")
    assert snapshot.usage.api.model_calls == 2
    assert snapshot.usage.api.input_tokens == 40
    assert snapshot.usage.api.output_tokens == 4
    assert snapshot.warning_thresholds_reached == (Decimal("0.50"),)
    assert not snapshot.exhausted


def test_reaching_one_hard_limit_stops_all_further_consumption() -> None:
    tracker = BudgetTracker(limits())

    snapshot = tracker.consume_api(model_calls=10)

    assert snapshot.exhausted
    assert "api.model_calls" in snapshot.exhausted_reasons
    with pytest.raises(BudgetExhaustedError, match="already exhausted"):
        tracker.consume_http(endpoint_key="GET /health")


def test_operation_that_would_exceed_budget_is_rejected_atomically() -> None:
    tracker = BudgetTracker(limits())

    with pytest.raises(BudgetExhaustedError, match="api.output_tokens"):
        tracker.consume_api(model_calls=1, output_tokens=21)

    assert tracker.usage.api.model_calls == 0
    assert tracker.usage.api.output_tokens == 0


def test_http_budget_tracks_endpoint_mutation_and_replay_separately() -> None:
    tracker = BudgetTracker(limits())

    first = tracker.consume_http(
        endpoint_key="GET /api/users/{id}", replay_key="request-hash", is_mutation=True
    )
    second = tracker.consume_http(
        endpoint_key="GET /api/users/{id}", replay_key="request-hash"
    )

    assert second.usage.http.requests == 2
    assert second.usage.http.mutations == 1
    assert second.usage.http.per_endpoint["GET /api/users/{id}"] == 2
    assert second.usage.http.identical_replays["request-hash"] == 2
    assert not first.exhausted
    assert second.exhausted
    assert "http.per_endpoint" in second.exhausted_reasons
    assert "http.identical_replays" in second.exhausted_reasons


def test_limits_are_immutable_and_usage_is_separate() -> None:
    configured = limits()
    tracker = BudgetTracker(configured)
    tracker.consume_api(model_calls=1)

    assert configured.api.max_model_calls == 10
    assert tracker.usage.api.model_calls == 1
    with pytest.raises(ValidationError):
        configured.api.max_model_calls = 999  # type: ignore[misc]


def test_warning_thresholds_must_be_ordered_unique_and_below_one() -> None:
    common = dict(
        max_cost_usd=Decimal("1"),
        max_model_calls=1,
        max_input_tokens=1,
        max_output_tokens=1,
    )
    with pytest.raises(ValidationError):
        ApiBudgetLimits(**common, warning_thresholds=(Decimal("0.8"), Decimal("0.5")))
    with pytest.raises(ValidationError):
        ApiBudgetLimits(**common, warning_thresholds=(Decimal("1.0"),))
