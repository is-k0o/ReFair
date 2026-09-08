"""Deterministic, value-free projections of raw HTTP observations."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BodyKind(StrEnum):
    EMPTY = "EMPTY"
    JSON = "JSON"
    FORM = "FORM"
    MULTIPART = "MULTIPART"
    TEXT = "TEXT"
    OTHER = "OTHER"


class NormalizedExchange(BaseModel):
    """A deterministic projection of one immutable Observation."""

    model_config = ConfigDict(frozen=True)

    observation_id: UUID
    normalizer_version: int = Field(ge=1)

    scheme: str
    host: str
    port: int | None = Field(default=None, ge=1, le=65535)
    path: str

    query_parameter_names: tuple[str, ...] = ()
    raw_query_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    request_content_type: str | None = None
    request_body_kind: BodyKind
    request_body_size: int = Field(ge=0)
    request_body_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    response_content_type: str | None = None
    response_body_kind: BodyKind
    response_body_size: int = Field(ge=0)
    response_body_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    warnings: tuple[str, ...] = ()
