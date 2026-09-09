from __future__ import annotations

import gzip
import sqlite3
from uuid import UUID

import pytest

import refair.structure as structure
from refair.models import (
    JsonArrayItem,
    JsonDirection,
    JsonParseStatus,
    JsonType,
    Observation,
    ObservationProvenance,
)
from refair.normalization import normalize_observation
from refair.storage import SQLiteRepository
from refair.structure import extract_structure

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")


def make_observation(
    *,
    request_body: bytes = b"",
    request_content_type: str | None = "application/json",
    request_content_encodings: tuple[str, ...] = (),
    response_body: bytes = b"",
    response_content_type: str | None = None,
    response_content_encodings: tuple[str, ...] = (),
    url: str = "https://api.test/items",
) -> Observation:
    request_headers = (
        b"Content-Type: " + request_content_type.encode("ascii") + b"\r\n"
        if request_content_type is not None
        else b""
    )
    response_headers = (
        b"Content-Type: " + response_content_type.encode("ascii") + b"\r\n"
        if response_content_type is not None
        else b""
    )
    request_headers += b"".join(
        b"Content-Encoding: " + value.encode("ascii") + b"\r\n"
        for value in request_content_encodings
    )
    response_headers += b"".join(
        b"Content-Encoding: " + value.encode("ascii") + b"\r\n"
        for value in response_content_encodings
    )
    return Observation(
        project_id=PROJECT_ID,
        provenance=ObservationProvenance.BROWSER,
        actor_id="actor_a",
        method="POST",
        url=url,
        response_status=200,
        raw_request=(
            b"POST /items HTTP/1.1\r\n"
            + request_headers
            + b"\r\n"
            + request_body
        ),
        raw_response=(
            b"HTTP/1.1 200 OK\r\n"
            + response_headers
            + b"\r\n"
            + response_body
        ),
    )


def persist(repository: SQLiteRepository, observation: Observation):
    repository.add_observation(observation)
    normalized = repository.add_normalized_exchange(
        normalize_observation(observation)
    )
    extraction = extract_structure(observation, normalized)
    assert repository.add_structural_extraction(extraction)
    return extraction


def test_basic_json_structure_contains_types_but_not_values(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "basic.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=b'{"user":{"id":"abc","active":true}}'
        ),
    )

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [
        (item.direction, item.parse_status, item.root_type, item.observation_count)
        for item in outcomes
    ] == [(JsonDirection.REQUEST, JsonParseStatus.PARSED, JsonType.OBJECT, 1)]
    fields = repository.operation_json_fields(extraction.operation.id)
    assert {(item.path, item.json_type) for item in fields} == {
        (("user",), JsonType.OBJECT),
        (("user", "id"), JsonType.STRING),
        (("user", "active"), JsonType.BOOLEAN),
    }
    assert "abc" not in "".join(item.model_dump_json() for item in fields)
    with sqlite3.connect(repository.path) as connection:
        persisted_structure = connection.execute(
            "SELECT direction, path, json_type, duplicate_key_observed "
            "FROM json_field_observations"
        ).fetchall()
    assert "abc" not in repr(persisted_structure)


def test_id_representations_remain_json_native_types(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "ids.sqlite3")
    repository.initialize()
    values = (
        b'"550e8400-e29b-41d4-a716-446655440000"',
        b'"7f3ac18b"',
        b'"YWJjZGVmZw=="',
        b"42",
    )
    extractions = [
        persist(repository, make_observation(request_body=b'{"id":' + value + b"}"))
        for value in values
    ]

    operation_id = extractions[0].operation.id
    assert {item.operation.id for item in extractions} == {operation_id}
    fields = repository.operation_json_fields(operation_id)
    id_types = {
        item.json_type: item.observation_count
        for item in fields
        if item.path == ("id",)
    }
    assert id_types == {JsonType.STRING: 3, JsonType.NUMBER: 1}
    assert set(JsonType) == {
        JsonType.OBJECT,
        JsonType.ARRAY,
        JsonType.STRING,
        JsonType.NUMBER,
        JsonType.BOOLEAN,
        JsonType.NULL,
    }


def test_arrays_use_typed_wildcard_paths_and_keep_heterogeneous_types(
    tmp_path,
) -> None:
    repository = SQLiteRepository(tmp_path / "arrays.sqlite3")
    repository.initialize()
    nested = persist(
        repository,
        make_observation(request_body=b'{"users":[{"id":1},{"id":2}]}'),
    )
    heterogeneous = persist(
        repository,
        make_observation(request_body=b'[1,"x",null]', url="https://api.test/mixed"),
    )

    nested_fields = repository.operation_json_fields(nested.operation.id)
    assert {(item.path, item.json_type) for item in nested_fields} == {
        (("users",), JsonType.ARRAY),
        (("users", JsonArrayItem.ITEM), JsonType.OBJECT),
        (("users", JsonArrayItem.ITEM, "id"), JsonType.NUMBER),
    }
    serialized_paths = {item.path for item in nested_fields}
    assert ("users", "0", "id") not in serialized_paths
    assert ("users", "1", "id") not in serialized_paths

    mixed_fields = repository.operation_json_fields(heterogeneous.operation.id)
    assert {
        item.json_type
        for item in mixed_fields
        if item.path == (JsonArrayItem.ITEM,)
    } == {JsonType.NUMBER, JsonType.STRING, JsonType.NULL}


def test_object_member_names_are_exact_typed_path_segments(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "member-names.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=b'{"a/b":{"c.d":1},"ARRAY_ITEM":"literal"}'
        ),
    )

    fields = repository.operation_json_fields(extraction.operation.id)
    assert {(item.path, item.json_type) for item in fields} == {
        (("a/b",), JsonType.OBJECT),
        (("a/b", "c.d"), JsonType.NUMBER),
        (("ARRAY_ITEM",), JsonType.STRING),
    }
    assert all(JsonArrayItem.ITEM not in item.path for item in fields)


def test_duplicate_keys_keep_type_facts_without_values(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "duplicates.sqlite3")
    repository.initialize()
    first = persist(
        repository,
        make_observation(request_body=b'{"role":"user","role":"admin"}'),
    )
    persist(
        repository,
        make_observation(request_body=b'{"x":1,"x":"a"}'),
    )

    fields = repository.operation_json_fields(first.operation.id)
    observed = {
        (item.path, item.json_type): item.duplicate_key_observed for item in fields
    }
    assert observed[(("role",), JsonType.STRING)]
    assert observed[(("x",), JsonType.NUMBER)]
    assert observed[(("x",), JsonType.STRING)]
    serialized = "".join(item.model_dump_json() for item in fields)
    assert all(value not in serialized for value in ("user", "admin", '"a"'))


def test_request_and_response_paths_are_direction_distinct(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "directions.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=b'{"id":"request-value","name":"n"}',
            response_content_type="application/problem+json",
            response_body=b'{"id":7,"role":"r"}',
        ),
    )

    fields = repository.operation_json_fields(extraction.operation.id)
    assert {
        (item.direction, item.path, item.json_type) for item in fields
    } == {
        (JsonDirection.REQUEST, ("id",), JsonType.STRING),
        (JsonDirection.REQUEST, ("name",), JsonType.STRING),
        (JsonDirection.RESPONSE, ("id",), JsonType.NUMBER),
        (JsonDirection.RESPONSE, ("role",), JsonType.STRING),
    }


@pytest.mark.parametrize(
    ("body", "root_type"),
    [
        (b"{}", JsonType.OBJECT),
        (b"[]", JsonType.ARRAY),
        (b'"value"', JsonType.STRING),
        (b"null", JsonType.NULL),
    ],
)
def test_root_json_values_are_recorded(tmp_path, body, root_type) -> None:
    repository = SQLiteRepository(tmp_path / f"root-{root_type.value}.sqlite3")
    repository.initialize()
    extraction = persist(repository, make_observation(request_body=body))

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.root_type) for item in outcomes] == [
        (JsonParseStatus.PARSED, root_type)
    ]


def test_malformed_json_has_document_result_and_no_partial_fields(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "malformed.sqlite3")
    repository.initialize()
    extraction = persist(
        repository, make_observation(request_body=b'{"id":1,"broken":')
    )

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.root_type) for item in outcomes] == [
        (JsonParseStatus.MALFORMED, None)
    ]
    assert repository.operation_json_fields(extraction.operation.id) == ()


def test_only_normalized_json_nonempty_bodies_are_attempted(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "gate.sqlite3")
    repository.initialize()
    text = persist(
        repository,
        make_observation(request_content_type="text/plain", request_body=b"{}"),
    )
    empty = persist(
        repository,
        make_observation(request_body=b"", url="https://api.test/empty"),
    )

    assert repository.operation_json_document_outcomes(text.operation.id) == ()
    assert repository.operation_json_document_outcomes(empty.operation.id) == ()
    assert repository.count_json_documents() == 0


def test_json_limits_store_status_without_partial_fields(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "limits.sqlite3")
    repository.initialize()

    monkeypatch.setattr(structure, "MAX_JSON_BODY_BYTES", 1)
    too_large = persist(
        repository,
        make_observation(request_body=b"{}", url="https://api.test/large"),
    )

    monkeypatch.setattr(structure, "MAX_JSON_BODY_BYTES", 1024)
    monkeypatch.setattr(structure, "MAX_JSON_DEPTH", 1)
    too_deep = persist(
        repository,
        make_observation(
            request_body=b'{"a":{"b":1}}', url="https://api.test/deep"
        ),
    )

    monkeypatch.setattr(structure, "MAX_JSON_DEPTH", 64)
    monkeypatch.setattr(structure, "MAX_JSON_NODES", 2)
    too_many_nodes = persist(
        repository,
        make_observation(
            request_body=b"[1,2,3]", url="https://api.test/nodes"
        ),
    )

    expected = (
        (too_large, JsonParseStatus.SKIPPED_TOO_LARGE),
        (too_deep, JsonParseStatus.LIMIT_EXCEEDED),
        (too_many_nodes, JsonParseStatus.LIMIT_EXCEEDED),
    )
    for extraction, status in expected:
        outcomes = repository.operation_json_document_outcomes(
            extraction.operation.id
        )
        assert [(item.parse_status, item.root_type) for item in outcomes] == [
            (status, None)
        ]
        assert repository.operation_json_fields(extraction.operation.id) == ()


def test_valid_gzip_json_request_is_decoded_before_parsing(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "gzip-request.sqlite3")
    repository.initialize()
    observation = make_observation(
        request_body=gzip.compress(
            b'{"user":{"id":"request-secret","active":true}}', mtime=0
        ),
        request_content_encodings=(" GZip ",),
    )
    observation = observation.model_copy(
        update={
            "raw_request": observation.raw_request.replace(
                b"Content-Encoding", b"cOnTeNt-EnCoDiNg"
            )
        }
    )
    extraction = persist(
        repository,
        observation,
    )

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.root_type) for item in outcomes] == [
        (JsonParseStatus.PARSED, JsonType.OBJECT)
    ]
    fields = repository.operation_json_fields(extraction.operation.id)
    assert {(item.path, item.json_type) for item in fields} == {
        (("user",), JsonType.OBJECT),
        (("user", "id"), JsonType.STRING),
        (("user", "active"), JsonType.BOOLEAN),
    }
    assert "request-secret" not in "".join(
        item.model_dump_json() for item in fields
    )


def test_valid_gzip_json_response_is_direction_distinct(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "gzip-response.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_content_type=None,
            response_content_type="application/problem+json",
            response_content_encodings=("x-gzip",),
            response_body=gzip.compress(b'{"result":[1,2]}', mtime=0),
        ),
    )

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [
        (item.direction, item.parse_status, item.root_type) for item in outcomes
    ] == [(JsonDirection.RESPONSE, JsonParseStatus.PARSED, JsonType.OBJECT)]
    fields = repository.operation_json_fields(extraction.operation.id)
    assert {(item.direction, item.path, item.json_type) for item in fields} == {
        (JsonDirection.RESPONSE, ("result",), JsonType.ARRAY),
        (
            JsonDirection.RESPONSE,
            ("result", JsonArrayItem.ITEM),
            JsonType.NUMBER,
        ),
    }


def test_corrupt_gzip_is_a_content_decoding_failure(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "corrupt-gzip.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=b"not-a-gzip-stream",
            request_content_encodings=("gzip",),
        ),
    )

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.root_type) for item in outcomes] == [
        (JsonParseStatus.CONTENT_DECODING_FAILED, None)
    ]
    assert repository.operation_json_fields(extraction.operation.id) == ()


def test_gzip_decoded_malformed_json_remains_malformed(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "gzip-malformed.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=gzip.compress(b'{"broken":', mtime=0),
            request_content_encodings=("gzip",),
        ),
    )

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.root_type) for item in outcomes] == [
        (JsonParseStatus.MALFORMED, None)
    ]
    assert repository.operation_json_fields(extraction.operation.id) == ()


def test_gzip_decoded_size_limit_has_no_partial_fields(
    tmp_path, monkeypatch
) -> None:
    repository = SQLiteRepository(tmp_path / "gzip-size.sqlite3")
    repository.initialize()
    monkeypatch.setattr(structure, "MAX_JSON_DECODED_BYTES", 8)
    extraction = persist(
        repository,
        make_observation(
            request_body=gzip.compress(b'{"expanded":true}', mtime=0),
            request_content_encodings=("gzip",),
        ),
    )

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.root_type) for item in outcomes] == [
        (JsonParseStatus.SKIPPED_TOO_LARGE, None)
    ]
    assert repository.operation_json_fields(extraction.operation.id) == ()


@pytest.mark.parametrize(
    "content_encodings",
    [
        ("br",),
        ("gzip, br",),
        ("gzip", "identity"),
    ],
)
def test_unsupported_or_ambiguous_content_encodings_are_not_parsed(
    tmp_path, content_encodings
) -> None:
    repository = SQLiteRepository(tmp_path / "unsupported.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=b"{}",
            request_content_encodings=content_encodings,
        ),
    )

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.root_type) for item in outcomes] == [
        (JsonParseStatus.UNSUPPORTED_CONTENT_ENCODING, None)
    ]
    assert repository.operation_json_fields(extraction.operation.id) == ()


@pytest.mark.parametrize("content_encodings", [(), ("identity",)])
def test_absent_and_identity_content_encoding_keep_json_behavior(
    tmp_path, content_encodings
) -> None:
    repository = SQLiteRepository(tmp_path / "identity.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=b'{"id":1}',
            request_content_encodings=content_encodings,
        ),
    )

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.root_type) for item in outcomes] == [
        (JsonParseStatus.PARSED, JsonType.OBJECT)
    ]


def test_anti_xssi_prefix_is_not_stripped(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "anti-xssi.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(request_body=b")]}\'\n[[],{}]"),
    )

    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.root_type) for item in outcomes] == [
        (JsonParseStatus.MALFORMED, None)
    ]
    assert repository.operation_json_fields(extraction.operation.id) == ()
