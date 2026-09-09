"""Small, explicit SQLite persistence layer."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from refair.assets.identity import Asset, content_sha256, normalize_response_body
from refair.models.evidence import Hypothesis, Observation, ObservationProvenance
from refair.models.normalized import BodyKind, NormalizedExchange
from refair.models.structure import (
    ActorOutcome,
    ExactEndpoint,
    FormDirection,
    FormParseStatus,
    HttpOperation,
    JsonArrayItem,
    JsonDirection,
    JsonParseStatus,
    JsonPath,
    JsonType,
    MethodAdvertisement,
    MethodAdvertisementSource,
    MultipartDirection,
    MultipartParseStatus,
    MultipartPartName,
    MultipartPartObservation,
    OperationFormDocumentOutcome,
    OperationFormField,
    OperationJsonDocumentOutcome,
    OperationJsonField,
    OperationMultipartDocumentOutcome,
    OperationQueryShape,
    RequestRepresentation,
    ResponseRepresentation,
)
from refair.structure import StructuralExtraction

CURRENT_SCHEMA_VERSION = 6

LEGACY_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK (
        provenance IN ('BROWSER', 'HUMAN_REPEATER', 'AGENT_REPLAY')
    ),
    actor_id TEXT,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    response_status INTEGER,
    raw_request BLOB NOT NULL,
    raw_response BLOB
);

CREATE TRIGGER IF NOT EXISTS observations_immutable_update
BEFORE UPDATE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations are immutable');
END;

CREATE TRIGGER IF NOT EXISTS observations_immutable_delete
BEFORE DELETE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations are immutable');
END;

CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('PROPOSED', 'INFERRED', 'TESTED', 'SUPPORTED', 'REFUTED', 'INCONCLUSIVE')
    )
);

CREATE TABLE IF NOT EXISTS hypothesis_evidence (
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
    observation_id TEXT NOT NULL REFERENCES observations(id),
    relationship TEXT NOT NULL CHECK (relationship IN ('SUPPORTS', 'CONTRADICTS')),
    PRIMARY KEY (hypothesis_id, observation_id, relationship)
);

CREATE TABLE IF NOT EXISTS assets (
    content_hash TEXT PRIMARY KEY CHECK(length(content_hash) = 64),
    body BLOB NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS assets_content_hash_idx ON assets(content_hash);

CREATE TRIGGER IF NOT EXISTS assets_immutable_update
BEFORE UPDATE ON assets
BEGIN
    SELECT RAISE(ABORT, 'asset content is immutable');
END;

CREATE TRIGGER IF NOT EXISTS assets_immutable_delete
BEFORE DELETE ON assets
BEGIN
    SELECT RAISE(ABORT, 'asset content is immutable');
END;

CREATE TABLE IF NOT EXISTS asset_urls (
    content_hash TEXT NOT NULL REFERENCES assets(content_hash),
    url TEXT NOT NULL,
    PRIMARY KEY (content_hash, url)
);

CREATE INDEX IF NOT EXISTS asset_urls_url_idx ON asset_urls(url);
"""

MIGRATIONS = {
    1: """
CREATE TABLE normalized_exchanges (
    observation_id TEXT PRIMARY KEY REFERENCES observations(id),
    normalizer_version INTEGER NOT NULL CHECK(normalizer_version >= 1),
    scheme TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER CHECK(port IS NULL OR port BETWEEN 1 AND 65535),
    path TEXT NOT NULL,
    query_parameter_names TEXT NOT NULL,
    raw_query_sha256 TEXT CHECK(
        raw_query_sha256 IS NULL OR length(raw_query_sha256) = 64
    ),
    request_content_type TEXT,
    request_body_kind TEXT NOT NULL CHECK(
        request_body_kind IN ('EMPTY', 'JSON', 'FORM', 'MULTIPART', 'TEXT', 'OTHER')
    ),
    request_body_size INTEGER NOT NULL CHECK(request_body_size >= 0),
    request_body_sha256 TEXT CHECK(
        request_body_sha256 IS NULL OR length(request_body_sha256) = 64
    ),
    response_content_type TEXT,
    response_body_kind TEXT NOT NULL CHECK(
        response_body_kind IN ('EMPTY', 'JSON', 'FORM', 'MULTIPART', 'TEXT', 'OTHER')
    ),
    response_body_size INTEGER NOT NULL CHECK(response_body_size >= 0),
    response_body_sha256 TEXT CHECK(
        response_body_sha256 IS NULL OR length(response_body_sha256) = 64
    ),
    warnings TEXT NOT NULL
);
""",
    2: """
CREATE TABLE exact_endpoints (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scheme TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER CHECK(port IS NULL OR port BETWEEN 1 AND 65535),
    path TEXT NOT NULL
);

CREATE UNIQUE INDEX exact_endpoints_identity_idx ON exact_endpoints (
    project_id, scheme, host, COALESCE(port, 0), path
);

CREATE TABLE http_operations (
    id TEXT PRIMARY KEY,
    endpoint_id TEXT NOT NULL REFERENCES exact_endpoints(id),
    method TEXT NOT NULL,
    UNIQUE(endpoint_id, method)
);

CREATE TABLE operation_observations (
    observation_id TEXT PRIMARY KEY REFERENCES observations(id),
    operation_id TEXT NOT NULL REFERENCES http_operations(id)
);

CREATE INDEX operation_observations_operation_idx
ON operation_observations(operation_id);

CREATE TABLE method_advertisements (
    endpoint_id TEXT NOT NULL REFERENCES exact_endpoints(id),
    source TEXT NOT NULL CHECK(source IN ('ALLOW_HEADER', 'CORS_ALLOW_METHODS')),
    advertised_method TEXT NOT NULL CHECK(length(advertised_method) > 0),
    observation_id TEXT NOT NULL REFERENCES observations(id),
    PRIMARY KEY (endpoint_id, source, advertised_method, observation_id)
);

CREATE INDEX method_advertisements_observation_idx
ON method_advertisements(observation_id);

CREATE TABLE structural_processing (
    observation_id TEXT PRIMARY KEY REFERENCES observations(id),
    structural_version INTEGER NOT NULL CHECK(structural_version >= 1)
);
""",
    3: """
CREATE TABLE json_documents (
    observation_id TEXT NOT NULL REFERENCES observations(id),
    direction TEXT NOT NULL CHECK(direction IN ('REQUEST', 'RESPONSE')),
    parse_status TEXT NOT NULL CHECK(
        parse_status IN ('PARSED', 'MALFORMED', 'SKIPPED_TOO_LARGE', 'LIMIT_EXCEEDED')
    ),
    root_type TEXT CHECK(
        root_type IS NULL OR
        root_type IN ('OBJECT', 'ARRAY', 'STRING', 'NUMBER', 'BOOLEAN', 'NULL')
    ),
    CHECK(
        (parse_status = 'PARSED' AND root_type IS NOT NULL) OR
        (parse_status != 'PARSED' AND root_type IS NULL)
    ),
    PRIMARY KEY (observation_id, direction)
);

CREATE TABLE json_field_observations (
    observation_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    path TEXT NOT NULL,
    json_type TEXT NOT NULL CHECK(
        json_type IN ('OBJECT', 'ARRAY', 'STRING', 'NUMBER', 'BOOLEAN', 'NULL')
    ),
    duplicate_key_observed INTEGER NOT NULL CHECK(
        duplicate_key_observed IN (0, 1)
    ),
    PRIMARY KEY (observation_id, direction, path, json_type),
    FOREIGN KEY (observation_id, direction)
        REFERENCES json_documents(observation_id, direction) ON DELETE CASCADE
);

CREATE INDEX json_field_observations_operation_join_idx
ON json_field_observations(observation_id, direction);
""",
    4: """
ALTER TABLE json_field_observations
RENAME TO json_field_observations_schema_3;

ALTER TABLE json_documents
RENAME TO json_documents_schema_3;

CREATE TABLE json_documents (
    observation_id TEXT NOT NULL REFERENCES observations(id),
    direction TEXT NOT NULL CHECK(direction IN ('REQUEST', 'RESPONSE')),
    parse_status TEXT NOT NULL CHECK(
        parse_status IN (
            'PARSED', 'MALFORMED', 'SKIPPED_TOO_LARGE', 'LIMIT_EXCEEDED',
            'UNSUPPORTED_CONTENT_ENCODING', 'CONTENT_DECODING_FAILED'
        )
    ),
    root_type TEXT CHECK(
        root_type IS NULL OR
        root_type IN ('OBJECT', 'ARRAY', 'STRING', 'NUMBER', 'BOOLEAN', 'NULL')
    ),
    CHECK(
        (parse_status = 'PARSED' AND root_type IS NOT NULL) OR
        (parse_status != 'PARSED' AND root_type IS NULL)
    ),
    PRIMARY KEY (observation_id, direction)
);

CREATE TABLE json_field_observations (
    observation_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    path TEXT NOT NULL,
    json_type TEXT NOT NULL CHECK(
        json_type IN ('OBJECT', 'ARRAY', 'STRING', 'NUMBER', 'BOOLEAN', 'NULL')
    ),
    duplicate_key_observed INTEGER NOT NULL CHECK(
        duplicate_key_observed IN (0, 1)
    ),
    PRIMARY KEY (observation_id, direction, path, json_type),
    FOREIGN KEY (observation_id, direction)
        REFERENCES json_documents(observation_id, direction) ON DELETE CASCADE
);

INSERT INTO json_documents (
    observation_id, direction, parse_status, root_type
)
SELECT observation_id, direction, parse_status, root_type
FROM json_documents_schema_3;

INSERT INTO json_field_observations (
    observation_id, direction, path, json_type, duplicate_key_observed
)
SELECT observation_id, direction, path, json_type, duplicate_key_observed
FROM json_field_observations_schema_3;

DROP TABLE json_field_observations_schema_3;
DROP TABLE json_documents_schema_3;

CREATE INDEX json_field_observations_operation_join_idx
ON json_field_observations(observation_id, direction);
""",
    5: """
CREATE TABLE form_documents (
    observation_id TEXT NOT NULL REFERENCES observations(id),
    direction TEXT NOT NULL CHECK(direction IN ('REQUEST', 'RESPONSE')),
    parse_status TEXT NOT NULL CHECK(
        parse_status IN (
            'PARSED', 'SKIPPED_TOO_LARGE', 'LIMIT_EXCEEDED',
            'UNSUPPORTED_CONTENT_ENCODING', 'CONTENT_DECODING_FAILED'
        )
    ),
    PRIMARY KEY (observation_id, direction)
);

CREATE TABLE form_field_observations (
    observation_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    field_name BLOB NOT NULL CHECK(typeof(field_name) = 'blob'),
    occurrence_count INTEGER NOT NULL CHECK(occurrence_count >= 1),
    assigned_occurrence_count INTEGER NOT NULL CHECK(
        assigned_occurrence_count >= 0 AND
        assigned_occurrence_count <= occurrence_count
    ),
    PRIMARY KEY (observation_id, direction, field_name),
    FOREIGN KEY (observation_id, direction)
        REFERENCES form_documents(observation_id, direction) ON DELETE CASCADE
);
""",
    6: """
CREATE TABLE multipart_documents (
    observation_id TEXT NOT NULL REFERENCES observations(id),
    direction TEXT NOT NULL CHECK(direction IN ('REQUEST', 'RESPONSE')),
    parse_status TEXT NOT NULL CHECK(
        parse_status IN (
            'PARSED', 'MALFORMED', 'SKIPPED_TOO_LARGE', 'LIMIT_EXCEEDED',
            'UNSUPPORTED_CONTENT_ENCODING', 'CONTENT_DECODING_FAILED'
        )
    ),
    PRIMARY KEY (observation_id, direction)
);

CREATE TABLE multipart_parts (
    observation_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    part_index INTEGER NOT NULL CHECK(part_index >= 0),
    disposition_type TEXT,
    content_type TEXT,
    name_parameter_count INTEGER NOT NULL CHECK(name_parameter_count >= 0),
    filename_parameter_count INTEGER NOT NULL CHECK(filename_parameter_count >= 0),
    filename_empty_count INTEGER NOT NULL CHECK(filename_empty_count >= 0),
    filename_nonempty_count INTEGER NOT NULL CHECK(filename_nonempty_count >= 0),
    CHECK(filename_parameter_count = filename_empty_count + filename_nonempty_count),
    PRIMARY KEY (observation_id, direction, part_index),
    FOREIGN KEY (observation_id, direction)
        REFERENCES multipart_documents(observation_id, direction) ON DELETE CASCADE
);

CREATE TABLE multipart_part_names (
    observation_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    part_index INTEGER NOT NULL,
    name BLOB NOT NULL CHECK(typeof(name) = 'blob'),
    occurrence_count INTEGER NOT NULL CHECK(occurrence_count >= 1),
    PRIMARY KEY (observation_id, direction, part_index, name),
    FOREIGN KEY (observation_id, direction, part_index)
        REFERENCES multipart_parts(observation_id, direction, part_index)
        ON DELETE CASCADE
);
""",
}


class UnsupportedSchemaVersionError(RuntimeError):
    """Raised when a database was created by newer ReFair code."""


@dataclass(frozen=True)
class ObservationSummary:
    total: int
    by_actor: dict[str, int]
    by_provenance: dict[str, int]


@dataclass(frozen=True)
class ObservationMetadata:
    id: UUID
    project_id: UUID
    observed_at: datetime
    provenance: ObservationProvenance
    actor_id: str | None
    method: str
    url: str
    response_status: int | None
    request_size: int
    response_size: int


def _serialize_json_path(path: JsonPath) -> str:
    return json.dumps(
        [None if segment is JsonArrayItem.ITEM else segment for segment in path],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _deserialize_json_path(value: str) -> JsonPath:
    segments = json.loads(value)
    return tuple(
        JsonArrayItem.ITEM if segment is None else segment for segment in segments
    )


class SQLiteRepository:
    """Repository for immutable evidence, hypotheses, and content-addressed assets."""

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            database = f"{self.path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(database, uri=True)
        else:
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > CURRENT_SCHEMA_VERSION:
                raise UnsupportedSchemaVersionError(
                    f"database schema version {version} is newer than supported "
                    f"version {CURRENT_SCHEMA_VERSION}"
                )
            yield connection
            if not self.read_only:
                connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self._require_writable()
        with self._connection() as connection:
            current_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            scripts = ["BEGIN IMMEDIATE;"]
            if current_version == 0:
                scripts.append(LEGACY_SCHEMA)
            for version in range(current_version + 1, CURRENT_SCHEMA_VERSION + 1):
                scripts.append(MIGRATIONS[version])
                scripts.append(f"PRAGMA user_version = {version};")
            scripts.append("COMMIT;")
            if len(scripts) > 2:
                connection.executescript("\n".join(scripts))

    def add_observation(self, observation: Observation) -> Observation:
        self._require_writable()
        with self._connection() as connection:
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
        return observation

    def get_observation(self, observation_id: UUID) -> Observation | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM observations WHERE id = ?", (str(observation_id),)
            ).fetchone()
        if row is None:
            return None
        return self._observation_from_row(row)

    def observation_summary(self) -> ObservationSummary:
        with self._connection() as connection:
            total_row = connection.execute(
                "SELECT COUNT(*) AS count FROM observations"
            ).fetchone()
            actor_rows = connection.execute(
                """
                SELECT COALESCE(actor_id, 'UNKNOWN') AS category, COUNT(*) AS count
                FROM observations
                GROUP BY actor_id
                ORDER BY category
                """
            ).fetchall()
            provenance_rows = connection.execute(
                """
                SELECT provenance AS category, COUNT(*) AS count
                FROM observations
                GROUP BY provenance
                ORDER BY category
                """
            ).fetchall()
        assert total_row is not None
        return ObservationSummary(
            total=int(total_row["count"]),
            by_actor={row["category"]: int(row["count"]) for row in actor_rows},
            by_provenance={
                row["category"]: int(row["count"]) for row in provenance_rows
            },
        )

    def recent_observation_metadata(
        self,
        *,
        limit: int,
        actor_id: str | None = None,
        provenance: ObservationProvenance | None = None,
        method: str | None = None,
        response_status: int | None = None,
    ) -> tuple[ObservationMetadata, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        clauses: list[str] = []
        parameters: list[str | int] = []
        for column, value in (
            ("actor_id", actor_id),
            ("provenance", provenance.value if provenance is not None else None),
            ("method", method),
            ("response_status", response_status),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, project_id, observed_at, provenance, actor_id, method, "
                "url, response_status, LENGTH(raw_request) AS request_size, "
                f"COALESCE(LENGTH(raw_response), 0) AS response_size FROM observations{where} "
                "ORDER BY observed_at DESC, id DESC LIMIT ?",
                parameters,
            ).fetchall()
        return tuple(
            ObservationMetadata(
                id=UUID(row["id"]),
                project_id=UUID(row["project_id"]),
                observed_at=datetime.fromisoformat(row["observed_at"]),
                provenance=ObservationProvenance(row["provenance"]),
                actor_id=row["actor_id"],
                method=row["method"],
                url=row["url"],
                response_status=row["response_status"],
                request_size=int(row["request_size"]),
                response_size=int(row["response_size"]),
            )
            for row in rows
        )

    @staticmethod
    def _observation_from_row(row: sqlite3.Row) -> Observation:
        return Observation(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            provenance=ObservationProvenance(row["provenance"]),
            actor_id=row["actor_id"],
            method=row["method"],
            url=row["url"],
            response_status=row["response_status"],
            raw_request=bytes(row["raw_request"]),
            raw_response=(bytes(row["raw_response"]) if row["raw_response"] is not None else None),
        )

    def count_observations(self) -> int:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM observations").fetchone()
        assert row is not None
        return int(row["count"])

    def list_observations(self) -> tuple[Observation, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM observations ORDER BY observed_at, id"
            ).fetchall()
        return tuple(self._observation_from_row(row) for row in rows)

    def add_normalized_exchange(
        self, exchange: NormalizedExchange
    ) -> NormalizedExchange:
        self._require_writable()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO normalized_exchanges (
                    observation_id, normalizer_version, scheme, host, port, path,
                    query_parameter_names, raw_query_sha256, request_content_type,
                    request_body_kind, request_body_size, request_body_sha256,
                    response_content_type, response_body_kind, response_body_size,
                    response_body_sha256, warnings
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    normalizer_version = excluded.normalizer_version,
                    scheme = excluded.scheme,
                    host = excluded.host,
                    port = excluded.port,
                    path = excluded.path,
                    query_parameter_names = excluded.query_parameter_names,
                    raw_query_sha256 = excluded.raw_query_sha256,
                    request_content_type = excluded.request_content_type,
                    request_body_kind = excluded.request_body_kind,
                    request_body_size = excluded.request_body_size,
                    request_body_sha256 = excluded.request_body_sha256,
                    response_content_type = excluded.response_content_type,
                    response_body_kind = excluded.response_body_kind,
                    response_body_size = excluded.response_body_size,
                    response_body_sha256 = excluded.response_body_sha256,
                    warnings = excluded.warnings
                WHERE normalized_exchanges.normalizer_version
                    < excluded.normalizer_version
                """,
                (
                    str(exchange.observation_id),
                    exchange.normalizer_version,
                    exchange.scheme,
                    exchange.host,
                    exchange.port,
                    exchange.path,
                    json.dumps(exchange.query_parameter_names, separators=(",", ":")),
                    exchange.raw_query_sha256,
                    exchange.request_content_type,
                    exchange.request_body_kind.value,
                    exchange.request_body_size,
                    exchange.request_body_sha256,
                    exchange.response_content_type,
                    exchange.response_body_kind.value,
                    exchange.response_body_size,
                    exchange.response_body_sha256,
                    json.dumps(exchange.warnings, separators=(",", ":")),
                ),
            )
            row = connection.execute(
                "SELECT * FROM normalized_exchanges WHERE observation_id = ?",
                (str(exchange.observation_id),),
            ).fetchone()
        assert row is not None
        return self._normalized_exchange_from_row(row)

    def get_normalized_exchange(
        self, observation_id: UUID
    ) -> NormalizedExchange | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM normalized_exchanges WHERE observation_id = ?",
                (str(observation_id),),
            ).fetchone()
        if row is None:
            return None
        return self._normalized_exchange_from_row(row)

    @staticmethod
    def _normalized_exchange_from_row(row: sqlite3.Row) -> NormalizedExchange:
        return NormalizedExchange(
            observation_id=UUID(row["observation_id"]),
            normalizer_version=int(row["normalizer_version"]),
            scheme=row["scheme"],
            host=row["host"],
            port=row["port"],
            path=row["path"],
            query_parameter_names=tuple(json.loads(row["query_parameter_names"])),
            raw_query_sha256=row["raw_query_sha256"],
            request_content_type=row["request_content_type"],
            request_body_kind=BodyKind(row["request_body_kind"]),
            request_body_size=int(row["request_body_size"]),
            request_body_sha256=row["request_body_sha256"],
            response_content_type=row["response_content_type"],
            response_body_kind=BodyKind(row["response_body_kind"]),
            response_body_size=int(row["response_body_size"]),
            response_body_sha256=row["response_body_sha256"],
            warnings=tuple(json.loads(row["warnings"])),
        )

    def count_normalized_exchanges(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM normalized_exchanges"
            ).fetchone()
        assert row is not None
        return int(row["count"])

    def list_pending_observations(
        self, *, target_normalizer_version: int, limit: int | None = None
    ) -> tuple[Observation, ...]:
        if target_normalizer_version < 1:
            raise ValueError("target normalizer version must be positive")
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        parameters = (
            (target_normalizer_version,)
            if limit is None
            else (target_normalizer_version, limit)
        )
        limit_clause = "" if limit is None else " LIMIT ?"
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT observations.* FROM observations "
                "LEFT JOIN normalized_exchanges ON "
                "normalized_exchanges.observation_id = observations.id "
                "WHERE normalized_exchanges.observation_id IS NULL "
                "OR normalized_exchanges.normalizer_version < ? "
                f"ORDER BY observations.observed_at, observations.id{limit_clause}",
                parameters,
            ).fetchall()
        return tuple(self._observation_from_row(row) for row in rows)

    def count_pending_observations(self, *, target_normalizer_version: int) -> int:
        if target_normalizer_version < 1:
            raise ValueError("target normalizer version must be positive")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM observations "
                "LEFT JOIN normalized_exchanges ON "
                "normalized_exchanges.observation_id = observations.id "
                "WHERE normalized_exchanges.observation_id IS NULL "
                "OR normalized_exchanges.normalizer_version < ?",
                (target_normalizer_version,),
            ).fetchone()
        assert row is not None
        return int(row["count"])

    def list_pending_structural_inputs(
        self, *, target_structural_version: int, limit: int | None = None
    ) -> tuple[tuple[Observation, NormalizedExchange], ...]:
        if target_structural_version < 1:
            raise ValueError("target structural version must be positive")
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        parameters = (
            (target_structural_version,)
            if limit is None
            else (target_structural_version, limit)
        )
        limit_clause = "" if limit is None else " LIMIT ?"
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT observations.*, normalized_exchanges.* "
                "FROM observations JOIN normalized_exchanges ON "
                "normalized_exchanges.observation_id = observations.id "
                "LEFT JOIN structural_processing ON "
                "structural_processing.observation_id = observations.id "
                "WHERE structural_processing.observation_id IS NULL "
                "OR structural_processing.structural_version < ? "
                f"ORDER BY observations.observed_at, observations.id{limit_clause}",
                parameters,
            ).fetchall()
        return tuple(
            (self._observation_from_row(row), self._normalized_exchange_from_row(row))
            for row in rows
        )

    def count_pending_structural_observations(
        self, *, target_structural_version: int
    ) -> int:
        if target_structural_version < 1:
            raise ValueError("target structural version must be positive")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM observations "
                "JOIN normalized_exchanges ON "
                "normalized_exchanges.observation_id = observations.id "
                "LEFT JOIN structural_processing ON "
                "structural_processing.observation_id = observations.id "
                "WHERE structural_processing.observation_id IS NULL "
                "OR structural_processing.structural_version < ?",
                (target_structural_version,),
            ).fetchone()
        assert row is not None
        return int(row["count"])

    def add_structural_extraction(self, extraction: StructuralExtraction) -> bool:
        """Persist newer derived structure atomically; never downgrade it."""

        self._require_writable()
        if extraction.structural_version < 1:
            raise ValueError("structural version must be positive")
        if extraction.operation.endpoint_id != extraction.endpoint.id:
            raise ValueError("operation does not belong to endpoint")
        if any(
            advertisement.endpoint_id != extraction.endpoint.id
            or advertisement.observation_id != extraction.observation_id
            for advertisement in extraction.advertisements
        ):
            raise ValueError("advertisement does not belong to extraction")
        document_keys = {
            (document.observation_id, document.direction)
            for document in extraction.json_documents
        }
        if any(
            document.observation_id != extraction.observation_id
            for document in extraction.json_documents
        ):
            raise ValueError("JSON document does not belong to extraction")
        if any(
            field.observation_id != extraction.observation_id
            or (field.observation_id, field.direction) not in document_keys
            for field in extraction.json_fields
        ):
            raise ValueError("JSON field does not belong to extraction document")
        form_document_keys = {
            (document.observation_id, document.direction)
            for document in extraction.form_documents
        }
        if any(
            document.observation_id != extraction.observation_id
            for document in extraction.form_documents
        ):
            raise ValueError("FORM document does not belong to extraction")
        if any(
            field.observation_id != extraction.observation_id
            or (field.observation_id, field.direction) not in form_document_keys
            for field in extraction.form_fields
        ):
            raise ValueError("FORM field does not belong to extraction document")
        multipart_document_keys = {
            (document.observation_id, document.direction)
            for document in extraction.multipart_documents
        }
        if any(
            document.observation_id != extraction.observation_id
            for document in extraction.multipart_documents
        ):
            raise ValueError("multipart document does not belong to extraction")
        multipart_part_keys = [
            (part.observation_id, part.direction, part.part_index)
            for part in extraction.multipart_parts
        ]
        if len(multipart_part_keys) != len(set(multipart_part_keys)):
            raise ValueError("duplicate multipart part occurrence")
        if any(
            part.observation_id != extraction.observation_id
            or (part.observation_id, part.direction) not in multipart_document_keys
            for part in extraction.multipart_parts
        ):
            raise ValueError("multipart part does not belong to extraction document")

        with self._connection() as connection:
            existing = connection.execute(
                "SELECT structural_version FROM structural_processing "
                "WHERE observation_id = ?",
                (str(extraction.observation_id),),
            ).fetchone()
            if (
                existing is not None
                and int(existing["structural_version"])
                >= extraction.structural_version
            ):
                return False

            endpoint = extraction.endpoint
            connection.execute(
                """
                INSERT INTO exact_endpoints (
                    id, project_id, scheme, host, port, path
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    str(endpoint.id),
                    str(endpoint.project_id),
                    endpoint.scheme,
                    endpoint.host,
                    endpoint.port,
                    endpoint.path,
                ),
            )
            operation = extraction.operation
            connection.execute(
                """
                INSERT INTO http_operations (id, endpoint_id, method)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (str(operation.id), str(operation.endpoint_id), operation.method),
            )
            connection.execute(
                """
                INSERT INTO operation_observations (observation_id, operation_id)
                VALUES (?, ?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    operation_id = excluded.operation_id
                """,
                (str(extraction.observation_id), str(operation.id)),
            )
            connection.execute(
                "DELETE FROM method_advertisements WHERE observation_id = ?",
                (str(extraction.observation_id),),
            )
            connection.executemany(
                """
                INSERT INTO method_advertisements (
                    endpoint_id, source, advertised_method, observation_id
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
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
                "DELETE FROM json_field_observations WHERE observation_id = ?",
                (str(extraction.observation_id),),
            )
            connection.execute(
                "DELETE FROM json_documents WHERE observation_id = ?",
                (str(extraction.observation_id),),
            )
            connection.executemany(
                """
                INSERT INTO json_documents (
                    observation_id, direction, parse_status, root_type
                ) VALUES (?, ?, ?, ?)
                """,
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
                """
                INSERT INTO json_field_observations (
                    observation_id, direction, path, json_type,
                    duplicate_key_observed
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(item.observation_id),
                        item.direction.value,
                        _serialize_json_path(item.path),
                        item.json_type.value,
                        int(item.duplicate_key_observed),
                    )
                    for item in extraction.json_fields
                ],
            )
            connection.execute(
                "DELETE FROM form_field_observations WHERE observation_id = ?",
                (str(extraction.observation_id),),
            )
            connection.execute(
                "DELETE FROM form_documents WHERE observation_id = ?",
                (str(extraction.observation_id),),
            )
            connection.executemany(
                """
                INSERT INTO form_documents (
                    observation_id, direction, parse_status
                ) VALUES (?, ?, ?)
                """,
                [
                    (
                        str(item.observation_id),
                        item.direction.value,
                        item.parse_status.value,
                    )
                    for item in extraction.form_documents
                ],
            )
            connection.executemany(
                """
                INSERT INTO form_field_observations (
                    observation_id, direction, field_name, occurrence_count,
                    assigned_occurrence_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(item.observation_id),
                        item.direction.value,
                        item.field_name,
                        item.occurrence_count,
                        item.assigned_occurrence_count,
                    )
                    for item in extraction.form_fields
                ],
            )
            connection.execute(
                "DELETE FROM multipart_part_names WHERE observation_id = ?",
                (str(extraction.observation_id),),
            )
            connection.execute(
                "DELETE FROM multipart_parts WHERE observation_id = ?",
                (str(extraction.observation_id),),
            )
            connection.execute(
                "DELETE FROM multipart_documents WHERE observation_id = ?",
                (str(extraction.observation_id),),
            )
            connection.executemany(
                """
                INSERT INTO multipart_documents (
                    observation_id, direction, parse_status
                ) VALUES (?, ?, ?)
                """,
                [
                    (
                        str(item.observation_id),
                        item.direction.value,
                        item.parse_status.value,
                    )
                    for item in extraction.multipart_documents
                ],
            )
            connection.executemany(
                """
                INSERT INTO multipart_parts (
                    observation_id, direction, part_index, disposition_type,
                    content_type, name_parameter_count,
                    filename_parameter_count, filename_empty_count,
                    filename_nonempty_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(item.observation_id),
                        item.direction.value,
                        item.part_index,
                        item.disposition_type,
                        item.content_type,
                        item.name_parameter_count,
                        item.filename_parameter_count,
                        item.filename_empty_count,
                        item.filename_nonempty_count,
                    )
                    for item in extraction.multipart_parts
                ],
            )
            connection.executemany(
                """
                INSERT INTO multipart_part_names (
                    observation_id, direction, part_index, name,
                    occurrence_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(part.observation_id),
                        part.direction.value,
                        part.part_index,
                        name.name,
                        name.occurrence_count,
                    )
                    for part in extraction.multipart_parts
                    for name in part.names
                ],
            )
            connection.execute(
                """
                INSERT INTO structural_processing (
                    observation_id, structural_version
                ) VALUES (?, ?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    structural_version = excluded.structural_version
                WHERE structural_processing.structural_version
                    < excluded.structural_version
                """,
                (str(extraction.observation_id), extraction.structural_version),
            )
        return True

    @staticmethod
    def _exact_endpoint_from_row(row: sqlite3.Row) -> ExactEndpoint:
        return ExactEndpoint(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            scheme=row["scheme"],
            host=row["host"],
            port=row["port"],
            path=row["path"],
        )

    def list_exact_endpoints(self) -> tuple[ExactEndpoint, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM exact_endpoints ORDER BY "
                "project_id, scheme, host, COALESCE(port, 0), path, id"
            ).fetchall()
        return tuple(self._exact_endpoint_from_row(row) for row in rows)

    @staticmethod
    def _http_operation_from_row(row: sqlite3.Row) -> HttpOperation:
        return HttpOperation(
            id=UUID(row["id"]),
            endpoint_id=UUID(row["endpoint_id"]),
            method=row["method"],
        )

    def list_http_operations(
        self, *, endpoint_id: UUID | None = None
    ) -> tuple[HttpOperation, ...]:
        where = "" if endpoint_id is None else " WHERE endpoint_id = ?"
        parameters = () if endpoint_id is None else (str(endpoint_id),)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM http_operations{where} ORDER BY endpoint_id, method, id",
                parameters,
            ).fetchall()
        return tuple(self._http_operation_from_row(row) for row in rows)

    def list_method_advertisements(
        self, *, endpoint_id: UUID
    ) -> tuple[MethodAdvertisement, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM method_advertisements WHERE endpoint_id = ? "
                "ORDER BY source, advertised_method, observation_id",
                (str(endpoint_id),),
            ).fetchall()
        return tuple(
            MethodAdvertisement(
                endpoint_id=UUID(row["endpoint_id"]),
                source=MethodAdvertisementSource(row["source"]),
                advertised_method=row["advertised_method"],
                observation_id=UUID(row["observation_id"]),
            )
            for row in rows
        )

    def count_exact_endpoints(self) -> int:
        return self._table_count("exact_endpoints")

    def count_http_operations(self) -> int:
        return self._table_count("http_operations")

    def count_operation_observations(self) -> int:
        return self._table_count("operation_observations")

    def count_method_advertisements(self) -> int:
        return self._table_count("method_advertisements")

    def count_json_documents(self) -> int:
        return self._table_count("json_documents")

    def count_json_field_observations(self) -> int:
        return self._table_count("json_field_observations")

    def count_form_documents(self) -> int:
        return self._table_count("form_documents")

    def count_form_field_observations(self) -> int:
        return self._table_count("form_field_observations")

    def count_multipart_documents(self) -> int:
        return self._table_count("multipart_documents")

    def count_multipart_parts(self) -> int:
        return self._table_count("multipart_parts")

    def count_multipart_part_names(self) -> int:
        return self._table_count("multipart_part_names")

    def _table_count(self, table: str) -> int:
        allowed = {
            "exact_endpoints",
            "http_operations",
            "operation_observations",
            "method_advertisements",
            "json_documents",
            "json_field_observations",
            "form_documents",
            "form_field_observations",
            "multipart_documents",
            "multipart_parts",
            "multipart_part_names",
        }
        if table not in allowed:
            raise ValueError("unsupported structural table")
        with self._connection() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        assert row is not None
        return int(row["count"])

    def operation_query_shapes(
        self, operation_id: UUID
    ) -> tuple[OperationQueryShape, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT normalized_exchanges.raw_query_sha256 IS NOT NULL
                           AS query_present,
                       normalized_exchanges.query_parameter_names,
                       COUNT(*) AS observation_count
                FROM operation_observations
                JOIN normalized_exchanges ON
                    normalized_exchanges.observation_id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                GROUP BY query_present,
                         normalized_exchanges.query_parameter_names
                ORDER BY query_present,
                         normalized_exchanges.query_parameter_names
                """,
                (str(operation_id),),
            ).fetchall()
        return tuple(
            OperationQueryShape(
                query_present=bool(row["query_present"]),
                query_parameter_names=tuple(
                    json.loads(row["query_parameter_names"])
                ),
                observation_count=int(row["observation_count"]),
            )
            for row in rows
        )

    def operation_request_representations(
        self, operation_id: UUID
    ) -> tuple[RequestRepresentation, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT normalized_exchanges.request_content_type AS content_type,
                       normalized_exchanges.request_body_kind AS body_kind,
                       COUNT(*) AS observation_count
                FROM operation_observations
                JOIN normalized_exchanges ON
                    normalized_exchanges.observation_id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                GROUP BY content_type, body_kind
                ORDER BY content_type, body_kind
                """,
                (str(operation_id),),
            ).fetchall()
        return tuple(
            RequestRepresentation(
                content_type=row["content_type"],
                body_kind=BodyKind(row["body_kind"]),
                observation_count=int(row["observation_count"]),
            )
            for row in rows
        )

    def operation_response_representations(
        self, operation_id: UUID
    ) -> tuple[ResponseRepresentation, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT observations.response_status,
                       normalized_exchanges.response_content_type AS content_type,
                       normalized_exchanges.response_body_kind AS body_kind,
                       COUNT(*) AS observation_count
                FROM operation_observations
                JOIN observations ON observations.id =
                    operation_observations.observation_id
                JOIN normalized_exchanges ON normalized_exchanges.observation_id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                GROUP BY observations.response_status, content_type, body_kind
                ORDER BY observations.response_status, content_type, body_kind
                """,
                (str(operation_id),),
            ).fetchall()
        return tuple(
            ResponseRepresentation(
                response_status=row["response_status"],
                content_type=row["content_type"],
                body_kind=BodyKind(row["body_kind"]),
                observation_count=int(row["observation_count"]),
            )
            for row in rows
        )

    def operation_actor_outcomes(
        self, operation_id: UUID
    ) -> tuple[ActorOutcome, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT observations.actor_id, observations.provenance,
                       observations.response_status,
                       COUNT(*) AS observation_count
                FROM operation_observations
                JOIN observations ON observations.id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                GROUP BY observations.actor_id, observations.provenance,
                         observations.response_status
                ORDER BY observations.actor_id, observations.provenance,
                         observations.response_status
                """,
                (str(operation_id),),
            ).fetchall()
        return tuple(
            ActorOutcome(
                actor_id=row["actor_id"],
                provenance=ObservationProvenance(row["provenance"]),
                response_status=row["response_status"],
                observation_count=int(row["observation_count"]),
            )
            for row in rows
        )

    def operation_json_document_outcomes(
        self, operation_id: UUID
    ) -> tuple[OperationJsonDocumentOutcome, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT json_documents.direction, json_documents.parse_status,
                       json_documents.root_type, COUNT(*) AS observation_count
                FROM operation_observations
                JOIN json_documents ON json_documents.observation_id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                GROUP BY json_documents.direction, json_documents.parse_status,
                         json_documents.root_type
                ORDER BY json_documents.direction, json_documents.parse_status,
                         json_documents.root_type
                """,
                (str(operation_id),),
            ).fetchall()
        return tuple(
            OperationJsonDocumentOutcome(
                direction=JsonDirection(row["direction"]),
                parse_status=JsonParseStatus(row["parse_status"]),
                root_type=(
                    JsonType(row["root_type"])
                    if row["root_type"] is not None
                    else None
                ),
                observation_count=int(row["observation_count"]),
            )
            for row in rows
        )

    def operation_json_fields(
        self, operation_id: UUID
    ) -> tuple[OperationJsonField, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT json_field_observations.direction,
                       json_field_observations.path,
                       json_field_observations.json_type,
                       COUNT(*) AS observation_count,
                       MAX(json_field_observations.duplicate_key_observed)
                           AS duplicate_key_observed
                FROM operation_observations
                JOIN json_field_observations ON
                    json_field_observations.observation_id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                GROUP BY json_field_observations.direction,
                         json_field_observations.path,
                         json_field_observations.json_type
                ORDER BY json_field_observations.direction,
                         json_field_observations.path,
                         json_field_observations.json_type
                """,
                (str(operation_id),),
            ).fetchall()
        return tuple(
            OperationJsonField(
                direction=JsonDirection(row["direction"]),
                path=_deserialize_json_path(row["path"]),
                json_type=JsonType(row["json_type"]),
                observation_count=int(row["observation_count"]),
                duplicate_key_observed=bool(row["duplicate_key_observed"]),
            )
            for row in rows
        )

    def operation_form_document_outcomes(
        self, operation_id: UUID
    ) -> tuple[OperationFormDocumentOutcome, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT form_documents.direction, form_documents.parse_status,
                       COUNT(*) AS observation_count
                FROM operation_observations
                JOIN form_documents ON form_documents.observation_id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                GROUP BY form_documents.direction, form_documents.parse_status
                ORDER BY form_documents.direction, form_documents.parse_status
                """,
                (str(operation_id),),
            ).fetchall()
        return tuple(
            OperationFormDocumentOutcome(
                direction=FormDirection(row["direction"]),
                parse_status=FormParseStatus(row["parse_status"]),
                observation_count=int(row["observation_count"]),
            )
            for row in rows
        )

    def operation_form_fields(
        self, operation_id: UUID
    ) -> tuple[OperationFormField, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT form_field_observations.direction,
                       form_field_observations.field_name,
                       COUNT(*) AS observation_count,
                       SUM(form_field_observations.occurrence_count)
                           AS total_occurrence_count,
                       SUM(form_field_observations.assigned_occurrence_count)
                           AS total_assigned_occurrence_count,
                       MAX(form_field_observations.occurrence_count)
                           AS max_occurrence_count
                FROM operation_observations
                JOIN form_field_observations ON
                    form_field_observations.observation_id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                GROUP BY form_field_observations.direction,
                         form_field_observations.field_name
                ORDER BY form_field_observations.direction,
                         form_field_observations.field_name
                """,
                (str(operation_id),),
            ).fetchall()
        return tuple(
            OperationFormField(
                direction=FormDirection(row["direction"]),
                field_name=bytes(row["field_name"]),
                observation_count=int(row["observation_count"]),
                total_occurrence_count=int(row["total_occurrence_count"]),
                total_assigned_occurrence_count=int(
                    row["total_assigned_occurrence_count"]
                ),
                max_occurrence_count=int(row["max_occurrence_count"]),
            )
            for row in rows
        )

    def operation_multipart_document_outcomes(
        self, operation_id: UUID
    ) -> tuple[OperationMultipartDocumentOutcome, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT multipart_documents.direction,
                       multipart_documents.parse_status,
                       COUNT(*) AS observation_count
                FROM operation_observations
                JOIN multipart_documents ON multipart_documents.observation_id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                GROUP BY multipart_documents.direction,
                         multipart_documents.parse_status
                ORDER BY multipart_documents.direction,
                         multipart_documents.parse_status
                """,
                (str(operation_id),),
            ).fetchall()
        return tuple(
            OperationMultipartDocumentOutcome(
                direction=MultipartDirection(row["direction"]),
                parse_status=MultipartParseStatus(row["parse_status"]),
                observation_count=int(row["observation_count"]),
            )
            for row in rows
        )

    def operation_multipart_parts(
        self, operation_id: UUID
    ) -> tuple[MultipartPartObservation, ...]:
        with self._connection() as connection:
            part_rows = connection.execute(
                """
                SELECT multipart_parts.*
                FROM operation_observations
                JOIN multipart_parts ON multipart_parts.observation_id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                ORDER BY multipart_parts.observation_id,
                         multipart_parts.direction,
                         multipart_parts.part_index
                """,
                (str(operation_id),),
            ).fetchall()
            name_rows = connection.execute(
                """
                SELECT multipart_part_names.*
                FROM operation_observations
                JOIN multipart_part_names ON multipart_part_names.observation_id =
                    operation_observations.observation_id
                WHERE operation_observations.operation_id = ?
                ORDER BY multipart_part_names.observation_id,
                         multipart_part_names.direction,
                         multipart_part_names.part_index,
                         multipart_part_names.name
                """,
                (str(operation_id),),
            ).fetchall()
        names: dict[tuple[str, str, int], list[MultipartPartName]] = {}
        for row in name_rows:
            key = (row["observation_id"], row["direction"], row["part_index"])
            names.setdefault(key, []).append(
                MultipartPartName(
                    name=bytes(row["name"]),
                    occurrence_count=int(row["occurrence_count"]),
                )
            )
        return tuple(
            MultipartPartObservation(
                observation_id=UUID(row["observation_id"]),
                direction=MultipartDirection(row["direction"]),
                part_index=int(row["part_index"]),
                disposition_type=row["disposition_type"],
                content_type=row["content_type"],
                name_parameter_count=int(row["name_parameter_count"]),
                filename_parameter_count=int(row["filename_parameter_count"]),
                filename_empty_count=int(row["filename_empty_count"]),
                filename_nonempty_count=int(row["filename_nonempty_count"]),
                names=tuple(
                    names.get(
                        (row["observation_id"], row["direction"], row["part_index"]),
                        (),
                    )
                ),
            )
            for row in part_rows
        )

    def add_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        self._require_writable()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO hypotheses (id, project_id, statement, status) VALUES (?, ?, ?, ?)",
                (
                    str(hypothesis.id),
                    str(hypothesis.project_id),
                    hypothesis.statement,
                    hypothesis.status.value,
                ),
            )
            relationships = [
                (hypothesis.id, evidence_id, "SUPPORTS")
                for evidence_id in hypothesis.supporting_evidence_ids
            ] + [
                (hypothesis.id, evidence_id, "CONTRADICTS")
                for evidence_id in hypothesis.contradicting_evidence_ids
            ]
            connection.executemany(
                """
                INSERT INTO hypothesis_evidence (
                    hypothesis_id, observation_id, relationship
                ) VALUES (?, ?, ?)
                """,
                [
                    (str(hypothesis_id), str(observation_id), relationship)
                    for hypothesis_id, observation_id, relationship in relationships
                ],
            )
        return hypothesis

    def add_asset(
        self, body: bytes, observed_url: str, content_encoding: str | None = None
    ) -> Asset:
        self._require_writable()
        normalized = normalize_response_body(body, content_encoding)
        content_hash = content_sha256(normalized)
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO assets (content_hash, body) VALUES (?, ?)",
                (content_hash, normalized),
            )
            connection.execute(
                "INSERT OR IGNORE INTO asset_urls (content_hash, url) VALUES (?, ?)",
                (content_hash, observed_url),
            )
            rows = connection.execute(
                "SELECT url FROM asset_urls WHERE content_hash = ? ORDER BY url",
                (content_hash,),
            ).fetchall()
        return Asset(
            content_hash=content_hash,
            body=normalized,
            observed_urls=tuple(row["url"] for row in rows),
        )

    def get_asset(self, content_hash: str) -> Asset | None:
        with self._connection() as connection:
            asset_row = connection.execute(
                "SELECT body FROM assets WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            if asset_row is None:
                return None
            url_rows = connection.execute(
                "SELECT url FROM asset_urls WHERE content_hash = ? ORDER BY url",
                (content_hash,),
            ).fetchall()
        return Asset(
            content_hash=content_hash,
            body=bytes(asset_row["body"]),
            observed_urls=tuple(row["url"] for row in url_rows),
        )

    def _require_writable(self) -> None:
        if self.read_only:
            raise RuntimeError("repository was opened read-only")
