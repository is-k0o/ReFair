from __future__ import annotations

import base64
import asyncio
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import yaml
from fastapi import FastAPI
from pydantic import ValidationError

from refair.bridge import PassiveExchangeEnvelope, create_app
from refair.bridge.models import MAX_EXCHANGE_BYTES, MAX_TRANSPORT_BYTES
from refair.config import ReFairConfig, load_config
from refair.models import ObservationProvenance
from refair.storage import SQLiteRepository

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")


def configured_for(tmp_path: Path) -> ReFairConfig:
    config = load_config("config.example.yaml")
    bridge = config.bridge.model_copy(update={"sqlite_path": tmp_path / "bridge.sqlite3"})
    return config.model_copy(update={"bridge": bridge})


def envelope(listener_port: int) -> dict[str, object]:
    request = b"POST /api/items HTTP/1.1\r\nHost: example.test\r\n\r\n\x00\xff"
    response = b"HTTP/1.1 201 Created\r\nContent-Length: 3\r\n\r\n\x00\xfe\xff"
    return {
        "listener_port": listener_port,
        "observed_at": "2026-09-06T12:34:56.123456+00:00",
        "method": "POST",
        "url": "https://example.test/api/items",
        "response_status": 201,
        "raw_request_base64": base64.b64encode(request).decode("ascii"),
        "raw_response_base64": base64.b64encode(response).decode("ascii"),
    }


def post(app: FastAPI, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://collector.test"
        ) as client:
            return await client.post("/v1/observations/passive", **kwargs)

    return asyncio.run(send())


def test_configuration_has_only_actor_listeners_and_stable_project_id() -> None:
    raw = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    first = load_config("config.example.yaml")
    second = load_config("config.example.yaml")

    assert "listeners" not in raw
    assert first.project.id == second.project.id == PROJECT_ID
    assert first.actor_id_for_listener(8082) == "actor_a"
    assert first.actor_id_for_listener(8083) == "actor_b"
    assert first.actor_id_for_listener(8081) is None
    assert first.actor_id_for_listener(49152) is None


def test_actor_listeners_and_profiles_must_be_unique() -> None:
    config = load_config("config.example.yaml")
    duplicate_port = config.model_dump()
    duplicate_port["actors"]["actor_b"]["listener"] = 8082
    with pytest.raises(ValidationError, match="listener ports must be unique"):
        ReFairConfig.model_validate(duplicate_port)

    duplicate_profile = config.model_dump()
    duplicate_profile["actors"]["actor_b"]["firefox_profile"] = "example-ai-a"
    with pytest.raises(ValidationError, match="profiles must be unique"):
        ReFairConfig.model_validate(duplicate_profile)


def test_bridge_rejects_non_loopback_bind_address() -> None:
    config = load_config("config.example.yaml")
    raw = config.model_dump()
    raw["bridge"]["bind_address"] = "0.0.0.0"

    with pytest.raises(ValidationError, match="must be a loopback"):
        ReFairConfig.model_validate(raw)


@pytest.mark.parametrize(
    ("listener_port", "expected_actor"), [(8082, "actor_a"), (8083, "actor_b")]
)
def test_valid_exchange_is_attributed_and_persisted_byte_for_byte(
    tmp_path: Path, listener_port: int, expected_actor: str
) -> None:
    config = configured_for(tmp_path)
    repository = SQLiteRepository(config.bridge.sqlite_path)
    app = create_app(config, repository)
    payload = envelope(listener_port)

    response = post(app, json=payload)

    assert response.status_code == 201
    observation = repository.get_observation(UUID(response.json()["observation_id"]))
    assert observation is not None
    assert repository.count_observations() == 1
    assert observation.project_id == PROJECT_ID
    assert observation.actor_id == expected_actor
    assert observation.provenance is ObservationProvenance.BROWSER
    assert observation.method == payload["method"]
    assert observation.url == payload["url"]
    assert observation.response_status == payload["response_status"]
    assert observation.raw_request == base64.b64decode(payload["raw_request_base64"])
    assert observation.raw_response == base64.b64decode(payload["raw_response_base64"])


@pytest.mark.parametrize("listener_port", [8081, 49152])
def test_unconfigured_listener_is_observably_ignored_without_persistence(
    tmp_path: Path, listener_port: int
) -> None:
    config = configured_for(tmp_path)
    repository = SQLiteRepository(config.bridge.sqlite_path)
    app = create_app(config, repository)

    response = post(app, json=envelope(listener_port))

    assert response.status_code == 202
    assert response.json() == {
        "status": "ignored",
        "observation_id": None,
        "reason": "listener_not_configured_for_refair",
    }
    assert repository.count_observations() == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(raw_request_base64="not base64!"),
        lambda payload: payload.update(response_status=99),
        lambda payload: payload.pop("method"),
        lambda payload: payload.update(actor_id="actor_a"),
    ],
)
def test_malformed_envelopes_are_rejected_without_evidence(
    tmp_path: Path, mutation
) -> None:
    config = configured_for(tmp_path)
    repository = SQLiteRepository(config.bridge.sqlite_path)
    app = create_app(config, repository)
    payload = envelope(8082)
    mutation(payload)

    response = post(app, json=payload)

    assert response.status_code == 422
    assert repository.count_observations() == 0


def test_oversized_decoded_exchange_is_rejected_before_ingestion() -> None:
    payload = envelope(8082)
    payload["raw_request_base64"] = base64.b64encode(
        b"x" * MAX_EXCHANGE_BYTES
    ).decode("ascii")
    payload["raw_response_base64"] = base64.b64encode(b"x").decode("ascii")

    with pytest.raises(ValidationError, match="exceed"):
        PassiveExchangeEnvelope.model_validate(payload)


def test_transport_rejects_declared_oversized_body_without_persistence(
    tmp_path: Path,
) -> None:
    config = configured_for(tmp_path)
    repository = SQLiteRepository(config.bridge.sqlite_path)
    app = create_app(config, repository)

    response = post(
        app,
        content=b"{}",
        headers={"Content-Length": str(MAX_TRANSPORT_BYTES + 1)},
    )

    assert response.status_code == 413
    assert repository.count_observations() == 0
