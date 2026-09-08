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

CURRENT_SCHEMA_VERSION = 1

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
                ON CONFLICT(observation_id) DO NOTHING
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
        self, *, limit: int | None = None
    ) -> tuple[Observation, ...]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        parameters: tuple[int, ...] = () if limit is None else (limit,)
        limit_clause = "" if limit is None else " LIMIT ?"
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT observations.* FROM observations "
                "LEFT JOIN normalized_exchanges ON "
                "normalized_exchanges.observation_id = observations.id "
                "WHERE normalized_exchanges.observation_id IS NULL "
                f"ORDER BY observations.observed_at, observations.id{limit_clause}",
                parameters,
            ).fetchall()
        return tuple(self._observation_from_row(row) for row in rows)

    def count_pending_observations(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM observations "
                "LEFT JOIN normalized_exchanges ON "
                "normalized_exchanges.observation_id = observations.id "
                "WHERE normalized_exchanges.observation_id IS NULL"
            ).fetchone()
        assert row is not None
        return int(row["count"])

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
