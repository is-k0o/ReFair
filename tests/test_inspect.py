from __future__ import annotations

import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

from refair import __version__
from refair.bridge.__main__ import main as collector_main
from refair.bridge.collector import create_app
from refair.config import load_config
from refair.inspect.cli import MAX_RAW_PREVIEW_BYTES, main
from refair.models import Observation, ObservationProvenance
from refair.storage import SQLiteRepository

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")


def seed_database(tmp_path: Path) -> tuple[Path, SQLiteRepository, tuple[Observation, ...]]:
    database = tmp_path / "observations.sqlite3"
    repository = SQLiteRepository(database)
    repository.initialize()
    base_time = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    long_query = "token=" + "sensitive-query-value-" * 200
    observations = (
        Observation(
            project_id=PROJECT_ID,
            observed_at=base_time,
            provenance=ObservationProvenance.BROWSER,
            actor_id="actor_a",
            method="GET",
            url=f"https://www.google.com/async/hpba?{long_query}",
            response_status=200,
            raw_request=b"GET /secret HTTP/1.1\r\nCookie: SESSION=very-secret\r\n\r\n",
            raw_response=b"HTTP/1.1 200 OK\r\n\r\nprivate-response",
        ),
        Observation(
            project_id=PROJECT_ID,
            observed_at=base_time + timedelta(seconds=1),
            provenance=ObservationProvenance.BROWSER,
            actor_id="actor_a",
            method="POST",
            url="https://example.test/api/items/1",
            response_status=201,
            raw_request=b"POST /api/items/1 HTTP/1.1\r\n\r\n{}",
            raw_response=b"HTTP/1.1 201 Created\r\n\r\n{}",
        ),
        Observation(
            project_id=PROJECT_ID,
            observed_at=base_time + timedelta(seconds=2),
            provenance=ObservationProvenance.HUMAN_REPEATER,
            actor_id="actor_b",
            method="DELETE",
            url="https://example.test/api/items/2",
            response_status=204,
            raw_request=b"DELETE /api/items/2 HTTP/1.1\r\nAuthorization: secret\r\n\r\n",
            raw_response=b"HTTP/1.1 204 No Content\r\n\r\n",
        ),
    )
    for observation in observations:
        repository.add_observation(observation)

    raw_config = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    raw_config["bridge"]["sqlite_path"] = str(database)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw_config), encoding="utf-8")
    return config_path, repository, observations


def test_versions_are_consistent_in_python_metadata_and_fastapi(tmp_path) -> None:
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    config = load_config("config.example.yaml")
    bridge = config.bridge.model_copy(update={"sqlite_path": tmp_path / "version.sqlite3"})
    app = create_app(config.model_copy(update={"bridge": bridge}))

    assert metadata["project"]["version"] == "0.1.5"
    assert __version__ == "0.1.5"
    assert app.version == "0.1.5"


def test_collector_cli_disables_colors_and_prints_safe_startup_summary(
    tmp_path, monkeypatch, capsys
) -> None:
    config_path, _, _ = seed_database(tmp_path)
    call: dict[str, object] = {}

    def fake_run(app, **kwargs) -> None:
        call.update(kwargs)

    monkeypatch.setattr("refair.bridge.__main__.uvicorn.run", fake_run)
    collector_main(["--config", str(config_path)])

    output = capsys.readouterr().out
    assert call["use_colors"] is False
    assert "bind: 127.0.0.1:8765" in output
    assert "actor_a -> listener 8082" in output
    assert "\x1b[" not in output


def test_summary_reports_total_actor_and_provenance_counts(tmp_path, capsys) -> None:
    config_path, _, _ = seed_database(tmp_path)

    assert main(["--config", str(config_path), "summary"]) == 0

    output = capsys.readouterr().out
    assert "Observations: 3" in output
    assert "actor_a: 2" in output
    assert "actor_b: 1" in output
    assert "BROWSER: 2" in output
    assert "HUMAN_REPEATER: 1" in output


def test_list_hides_query_by_default_and_full_url_is_explicit(tmp_path, capsys) -> None:
    config_path, _, observations = seed_database(tmp_path)

    assert main(["--config", str(config_path), "list", "--limit", "20"]) == 0
    safe_output = capsys.readouterr().out
    assert str(observations[0].id) in safe_output
    assert "actor_a" in safe_output
    assert "GET" in safe_output
    assert "200" in safe_output
    assert "www.google.com/async/hpba" in safe_output
    assert "sensitive-query-value" not in safe_output

    assert main(
        ["--config", str(config_path), "list", "--limit", "20", "--full-url"]
    ) == 0
    full_output = capsys.readouterr().out
    assert observations[0].url in full_output


def test_list_uuid_is_directly_usable_with_show(tmp_path, capsys) -> None:
    config_path, _, observations = seed_database(tmp_path)

    assert main(["--config", str(config_path), "list", "--limit", "1"]) == 0
    list_output = capsys.readouterr().out.strip()
    listed_id = list_output.split(maxsplit=1)[0]

    assert listed_id == str(observations[-1].id)
    assert main(["--config", str(config_path), "show", listed_id]) == 0
    show_output = capsys.readouterr().out
    assert f"ID: {listed_id}" in show_output


def test_list_limit_and_filters_apply_to_recent_observations(tmp_path, capsys) -> None:
    config_path, _, _ = seed_database(tmp_path)

    assert main(
        [
            "--config",
            str(config_path),
            "list",
            "--limit",
            "1",
            "--actor",
            "actor_a",
            "--method",
            "post",
            "--status",
            "201",
        ]
    ) == 0

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert "actor_a" in lines[0]
    assert "POST" in lines[0]
    assert "201" in lines[0]


def test_show_defaults_to_metadata_without_raw_evidence(tmp_path, capsys) -> None:
    config_path, _, observations = seed_database(tmp_path)
    observation = observations[0]

    assert main(["--config", str(config_path), "show", str(observation.id)]) == 0

    output = capsys.readouterr().out
    assert f"ID: {observation.id}" in output
    assert "Actor: actor_a" in output
    assert "Request bytes:" in output
    assert "Response bytes:" in output
    assert "SESSION=very-secret" not in output
    assert "private-response" not in output
    assert "Raw request preview" not in output


def test_show_raw_preview_is_explicit_byte_safe_and_bounded(tmp_path, capsys) -> None:
    config_path, _, observations = seed_database(tmp_path)
    observation = observations[0]

    assert main(
        [
            "--config",
            str(config_path),
            "show",
            str(observation.id),
            "--raw-preview",
            "8",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert "WARNING: raw evidence may contain credentials" in output
    assert "Raw request preview (8/" in output
    assert repr(observation.raw_request[:8]) in output
    assert "SESSION=very-secret" not in output
    assert "private-response" not in output

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "--config",
                str(config_path),
                "show",
                str(observation.id),
                "--raw-preview",
                str(MAX_RAW_PREVIEW_BYTES + 1),
            ]
        )
    assert exit_info.value.code == 2


def test_unknown_observation_exits_cleanly_nonzero(tmp_path, capsys) -> None:
    config_path, _, _ = seed_database(tmp_path)
    unknown_id = uuid4()

    assert main(["--config", str(config_path), "show", str(unknown_id)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"Observation not found: {unknown_id}" in captured.err


def test_inspection_uses_read_only_repository_and_preserves_evidence(
    tmp_path, capsys
) -> None:
    config_path, repository, observations = seed_database(tmp_path)
    before = repository.list_observations()

    assert main(["--config", str(config_path), "summary"]) == 0
    capsys.readouterr()

    assert repository.list_observations() == before == observations
    read_only = SQLiteRepository(repository.path, read_only=True)
    with pytest.raises(RuntimeError, match="read-only"):
        read_only.add_observation(observations[0].model_copy(update={"id": uuid4()}))
