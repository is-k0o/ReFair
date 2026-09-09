from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import UUID

import pytest

from refair.models import Observation, ObservationProvenance
from refair.normalization import normalize_observation
from refair.storage import (
    CURRENT_SCHEMA_VERSION,
    SQLiteRepository,
    UnsupportedSchemaVersionError,
)
from refair.storage.sqlite import LEGACY_SCHEMA, MIGRATIONS


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
    assert version == CURRENT_SCHEMA_VERSION == 2
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
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
