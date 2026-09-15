import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

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


def test_observation_metadata_window_is_scoped_ordered_and_raw_free(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "refair.sqlite3")
    repository.initialize()
    project_id = UUID("11111111-1111-4111-8111-111111111111")
    other_project_id = UUID("99999999-9999-4999-8999-999999999999")
    base = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)

    def item(
        identifier: str,
        *,
        seconds: int,
        actor_id: str | None = "actor_a",
        item_project_id: UUID = project_id,
    ) -> Observation:
        return make_observation().model_copy(
            update={
                "id": UUID(identifier),
                "project_id": item_project_id,
                "observed_at": base + timedelta(seconds=seconds),
                "actor_id": actor_id,
            }
        )

    later_id = item("44444444-4444-4444-8444-444444444442", seconds=5)
    earlier_id = item("44444444-4444-4444-8444-444444444441", seconds=5)
    before = item("44444444-4444-4444-8444-444444444443", seconds=-1)
    after = item("44444444-4444-4444-8444-444444444444", seconds=11)
    other_actor = item(
        "44444444-4444-4444-8444-444444444445",
        seconds=5,
        actor_id="actor_b",
    )
    other_project = item(
        "44444444-4444-4444-8444-444444444446",
        seconds=5,
        item_project_id=other_project_id,
    )
    for observation in (
        later_id,
        earlier_id,
        before,
        after,
        other_actor,
        other_project,
    ):
        repository.add_observation(observation)

    rows = SQLiteRepository(
        repository.path, read_only=True
    ).observation_metadata_window(
        project_id=project_id,
        start=base,
        end=base + timedelta(seconds=10),
        actor_ids=("actor_a",),
    )
    assert tuple(row.id for row in rows) == (earlier_id.id, later_id.id)
    assert all(row.project_id == project_id and row.actor_id == "actor_a" for row in rows)
    assert all(row.observed_at.utcoffset() is not None for row in rows)
    assert all(not hasattr(row, "raw_request") for row in rows)
