"""Small, explicit SQLite persistence layer."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID

from refair.assets.identity import Asset, content_sha256, normalize_response_body
from refair.models.evidence import Hypothesis, Observation, ObservationProvenance

SCHEMA = """
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


class SQLiteRepository:
    """Repository for immutable evidence, hypotheses, and content-addressed assets."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(SCHEMA)

    def add_observation(self, observation: Observation) -> Observation:
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
                "SELECT id FROM observations ORDER BY observed_at, id"
            ).fetchall()
        observations = tuple(self.get_observation(UUID(row["id"])) for row in rows)
        return tuple(observation for observation in observations if observation is not None)

    def add_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
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
