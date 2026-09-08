from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import yaml

from refair.models import Observation, ObservationProvenance
from refair.process.cli import main
from refair.storage import SQLiteRepository


def test_processor_uses_config_database_and_only_processes_pending(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "processor.sqlite3"
    repository = SQLiteRepository(database)
    repository.initialize()
    observations = (
        Observation(
            project_id=uuid4(),
            provenance=ObservationProvenance.BROWSER,
            actor_id="actor_a",
            method="GET",
            url="https://example.test/a?id=secret",
            response_status=200,
            raw_request=b"GET /a?id=secret HTTP/1.1\r\n\r\n",
            raw_response=b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}",
        ),
        Observation(
            project_id=uuid4(),
            provenance=ObservationProvenance.BROWSER,
            actor_id="actor_b",
            method="POST",
            url="http://example.test/b",
            response_status=204,
            raw_request=b"POST /b HTTP/2\r\nContent-Type: text/plain\r\n\r\nhello",
            raw_response=b"HTTP/2 204\r\n\r\n",
        ),
    )
    for observation in observations:
        repository.add_observation(observation)
    raw_before = repository.list_observations()

    config = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    config["bridge"]["sqlite_path"] = str(database)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    assert main(["--config", str(config_path)]) == 0
    first_output = capsys.readouterr().out
    assert "processed: 2" in first_output
    assert "pending: 0" in first_output
    assert "warnings: 0" in first_output

    assert main(["--config", str(config_path)]) == 0
    second_output = capsys.readouterr().out
    assert "processed: 0" in second_output
    assert "pending: 0" in second_output
    assert repository.count_normalized_exchanges() == 2
    assert repository.list_observations() == raw_before
    assert {item.id for item in raw_before} == {item.id for item in observations}
