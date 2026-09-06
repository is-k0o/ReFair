"""Strict, binary-safe Java-to-Python transport contract."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

MAX_EXCHANGE_BYTES = 16 * 1024 * 1024
MAX_TRANSPORT_BYTES = ((MAX_EXCHANGE_BYTES + 2) // 3) * 4 + 64 * 1024


class PassiveExchangeEnvelope(BaseModel):
    """One completed passive Proxy request/response exchange."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    listener_port: StrictInt = Field(ge=1, le=65535)
    observed_at: datetime
    method: str = Field(min_length=1, max_length=32)
    url: str = Field(min_length=1, max_length=8192)
    response_status: StrictInt = Field(ge=100, le=599)
    raw_request_base64: bytes = Field(min_length=1, repr=False)
    raw_response_base64: bytes = Field(min_length=1, repr=False)

    @field_validator("observed_at")
    @classmethod
    def observed_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone offset")
        return value

    @field_validator("url")
    @classmethod
    def url_is_http(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an absolute HTTP(S) URL")
        return value

    @field_validator("raw_request_base64", "raw_response_base64", mode="before")
    @classmethod
    def decode_base64(cls, value: Any) -> bytes:
        if not isinstance(value, str):
            raise ValueError("raw HTTP messages must be base64 strings")
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("raw HTTP message is not valid base64") from error

    @model_validator(mode="after")
    def exchange_size_is_bounded(self) -> PassiveExchangeEnvelope:
        size = len(self.raw_request_base64) + len(self.raw_response_base64)
        if size > MAX_EXCHANGE_BYTES:
            raise ValueError(
                f"decoded request and response exceed {MAX_EXCHANGE_BYTES} bytes"
            )
        return self

    @property
    def raw_request(self) -> bytes:
        return self.raw_request_base64

    @property
    def raw_response(self) -> bytes:
        return self.raw_response_base64
