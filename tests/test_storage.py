import sqlite3
from uuid import uuid4

import pytest

from refair.models import (
    Hypothesis,
    Observation,
    ObservationProvenance,
)
from refair.storage import SQLiteRepository


def make_observation() -> Observation:
    return Observation(
        project_id=uuid4(),
        provenance=ObservationProvenance.BROWSER,
        actor_id="actor_a",
        method="GET",
        url="https://example.test/api/users/291",
        response_status=200,
        raw_request=b"GET /api/users/291 HTTP/1.1\r\n\r\n",
        raw_response=b"HTTP/1.1 200 OK\r\n\r\n{}",
    )


def test_observation_round_trip_preserves_raw_evidence(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "refair.sqlite3")
    repository.initialize()
    original = make_observation()

    repository.add_observation(original)
    stored = repository.get_observation(original.id)

    assert stored == original
    assert stored is not None
    assert stored.raw_request == original.raw_request
    assert stored.raw_response == original.raw_response


def test_database_rejects_raw_evidence_update_and_delete(tmp_path) -> None:
    database = tmp_path / "refair.sqlite3"
    repository = SQLiteRepository(database)
    repository.initialize()
    observation = repository.add_observation(make_observation())

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="observations are immutable"):
            connection.execute(
                "UPDATE observations SET raw_response = ? WHERE id = ?",
                (b"rewritten", str(observation.id)),
            )
        with pytest.raises(sqlite3.IntegrityError, match="observations are immutable"):
            connection.execute(
                "DELETE FROM observations WHERE id = ?", (str(observation.id),)
            )


def test_interpretation_can_reference_existing_evidence(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "refair.sqlite3")
    repository.initialize()
    observation = repository.add_observation(make_observation())
    hypothesis = Hypothesis(
        project_id=observation.project_id,
        statement="The response identifies a user object",
        supporting_evidence_ids=(observation.id,),
    )

    assert repository.add_hypothesis(hypothesis) == hypothesis


def test_interpretation_cannot_reference_missing_evidence(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "refair.sqlite3")
    repository.initialize()
    hypothesis = Hypothesis(
        project_id=uuid4(),
        statement="Unsupported by stored evidence",
        supporting_evidence_ids=(uuid4(),),
    )

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        repository.add_hypothesis(hypothesis)


def test_schema_initialization_is_repeatable(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "refair.sqlite3")
    repository.initialize()
    repository.initialize()

    observation = make_observation()
    repository.add_observation(observation)
    assert repository.get_observation(observation.id) == observation
