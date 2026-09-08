from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest
from pydantic import ValidationError

from refair.models import BodyKind, Observation, ObservationProvenance
from refair.normalization import NORMALIZER_VERSION, normalize_observation
from refair.storage import SQLiteRepository


def observation_for(
    *,
    url: str = "https://example.test/",
    raw_request: bytes = b"GET / HTTP/1.1\r\n\r\n",
    raw_response: bytes | None = b"HTTP/1.1 200 OK\r\n\r\n",
) -> Observation:
    return Observation(
        project_id=uuid4(),
        provenance=ObservationProvenance.BROWSER,
        actor_id="actor_a",
        method="GET",
        url=url,
        response_status=200,
        raw_request=raw_request,
        raw_response=raw_response,
    )


@pytest.mark.parametrize(
    ("url", "scheme", "host", "port", "path"),
    [
        ("HTTP://Example.TEST", "http", "example.test", 80, "/"),
        ("HTTPS://Example.TEST", "https", "example.test", 443, "/"),
        (
            "HTTPS://Example.TEST:8443//A/../B/%2f",
            "https",
            "example.test",
            8443,
            "//A/../B/%2f",
        ),
    ],
)
def test_url_components_are_normalized_conservatively(
    url, scheme, host, port, path
) -> None:
    normalized = normalize_observation(observation_for(url=url))

    assert normalized.scheme == scheme
    assert normalized.host == host
    assert normalized.port == port
    assert normalized.path == path


def test_query_names_preserve_order_and_duplicates_without_values() -> None:
    normalized = normalize_observation(
        observation_for(
            url=(
                "https://example.test/items?"
                "id=value-one&%69d=value-two&a+b=value-three&"
                "a%20b=value-four&id=value-five"
            )
        )
    )

    assert normalized.query_parameter_names == (
        "id",
        "%69d",
        "a+b",
        "a%20b",
        "id",
    )
    serialized = normalized.model_dump_json()
    values = (
        "value-one",
        "value-two",
        "value-three",
        "value-four",
        "value-five",
    )
    for value in values:
        assert value not in serialized


def test_exact_raw_query_variants_have_different_hashes() -> None:
    first = normalize_observation(
        observation_for(url="https://example.test/items?id=1&type=x")
    )
    second = normalize_observation(
        observation_for(url="https://example.test/items?id=2&type=x")
    )

    assert first.query_parameter_names == second.query_parameter_names
    assert first.raw_query_sha256 != second.raw_query_sha256
    assert first.raw_query_sha256 == sha256(b"id=1&type=x").hexdigest()

    raw_name = normalize_observation(
        observation_for(url="https://example.test/items?id=1")
    )
    encoded_name = normalize_observation(
        observation_for(url="https://example.test/items?%69d=1")
    )
    assert raw_name.query_parameter_names == ("id",)
    assert encoded_name.query_parameter_names == ("%69d",)
    assert raw_name.raw_query_sha256 != encoded_name.raw_query_sha256

    empty_query = normalize_observation(
        observation_for(url="https://example.test/items?")
    )
    assert empty_query.query_parameter_names == ()
    assert empty_query.raw_query_sha256 == sha256(b"").hexdigest()


@pytest.mark.parametrize(
    ("content_type", "body", "expected_kind"),
    [
        ("application/json", b"{}", BodyKind.JSON),
        ("application/problem+json", b"{}", BodyKind.JSON),
        ("application/x-www-form-urlencoded", b"a=secret", BodyKind.FORM),
        ("multipart/form-data", b"--boundary", BodyKind.MULTIPART),
        ("text/plain", b"hello", BodyKind.TEXT),
        ("application/octet-stream", b"\x00\xff", BodyKind.OTHER),
        ("application/json", b"", BodyKind.EMPTY),
    ],
)
def test_request_body_kind_uses_content_type_and_empty_body(
    content_type, body, expected_kind
) -> None:
    raw_request = (
        b"POST / HTTP/2\r\ncOnTeNt-TyPe: "
        + content_type.upper().encode("ascii")
        + b"; charset=utf-8\r\n\r\n"
        + body
    )

    normalized = normalize_observation(
        observation_for(raw_request=raw_request)
    )

    assert normalized.request_content_type == content_type
    assert normalized.request_body_kind is expected_kind
    assert normalized.request_body_size == len(body)
    assert normalized.request_body_sha256 == (
        sha256(body).hexdigest() if body else None
    )


def test_response_body_metadata_hashes_exact_bytes_and_keeps_no_raw_copy() -> None:
    request_body = b'{"token":"request-secret"}\r\n'
    response_body = b"line one\r\nresponse-secret\x00"
    normalized = normalize_observation(
        observation_for(
            raw_request=(
                b"POST / HTTP/1.1\r\nContent-Type: Application/JSON; charset=utf-8"
                b"\r\n\r\n" + request_body
            ),
            raw_response=(
                b"HTTP/2 200\r\nCONTENT-TYPE: Text/Plain; Charset=UTF-8"
                b"\r\n\r\n" + response_body
            ),
        )
    )

    assert normalized.normalizer_version == NORMALIZER_VERSION == 2
    assert normalized.request_content_type == "application/json"
    assert normalized.response_content_type == "text/plain"
    assert normalized.response_body_kind is BodyKind.TEXT
    assert normalized.request_body_sha256 == sha256(request_body).hexdigest()
    assert normalized.response_body_sha256 == sha256(response_body).hexdigest()
    assert "raw_request" not in type(normalized).model_fields
    assert "raw_response" not in type(normalized).model_fields
    assert "request-secret" not in normalized.model_dump_json()
    assert "response-secret" not in normalized.model_dump_json()

    with pytest.raises(ValidationError):
        normalized.path = "/changed"  # type: ignore[misc]


def test_unusual_message_adds_warning_without_dropping_observation() -> None:
    normalized = normalize_observation(
        observation_for(raw_request=b"GET / HTTP/1.1", raw_response=None)
    )

    assert normalized.request_body_kind is BodyKind.EMPTY
    assert normalized.response_body_kind is BodyKind.EMPTY
    assert normalized.warnings == (
        "request_header_body_separator_missing",
        "response_message_missing",
    )

    invalid_port = normalize_observation(
        observation_for(url="https://example.test:0/path")
    )
    assert invalid_port.port is None
    assert "url_port_invalid" in invalid_port.warnings


def test_normalized_storage_is_deterministic_and_idempotent(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "refair.sqlite3")
    repository.initialize()
    observation = repository.add_observation(
        observation_for(url="https://example.test/items?%69d=1&id=2")
    )

    version_two = normalize_observation(observation)
    version_one = version_two.model_copy(
        update={"normalizer_version": 1, "query_parameter_names": ("id", "id")}
    )

    assert repository.add_normalized_exchange(version_one) == version_one
    assert repository.add_normalized_exchange(version_two) == version_two
    assert repository.add_normalized_exchange(version_two) == version_two
    assert repository.count_normalized_exchanges() == 1
    assert repository.get_normalized_exchange(observation.id) == version_two

    version_three = version_two.model_copy(
        update={"normalizer_version": 3, "query_parameter_names": ("future",)}
    )
    assert repository.add_normalized_exchange(version_three) == version_three
    assert repository.add_normalized_exchange(version_two) == version_three
    assert repository.get_normalized_exchange(observation.id) == version_three
    assert repository.count_normalized_exchanges() == 1


def test_pending_detection_is_relative_to_target_normalizer_version(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "refair.sqlite3")
    repository.initialize()
    observations = tuple(
        repository.add_observation(
            observation_for(url=f"https://example.test/items/{index}")
        )
        for index in range(4)
    )
    normalized = tuple(normalize_observation(item) for item in observations)
    repository.add_normalized_exchange(
        normalized[1].model_copy(update={"normalizer_version": 1})
    )
    repository.add_normalized_exchange(normalized[2])
    repository.add_normalized_exchange(
        normalized[3].model_copy(update={"normalizer_version": 3})
    )

    pending = repository.list_pending_observations(target_normalizer_version=2)

    assert {item.id for item in pending} == {observations[0].id, observations[1].id}
    assert repository.count_pending_observations(target_normalizer_version=2) == 2
    assert len(
        repository.list_pending_observations(
            target_normalizer_version=2, limit=1
        )
    ) == 1
