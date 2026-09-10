from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from refair.models import (
    AssetGrounding,
    ExactEndpointGrounding,
    ExplorationLead,
    GroundingReference,
    HttpOperationGrounding,
    ObservationGrounding,
)

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
OBSERVATION_ID = UUID("22222222-2222-4222-8222-222222222222")
ENDPOINT_ID = UUID("33333333-3333-4333-8333-333333333333")
OPERATION_ID = UUID("44444444-4444-4444-8444-444444444444")
ASSET_HASH = "a" * 64


def test_grounding_models_accept_their_typed_identifiers() -> None:
    assert ObservationGrounding(observation_id=OBSERVATION_ID).observation_id == OBSERVATION_ID
    assert AssetGrounding(content_hash=ASSET_HASH).content_hash == ASSET_HASH
    assert ExactEndpointGrounding(endpoint_id=ENDPOINT_ID).endpoint_id == ENDPOINT_ID
    assert HttpOperationGrounding(operation_id=OPERATION_ID).operation_id == OPERATION_ID


@pytest.mark.parametrize("content_hash", ["A" * 64, "g" * 64, "a" * 63, "a" * 65])
def test_asset_grounding_rejects_non_lowercase_sha256(content_hash) -> None:
    with pytest.raises(ValidationError):
        AssetGrounding(content_hash=content_hash)


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        ({"kind": "OBSERVATION", "observation_id": str(OBSERVATION_ID)}, ObservationGrounding),
        ({"kind": "ASSET", "content_hash": ASSET_HASH}, AssetGrounding),
        ({"kind": "EXACT_ENDPOINT", "endpoint_id": str(ENDPOINT_ID)}, ExactEndpointGrounding),
        ({"kind": "HTTP_OPERATION", "operation_id": str(OPERATION_ID)}, HttpOperationGrounding),
    ],
)
def test_grounding_reference_discriminates_all_variants(payload, expected_type) -> None:
    assert isinstance(TypeAdapter(GroundingReference).validate_python(payload), expected_type)


def make_lead(*, grounding):
    return ExplorationLead(
        project_id=PROJECT_ID,
        question="What does this observed reference expose?",
        rationale="The reference has not been represented by an observed operation.",
        grounding=grounding,
    )


@pytest.mark.parametrize("field", ["question", "rationale"])
def test_exploration_lead_requires_nonempty_text(field) -> None:
    values = {
        "project_id": PROJECT_ID,
        "question": "Question",
        "rationale": "Rationale",
        "grounding": (ObservationGrounding(observation_id=OBSERVATION_ID),),
    }
    values[field] = ""
    with pytest.raises(ValidationError):
        ExplorationLead(**values)


def test_exploration_lead_requires_grounding() -> None:
    with pytest.raises(ValidationError):
        make_lead(grounding=())


@pytest.mark.parametrize(
    "grounding",
    [
        (
            AssetGrounding(content_hash=ASSET_HASH),
            HttpOperationGrounding(operation_id=OPERATION_ID),
        ),
        (
            ObservationGrounding(observation_id=OBSERVATION_ID),
            ExactEndpointGrounding(endpoint_id=ENDPOINT_ID),
        ),
    ],
)
def test_exploration_lead_accepts_evidence_anchor_plus_structural_fact(grounding) -> None:
    assert make_lead(grounding=grounding).grounding == grounding


@pytest.mark.parametrize(
    "grounding",
    [
        (ExactEndpointGrounding(endpoint_id=ENDPOINT_ID),),
        (HttpOperationGrounding(operation_id=OPERATION_ID),),
    ],
)
def test_exploration_lead_rejects_structural_only_grounding(grounding) -> None:
    with pytest.raises(ValidationError, match="observation or asset anchor"):
        make_lead(grounding=grounding)


def test_analytic_contracts_are_frozen() -> None:
    lead = make_lead(
        grounding=(ObservationGrounding(observation_id=OBSERVATION_ID),)
    )
    with pytest.raises(ValidationError):
        lead.question = "Changed"
