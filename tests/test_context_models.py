from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from refair.context import ContextLimits, OperationContextInput, compile_operation_context
from refair.models import (
    ContextByteString,
    ContextHypothesis,
    ContextObservationRef,
    ContextSection,
    ContextSectionCoverage,
    ExactEndpoint,
    HttpOperation,
    HypothesisStatus,
    ObservationProvenance,
)

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
ENDPOINT_ID = UUID("22222222-2222-4222-8222-222222222222")
OPERATION_ID = UUID("33333333-3333-4333-8333-333333333333")
OBSERVATION_ID = UUID("44444444-4444-4444-8444-444444444444")
HYPOTHESIS_ID = UUID("55555555-5555-4555-8555-555555555555")


def empty_source() -> OperationContextInput:
    endpoint = ExactEndpoint(
        id=ENDPOINT_ID,
        project_id=PROJECT_ID,
        scheme="https",
        host="api.test",
        path="/resource",
    )
    return OperationContextInput(
        project_id=PROJECT_ID,
        endpoint=endpoint,
        operation=HttpOperation(
            id=OPERATION_ID,
            endpoint_id=ENDPOINT_ID,
            method="GET",
        ),
    )


def test_snapshot_v1_metadata_is_fixed_and_explicit() -> None:
    snapshot = compile_operation_context(empty_source())
    assert snapshot.snapshot_version == 1
    assert snapshot.scope == "ANCHOR_OPERATION_SAME_ENDPOINT_V1"
    assert snapshot.raw_messages_included is False
    assert snapshot.credential_material_included is False
    assert snapshot.scalar_application_values_included is False

    payload = snapshot.model_dump()
    for field, invalid in (
        ("snapshot_version", 2),
        ("scope", "PROJECT_WIDE"),
        ("raw_messages_included", True),
        ("credential_material_included", True),
        ("scalar_application_values_included", True),
    ):
        changed = {**payload, field: invalid}
        with pytest.raises(ValidationError):
            type(snapshot).model_validate(changed)


def test_new_context_contracts_are_frozen_and_forbid_extra_fields() -> None:
    value = ContextByteString(value="avatar")
    with pytest.raises(ValidationError):
        value.value = "changed"
    with pytest.raises(ValidationError):
        ContextByteString(value="avatar", unexpected=True)
    with pytest.raises(ValidationError):
        OperationContextInput(**empty_source().model_dump(), raw_request=b"secret")
    with pytest.raises(ValidationError):
        ContextLimits(unknown_limit=1)


def test_observation_reference_is_metadata_only() -> None:
    ref = ContextObservationRef(
        observation_id=OBSERVATION_ID,
        project_id=PROJECT_ID,
        observed_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        actor_id="actor_a",
        provenance=ObservationProvenance.BROWSER,
        response_status=200,
    )
    assert set(type(ref).model_fields) == {
        "observation_id",
        "project_id",
        "observed_at",
        "actor_id",
        "provenance",
        "response_status",
    }
    with pytest.raises(ValidationError):
        ContextObservationRef(
            **ref.model_dump(),
            raw_response=b"secret",
        )
    with pytest.raises(ValidationError):
        ContextObservationRef(
            observation_id=OBSERVATION_ID,
            project_id=PROJECT_ID,
            observed_at=datetime(2026, 9, 10),
            provenance=ObservationProvenance.BROWSER,
        )


def test_context_hypothesis_requires_bounded_relation_witnesses() -> None:
    supporting = ContextHypothesis(
        id=HYPOTHESIS_ID,
        project_id=PROJECT_ID,
        statement="Authorization differs across actors.",
        status=HypothesisStatus.PROPOSED,
        supporting_witness_observation_ids=(OBSERVATION_ID,),
    )
    contradicting = ContextHypothesis(
        id=HYPOTHESIS_ID,
        project_id=PROJECT_ID,
        statement="Authorization differs across actors.",
        status=HypothesisStatus.PROPOSED,
        contradicting_witness_observation_ids=(OBSERVATION_ID,),
    )
    assert supporting.supporting_witness_observation_ids == (OBSERVATION_ID,)
    assert contradicting.contradicting_witness_observation_ids == (OBSERVATION_ID,)

    base = {
        "id": HYPOTHESIS_ID,
        "project_id": PROJECT_ID,
        "statement": "Statement",
        "status": HypothesisStatus.PROPOSED,
    }
    with pytest.raises(ValidationError, match="at least one witness"):
        ContextHypothesis(**base)
    with pytest.raises(ValidationError, match="both support and contradict"):
        ContextHypothesis(
            **base,
            supporting_witness_observation_ids=(OBSERVATION_ID,),
            contradicting_witness_observation_ids=(OBSERVATION_ID,),
        )


def test_section_coverage_enforces_exact_accounting() -> None:
    coverage = ContextSectionCoverage(
        section=ContextSection.JSON_FIELDS,
        available_count=10,
        included_count=4,
        omitted_too_large_count=1,
        omitted_by_limit_count=5,
    )
    assert coverage.available_count == 10
    with pytest.raises(ValidationError, match="sum"):
        ContextSectionCoverage(
            section=ContextSection.JSON_FIELDS,
            available_count=10,
            included_count=4,
            omitted_too_large_count=1,
            omitted_by_limit_count=4,
        )


def test_context_limits_defaults_and_bounds() -> None:
    limits = ContextLimits()
    assert limits.max_sibling_operations == 16
    assert limits.max_json_fields == 128
    assert limits.max_multipart_parts == 64
    assert limits.max_anchor_serialized_bytes == 16_384
    assert limits.max_item_serialized_bytes == 4_096
    with pytest.raises(ValidationError):
        ContextLimits(max_json_fields=-1)
    with pytest.raises(ValidationError):
        ContextLimits(max_item_serialized_bytes=0)
