from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from refair.analytics import (
    derive_json_duplicate_key_leads,
    derive_multipart_disposition_ambiguity_leads,
    derive_unobserved_advertised_method_leads,
)
from refair.models import (
    ExactEndpoint,
    ExactEndpointGrounding,
    HttpOperation,
    HttpOperationGrounding,
    JsonArrayItem,
    JsonDirection,
    JsonFieldObservation,
    JsonType,
    MethodAdvertisement,
    MethodAdvertisementSource,
    MultipartDirection,
    MultipartPartName,
    MultipartPartObservation,
    ObservationGrounding,
)

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
ENDPOINT_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_ENDPOINT_ID = UUID("33333333-3333-4333-8333-333333333333")
OPERATION_ID = UUID("44444444-4444-4444-8444-444444444444")
OBSERVATION_A = UUID("55555555-5555-4555-8555-555555555555")
OBSERVATION_B = UUID("66666666-6666-4666-8666-666666666666")


def endpoint(*, project_id=PROJECT_ID) -> ExactEndpoint:
    return ExactEndpoint(
        id=ENDPOINT_ID,
        project_id=project_id,
        scheme="https",
        host="api.test",
        path="/report",
    )


def operation(*, method="GET", endpoint_id=ENDPOINT_ID, id=OPERATION_ID):
    return HttpOperation(id=id, endpoint_id=endpoint_id, method=method)


def advertisement(
    method,
    *,
    source=MethodAdvertisementSource.ALLOW_HEADER,
    observation_id=OBSERVATION_A,
    endpoint_id=ENDPOINT_ID,
):
    return MethodAdvertisement(
        endpoint_id=endpoint_id,
        source=source,
        advertised_method=method,
        observation_id=observation_id,
    )


def method_leads(*, operations=(), advertisements=()):
    return derive_unobserved_advertised_method_leads(
        project_id=PROJECT_ID,
        endpoint=endpoint(),
        operations=operations,
        advertisements=advertisements,
    )


def test_advertised_unobserved_method_produces_one_careful_lead() -> None:
    leads = method_leads(
        operations=(operation(),),
        advertisements=(advertisement("GET"), advertisement("POST")),
    )
    assert len(leads) == 1
    assert '"POST"' in leads[0].question
    assert "not confirm support or authorization" in leads[0].rationale
    assert leads[0].grounding == (
        ExactEndpointGrounding(endpoint_id=ENDPOINT_ID),
        ObservationGrounding(observation_id=OBSERVATION_A),
    )


def test_observed_advertised_method_and_no_advertisements_produce_no_leads() -> None:
    assert method_leads(
        operations=(operation(),), advertisements=(advertisement("GET"),)
    ) == ()
    assert method_leads(operations=(operation(),), advertisements=()) == ()


def test_method_advertisements_group_with_one_deterministic_witness_per_source() -> None:
    allow_later = advertisement("POST", observation_id=OBSERVATION_B)
    allow_earlier = advertisement("POST", observation_id=OBSERVATION_A)
    cors = advertisement(
        "POST",
        source=MethodAdvertisementSource.CORS_ALLOW_METHODS,
        observation_id=OBSERVATION_B,
    )
    leads = method_leads(
        advertisements=(allow_later, cors, allow_earlier, allow_earlier)
    )
    assert leads == method_leads(
        advertisements=(allow_earlier, cors, allow_later, allow_earlier)
    )
    assert len(leads) == 1
    assert leads[0].grounding == (
        ExactEndpointGrounding(endpoint_id=ENDPOINT_ID),
        ObservationGrounding(observation_id=OBSERVATION_A),
        ObservationGrounding(observation_id=OBSERVATION_B),
    )
    assert "ALLOW_HEADER, CORS_ALLOW_METHODS" in leads[0].rationale


def test_same_method_witness_for_both_sources_is_deduplicated() -> None:
    leads = method_leads(
        advertisements=(
            advertisement("POST"),
            advertisement("POST", source=MethodAdvertisementSource.CORS_ALLOW_METHODS),
        )
    )
    assert leads[0].grounding.count(
        ObservationGrounding(observation_id=OBSERVATION_A)
    ) == 1


def test_method_output_is_exact_token_sorted_order_invariant_and_repeatable() -> None:
    facts = (advertisement("POST"), advertisement("get"), advertisement("DELETE"))
    first = method_leads(operations=(operation(),), advertisements=facts)
    second = method_leads(operations=(operation(),), advertisements=reversed(facts))
    assert first == second == method_leads(
        operations=(operation(),), advertisements=facts
    )
    assert ['"DELETE"', '"POST"', '"get"'] == [
        lead.question.split("advertised method ", 1)[1].split(" supported", 1)[0]
        for lead in first
    ]
    assert len({lead.id for lead in first}) == 3


def test_method_exact_case_semantics_are_preserved() -> None:
    leads = method_leads(
        operations=(operation(method="GET"),),
        advertisements=(advertisement("get"),),
    )
    assert len(leads) == 1


def test_duplicate_identical_operations_are_tolerated() -> None:
    observed = operation()
    assert method_leads(
        operations=(observed, observed), advertisements=(advertisement("GET"),)
    ) == ()


def test_conflicting_operation_duplicates_fail_fast() -> None:
    with pytest.raises(ValueError, match="conflicting operation"):
        method_leads(
            operations=(operation(), operation(method="POST")),
            advertisements=(),
        )


def test_method_rule_rejects_wrong_project_endpoint() -> None:
    with pytest.raises(ValueError, match="project"):
        derive_unobserved_advertised_method_leads(
            project_id=PROJECT_ID,
            endpoint=endpoint(
                project_id=UUID("99999999-9999-4999-8999-999999999999")
            ),
            operations=(),
            advertisements=(),
        )


def test_method_rule_rejects_wrong_endpoint_facts() -> None:
    with pytest.raises(ValueError, match="operation"):
        method_leads(operations=(operation(endpoint_id=OTHER_ENDPOINT_ID),))
    with pytest.raises(ValueError, match="advertisement"):
        method_leads(advertisements=(advertisement("POST", endpoint_id=OTHER_ENDPOINT_ID),))


def json_field(
    *,
    observation_id=OBSERVATION_A,
    direction=JsonDirection.REQUEST,
    path=("user", "id"),
    json_type=JsonType.STRING,
    duplicate=True,
):
    return JsonFieldObservation(
        observation_id=observation_id,
        direction=direction,
        path=path,
        json_type=json_type,
        duplicate_key_observed=duplicate,
    )


def json_leads(fields):
    return derive_json_duplicate_key_leads(
        project_id=PROJECT_ID,
        operation=operation(),
        fields=fields,
    )


def test_nonduplicate_json_field_produces_no_lead() -> None:
    assert json_leads((json_field(duplicate=False),)) == ()


def test_duplicate_json_key_lead_has_exact_grounding() -> None:
    lead = json_leads((json_field(),))[0]
    assert lead.grounding == (
        ObservationGrounding(observation_id=OBSERVATION_A),
        HttpOperationGrounding(operation_id=OPERATION_ID),
    )
    assert "not confirmation" in lead.rationale


def test_duplicate_json_facts_group_per_observation_direction_and_path() -> None:
    string = json_field(json_type=JsonType.STRING)
    number = json_field(json_type=JsonType.NUMBER)
    leads = json_leads((number, string, string))
    assert len(leads) == 1
    assert "NUMBER, STRING" in leads[0].rationale


def test_conflicting_duplicate_json_facts_fail_fast() -> None:
    with pytest.raises(ValueError, match="conflicting JSON"):
        json_leads((json_field(), json_field(duplicate=False)))


def test_json_distinct_paths_directions_and_observations_have_distinct_leads() -> None:
    fields = (
        json_field(path=("a",)),
        json_field(path=("b",)),
        json_field(path=("a",), direction=JsonDirection.RESPONSE),
        json_field(path=("a",), observation_id=OBSERVATION_B),
    )
    leads = json_leads(fields)
    assert len(leads) == 4
    assert len({lead.id for lead in leads}) == 4


def test_json_path_identity_is_structured_and_lossless() -> None:
    fields = (
        json_field(path=("a.b",)),
        json_field(path=("a", "b")),
        json_field(path=("a/b[0]",)),
        json_field(path=("users", JsonArrayItem.ITEM, "id")),
    )
    leads = json_leads(fields)
    assert len(leads) == 4
    assert len({lead.id for lead in leads}) == 4
    questions = {lead.question for lead in leads}
    assert '$["a.b"]' in " ".join(questions)
    assert '$["a"]["b"]' in " ".join(questions)
    assert '$["users"][*]["id"]' in " ".join(questions)


def test_json_output_is_order_invariant_deduplicated_and_repeatable() -> None:
    fields = (json_field(path=("z",)), json_field(path=("a",)))
    first = json_leads(fields)
    assert first == json_leads(reversed(fields)) == json_leads(fields)
    assert json_leads((fields[0], fields[0])) == json_leads((fields[0],))


def multipart_part(
    *,
    observation_id=OBSERVATION_A,
    direction=MultipartDirection.REQUEST,
    part_index=0,
    name_count=1,
    filename_count=0,
    name=b"tag",
):
    names = (
        (MultipartPartName(name=name, occurrence_count=name_count),)
        if name_count
        else ()
    )
    return MultipartPartObservation(
        observation_id=observation_id,
        direction=direction,
        part_index=part_index,
        disposition_type="form-data",
        name_parameter_count=name_count,
        filename_parameter_count=filename_count,
        filename_empty_count=0,
        filename_nonempty_count=filename_count,
        names=names,
    )


def multipart_leads(parts):
    return derive_multipart_disposition_ambiguity_leads(
        project_id=PROJECT_ID,
        operation=operation(method="POST"),
        parts=parts,
    )


@pytest.mark.parametrize(
    "part",
    [multipart_part(), multipart_part(name_count=0, filename_count=1)],
)
def test_normal_single_multipart_parameters_do_not_trigger(part) -> None:
    assert multipart_leads((part,)) == ()


@pytest.mark.parametrize(
    ("name_count", "filename_count"),
    [(2, 0), (0, 2), (2, 2)],
)
def test_multipart_duplicate_parameters_produce_one_lead_per_part(
    name_count, filename_count
) -> None:
    leads = multipart_leads(
        (multipart_part(name_count=name_count, filename_count=filename_count),)
    )
    assert len(leads) == 1
    assert f"name_parameter_count={name_count}" in leads[0].rationale
    assert f"filename_parameter_count={filename_count}" in leads[0].rationale


def test_repeated_names_across_separate_normal_parts_do_not_trigger() -> None:
    assert multipart_leads(
        (
            multipart_part(part_index=2, name=b"tag"),
            multipart_part(part_index=3, name=b"tag"),
        )
    ) == ()


def test_separate_ambiguous_parts_and_directions_get_distinct_grounded_leads() -> None:
    parts = (
        multipart_part(part_index=0, name_count=2),
        multipart_part(part_index=1, filename_count=2),
        multipart_part(
            part_index=0,
            direction=MultipartDirection.RESPONSE,
            name_count=2,
        ),
    )
    leads = multipart_leads(parts)
    assert len(leads) == 3
    assert len({lead.id for lead in leads}) == 3
    assert all(
        lead.grounding
        == (
            ObservationGrounding(observation_id=OBSERVATION_A),
            HttpOperationGrounding(operation_id=OPERATION_ID),
        )
        for lead in leads
    )


def test_multipart_output_is_order_invariant_deduplicated_and_repeatable() -> None:
    parts = (
        multipart_part(part_index=2, name_count=2),
        multipart_part(part_index=1, filename_count=2),
    )
    first = multipart_leads(parts)
    assert first == multipart_leads(reversed(parts)) == multipart_leads(parts)
    assert multipart_leads((parts[0], parts[0])) == multipart_leads((parts[0],))


def test_conflicting_multipart_part_facts_fail_fast() -> None:
    with pytest.raises(ValueError, match="conflicting multipart"):
        multipart_leads(
            (multipart_part(name_count=2), multipart_part(filename_count=2))
        )


def test_all_generators_return_tuples_with_frozen_c1_leads() -> None:
    outputs = (
        method_leads(advertisements=(advertisement("POST"),)),
        json_leads((json_field(),)),
        multipart_leads((multipart_part(name_count=2),)),
    )
    assert all(isinstance(output, tuple) for output in outputs)
    assert all(
        any(isinstance(item, ObservationGrounding) for item in lead.grounding)
        for output in outputs
        for lead in output
    )
    for output in outputs:
        with pytest.raises(ValidationError):
            output[0].question = "changed"


def test_rule_namespaces_and_versioned_identity_keys_are_fixed() -> None:
    assert method_leads(advertisements=(advertisement("POST"),))[0].id == UUID(
        "b1ce67eb-a78c-5df9-be7c-edfef8b592a7"
    )
    assert json_leads((json_field(),))[0].id == UUID(
        "1e02d027-575b-520a-b03a-76917bcbefa2"
    )
    assert multipart_leads((multipart_part(name_count=2),))[0].id == UUID(
        "289d1c7a-4dc8-593e-9ebc-2f2917c9d9fd"
    )
