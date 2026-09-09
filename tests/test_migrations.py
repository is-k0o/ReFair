from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import datetime, timezone
from uuid import UUID

import pytest

from refair.models import (
    FormParseStatus,
    JsonParseStatus,
    JsonType,
    Observation,
    ObservationProvenance,
)
from refair.normalization import normalize_observation
from refair.process.cli import process_structure_pending
from refair.storage import (
    CURRENT_SCHEMA_VERSION,
    SQLiteRepository,
    UnsupportedSchemaVersionError,
)
from refair.storage.sqlite import LEGACY_SCHEMA, MIGRATIONS
from refair.structure import STRUCTURAL_VERSION, extract_structure


def test_legacy_database_migrates_without_changing_raw_observation(tmp_path) -> None:
    database = tmp_path / "legacy.sqlite3"
    observation = Observation(
        id=UUID("22222222-2222-4222-8222-222222222222"),
        project_id=UUID("11111111-1111-4111-8111-111111111111"),
        observed_at=datetime(2026, 9, 8, 8, 30, tzinfo=timezone.utc),
        provenance=ObservationProvenance.BROWSER,
        actor_id="actor_a",
        method="POST",
        url="https://example.test/items?id=secret",
        response_status=201,
        raw_request=b"POST /items?id=secret HTTP/2\r\nX-Binary: \xff\r\n\r\n\x00request",
        raw_response=b"HTTP/2 201\r\nContent-Type: application/octet-stream\r\n\r\n\xffresponse",
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute(
            """
            INSERT INTO observations (
                id, project_id, observed_at, provenance, actor_id, method, url,
                response_status, raw_request, raw_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(observation.id),
                str(observation.project_id),
                observation.observed_at.isoformat(),
                observation.provenance.value,
                observation.actor_id,
                observation.method,
                observation.url,
                observation.response_status,
                observation.raw_request,
                observation.raw_response,
            ),
        )
        before = connection.execute("SELECT * FROM observations").fetchone()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0

    SQLiteRepository(database).initialize()

    with sqlite3.connect(database) as connection:
        after = connection.execute("SELECT * FROM observations").fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        normalized_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'normalized_exchanges'"
        ).fetchone()
        normalized_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(normalized_exchanges)")
        }
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'observations'"
            )
        }

    assert after == before
    assert bytes(after[8]) == observation.raw_request
    assert bytes(after[9]) == observation.raw_response
    assert version == CURRENT_SCHEMA_VERSION == 6
    assert normalized_table == ("normalized_exchanges",)
    assert "raw_request" not in normalized_columns
    assert "raw_response" not in normalized_columns
    assert triggers == {
        "observations_immutable_update",
        "observations_immutable_delete",
    }


def test_schema_one_migration_preserves_raw_and_normalized_rows(tmp_path) -> None:
    database = tmp_path / "schema-one.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.executescript(MIGRATIONS[1])
        connection.execute("PRAGMA user_version = 1")

    repository = SQLiteRepository(database)
    observation = repository.add_observation(
        Observation(
            id=UUID("33333333-3333-4333-8333-333333333333"),
            project_id=UUID("11111111-1111-4111-8111-111111111111"),
            observed_at=datetime(2026, 9, 9, 8, 30, tzinfo=timezone.utc),
            provenance=ObservationProvenance.BROWSER,
            actor_id="actor_b",
            method="PATCH",
            url="https://example.test/exact/path?id=secret",
            response_status=403,
            raw_request=b"PATCH /exact/path?id=secret HTTP/2\r\n\r\n\x00raw",
            raw_response=b"HTTP/2 403\r\nContent-Type: text/plain\r\n\r\n\xffraw",
        )
    )
    normalized = repository.add_normalized_exchange(
        normalize_observation(observation)
    )
    with sqlite3.connect(database) as connection:
        raw_before = connection.execute("SELECT * FROM observations").fetchall()
        normalized_before = connection.execute(
            "SELECT * FROM normalized_exchanges"
        ).fetchall()

    repository.initialize()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert (
            connection.execute("SELECT * FROM observations").fetchall()
            == raw_before
        )
        assert (
            connection.execute("SELECT * FROM normalized_exchanges").fetchall()
            == normalized_before
        )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'observations'"
            )
        }
        with pytest.raises(sqlite3.IntegrityError, match="observations are immutable"):
            connection.execute(
                "UPDATE observations SET raw_response = ? WHERE id = ?",
                (b"changed", str(observation.id)),
            )

    assert normalized.normalizer_version == 2
    assert {
        "exact_endpoints",
        "http_operations",
        "operation_observations",
        "method_advertisements",
        "structural_processing",
    }.issubset(tables)
    assert triggers == {
        "observations_immutable_update",
        "observations_immutable_delete",
    }


def test_future_schema_version_is_rejected_without_downgrade(tmp_path) -> None:
    database = tmp_path / "future.sqlite3"
    future_version = CURRENT_SCHEMA_VERSION + 1
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version = {future_version}")

    with pytest.raises(UnsupportedSchemaVersionError, match="newer than supported"):
        SQLiteRepository(database).initialize()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == future_version
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'normalized_exchanges'"
        ).fetchone() is None


def test_schema_two_migrates_and_reprocesses_json_without_changing_b1_or_evidence(
    tmp_path,
) -> None:
    database = tmp_path / "schema-two.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.executescript(MIGRATIONS[1])
        connection.executescript(MIGRATIONS[2])
        connection.execute("PRAGMA user_version = 2")

    repository = SQLiteRepository(database)
    observation = repository.add_observation(
        Observation(
            id=UUID("44444444-4444-4444-8444-444444444444"),
            project_id=UUID("11111111-1111-4111-8111-111111111111"),
            observed_at=datetime(2026, 9, 9, 9, 30, tzinfo=timezone.utc),
            provenance=ObservationProvenance.BROWSER,
            actor_id="actor_json",
            method="POST",
            url="https://example.test/json?id=secret",
            response_status=200,
            raw_request=(
                b"POST /json?id=secret HTTP/1.1\r\n"
                b"Content-Type: application/json\r\n\r\n"
                b'{"request_id":"raw-secret"}'
            ),
            raw_response=(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Allow: GET, POST\r\n\r\n{\"response_id\":7}"
            ),
        )
    )
    normalized = repository.add_normalized_exchange(
        normalize_observation(observation)
    )
    extraction = extract_structure(observation, normalized)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO exact_endpoints VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(extraction.endpoint.id),
                str(extraction.endpoint.project_id),
                extraction.endpoint.scheme,
                extraction.endpoint.host,
                extraction.endpoint.port,
                extraction.endpoint.path,
            ),
        )
        connection.execute(
            "INSERT INTO http_operations VALUES (?, ?, ?)",
            (
                str(extraction.operation.id),
                str(extraction.operation.endpoint_id),
                extraction.operation.method,
            ),
        )
        connection.execute(
            "INSERT INTO operation_observations VALUES (?, ?)",
            (str(observation.id), str(extraction.operation.id)),
        )
        connection.executemany(
            "INSERT INTO method_advertisements VALUES (?, ?, ?, ?)",
            [
                (
                    str(item.endpoint_id),
                    item.source.value,
                    item.advertised_method,
                    str(item.observation_id),
                )
                for item in extraction.advertisements
            ],
        )
        connection.execute(
            "INSERT INTO structural_processing VALUES (?, 1)",
            (str(observation.id),),
        )
        preserved = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in (
                "observations",
                "normalized_exchanges",
                "exact_endpoints",
                "http_operations",
                "operation_observations",
                "method_advertisements",
                "structural_processing",
            )
        }

    repository.initialize()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        for table, rows in preserved.items():
            assert connection.execute(f"SELECT * FROM {table}").fetchall() == rows
        assert bytes(preserved["observations"][0][8]) == observation.raw_request
        assert bytes(preserved["observations"][0][9]) == observation.raw_response
        assert connection.execute(
            "SELECT normalizer_version FROM normalized_exchanges"
        ).fetchone() == (2,)
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }.issuperset({"json_documents", "json_field_observations"})
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'observations'"
            )
        } == {
            "observations_immutable_update",
            "observations_immutable_delete",
        }

    first = process_structure_pending(repository)
    second = process_structure_pending(repository)

    assert first.processed == 1
    assert first.pending == 0
    assert second.processed == 0
    assert repository.count_json_documents() == 2
    assert repository.count_json_field_observations() == 2
    assert repository.count_exact_endpoints() == 1
    assert repository.count_http_operations() == 1
    assert repository.count_operation_observations() == 1
    assert repository.count_method_advertisements() == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT structural_version FROM structural_processing"
        ).fetchone() == (STRUCTURAL_VERSION,)


def test_schema_four_preserves_b2a_rows_then_reprocesses_for_form_extraction(
    tmp_path,
) -> None:
    database = tmp_path / "schema-four.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.executescript(MIGRATIONS[1])
        connection.executescript(MIGRATIONS[2])
        connection.executescript(MIGRATIONS[3])
        connection.executescript(MIGRATIONS[4])
        connection.execute("PRAGMA user_version = 4")

    repository = SQLiteRepository(database)
    observation = repository.add_observation(
        Observation(
            id=UUID("55555555-5555-4555-8555-555555555555"),
            project_id=UUID("11111111-1111-4111-8111-111111111111"),
            observed_at=datetime(2026, 9, 9, 10, 30, tzinfo=timezone.utc),
            provenance=ObservationProvenance.BROWSER,
            actor_id="actor_gzip",
            method="POST",
            url="https://example.test/form",
            response_status=200,
            raw_request=(
                b"POST /form HTTP/1.1\r\n"
                b"Content-Type: application/x-www-form-urlencoded\r\n"
                b"Content-Encoding: gzip\r\n\r\n"
                + gzip.compress(b"id=form-secret", mtime=0)
            ),
            raw_response=(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
                b'{"id":"raw-secret"}'
            ),
        )
    )
    normalized = repository.add_normalized_exchange(
        normalize_observation(observation)
    )
    extraction = extract_structure(observation, normalized)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO exact_endpoints VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(extraction.endpoint.id),
                str(extraction.endpoint.project_id),
                extraction.endpoint.scheme,
                extraction.endpoint.host,
                extraction.endpoint.port,
                extraction.endpoint.path,
            ),
        )
        connection.execute(
            "INSERT INTO http_operations VALUES (?, ?, ?)",
            (
                str(extraction.operation.id),
                str(extraction.operation.endpoint_id),
                extraction.operation.method,
            ),
        )
        connection.execute(
            "INSERT INTO operation_observations VALUES (?, ?)",
            (str(observation.id), str(extraction.operation.id)),
        )
        connection.executemany(
            "INSERT INTO method_advertisements VALUES (?, ?, ?, ?)",
            [
                (
                    str(item.endpoint_id),
                    item.source.value,
                    item.advertised_method,
                    str(item.observation_id),
                )
                for item in extraction.advertisements
            ],
        )
        connection.executemany(
            "INSERT INTO json_documents VALUES (?, ?, ?, ?)",
            [
                (
                    str(item.observation_id),
                    item.direction.value,
                    item.parse_status.value,
                    item.root_type.value if item.root_type is not None else None,
                )
                for item in extraction.json_documents
            ],
        )
        connection.executemany(
            "INSERT INTO json_field_observations VALUES (?, ?, ?, ?, ?)",
            [
                (
                    str(item.observation_id),
                    item.direction.value,
                    json.dumps(item.path, separators=(",", ":")),
                    item.json_type.value,
                    int(item.duplicate_key_observed),
                )
                for item in extraction.json_fields
            ],
        )
        connection.execute(
            "INSERT INTO structural_processing VALUES (?, 3)",
            (str(observation.id),),
        )

    preserved_tables = (
        "observations",
        "normalized_exchanges",
        "exact_endpoints",
        "http_operations",
        "operation_observations",
        "method_advertisements",
        "json_documents",
        "json_field_observations",
        "structural_processing",
    )
    with sqlite3.connect(database) as connection:
        preserved = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in preserved_tables
        }

    repository.initialize()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        for table, rows in preserved.items():
            assert connection.execute(f"SELECT * FROM {table}").fetchall() == rows
        assert bytes(preserved["observations"][0][8]) == observation.raw_request
        assert bytes(preserved["observations"][0][9]) == observation.raw_response
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'observations'"
            )
        } == {
            "observations_immutable_update",
            "observations_immutable_delete",
        }

    first = process_structure_pending(repository)
    second = process_structure_pending(repository)

    assert first.processed == 1
    assert first.pending == 0
    assert second.processed == 0
    outcomes = repository.operation_json_document_outcomes(extraction.operation.id)
    assert [(item.parse_status, item.root_type) for item in outcomes] == [
        (JsonParseStatus.PARSED, JsonType.OBJECT)
    ]
    assert repository.count_json_field_observations() == 1
    form_outcomes = repository.operation_form_document_outcomes(
        extraction.operation.id
    )
    assert [item.parse_status for item in form_outcomes] == [
        FormParseStatus.PARSED
    ]
    assert repository.count_form_field_observations() == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT structural_version FROM structural_processing"
        ).fetchone() == (STRUCTURAL_VERSION,)


def test_schema_five_adds_only_multipart_tables_and_reprocesses_once(tmp_path) -> None:
    database = tmp_path / "schema-five.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(LEGACY_SCHEMA)
        for version in range(1, 6):
            connection.executescript(MIGRATIONS[version])
        connection.execute("PRAGMA user_version = 5")

    repository = SQLiteRepository(database)
    observations = (
        Observation(
            id=UUID("66666666-6666-4666-8666-666666666666"),
            project_id=UUID("11111111-1111-4111-8111-111111111111"),
            observed_at=datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc),
            provenance=ObservationProvenance.BROWSER,
            actor_id="actor_multipart",
            method="POST",
            url="https://example.test/upload",
            response_status=200,
            raw_request=(
                b"POST /upload HTTP/1.1\r\nContent-Type: multipart/form-data; "
                b"boundary=b\r\n\r\n--b\r\nContent-Disposition: form-data; "
                b'name="avatar"; filename="private.png"\r\nContent-Type: '
                b"image/png\r\n\r\nprivate-file-bytes\r\n--b--\r\n"
            ),
            raw_response=(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
                b'{"id":"secret"}'
            ),
        ),
        Observation(
            id=UUID("77777777-7777-4777-8777-777777777777"),
            project_id=UUID("11111111-1111-4111-8111-111111111111"),
            observed_at=datetime(2026, 9, 9, 11, 1, tzinfo=timezone.utc),
            provenance=ObservationProvenance.BROWSER,
            actor_id="actor_form",
            method="POST",
            url="https://example.test/upload",
            response_status=204,
            raw_request=(
                b"POST /upload HTTP/1.1\r\nContent-Type: "
                b"application/x-www-form-urlencoded\r\n\r\nfield=value"
            ),
            raw_response=b"HTTP/1.1 204 No Content\r\n\r\n",
        ),
    )
    extractions = []
    for observation in observations:
        repository.add_observation(observation)
        normalized = repository.add_normalized_exchange(
            normalize_observation(observation)
        )
        extractions.append(extract_structure(observation, normalized))

    with sqlite3.connect(database) as connection:
        for extraction in extractions:
            endpoint = extraction.endpoint
            operation = extraction.operation
            connection.execute(
                "INSERT OR IGNORE INTO exact_endpoints VALUES (?, ?, ?, ?, ?, ?)",
                (str(endpoint.id), str(endpoint.project_id), endpoint.scheme, endpoint.host, endpoint.port, endpoint.path),
            )
            connection.execute(
                "INSERT OR IGNORE INTO http_operations VALUES (?, ?, ?)",
                (str(operation.id), str(operation.endpoint_id), operation.method),
            )
            connection.execute(
                "INSERT INTO operation_observations VALUES (?, ?)",
                (str(extraction.observation_id), str(operation.id)),
            )
            connection.executemany(
                "INSERT INTO method_advertisements VALUES (?, ?, ?, ?)",
                [(str(item.endpoint_id), item.source.value, item.advertised_method, str(item.observation_id)) for item in extraction.advertisements],
            )
            connection.executemany(
                "INSERT INTO json_documents VALUES (?, ?, ?, ?)",
                [(str(item.observation_id), item.direction.value, item.parse_status.value, item.root_type.value if item.root_type else None) for item in extraction.json_documents],
            )
            connection.executemany(
                "INSERT INTO json_field_observations VALUES (?, ?, ?, ?, ?)",
                [(str(item.observation_id), item.direction.value, json.dumps(item.path, separators=(",", ":")), item.json_type.value, int(item.duplicate_key_observed)) for item in extraction.json_fields],
            )
            connection.executemany(
                "INSERT INTO form_documents VALUES (?, ?, ?)",
                [(str(item.observation_id), item.direction.value, item.parse_status.value) for item in extraction.form_documents],
            )
            connection.executemany(
                "INSERT INTO form_field_observations VALUES (?, ?, ?, ?, ?)",
                [(str(item.observation_id), item.direction.value, item.field_name, item.occurrence_count, item.assigned_occurrence_count) for item in extraction.form_fields],
            )
            connection.execute(
                "INSERT INTO structural_processing VALUES (?, 4)",
                (str(extraction.observation_id),),
            )

    preserved_tables = (
        "observations",
        "normalized_exchanges",
        "exact_endpoints",
        "http_operations",
        "operation_observations",
        "method_advertisements",
        "json_documents",
        "json_field_observations",
        "form_documents",
        "form_field_observations",
    )
    with sqlite3.connect(database) as connection:
        preserved = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in preserved_tables
        }

    repository.initialize()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        for table, rows in preserved.items():
            assert connection.execute(f"SELECT * FROM {table}").fetchall() == rows
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'multipart_%'"
            )
        } == {"multipart_documents", "multipart_parts", "multipart_part_names"}

    first = process_structure_pending(repository)
    second = process_structure_pending(repository)
    assert (first.processed, first.pending) == (2, 0)
    assert second.processed == 0
    with sqlite3.connect(database) as connection:
        for table, rows in preserved.items():
            assert connection.execute(f"SELECT * FROM {table}").fetchall() == rows
        assert connection.execute("SELECT COUNT(*) FROM multipart_documents").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM multipart_parts").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM multipart_part_names").fetchone() == (1,)
        assert connection.execute(
            "SELECT structural_version, COUNT(*) FROM structural_processing GROUP BY structural_version"
        ).fetchall() == [(STRUCTURAL_VERSION, 2)]
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='observations'"
            )
        } == {"observations_immutable_update", "observations_immutable_delete"}
