from __future__ import annotations

import gzip
import sqlite3
from uuid import UUID

import pytest

import refair.structure as structure
from refair.models import (
    MultipartDirection,
    MultipartParseStatus,
    Observation,
    ObservationProvenance,
)
from refair.normalization import normalize_observation
from refair.storage import SQLiteRepository
from refair.structure import extract_structure

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")


def multipart(
    boundary: bytes,
    parts: tuple[bytes, ...] = (),
    *,
    preamble: bytes = b"",
    epilogue: bytes = b"",
    close: bool = True,
    newline: bytes = b"\r\n",
) -> bytes:
    result = preamble
    for part in parts:
        result += b"--" + boundary + newline + part + newline
    if close:
        result += b"--" + boundary + b"--" + newline + epilogue
    return result


def part(headers: bytes = b"", body: bytes = b"") -> bytes:
    return headers + b"\r\n\r\n" + body


def make_observation(
    *,
    body: bytes,
    content_type: bytes = b"multipart/form-data; boundary=boundary",
    extra_content_types: tuple[bytes, ...] = (),
    content_encoding: bytes | None = None,
    response_body: bytes = b"",
    response_content_type: bytes | None = None,
    url: str = "https://api.test/upload",
) -> Observation:
    request_headers = b"Content-Type: " + content_type + b"\r\n"
    request_headers += b"".join(
        b"Content-Type: " + value + b"\r\n" for value in extra_content_types
    )
    if content_encoding is not None:
        request_headers += b"Content-Encoding: " + content_encoding + b"\r\n"
    response_headers = (
        b"Content-Type: " + response_content_type + b"\r\n"
        if response_content_type is not None
        else b""
    )
    return Observation(
        project_id=PROJECT_ID,
        provenance=ObservationProvenance.BROWSER,
        actor_id="actor_a",
        method="POST",
        url=url,
        response_status=200,
        raw_request=b"POST /upload HTTP/1.1\r\n" + request_headers + b"\r\n" + body,
        raw_response=b"HTTP/1.1 200 OK\r\n" + response_headers + b"\r\n" + response_body,
    )


def persist(repository: SQLiteRepository, observation: Observation):
    repository.add_observation(observation)
    normalized = repository.add_normalized_exchange(normalize_observation(observation))
    extraction = extract_structure(observation, normalized)
    assert repository.add_structural_extraction(extraction)
    return extraction


def outcome(repository: SQLiteRepository, operation_id):
    values = repository.operation_multipart_document_outcomes(operation_id)
    assert len(values) == 1
    return values[0]


def test_basic_text_and_file_parts_persist_only_selected_structure(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "basic.sqlite3")
    repository.initialize()
    body = multipart(
        b"boundary",
        (
            part(b'Content-Disposition: form-data; name="username"', b"alice"),
            part(
                b'Content-Disposition: form-data; NAME="avatar"; FILENAME="cat.png"\r\n'
                b"Content-Type: Image/PNG; charset=ignored",
                b"secret-file-bytes",
            ),
        ),
    )
    extraction = persist(repository, make_observation(body=body))

    assert outcome(repository, extraction.operation.id).parse_status is MultipartParseStatus.PARSED
    parts = repository.operation_multipart_parts(extraction.operation.id)
    assert [(item.part_index, item.disposition_type, item.content_type) for item in parts] == [
        (0, "form-data", None),
        (1, "form-data", "image/png"),
    ]
    assert [[name.name for name in item.names] for item in parts] == [[b"username"], [b"avatar"]]
    assert (parts[0].filename_parameter_count, parts[0].filename_empty_count, parts[0].filename_nonempty_count) == (0, 0, 0)
    assert (parts[1].filename_parameter_count, parts[1].filename_empty_count, parts[1].filename_nonempty_count) == (1, 0, 1)
    with sqlite3.connect(repository.path) as connection:
        columns = {
            row[1]
            for table in ("multipart_parts", "multipart_part_names")
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        persisted = repr(
            (
                connection.execute("SELECT * FROM multipart_parts").fetchall(),
                connection.execute("SELECT * FROM multipart_part_names").fetchall(),
            )
        )
    assert {"filename", "body", "value", "hash"}.isdisjoint(columns)
    assert all(value not in persisted for value in ("alice", "cat.png", "secret-file-bytes"))


def test_part_occurrences_duplicate_names_and_exact_name_bytes(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "names.sqlite3")
    repository.initialize()
    headers = (
        b'Content-Disposition: form-data; name="x"; name="x"; name="a;b"; '
        b'name="a\\\"b"; name="slash\\\\name"; name="first+name"; '
        b'name="first%20name"; name="user[avatar]"; name="files[]"'
    )
    extraction = persist(
        repository,
        make_observation(
            body=multipart(
                b"boundary",
                (
                    part(headers),
                    part(b'Content-Disposition: form-data; name="x"'),
                    part(b"Content-Disposition: form-data"),
                    part(b'Content-Disposition: form-data; name=""'),
                ),
            )
        ),
    )

    parts = repository.operation_multipart_parts(extraction.operation.id)
    assert [item.part_index for item in parts] == [0, 1, 2, 3]
    assert parts[0].name_parameter_count == 9
    assert {item.name: item.occurrence_count for item in parts[0].names} == {
        b"x": 2,
        b"a;b": 1,
        b'a"b': 1,
        b"slash\\name": 1,
        b"first+name": 1,
        b"first%20name": 1,
        b"user[avatar]": 1,
        b"files[]": 1,
    }
    assert [item.name for item in parts[1].names] == [b"x"]
    assert parts[2].name_parameter_count == 0 and parts[2].names == ()
    assert parts[3].name_parameter_count == 1 and parts[3].names[0].name == b""


def test_duplicate_filename_parameters_preserve_counts_not_values(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "filenames.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            body=multipart(
                b"boundary",
                (
                    part(b'Content-Disposition: form-data; filename="a.txt"; filename="b.php"'),
                    part(b'Content-Disposition: form-data; filename=""; filename="c.pdf"; filename*=ignored'),
                ),
            )
        ),
    )
    parts = repository.operation_multipart_parts(extraction.operation.id)
    assert [(item.filename_parameter_count, item.filename_empty_count, item.filename_nonempty_count) for item in parts] == [
        (2, 0, 2),
        (2, 1, 1),
    ]
    with sqlite3.connect(repository.path) as connection:
        persisted = repr(connection.execute("SELECT * FROM multipart_parts").fetchall())
    assert all(value not in persisted for value in ("a.txt", "b.php", "c.pdf", "ignored"))


@pytest.mark.parametrize(
    "headers",
    [
        b'Content-Disposition: form-data; name="a"\r\nContent-Disposition: form-data; name="b"',
        b"Content-Type: text/plain\r\nContent-Type: image/png",
        b'Content-Disposition: form-data; name="unterminated',
        b"Content-Disposition: form-data; bad",
        b"Content-Disposition: form-data\r\n folded: value",
    ],
)
def test_ambiguous_or_malformed_selected_part_headers_are_all_or_nothing(tmp_path, headers) -> None:
    repository = SQLiteRepository(tmp_path / "malformed-part.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(
            body=multipart(
                b"boundary",
                (part(b'Content-Disposition: form-data; name="good"'), part(headers)),
            )
        ),
    )
    assert outcome(repository, extraction.operation.id).parse_status is MultipartParseStatus.MALFORMED
    assert repository.operation_multipart_parts(extraction.operation.id) == ()


@pytest.mark.parametrize(
    ("content_type", "extra", "expected"),
    [
        (b"multipart/form-data", (), MultipartParseStatus.MALFORMED),
        (b"multipart/form-data; boundary=", (), MultipartParseStatus.MALFORMED),
        (b"multipart/form-data; boundary=a; boundary=b", (), MultipartParseStatus.MALFORMED),
        (b"multipart/form-data; boundary=a", (b"multipart/form-data; boundary=a",), MultipartParseStatus.MALFORMED),
    ],
)
def test_ambiguous_or_missing_top_boundary_is_malformed(tmp_path, content_type, extra, expected) -> None:
    repository = SQLiteRepository(tmp_path / "top-header.sqlite3")
    repository.initialize()
    extraction = persist(
        repository,
        make_observation(body=multipart(b"a"), content_type=content_type, extra_content_types=extra),
    )
    assert outcome(repository, extraction.operation.id).parse_status is expected
    assert repository.operation_multipart_parts(extraction.operation.id) == ()


def test_token_quoted_empty_preamble_epilogue_and_line_aware_boundary(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "framing.sqlite3")
    repository.initialize()
    binary = b"prefix--quoted-middle\r\nnot--quoted\r\nsuffix"
    parsed = persist(
        repository,
        make_observation(
            body=multipart(
                b"quoted",
                (part(b'Content-Disposition: form-data; name="file"', binary),),
                preamble=b"ignored preamble\r\n",
                epilogue=b"ignored epilogue",
            ),
            content_type=b'multipart/form-data; boundary="quoted"',
            url="https://api.test/framing",
        ),
    )
    empty = persist(
        repository,
        make_observation(body=multipart(b"token--"), content_type=b"multipart/form-data; boundary=token--", url="https://api.test/empty"),
    )
    escaped = persist(
        repository,
        make_observation(
            body=multipart(b'a"b'),
            content_type=b'multipart/form-data; boundary="a\\\"b"',
            url="https://api.test/escaped-boundary",
        ),
    )
    missing_close = persist(
        repository,
        make_observation(body=multipart(b"no-close", (part(),), close=False), content_type=b"multipart/form-data; boundary=no-close", url="https://api.test/missing-close"),
    )
    assert outcome(repository, parsed.operation.id).parse_status is MultipartParseStatus.PARSED
    assert len(repository.operation_multipart_parts(parsed.operation.id)) == 1
    assert outcome(repository, empty.operation.id).parse_status is MultipartParseStatus.PARSED
    assert repository.operation_multipart_parts(empty.operation.id) == ()
    assert outcome(repository, escaped.operation.id).parse_status is MultipartParseStatus.PARSED
    assert outcome(repository, missing_close.operation.id).parse_status is MultipartParseStatus.MALFORMED


def test_scope_gate_directions_and_no_recursive_part_parsing(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "scope.sqlite3")
    repository.initialize()
    nested = multipart(
        b"boundary",
        (
            part(b'Content-Disposition: form-data; name="json"\r\nContent-Type: application/json', b'{"secret":1}'),
            part(b'Content-Disposition: form-data; name="nested"\r\nContent-Type: multipart/mixed; boundary=x', b"--x--"),
        ),
    )
    multiline = multipart(b"response", (part(),))
    extraction = persist(
        repository,
        make_observation(
            body=nested,
            response_body=multiline,
            response_content_type=b"multipart/form-data; boundary=response",
        ),
    )
    parts = repository.operation_multipart_parts(extraction.operation.id)
    assert {(item.direction, item.content_type) for item in parts} == {
        (MultipartDirection.REQUEST, "application/json"),
        (MultipartDirection.REQUEST, "multipart/mixed"),
        (MultipartDirection.RESPONSE, None),
    }
    assert extraction.json_documents == ()
    mixed = persist(
        repository,
        make_observation(body=multiline, content_type=b"multipart/mixed; boundary=response", url="https://api.test/mixed"),
    )
    assert repository.operation_multipart_document_outcomes(mixed.operation.id) == ()


@pytest.mark.parametrize(
    ("body", "encoding", "expected"),
    [
        (gzip.compress(multipart(b"boundary", (part(),)), mtime=0), b"gzip", MultipartParseStatus.PARSED),
        (b"corrupt", b"gzip", MultipartParseStatus.CONTENT_DECODING_FAILED),
        (multipart(b"boundary"), b"br", MultipartParseStatus.UNSUPPORTED_CONTENT_ENCODING),
    ],
)
def test_shared_content_encoding_statuses(tmp_path, body, encoding, expected) -> None:
    repository = SQLiteRepository(tmp_path / "encoding.sqlite3")
    repository.initialize()
    extraction = persist(repository, make_observation(body=body, content_encoding=encoding))
    assert outcome(repository, extraction.operation.id).parse_status is expected
    if expected is not MultipartParseStatus.PARSED:
        assert repository.operation_multipart_parts(extraction.operation.id) == ()


@pytest.mark.parametrize(
    ("constant", "limit", "content_type", "body"),
    [
        ("MAX_MULTIPART_BODY_BYTES", 2, b"multipart/form-data; boundary=b", multipart(b"b")),
        ("MAX_MULTIPART_TOP_HEADER_BYTES", 2, b"multipart/form-data; boundary=b", multipart(b"b")),
        ("MAX_MULTIPART_BOUNDARY_BYTES", 2, b"multipart/form-data; boundary=long", multipart(b"long")),
        ("MAX_MULTIPART_PARTS", 1, b"multipart/form-data; boundary=b", multipart(b"b", (part(), part()))),
        ("MAX_MULTIPART_PART_HEADER_BYTES", 2, b"multipart/form-data; boundary=b", multipart(b"b", (part(b"Long: header"),))),
        ("MAX_MULTIPART_NAME_BYTES", 2, b"multipart/form-data; boundary=b", multipart(b"b", (part(b'Content-Disposition: form-data; name="long"'),))),
        ("MAX_MULTIPART_DISPOSITION_PARAMETERS", 1, b"multipart/form-data; boundary=b", multipart(b"b", (part(b"Content-Disposition: form-data; a=1; b=2"),))),
    ],
)
def test_structural_limits_are_all_or_nothing(tmp_path, monkeypatch, constant, limit, content_type, body) -> None:
    repository = SQLiteRepository(tmp_path / "limit.sqlite3")
    repository.initialize()
    monkeypatch.setattr(structure, constant, limit)
    extraction = persist(repository, make_observation(body=body, content_type=content_type))
    result = outcome(repository, extraction.operation.id)
    expected = MultipartParseStatus.SKIPPED_TOO_LARGE if constant == "MAX_MULTIPART_BODY_BYTES" else MultipartParseStatus.LIMIT_EXCEEDED
    assert result.parse_status is expected
    assert repository.operation_multipart_parts(extraction.operation.id) == ()


def test_decoded_body_limit_is_skipped_without_parts(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "decoded-limit.sqlite3")
    repository.initialize()
    monkeypatch.setattr(structure, "MAX_MULTIPART_DECODED_BYTES", 8)
    body = gzip.compress(multipart(b"boundary", (part(),)), mtime=0)
    extraction = persist(repository, make_observation(body=body, content_encoding=b"gzip"))
    assert outcome(repository, extraction.operation.id).parse_status is MultipartParseStatus.SKIPPED_TOO_LARGE
    assert repository.operation_multipart_parts(extraction.operation.id) == ()
