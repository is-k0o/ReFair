from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from refair.context import (
    DEFAULT_CONTEXT_LIMITS,
    ContextLimits,
    OperationContextInput,
    compile_operation_context,
)
from refair.models import (
    ActorOutcome,
    BodyKind,
    ContextHypothesis,
    ContextJsonArrayItem,
    ContextJsonKey,
    ContextObservationRef,
    ContextSection,
    ExactEndpoint,
    ExactEndpointGrounding,
    ExplorationLead,
    FormDirection,
    FormParseStatus,
    HttpOperation,
    HttpOperationGrounding,
    HypothesisStatus,
    JsonArrayItem,
    JsonDirection,
    JsonParseStatus,
    JsonType,
    MethodAdvertisement,
    MethodAdvertisementSource,
    MultipartDirection,
    MultipartParseStatus,
    MultipartPartName,
    MultipartPartObservation,
    ObservationGrounding,
    ObservationProvenance,
    OperationFormDocumentOutcome,
    OperationFormField,
    OperationJsonDocumentOutcome,
    OperationJsonField,
    OperationMultipartDocumentOutcome,
    OperationQueryShape,
    RequestRepresentation,
    ResponseRepresentation,
)

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
ENDPOINT_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_ENDPOINT_ID = UUID("44444444-4444-4444-8444-444444444444")
OPERATION_ID = UUID("55555555-5555-4555-8555-555555555555")
OBSERVATION_A = UUID("66666666-6666-4666-8666-666666666666")
OBSERVATION_B = UUID("77777777-7777-4777-8777-777777777777")
LEAD_ID = UUID("88888888-8888-4888-8888-888888888888")
HYPOTHESIS_ID = UUID("99999999-9999-4999-8999-999999999999")


def make_endpoint(*, project_id=PROJECT_ID, host="api.test") -> ExactEndpoint:
    return ExactEndpoint(
        id=ENDPOINT_ID,
        project_id=project_id,
        scheme="https",
        host=host,
        path="/users",
    )


def make_operation(
    *, id=OPERATION_ID, endpoint_id=ENDPOINT_ID, method="GET"
) -> HttpOperation:
    return HttpOperation(id=id, endpoint_id=endpoint_id, method=method)


def make_source(**updates) -> OperationContextInput:
    values = {
        "project_id": PROJECT_ID,
        "endpoint": make_endpoint(),
        "operation": make_operation(),
    }
    values.update(updates)
    return OperationContextInput(**values)


def make_limits(**updates) -> ContextLimits:
    values = DEFAULT_CONTEXT_LIMITS.model_dump()
    values.update(updates)
    return ContextLimits(**values)


def coverage(snapshot, section: ContextSection):
    return next(item for item in snapshot.coverage if item.section is section)


def make_ref(
    observation_id,
    *,
    observed_at=datetime(2026, 9, 10, 12, tzinfo=timezone.utc),
    project_id=PROJECT_ID,
    actor_id="actor_a",
):
    return ContextObservationRef(
        observation_id=observation_id,
        project_id=project_id,
        observed_at=observed_at,
        actor_id=actor_id,
        provenance=ObservationProvenance.BROWSER,
        response_status=200,
    )


def make_lead(*, id=LEAD_ID, project_id=PROJECT_ID, grounding=None, rationale="R"):
    if grounding is None:
        grounding = (
            ObservationGrounding(observation_id=OBSERVATION_A),
            HttpOperationGrounding(operation_id=OPERATION_ID),
        )
    return ExplorationLead(
        id=id,
        project_id=project_id,
        question="What remains uncertain?",
        rationale=rationale,
        grounding=grounding,
    )


def make_hypothesis(
    *, id=HYPOTHESIS_ID, project_id=PROJECT_ID, statement="Access differs."
):
    return ContextHypothesis(
        id=id,
        project_id=project_id,
        statement=statement,
        status=HypothesisStatus.PROPOSED,
        supporting_witness_observation_ids=(OBSERVATION_A,),
    )


def make_form_field(name=b"field", *, count=1) -> OperationFormField:
    return OperationFormField(
        direction=FormDirection.REQUEST,
        field_name=name,
        observation_count=count,
        total_occurrence_count=count,
        total_assigned_occurrence_count=count,
        max_occurrence_count=count,
    )


def make_json_field(
    path=("users", JsonArrayItem.ITEM, "id"), *, count=1
) -> OperationJsonField:
    return OperationJsonField(
        direction=JsonDirection.REQUEST,
        path=path,
        json_type=JsonType.STRING,
        observation_count=count,
        duplicate_key_observed=False,
    )


def make_multipart_part(
    *, part_index=0, name=b"file", name_count=1
) -> MultipartPartObservation:
    return MultipartPartObservation(
        observation_id=OBSERVATION_A,
        direction=MultipartDirection.REQUEST,
        part_index=part_index,
        disposition_type="form-data",
        content_type="application/octet-stream",
        name_parameter_count=name_count,
        filename_parameter_count=1,
        filename_empty_count=0,
        filename_nonempty_count=1,
        names=(MultipartPartName(name=name, occurrence_count=name_count),),
    )


def test_valid_anchor_compiles_with_complete_empty_coverage() -> None:
    snapshot = compile_operation_context(make_source())
    assert snapshot.endpoint == make_endpoint()
    assert snapshot.operation == make_operation()
    assert tuple(item.section for item in snapshot.coverage) == tuple(ContextSection)
    assert all(
        (
            item.available_count,
            item.included_count,
            item.omitted_too_large_count,
            item.omitted_by_limit_count,
        )
        == (0, 0, 0, 0)
        for item in snapshot.coverage
    )


def test_anchor_consistency_is_enforced() -> None:
    with pytest.raises(ValueError, match="project"):
        compile_operation_context(
            make_source(endpoint=make_endpoint(project_id=OTHER_PROJECT_ID))
        )
    with pytest.raises(ValueError, match="anchor operation"):
        compile_operation_context(
            make_source(operation=make_operation(endpoint_id=OTHER_ENDPOINT_ID))
        )


def test_anchor_is_excluded_from_siblings_and_duplicates_collapse() -> None:
    post = make_operation(
        id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), method="POST"
    )
    source = make_source(
        sibling_operations=(make_operation(), post, post, make_operation())
    )
    snapshot = compile_operation_context(source)
    assert snapshot.sibling_operations == (post,)
    sibling_coverage = coverage(snapshot, ContextSection.SIBLING_OPERATIONS)
    assert (sibling_coverage.available_count, sibling_coverage.included_count) == (1, 1)


def test_sibling_wrong_endpoint_or_conflicting_id_fails() -> None:
    with pytest.raises(ValueError, match="sibling operation"):
        compile_operation_context(
            make_source(
                sibling_operations=(make_operation(endpoint_id=OTHER_ENDPOINT_ID),)
            )
        )
    conflicting = make_operation(method="POST")
    with pytest.raises(ValueError, match="conflicting anchor"):
        compile_operation_context(make_source(sibling_operations=(conflicting,)))


def test_oversized_anchor_endpoint_and_operation_fail_without_truncation() -> None:
    limits = make_limits(max_anchor_serialized_bytes=300)
    with pytest.raises(ValueError, match="endpoint"):
        compile_operation_context(
            make_source(endpoint=make_endpoint(host="x" * 1_000)), limits
        )
    with pytest.raises(ValueError, match="operation"):
        compile_operation_context(
            make_source(operation=make_operation(method="X" * 1_000)), limits
        )


def test_byte_projection_is_lossless_and_json_safe() -> None:
    form_names = (b"avatar", b"a\\b", b"\xff\n\x00")
    multipart_name = b"\xfe\\\r"
    snapshot = compile_operation_context(
        make_source(
            form_fields=tuple(make_form_field(name) for name in form_names),
            multipart_parts=(make_multipart_part(name=multipart_name),),
        )
    )
    projected = {item.field_name.value for item in snapshot.form_fields}
    assert projected == {"avatar", "a\\\\b", "\\xff\\x0a\\x00"}
    assert snapshot.multipart_parts[0].names[0].name.value == "\\xfe\\\\\\x0d"
    dumped = snapshot.model_dump(mode="json")
    serialized = json.dumps(dumped)
    assert "�" not in serialized
    assert not any(isinstance(value, bytes) for value in _walk_values(dumped))


def _walk_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)
    else:
        yield value


def test_typed_json_path_projection_is_ordered_and_collision_free() -> None:
    fields = (
        make_json_field(path=("ARRAY_ITEM",)),
        make_json_field(path=(JsonArrayItem.ITEM,)),
        make_json_field(path=("a.b",)),
        make_json_field(path=("a", "b")),
        make_json_field(),
    )
    snapshot = compile_operation_context(make_source(json_fields=fields))
    paths = [item.path for item in snapshot.json_fields]
    assert len(paths) == 5
    assert (ContextJsonKey(value="ARRAY_ITEM"),) in paths
    assert (ContextJsonArrayItem(),) in paths
    assert (ContextJsonKey(value="a.b"),) in paths
    assert (ContextJsonKey(value="a"), ContextJsonKey(value="b")) in paths
    assert (
        ContextJsonKey(value="users"),
        ContextJsonArrayItem(),
        ContextJsonKey(value="id"),
    ) in paths
    json.dumps(snapshot.model_dump(mode="json"))


def test_size_filter_then_collection_limit_has_exact_coverage() -> None:
    normal_a = OperationQueryShape(
        query_present=True,
        query_parameter_names=("a",),
        observation_count=1,
    )
    normal_b = OperationQueryShape(
        query_present=True,
        query_parameter_names=("b",),
        observation_count=1,
    )
    huge_name = "never-truncate-" + "x" * 1_000
    huge = OperationQueryShape(
        query_present=True,
        query_parameter_names=(huge_name,),
        observation_count=1,
    )
    snapshot = compile_operation_context(
        make_source(query_shapes=(huge, normal_b, normal_a)),
        make_limits(max_query_shapes=1, max_item_serialized_bytes=200),
    )
    item = coverage(snapshot, ContextSection.QUERY_SHAPES)
    assert (
        item.available_count,
        item.included_count,
        item.omitted_too_large_count,
        item.omitted_by_limit_count,
    ) == (3, 1, 1, 1)
    assert snapshot.query_shapes == (normal_a,)
    assert huge_name not in json.dumps(snapshot.model_dump(mode="json"))


def test_zero_section_limit_counts_every_eligible_item_as_omitted_by_limit() -> None:
    shapes = (
        OperationQueryShape(
            query_present=False, query_parameter_names=(), observation_count=1
        ),
        OperationQueryShape(
            query_present=True,
            query_parameter_names=("id",),
            observation_count=1,
        ),
    )
    snapshot = compile_operation_context(
        make_source(query_shapes=shapes), make_limits(max_query_shapes=0)
    )
    item = coverage(snapshot, ContextSection.QUERY_SHAPES)
    assert snapshot.query_shapes == ()
    assert (item.available_count, item.included_count, item.omitted_by_limit_count) == (
        2,
        0,
        2,
    )


def test_observation_refs_are_deduplicated_newest_first_and_bounded() -> None:
    base = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    older = make_ref(OBSERVATION_A, observed_at=base)
    newer = make_ref(OBSERVATION_B, observed_at=base + timedelta(seconds=1))
    snapshot = compile_operation_context(
        make_source(observation_refs=(older, newer, older)),
        make_limits(max_observation_refs=1),
    )
    assert snapshot.observation_refs == (newer,)
    item = coverage(snapshot, ContextSection.OBSERVATION_REFS)
    assert (item.available_count, item.included_count, item.omitted_by_limit_count) == (
        2,
        1,
        1,
    )


def test_observation_timestamp_ties_use_uuid_and_offsets_compare_as_instants() -> None:
    instant = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    first = make_ref(OBSERVATION_A, observed_at=instant)
    second = make_ref(
        OBSERVATION_B,
        observed_at=instant.astimezone(timezone(timedelta(hours=2))),
    )
    snapshot = compile_operation_context(
        make_source(observation_refs=(second, first))
    )
    assert snapshot.observation_refs == (first, second)


def test_observation_ref_conflict_and_wrong_project_fail() -> None:
    ref = make_ref(OBSERVATION_A)
    conflicting = make_ref(OBSERVATION_A, actor_id="actor_b")
    with pytest.raises(ValueError, match="conflicting observation"):
        compile_operation_context(
            make_source(observation_refs=(ref, conflicting))
        )
    with pytest.raises(ValueError, match="project"):
        compile_operation_context(
            make_source(
                observation_refs=(make_ref(OBSERVATION_A, project_id=OTHER_PROJECT_ID),)
            )
        )


def test_conflicting_and_identical_structural_aggregates() -> None:
    json_field = make_json_field()
    form_field = make_form_field()
    part = make_multipart_part()
    snapshot = compile_operation_context(
        make_source(
            json_fields=(json_field, json_field),
            form_fields=(form_field, form_field),
            multipart_parts=(part, part),
        )
    )
    assert len(snapshot.json_fields) == len(snapshot.form_fields) == 1
    assert len(snapshot.multipart_parts) == 1

    with pytest.raises(ValueError, match="conflicting JSON"):
        compile_operation_context(
            make_source(json_fields=(json_field, make_json_field(count=2)))
        )
    with pytest.raises(ValueError, match="conflicting FORM"):
        compile_operation_context(
            make_source(form_fields=(form_field, make_form_field(count=2)))
        )
    with pytest.raises(ValueError, match="conflicting multipart"):
        compile_operation_context(
            make_source(
                multipart_parts=(part, make_multipart_part(name=b"different"))
            )
        )


def test_lead_anchor_consistency_deduplication_conflicts_and_size_omission() -> None:
    operation_lead = make_lead()
    endpoint_lead = make_lead(
        id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        grounding=(
            ObservationGrounding(observation_id=OBSERVATION_A),
            ExactEndpointGrounding(endpoint_id=ENDPOINT_ID),
        ),
    )
    snapshot = compile_operation_context(
        make_source(exploration_leads=(endpoint_lead, operation_lead, operation_lead))
    )
    assert snapshot.exploration_leads == (operation_lead, endpoint_lead)

    with pytest.raises(ValueError, match="project"):
        compile_operation_context(
            make_source(exploration_leads=(make_lead(project_id=OTHER_PROJECT_ID),))
        )
    unrelated = make_lead(
        grounding=(
            ObservationGrounding(observation_id=OBSERVATION_A),
            HttpOperationGrounding(operation_id=OTHER_ENDPOINT_ID),
        )
    )
    with pytest.raises(ValueError, match="not grounded"):
        compile_operation_context(make_source(exploration_leads=(unrelated,)))
    with pytest.raises(ValueError, match="conflicting exploration"):
        compile_operation_context(
            make_source(
                exploration_leads=(operation_lead, make_lead(rationale="Different"))
            )
        )

    huge = make_lead(rationale="exact-long-rationale-" + "x" * 1_000)
    omitted = compile_operation_context(
        make_source(exploration_leads=(huge,)),
        make_limits(max_item_serialized_bytes=200),
    )
    item = coverage(omitted, ContextSection.EXPLORATION_LEADS)
    assert omitted.exploration_leads == ()
    assert (item.available_count, item.omitted_too_large_count) == (1, 1)


def test_hypothesis_project_deduplication_conflict_and_size_omission() -> None:
    hypothesis = make_hypothesis()
    snapshot = compile_operation_context(
        make_source(hypotheses=(hypothesis, hypothesis))
    )
    assert snapshot.hypotheses == (hypothesis,)
    with pytest.raises(ValueError, match="project"):
        compile_operation_context(
            make_source(hypotheses=(make_hypothesis(project_id=OTHER_PROJECT_ID),))
        )
    with pytest.raises(ValueError, match="conflicting context hypothesis"):
        compile_operation_context(
            make_source(
                hypotheses=(hypothesis, make_hypothesis(statement="Different"))
            )
        )
    long_statement = "never-truncated-" + "x" * 1_000
    omitted = compile_operation_context(
        make_source(hypotheses=(make_hypothesis(statement=long_statement),)),
        make_limits(max_item_serialized_bytes=200),
    )
    item = coverage(omitted, ContextSection.HYPOTHESES)
    assert omitted.hypotheses == ()
    assert item.omitted_too_large_count == 1
    assert long_statement not in json.dumps(omitted.model_dump(mode="json"))


def realistic_source(*, reverse=False) -> OperationContextInput:
    sibling_values = (
        make_operation(
            id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), method="POST"
        ),
        make_operation(
            id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), method="DELETE"
        ),
    )
    advertisements = (
        MethodAdvertisement(
            endpoint_id=ENDPOINT_ID,
            source=MethodAdvertisementSource.ALLOW_HEADER,
            advertised_method="POST",
            observation_id=OBSERVATION_A,
        ),
        MethodAdvertisement(
            endpoint_id=ENDPOINT_ID,
            source=MethodAdvertisementSource.CORS_ALLOW_METHODS,
            advertised_method="DELETE",
            observation_id=OBSERVATION_B,
        ),
    )
    refs = (make_ref(OBSERVATION_A), make_ref(OBSERVATION_B, actor_id="actor_b"))
    query_shapes = (
        OperationQueryShape(
            query_present=True,
            query_parameter_names=("id",),
            observation_count=2,
        ),
        OperationQueryShape(
            query_present=False,
            query_parameter_names=(),
            observation_count=1,
        ),
    )
    json_fields = (make_json_field(path=("z",)), make_json_field(path=("a",)))
    form_fields = (make_form_field(b"z"), make_form_field(b"a"))
    multipart_parts = (
        make_multipart_part(part_index=1, name=b"z"),
        make_multipart_part(part_index=0, name=b"a"),
    )

    def ordered(values):
        return tuple(reversed(values)) if reverse else values

    return make_source(
        sibling_operations=ordered(sibling_values),
        method_advertisements=ordered(advertisements),
        observation_refs=ordered(refs),
        query_shapes=ordered(query_shapes),
        request_representations=(
            RequestRepresentation(
                content_type="application/json",
                body_kind=BodyKind.JSON,
                observation_count=2,
            ),
        ),
        response_representations=(
            ResponseRepresentation(
                response_status=200,
                content_type="application/json",
                body_kind=BodyKind.JSON,
                observation_count=2,
            ),
        ),
        actor_outcomes=ordered(
            (
                ActorOutcome(
                    actor_id="actor_b",
                    provenance=ObservationProvenance.BROWSER,
                    response_status=403,
                    observation_count=1,
                ),
                ActorOutcome(
                    actor_id="actor_a",
                    provenance=ObservationProvenance.BROWSER,
                    response_status=200,
                    observation_count=1,
                ),
            )
        ),
        json_document_outcomes=(
            OperationJsonDocumentOutcome(
                direction=JsonDirection.REQUEST,
                parse_status=JsonParseStatus.PARSED,
                root_type=JsonType.OBJECT,
                observation_count=2,
            ),
        ),
        json_fields=ordered(json_fields),
        form_document_outcomes=(
            OperationFormDocumentOutcome(
                direction=FormDirection.REQUEST,
                parse_status=FormParseStatus.PARSED,
                observation_count=1,
            ),
        ),
        form_fields=ordered(form_fields),
        multipart_document_outcomes=(
            OperationMultipartDocumentOutcome(
                direction=MultipartDirection.REQUEST,
                parse_status=MultipartParseStatus.PARSED,
                observation_count=2,
            ),
        ),
        multipart_parts=ordered(multipart_parts),
        exploration_leads=(make_lead(),),
        hypotheses=(make_hypothesis(),),
    )


def test_realistic_whole_snapshot_is_permutation_invariant_and_serializable() -> None:
    first = compile_operation_context(realistic_source())
    second = compile_operation_context(realistic_source(reverse=True))
    assert first == second
    assert json.loads(json.dumps(first.model_dump(mode="json")))["snapshot_version"] == 1
    assert all(
        item.available_count
        == item.included_count
        + item.omitted_too_large_count
        + item.omitted_by_limit_count
        for item in first.coverage
    )
    assert all(
        item.included_count
        == len(getattr(first, item.section.value.lower()))
        for item in first.coverage
    )
    dumped = json.dumps(first.model_dump(mode="json"))
    assert "raw_request" not in dumped
    assert "raw_response" not in dumped
    with pytest.raises(ValidationError):
        first.project_id = OTHER_PROJECT_ID
