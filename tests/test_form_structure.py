from __future__ import annotations

import gzip
import sqlite3
from uuid import UUID

import pytest

import refair.structure as structure
from refair.models import (
    FormDirection,
    FormParseStatus,
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
    request_content_type: str | None = "application/x-www-form-urlencoded",
    request_content_encodings: tuple[str, ...] = (),
    response_body: bytes = b"",
    response_content_type: str | None = None,
    response_content_encodings: tuple[str, ...] = (),
    url: str = "https://api.test/form",
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
            b"POST /form HTTP/1.1\r\n"
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


def field_counts(repository: SQLiteRepository, operation_id):
    return {
        item.field_name: (
            item.observation_count,
            item.total_occurrence_count,
            item.total_assigned_occurrence_count,
            item.max_occurrence_count,
        )
        for item in repository.operation_form_fields(operation_id)
    }


def test_basic_form_persists_names_and_counts_but_not_values(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "basic-form.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=b"username=alice&tenant_id=42&role=user"
        ),
    )

    outcomes = repository.operation_form_document_outcomes(extraction.operation.id)
    assert [
        (item.direction, item.parse_status, item.observation_count)
        for item in outcomes
    ] == [(FormDirection.REQUEST, FormParseStatus.PARSED, 1)]
    fields = repository.operation_form_fields(extraction.operation.id)
    assert {item.field_name for item in fields} == {
        b"username",
        b"tenant_id",
        b"role",
    }
    assert all(
        (
            item.observation_count,
            item.total_occurrence_count,
            item.total_assigned_occurrence_count,
            item.max_occurrence_count,
        )
        == (1, 1, 1, 1)
        for item in fields
    )
    assert {item.field_name for item in fields}.isdisjoint(
        {b"alice", b"42", b"user"}
    )
    with sqlite3.connect(repository.path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(form_field_observations)"
            )
        }
        persisted = connection.execute(
            "SELECT direction, field_name, occurrence_count, "
            "assigned_occurrence_count FROM form_field_observations"
        ).fetchall()
    assert columns == {
        "observation_id",
        "direction",
        "field_name",
        "occurrence_count",
        "assigned_occurrence_count",
    }
    assert all(value not in repr(persisted) for value in ("alice", "42"))


def test_plus_percent_and_repeated_names_share_byte_identity(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "decoded-names.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=b"first+name=x&first%20name=y&foo%2Fbar=z"
        ),
    )

    assert field_counts(repository, extraction.operation.id) == {
        b"first name": (1, 2, 2, 2),
        b"foo/bar": (1, 1, 1, 1),
    }


def test_repeated_bare_assigned_and_first_equals_semantics(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "occurrences.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=(
                b"role=user&role=admin&flag&flag=&flag=value&token=a=b=c"
                b"&semi=1;other=2"
            )
        ),
    )

    assert field_counts(repository, extraction.operation.id) == {
        b"role": (1, 2, 2, 2),
        b"flag": (1, 3, 2, 3),
        b"token": (1, 1, 1, 1),
        b"semi": (1, 1, 1, 1),
    }


def test_empty_segments_are_ignored_but_assigned_empty_name_is_kept(
    tmp_path,
) -> None:
    repository = SQLiteRepository(tmp_path / "empty-segments.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(request_body=b"a=1&&b=2&&=value&="),
    )

    assert field_counts(repository, extraction.operation.id) == {
        b"a": (1, 1, 1, 1),
        b"b": (1, 1, 1, 1),
        b"": (1, 2, 2, 2),
    }


def test_malformed_percent_sequences_remain_literal_bytes(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "percent-literal.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(request_body=b"bad%ZZ=x&bad%=y&bad%A=z"),
    )

    assert field_counts(repository, extraction.operation.id) == {
        b"bad%ZZ": (1, 1, 1, 1),
        b"bad%": (1, 1, 1, 1),
        b"bad%A": (1, 1, 1, 1),
    }


def test_percent_decoded_non_utf8_field_identity_stays_bytes(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "byte-name.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(request_body=b"%FF%00=value"),
    )

    fields = repository.operation_form_fields(extraction.operation.id)
    assert [item.field_name for item in fields] == [b"\xff\x00"]
    assert isinstance(fields[0].field_name, bytes)


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", "multipart/form-data; boundary=example"],
)
def test_only_normalized_form_bodies_are_attempted(tmp_path, content_type) -> None:
    repository = SQLiteRepository(tmp_path / "form-gate.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_content_type=content_type,
            request_body=b"a=1&b=2",
        ),
    )

    assert repository.operation_form_document_outcomes(extraction.operation.id) == ()
    assert repository.operation_form_fields(extraction.operation.id) == ()


def test_request_and_response_form_fields_are_direction_distinct(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "form-directions.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=b"id=request-value",
            response_content_type="application/x-www-form-urlencoded",
            response_body=b"id=response-value",
        ),
    )

    fields = repository.operation_form_fields(extraction.operation.id)
    assert {(item.direction, item.field_name) for item in fields} == {
        (FormDirection.REQUEST, b"id"),
        (FormDirection.RESPONSE, b"id"),
    }


def test_gzip_form_uses_shared_bounded_content_decoding(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "gzip-form.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=gzip.compress(b"first+name=x&role=user", mtime=0),
            request_content_encodings=("x-gzip",),
        ),
    )

    outcomes = repository.operation_form_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.observation_count) for item in outcomes] == [
        (FormParseStatus.PARSED, 1)
    ]
    assert set(field_counts(repository, extraction.operation.id)) == {
        b"first name",
        b"role",
    }


@pytest.mark.parametrize(
    ("body", "content_encodings", "status"),
    [
        (
            b"not-gzip",
            ("gzip",),
            FormParseStatus.CONTENT_DECODING_FAILED,
        ),
        (b"a=1", ("br",), FormParseStatus.UNSUPPORTED_CONTENT_ENCODING),
        (
            b"a=1",
            ("gzip, br",),
            FormParseStatus.UNSUPPORTED_CONTENT_ENCODING,
        ),
    ],
)
def test_failed_or_unsupported_form_encoding_has_no_fields(
    tmp_path, body, content_encodings, status
) -> None:
    repository = SQLiteRepository(tmp_path / "form-encoding-failure.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            request_body=body,
            request_content_encodings=content_encodings,
        ),
    )

    outcomes = repository.operation_form_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.observation_count) for item in outcomes] == [
        (status, 1)
    ]
    assert repository.operation_form_fields(extraction.operation.id) == ()


def test_form_raw_and_decoded_size_limits_have_no_partial_fields(
    tmp_path, monkeypatch
) -> None:
    repository = SQLiteRepository(tmp_path / "form-size-limits.sqlite3")
    repository.initialize()

    monkeypatch.setattr(structure, "MAX_FORM_BODY_BYTES", 2)
    raw_large = persist(
        repository,
        make_observation(request_body=b"a=1", url="https://api.test/raw-large"),
    )

    monkeypatch.setattr(structure, "MAX_FORM_BODY_BYTES", 1024)
    monkeypatch.setattr(structure, "MAX_FORM_DECODED_BYTES", 4)
    decoded_large = persist(
        repository,
        make_observation(
            request_body=gzip.compress(b"expanded=value", mtime=0),
            request_content_encodings=("gzip",),
            url="https://api.test/decoded-large",
        ),
    )

    for extraction in (raw_large, decoded_large):
        outcomes = repository.operation_form_document_outcomes(
            extraction.operation.id
        )
        assert [item.parse_status for item in outcomes] == [
            FormParseStatus.SKIPPED_TOO_LARGE
        ]
        assert repository.operation_form_fields(extraction.operation.id) == ()


def test_form_occurrence_and_decoded_name_limits_are_all_or_nothing(
    tmp_path, monkeypatch
) -> None:
    repository = SQLiteRepository(tmp_path / "form-structure-limits.sqlite3")
    repository.initialize()

    monkeypatch.setattr(structure, "MAX_FORM_FIELDS", 2)
    too_many = persist(
        repository,
        make_observation(
            request_body=b"x=1&x=2&x=3",
            url="https://api.test/too-many",
        ),
    )

    monkeypatch.setattr(structure, "MAX_FORM_FIELDS", 10_000)
    monkeypatch.setattr(structure, "MAX_FORM_FIELD_NAME_BYTES", 3)
    name_too_large = persist(
        repository,
        make_observation(
            request_body=b"%61%62%63%64=value",
            url="https://api.test/name-too-large",
        ),
    )

    for extraction in (too_many, name_too_large):
        outcomes = repository.operation_form_document_outcomes(
            extraction.operation.id
        )
        assert [item.parse_status for item in outcomes] == [
            FormParseStatus.LIMIT_EXCEEDED
        ]
        assert repository.operation_form_fields(extraction.operation.id) == ()


def test_separator_only_form_is_parsed_with_zero_fields(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "zero-fields.sqlite3")
    repository.initialize()
    extraction = persist(repository, make_observation(request_body=b"&&&"))

    outcomes = repository.operation_form_document_outcomes(extraction.operation.id)
    assert [item.parse_status for item in outcomes] == [FormParseStatus.PARSED]
    assert repository.operation_form_fields(extraction.operation.id) == ()


def test_operation_form_field_aggregation_is_evidence_counted(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "form-aggregation.sqlite3")
    repository.initialize()
    extractions = [
        persist(repository, make_observation(request_body=body))
        for body in (
            b"a=1&a=2&flag",
            b"a=3&flag=",
            b"flag=x&flag=y",
        )
    ]

    operation_id = extractions[0].operation.id
    assert {item.operation.id for item in extractions} == {operation_id}
    assert field_counts(repository, operation_id) == {
        b"a": (2, 3, 3, 2),
        b"flag": (3, 4, 3, 2),
    }
