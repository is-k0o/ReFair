"""FastAPI adapter that persists validated passive exchanges as evidence."""

from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from refair.bridge.models import MAX_TRANSPORT_BYTES, PassiveExchangeEnvelope
from refair.config import ReFairConfig
from refair.models.evidence import Observation, ObservationProvenance
from refair.storage import SQLiteRepository

LOGGER = logging.getLogger(__name__)


class BridgeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["persisted", "ignored"]
    observation_id: str | None = None
    reason: str | None = None


class CollectorService:
    """Deterministically attributes and persists one validated envelope."""

    def __init__(self, config: ReFairConfig, repository: SQLiteRepository) -> None:
        self.config = config
        self.repository = repository

    def ingest(self, envelope: PassiveExchangeEnvelope) -> Observation | None:
        actor_id = self.config.actor_id_for_listener(envelope.listener_port)
        if actor_id is None:
            LOGGER.info(
                "Ignoring passive exchange from unconfigured listener port %d",
                envelope.listener_port,
            )
            return None

        observation = Observation(
            project_id=self.config.project.id,
            observed_at=envelope.observed_at,
            provenance=ObservationProvenance.BROWSER,
            actor_id=actor_id,
            method=envelope.method,
            url=envelope.url,
            response_status=envelope.response_status,
            raw_request=envelope.raw_request,
            raw_response=envelope.raw_response,
        )
        return self.repository.add_observation(observation)


async def _bounded_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
            if declared_length < 0:
                raise HTTPException(status_code=400, detail="invalid Content-Length")
            if declared_length > MAX_TRANSPORT_BYTES:
                raise HTTPException(status_code=413, detail="bridge payload is too large")
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from error

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_TRANSPORT_BYTES:
            raise HTTPException(status_code=413, detail="bridge payload is too large")
    return bytes(body)


def create_app(
    config: ReFairConfig, repository: SQLiteRepository | None = None
) -> FastAPI:
    if repository is None:
        config.bridge.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        repository = SQLiteRepository(config.bridge.sqlite_path)
    repository.initialize()
    collector = CollectorService(config, repository)
    app = FastAPI(title="ReFair passive collector", version="0.1.0")

    @app.post("/v1/observations/passive", response_model=BridgeResult)
    async def ingest_passive_exchange(request: Request) -> JSONResponse:
        raw_body = await _bounded_body(request)
        try:
            payload = json.loads(raw_body)
            envelope = PassiveExchangeEnvelope.model_validate(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
            LOGGER.warning("Rejected malformed passive bridge payload: %s", error)
            return JSONResponse(
                status_code=422,
                content={"detail": "invalid passive exchange envelope"},
            )

        observation = collector.ingest(envelope)
        if observation is None:
            result = BridgeResult(
                status="ignored",
                reason="listener_not_configured_for_refair",
            )
            return JSONResponse(status_code=202, content=result.model_dump())

        result = BridgeResult(status="persisted", observation_id=str(observation.id))
        return JSONResponse(status_code=201, content=result.model_dump())

    return app
