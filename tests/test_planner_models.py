from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from refair.models import (
    AssetGrounding,
    AssetReferencedUrlTarget,
    ExactEndpointTarget,
    ExistingOperationTarget,
    ExperimentProposal,
    ExplorationProposal,
    ObservationGrounding,
    PlannerDecision,
    PlannerTarget,
    WaitDecision,
)

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
OBSERVATION_ID = UUID("22222222-2222-4222-8222-222222222222")
ENDPOINT_ID = UUID("33333333-3333-4333-8333-333333333333")
OPERATION_ID = UUID("44444444-4444-4444-8444-444444444444")
HYPOTHESIS_ID = UUID("55555555-5555-4555-8555-555555555555")
LEAD_ID = UUID("66666666-6666-4666-8666-666666666666")
ASSET_HASH = "b" * 64
EXACT_URL = "https://example.test/A%2Fb?x=%2F"


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        ({"kind": "EXISTING_OPERATION", "operation_id": str(OPERATION_ID)}, ExistingOperationTarget),
        ({"kind": "EXACT_ENDPOINT", "endpoint_id": str(ENDPOINT_ID)}, ExactEndpointTarget),
        (
            {"kind": "ASSET_REFERENCED_URL", "asset_content_hash": ASSET_HASH, "url": EXACT_URL},
            AssetReferencedUrlTarget,
        ),
    ],
)
def test_planner_target_discriminates_all_variants(payload, expected_type) -> None:
    assert isinstance(TypeAdapter(PlannerTarget).validate_python(payload), expected_type)


def test_asset_referenced_url_target_preserves_exact_url() -> None:
    target = AssetReferencedUrlTarget(asset_content_hash=ASSET_HASH, url=EXACT_URL)
    assert target.model_dump()["url"] == EXACT_URL


@pytest.mark.parametrize("asset_hash", ["B" * 64, "z" * 64, "b" * 63])
def test_asset_referenced_url_target_rejects_malformed_hash(asset_hash) -> None:
    with pytest.raises(ValidationError):
        AssetReferencedUrlTarget(asset_content_hash=asset_hash, url=EXACT_URL)


def test_asset_referenced_url_target_rejects_empty_url() -> None:
    with pytest.raises(ValidationError):
        AssetReferencedUrlTarget(asset_content_hash=ASSET_HASH, url="")


def experiment_values() -> dict[str, object]:
    return {
        "project_id": PROJECT_ID,
        "hypothesis_id": HYPOTHESIS_ID,
        "actor_id": "actor_a",
        "baseline_observation_id": OBSERVATION_ID,
        "target": ExistingOperationTarget(operation_id=OPERATION_ID),
        "rationale": "Test the specific hypothesis against its known operation.",
        "intended_change": "Use the alternate authorized actor.",
        "expected_secure_outcome": "Access remains denied.",
        "expected_interesting_outcome": "The response exposes the protected resource.",
        "grounding": (AssetGrounding(content_hash=ASSET_HASH),),
    }


def test_valid_experiment_proposal_has_fixed_decision_type() -> None:
    proposal = ExperimentProposal(**experiment_values())
    assert proposal.decision_type == "EXPERIMENT"
    with pytest.raises(ValidationError):
        ExperimentProposal(**experiment_values(), decision_type="WAIT")


@pytest.mark.parametrize(
    "field",
    [
        "actor_id",
        "rationale",
        "intended_change",
        "expected_secure_outcome",
        "expected_interesting_outcome",
    ],
)
def test_experiment_proposal_rejects_empty_required_text(field) -> None:
    values = experiment_values()
    values[field] = ""
    with pytest.raises(ValidationError):
        ExperimentProposal(**values)


def test_experiment_proposal_requires_additional_grounding() -> None:
    values = experiment_values()
    values["grounding"] = ()
    with pytest.raises(ValidationError):
        ExperimentProposal(**values)


def test_experiment_proposal_target_must_be_existing_operation() -> None:
    values = experiment_values()
    values["target"] = {
        "kind": "ASSET_REFERENCED_URL",
        "asset_content_hash": ASSET_HASH,
        "url": EXACT_URL,
    }
    with pytest.raises(ValidationError):
        ExperimentProposal(**values)


def exploration_values(target) -> dict[str, object]:
    return {
        "project_id": PROJECT_ID,
        "lead_id": LEAD_ID,
        "target": target,
        "rationale": "Resolve the specific evidence-grounded uncertainty.",
    }


@pytest.mark.parametrize(
    "target",
    [
        ExistingOperationTarget(operation_id=OPERATION_ID),
        ExactEndpointTarget(endpoint_id=ENDPOINT_ID),
        AssetReferencedUrlTarget(asset_content_hash=ASSET_HASH, url=EXACT_URL),
    ],
)
def test_exploration_proposal_accepts_each_typed_target(target) -> None:
    proposal = ExplorationProposal(**exploration_values(target))
    assert proposal.decision_type == "EXPLORATION"
    assert proposal.requested_request_cap == 1
    assert proposal.requested_actor_id is None


@pytest.mark.parametrize(
    "updates",
    [
        {"requested_request_cap": 0},
        {"requested_actor_id": ""},
        {"rationale": ""},
        {"decision_type": "WAIT"},
    ],
)
def test_exploration_proposal_rejects_invalid_contract_values(updates) -> None:
    values = exploration_values(ExistingOperationTarget(operation_id=OPERATION_ID))
    values.update(updates)
    with pytest.raises(ValidationError):
        ExplorationProposal(**values)


def test_wait_decision_is_valid_without_missing_information() -> None:
    decision = WaitDecision(project_id=PROJECT_ID, rationale="No justified action is useful now.")
    assert decision.decision_type == "WAIT"
    assert decision.missing_information == ()


def test_wait_decision_preserves_missing_information_and_related_ids() -> None:
    decision = WaitDecision(
        project_id=PROJECT_ID,
        rationale="Wait for concrete evidence.",
        missing_information=("Authenticated observation", "Known alternate actor"),
        related_hypothesis_ids=(HYPOTHESIS_ID,),
        related_lead_ids=(LEAD_ID,),
    )
    assert decision.missing_information == ("Authenticated observation", "Known alternate actor")
    assert decision.related_hypothesis_ids == (HYPOTHESIS_ID,)
    assert decision.related_lead_ids == (LEAD_ID,)


@pytest.mark.parametrize(
    "updates",
    [
        {"rationale": ""},
        {"missing_information": ("valid", "")},
        {"decision_type": "EXPERIMENT"},
    ],
)
def test_wait_decision_rejects_invalid_contract_values(updates) -> None:
    values = {"project_id": PROJECT_ID, "rationale": "Wait."}
    values.update(updates)
    with pytest.raises(ValidationError):
        WaitDecision(**values)


@pytest.mark.parametrize(
    "model",
    [
        ExperimentProposal(**experiment_values()),
        ExplorationProposal(
            **exploration_values(ExistingOperationTarget(operation_id=OPERATION_ID))
        ),
        WaitDecision(project_id=PROJECT_ID, rationale="Wait."),
    ],
)
def test_planner_contracts_are_frozen(model) -> None:
    with pytest.raises(ValidationError):
        model.project_id = UUID("77777777-7777-4777-8777-777777777777")


def planner_variants():
    return (
        ExperimentProposal(**experiment_values()),
        ExplorationProposal(
            **exploration_values(
                AssetReferencedUrlTarget(asset_content_hash=ASSET_HASH, url=EXACT_URL)
            )
        ),
        WaitDecision(project_id=PROJECT_ID, rationale="Wait."),
    )


@pytest.mark.parametrize("expected", planner_variants())
def test_planner_decision_discriminates_and_roundtrips(expected) -> None:
    adapter = TypeAdapter(PlannerDecision)
    validated = adapter.validate_python(expected.model_dump(mode="json"))
    assert type(validated) is type(expected)
    assert validated == expected


def test_planner_decision_rejects_unknown_decision_type() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(PlannerDecision).validate_python(
            {"decision_type": "UNKNOWN", "project_id": str(PROJECT_ID)}
        )


def test_planner_decision_schema_has_all_discriminated_alternatives() -> None:
    schema = TypeAdapter(PlannerDecision).json_schema()
    assert schema["discriminator"]["propertyName"] == "decision_type"
    assert set(schema["discriminator"]["mapping"]) == {
        "EXPERIMENT",
        "EXPLORATION",
        "WAIT",
    }
    assert len(schema["oneOf"]) == 3
