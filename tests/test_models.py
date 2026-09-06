from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from refair.config import ReFairConfig, load_config
from refair.models import (
    Actor,
    EntityClaim,
    Hypothesis,
    HypothesisStatus,
    Observation,
    ObservationProvenance,
    TenantKnowledge,
)


def test_actor_tenant_is_explicitly_unknown_by_default() -> None:
    actor = Actor(id="actor_a", firefox_profile="example-ai-a", listener=8082)

    assert actor.tenant_knowledge is TenantKnowledge.UNKNOWN
    assert actor.tenant_id is None


def test_actor_cannot_claim_tenant_without_known_status() -> None:
    with pytest.raises(ValidationError):
        Actor(
            id="actor_a",
            firefox_profile="example-ai-a",
            listener=8082,
            tenant_id="tenant:17",
        )


def test_invalid_epistemic_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Hypothesis(
            project_id=uuid4(),
            statement="Actor B can read Actor A's object",
            status="TRUE",
        )

    hypothesis = Hypothesis(
        project_id=uuid4(),
        statement="Access controls differ by actor",
        status=HypothesisStatus.INCONCLUSIVE,
    )
    assert hypothesis.status is HypothesisStatus.INCONCLUSIVE


def test_claim_requires_evidence_and_hypothesis_evidence_cannot_conflict() -> None:
    evidence_id = uuid4()
    with pytest.raises(ValidationError):
        EntityClaim(attribute="tenant", value="tenant:17", evidence_ids=())

    with pytest.raises(ValidationError):
        Hypothesis(
            project_id=uuid4(),
            statement="Conflicting interpretation",
            supporting_evidence_ids=(evidence_id,),
            contradicting_evidence_ids=(evidence_id,),
        )


def test_observation_model_is_immutable() -> None:
    observation = Observation(
        project_id=uuid4(),
        provenance=ObservationProvenance.BROWSER,
        method="GET",
        url="https://example.test/",
        raw_request=b"GET / HTTP/1.1\r\n\r\n",
    )

    with pytest.raises(ValidationError):
        observation.url = "https://example.test/changed"  # type: ignore[misc]


def test_example_configuration_loads_and_keeps_active_limit_at_one() -> None:
    config = load_config("config.example.yaml")

    assert config.execution.max_concurrent_active_requests == 1
    assert {actor.listener for actor in config.actors.values()} == {8082, 8083}
    assert config.project.id == UUID("11111111-1111-4111-8111-111111111111")
    assert not hasattr(config, "listeners")


def test_configuration_rejects_profile_outside_project_namespace() -> None:
    config = load_config("config.example.yaml")
    raw = config.model_dump()
    raw["actors"]["actor_a"]["firefox_profile"] = "other-ai-a"

    with pytest.raises(ValidationError, match="must start"):
        ReFairConfig.model_validate(raw)
