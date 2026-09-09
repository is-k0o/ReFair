from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import refair.models as models

from refair.models import (
    BodyKind,
    MethodAdvertisementSource,
    Observation,
    ObservationProvenance,
)
from refair.normalization import normalize_observation
from refair.storage import SQLiteRepository
from refair.structure import STRUCTURAL_VERSION, extract_structure

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")


def test_public_models_expose_only_b1_endpoint_abstraction() -> None:
    assert not hasattr(models, "Endpoint")
    assert hasattr(models, "ExactEndpoint")
    assert hasattr(models, "HttpOperation")


def make_observation(
    *,
    method: str,
    url: str,
    actor_id: str = "actor_a",
    request_content_type: str | None = None,
    request_body: bytes = b"",
    response_status: int = 200,
    response_content_type: str | None = None,
    response_body: bytes = b"",
    response_headers: tuple[tuple[str, str], ...] = (),
) -> Observation:
    request_headers = (
        [("Content-Type", request_content_type)] if request_content_type else []
    )
    request_head = [f"{method} / HTTP/1.1", *[f"{k}: {v}" for k, v in request_headers]]
    response_head = [
        f"HTTP/1.1 {response_status}",
        *(
            [(f"Content-Type: {response_content_type}")]
            if response_content_type
            else []
        ),
        *[f"{key}: {value}" for key, value in response_headers],
    ]
    return Observation(
        project_id=PROJECT_ID,
        provenance=ObservationProvenance.BROWSER,
        actor_id=actor_id,
        method=method,
        url=url,
        response_status=response_status,
        raw_request="\r\n".join(request_head).encode("ascii") + b"\r\n\r\n" + request_body,
        raw_response=(
            "\r\n".join(response_head).encode("ascii")
            + b"\r\n\r\n"
            + response_body
        ),
    )


def persist_structure(repository: SQLiteRepository, observation: Observation):
    repository.add_observation(observation)
    normalized = repository.add_normalized_exchange(
        normalize_observation(observation)
    )
    extraction = extract_structure(observation, normalized)
    assert repository.add_structural_extraction(extraction)
    return extraction


def test_exact_endpoint_and_method_identity_are_conservative(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "structure.sqlite3")
    repository.initialize()
    observations = (
        make_observation(method="GET", url="https://api.test/users?id=1"),
        make_observation(method="GET", url="https://api.test/users?id=2"),
        make_observation(method="POST", url="https://api.test/users"),
        make_observation(method="get", url="https://api.test/users"),
        make_observation(method="GET", url="https://api.test/users/123"),
        make_observation(method="GET", url="https://api.test/users/456"),
    )
    extractions = tuple(
        persist_structure(repository, observation) for observation in observations
    )

    assert len({item.endpoint.id for item in extractions[:4]}) == 1
    assert extractions[4].endpoint.id != extractions[5].endpoint.id
    assert repository.count_exact_endpoints() == 3
    assert repository.count_http_operations() == 5
    assert repository.count_operation_observations() == len(observations)

    users_endpoint = extractions[0].endpoint
    methods = {
        operation.method
        for operation in repository.list_http_operations(
            endpoint_id=users_endpoint.id
        )
    }
    assert methods == {"GET", "POST", "get"}
    assert not hasattr(users_endpoint, "route_template")


def test_nullable_port_identity_does_not_duplicate_endpoint(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "nullable.sqlite3")
    repository.initialize()
    first = make_observation(method="GET", url="https://api.test:0/items?id=1")
    second = make_observation(method="GET", url="https://api.test:0/items?id=2")

    first_extraction = persist_structure(repository, first)
    second_extraction = persist_structure(repository, second)

    assert first_extraction.endpoint.port is None
    assert first_extraction.endpoint.id == second_extraction.endpoint.id
    assert repository.count_exact_endpoints() == 1
    assert repository.count_http_operations() == 1


def test_query_shapes_are_joined_without_values_and_distinguish_empty_query(
    tmp_path,
) -> None:
    repository = SQLiteRepository(tmp_path / "queries.sqlite3")
    repository.initialize()
    urls = (
        "https://api.test/users?id=1&id=2&type=x",
        "https://api.test/users?id=10&id=20&type=y",
        "https://api.test/users",
        "https://api.test/users?",
        "https://api.test/users?id=1&type=x&id=2",
    )
    extractions = tuple(
        persist_structure(repository, make_observation(method="GET", url=url))
        for url in urls
    )

    operation_id = extractions[0].operation.id
    assert {item.operation.id for item in extractions} == {operation_id}
    shapes = repository.operation_query_shapes(operation_id)
    observed = {
        (item.query_present, item.query_parameter_names): item.observation_count
        for item in shapes
    }
    assert observed == {
        (False, ()): 1,
        (True, ()): 1,
        (True, ("id", "id", "type")): 2,
        (True, ("id", "type", "id")): 1,
    }
    serialized = "".join(item.model_dump_json() for item in shapes)
    assert all(value not in serialized for value in ("10", "20", '"x"', '"y"'))


def test_representation_and_actor_views_do_not_split_operation(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "representations.sqlite3")
    repository.initialize()
    form = make_observation(
        method="POST",
        url="https://api.test/login",
        actor_id="actor_a",
        request_content_type="application/x-www-form-urlencoded",
        request_body=b"username=secret",
        response_status=200,
        response_content_type="application/json",
        response_body=b'{"token":"secret"}',
    )
    json_request = make_observation(
        method="POST",
        url="https://api.test/login",
        actor_id="actor_b",
        request_content_type="application/json",
        request_body=b'{"username":"secret"}',
        response_status=403,
        response_content_type="text/html",
        response_body=b"<p>forbidden</p>",
    )
    first = persist_structure(repository, form)
    second = persist_structure(repository, json_request)

    assert first.endpoint.id == second.endpoint.id
    assert first.operation.id == second.operation.id
    operation_id = first.operation.id
    requests = repository.operation_request_representations(operation_id)
    assert {(item.content_type, item.body_kind) for item in requests} == {
        ("application/json", BodyKind.JSON),
        ("application/x-www-form-urlencoded", BodyKind.FORM),
    }
    responses = repository.operation_response_representations(operation_id)
    assert {
        (item.response_status, item.content_type, item.body_kind)
        for item in responses
    } == {
        (200, "application/json", BodyKind.JSON),
        (403, "text/html", BodyKind.TEXT),
    }
    outcomes = repository.operation_actor_outcomes(operation_id)
    assert {
        (item.actor_id, item.provenance, item.response_status, item.observation_count)
        for item in outcomes
    } == {
        ("actor_a", ObservationProvenance.BROWSER, 200, 1),
        ("actor_b", ObservationProvenance.BROWSER, 403, 1),
    }
    serialized = "".join(item.model_dump_json() for item in requests + responses)
    assert "secret" not in serialized


def test_method_advertisements_are_evidence_not_operations(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "advertisements.sqlite3")
    repository.initialize()
    observation = make_observation(
        method="OPTIONS",
        url="https://api.test/resource",
        response_headers=(
            ("aLlOw", "GET, post, OPTIONS"),
            ("Access-Control-Allow-Methods", "GET, POST"),
            ("Accept", "application/secret"),
        ),
    )
    extraction = persist_structure(repository, observation)

    advertisements = repository.list_method_advertisements(
        endpoint_id=extraction.endpoint.id
    )
    assert {
        (item.source, item.advertised_method, item.observation_id)
        for item in advertisements
    } == {
        (MethodAdvertisementSource.ALLOW_HEADER, "GET", observation.id),
        (MethodAdvertisementSource.ALLOW_HEADER, "post", observation.id),
        (MethodAdvertisementSource.ALLOW_HEADER, "OPTIONS", observation.id),
        (MethodAdvertisementSource.CORS_ALLOW_METHODS, "GET", observation.id),
        (MethodAdvertisementSource.CORS_ALLOW_METHODS, "POST", observation.id),
    }
    assert repository.count_method_advertisements() == 5
    assert {
        item.method
        for item in repository.list_http_operations(
            endpoint_id=extraction.endpoint.id
        )
    } == {"OPTIONS"}


def test_structural_processing_is_idempotent_and_version_aware(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "versions.sqlite3")
    repository.initialize()
    observation = make_observation(
        method="OPTIONS",
        url="https://api.test/resource",
        response_headers=(("Allow", "GET, POST"),),
    )
    repository.add_observation(observation)
    normalized = repository.add_normalized_exchange(
        normalize_observation(observation)
    )
    extraction = extract_structure(observation, normalized)

    assert extraction.structural_version == STRUCTURAL_VERSION == 5
    assert repository.add_structural_extraction(extraction)
    assert not repository.add_structural_extraction(extraction)
    counts = (
        repository.count_exact_endpoints(),
        repository.count_http_operations(),
        repository.count_operation_observations(),
        repository.count_method_advertisements(),
    )
    assert counts == (1, 1, 1, 2)
    assert repository.count_pending_structural_observations(
        target_structural_version=5
    ) == 0
    assert repository.count_pending_structural_observations(
        target_structural_version=6
    ) == 1

    newer = replace(extraction, structural_version=6)
    assert repository.add_structural_extraction(newer)
    assert not repository.add_structural_extraction(extraction)
    assert repository.count_pending_structural_observations(
        target_structural_version=5
    ) == 0
    assert (
        repository.count_exact_endpoints(),
        repository.count_http_operations(),
        repository.count_operation_observations(),
        repository.count_method_advertisements(),
    ) == counts
