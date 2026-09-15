"""Bounded, redacted HTTP evidence derived from snapshot-selected observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from refair.models import Observation, ObservationProvenance, OperationContextSnapshot
from refair.storage import SQLiteRepository


class EvidenceLimits(BaseModel):
    """Bounds that omit an oversized unique exchange whole, never truncate RAW."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_exchanges: int = Field(default=8, ge=0)
    max_request_bytes: int = Field(default=32 * 1024, ge=0)
    max_response_bytes: int = Field(default=128 * 1024, ge=0)
    max_total_bytes: int = Field(default=256 * 1024, ge=0)


DEFAULT_EVIDENCE_LIMITS = EvidenceLimits()


class HttpEvidenceMessage(BaseModel):
    """Losslessly decoded, header-redacted HTTP message for model input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    encoding: Literal["UTF8", "LATIN1"]
    content: str
    byte_count: int = Field(ge=0)
    body_omitted: bool
    omitted_body_byte_count: int = Field(ge=0)

    @model_validator(mode="after")
    def content_and_omission_counts_are_consistent(self) -> Self:
        codec = "utf-8" if self.encoding == "UTF8" else "latin-1"
        if len(self.content.encode(codec)) != self.byte_count:
            raise ValueError("message byte count must match its lossless encoding")
        if self.body_omitted != (self.omitted_body_byte_count > 0):
            raise ValueError("body omission flag and byte count must agree")
        return self


class HttpEvidenceExchange(BaseModel):
    """One exact redacted exchange, possibly representing duplicate observations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_ids: tuple[UUID, ...] = Field(min_length=1)
    actor_id: str | None
    provenance: ObservationProvenance
    method: str = Field(min_length=1)
    url: str = Field(min_length=1)
    response_status: int | None = Field(default=None, ge=100, le=599)
    request: HttpEvidenceMessage
    response: HttpEvidenceMessage | None

    @field_validator("observation_ids")
    @classmethod
    def observation_ids_are_canonical(
        cls, value: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        if tuple(sorted(set(value), key=str)) != value:
            raise ValueError("observation IDs must be unique and UUID-sorted")
        return value


class OperationEvidenceBundle(BaseModel):
    """Bounded HTTP evidence for exactly one preselected operation snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_version: Literal[1] = 1
    reusable_credentials_redacted: Literal[True] = True
    application_values_included: Literal[True] = True
    project_id: UUID
    operation_id: UUID
    exchanges: tuple[HttpEvidenceExchange, ...]
    available_exchange_count: int = Field(ge=0)
    included_exchange_count: int = Field(ge=0)
    omitted_duplicate_count: int = Field(ge=0)
    omitted_by_exchange_limit_count: int = Field(ge=0)
    omitted_too_large_count: int = Field(ge=0)
    total_included_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def coverage_is_consistent(self) -> Self:
        if self.included_exchange_count != len(self.exchanges):
            raise ValueError("included exchange count must match exchanges")
        if self.available_exchange_count != (
            self.included_exchange_count
            + self.omitted_duplicate_count
            + self.omitted_by_exchange_limit_count
            + self.omitted_too_large_count
        ):
            raise ValueError("evidence coverage counts must sum to available count")
        identifiers = [
            observation_id
            for exchange in self.exchanges
            for observation_id in exchange.observation_ids
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("an observation cannot occur in multiple exchanges")
        byte_count = sum(
            exchange.request.byte_count
            + (exchange.response.byte_count if exchange.response is not None else 0)
            for exchange in self.exchanges
        )
        if self.total_included_bytes != byte_count:
            raise ValueError("total included bytes must match included messages")
        return self


_AUTHORIZATION_HEADERS = {b"authorization", b"proxy-authorization"}
_COOKIE_HEADERS = {b"cookie", b"set-cookie"}
_BINARY_CONTENT_TYPE_PREFIXES = (
    b"application/octet-stream",
    b"application/pdf",
    b"application/zip",
    b"audio/",
    b"font/",
    b"image/",
    b"video/",
)
_REDACTED = b"<REDACTED>"


@dataclass(frozen=True)
class _PreparedMessage:
    redacted_bytes: bytes
    message: HttpEvidenceMessage


@dataclass(frozen=True)
class _PreparedExchange:
    observation: Observation
    request: _PreparedMessage
    response: _PreparedMessage | None

    @property
    def byte_count(self) -> int:
        return self.request.message.byte_count + (
            self.response.message.byte_count if self.response is not None else 0
        )


def _split_http_message(raw: bytes) -> tuple[bytes, bytes, bytes, bytes]:
    for delimiter, line_separator in ((b"\r\n\r\n", b"\r\n"), (b"\n\n", b"\n")):
        position = raw.find(delimiter)
        if position >= 0:
            return (
                raw[:position],
                raw[position + len(delimiter) :],
                delimiter,
                line_separator,
            )
    line_separator = b"\r\n" if b"\r\n" in raw else b"\n"
    return raw, b"", b"", line_separator


def _leading_header_whitespace(value: bytes) -> bytes:
    leading_length = len(value) - len(value.lstrip(b" \t"))
    return value[:leading_length] or b" "


def _redact_authorization(value: bytes) -> bytes:
    leading = _leading_header_whitespace(value)
    parts = value.strip().split(None, 1)
    if len(parts) == 2:
        return leading + parts[0] + b" " + _REDACTED
    return leading + _REDACTED


def _redact_cookie_pairs(value: bytes, *, preserve_attributes: bool) -> bytes:
    leading = _leading_header_whitespace(value)
    segments = value.strip().split(b";")
    redacted: list[bytes] = []
    for index, segment in enumerate(segments):
        stripped = segment.strip()
        if index > 0 and preserve_attributes:
            redacted.append(stripped)
            continue
        name, separator, _ = stripped.partition(b"=")
        redacted.append(name + b"=" + _REDACTED if separator else _REDACTED)
    return leading + b"; ".join(redacted)


def _redact_header_block(head: bytes, line_separator: bytes) -> bytes:
    redacted_lines: list[bytes] = []
    redact_continuation = False
    for line in head.split(line_separator):
        if line.startswith((b" ", b"\t")):
            redacted_lines.append(b" " + _REDACTED if redact_continuation else line)
            continue
        name, separator, value = line.partition(b":")
        if not separator:
            redact_continuation = False
            redacted_lines.append(line)
            continue
        normalized_name = name.strip().lower()
        redact_continuation = normalized_name in (
            _AUTHORIZATION_HEADERS | _COOKIE_HEADERS
        )
        if normalized_name in _AUTHORIZATION_HEADERS:
            value = _redact_authorization(value)
        elif normalized_name == b"cookie":
            value = _redact_cookie_pairs(value, preserve_attributes=False)
        elif normalized_name == b"set-cookie":
            value = _redact_cookie_pairs(value, preserve_attributes=True)
        redacted_lines.append(name + b":" + value)
    return line_separator.join(redacted_lines)


def _content_type(head: bytes, line_separator: bytes) -> bytes | None:
    for line in head.split(line_separator):
        name, separator, value = line.partition(b":")
        if separator and name.strip().lower() == b"content-type":
            return value.strip().lower().split(b";", 1)[0]
    return None


def _body_is_clearly_binary(
    head: bytes, body: bytes, line_separator: bytes
) -> bool:
    if not body:
        return False
    content_type = _content_type(head, line_separator)
    if content_type is not None and content_type.startswith(
        _BINARY_CONTENT_TYPE_PREFIXES
    ):
        return True
    return b"\x00" in body


def _decode_losslessly(raw: bytes) -> tuple[Literal["UTF8", "LATIN1"], str]:
    try:
        return "UTF8", raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "LATIN1", raw.decode("latin-1", errors="strict")


def _prepare_message(raw: bytes) -> _PreparedMessage:
    head, body, delimiter, line_separator = _split_http_message(raw)
    redacted_head = _redact_header_block(head, line_separator)
    body_omitted = _body_is_clearly_binary(head, body, line_separator)
    redacted = redacted_head + delimiter + (b"" if body_omitted else body)
    encoding, content = _decode_losslessly(redacted)
    return _PreparedMessage(
        redacted_bytes=redacted,
        message=HttpEvidenceMessage(
            encoding=encoding,
            content=content,
            byte_count=len(redacted),
            body_omitted=body_omitted,
            omitted_body_byte_count=len(body) if body_omitted else 0,
        ),
    )


def _exchange_identity(prepared: _PreparedExchange) -> tuple[object, ...]:
    observation = prepared.observation
    return (
        observation.actor_id,
        observation.provenance.value,
        observation.method,
        observation.url,
        observation.response_status,
        prepared.request.redacted_bytes,
        prepared.response.redacted_bytes if prepared.response is not None else None,
    )


def _to_evidence_exchange(
    prepared: _PreparedExchange, observation_ids: list[UUID]
) -> HttpEvidenceExchange:
    observation = prepared.observation
    return HttpEvidenceExchange(
        observation_ids=tuple(sorted(set(observation_ids), key=str)),
        actor_id=observation.actor_id,
        provenance=observation.provenance,
        method=observation.method,
        url=observation.url,
        response_status=observation.response_status,
        request=prepared.request.message,
        response=(prepared.response.message if prepared.response is not None else None),
    )


def assemble_operation_evidence(
    repository: SQLiteRepository,
    snapshot: OperationContextSnapshot,
    *,
    limits: EvidenceLimits = DEFAULT_EVIDENCE_LIMITS,
) -> OperationEvidenceBundle:
    """Load only snapshot observations, then redact, exactly dedupe, and bound them."""

    if not repository.read_only:
        raise ValueError("operation evidence assembly requires a read-only repository")

    grouped: dict[tuple[object, ...], tuple[_PreparedExchange, list[UUID]]] = {}
    for reference in snapshot.observation_refs:
        observation = repository.get_observation(reference.observation_id)
        if observation is None:
            raise RuntimeError(
                f"snapshot observation is missing from storage: {reference.observation_id}"
            )
        if observation.id != reference.observation_id:
            raise RuntimeError("loaded observation ID conflicts with snapshot reference")
        if observation.project_id != snapshot.project_id:
            raise RuntimeError(
                "loaded observation project conflicts with operation snapshot"
            )
        prepared = _PreparedExchange(
            observation=observation,
            request=_prepare_message(observation.raw_request),
            response=(
                _prepare_message(observation.raw_response)
                if observation.raw_response is not None
                else None
            ),
        )
        identity = _exchange_identity(prepared)
        existing = grouped.get(identity)
        if existing is None:
            grouped[identity] = (prepared, [observation.id])
        else:
            existing[1].append(observation.id)

    available_count = len(snapshot.observation_refs)
    omitted_duplicate_count = sum(
        len(observation_ids) - 1 for _, observation_ids in grouped.values()
    )
    included: list[HttpEvidenceExchange] = []
    included_bytes = 0
    omitted_too_large_count = 0
    omitted_by_exchange_limit_count = 0
    for prepared, observation_ids in grouped.values():
        response_bytes = (
            prepared.response.message.byte_count
            if prepared.response is not None
            else 0
        )
        individually_too_large = (
            prepared.request.message.byte_count > limits.max_request_bytes
            or response_bytes > limits.max_response_bytes
            or prepared.byte_count > limits.max_total_bytes
        )
        if individually_too_large:
            omitted_too_large_count += 1
            continue
        if len(included) >= limits.max_exchanges:
            omitted_by_exchange_limit_count += 1
            continue
        if included_bytes + prepared.byte_count > limits.max_total_bytes:
            omitted_too_large_count += 1
            continue
        included.append(_to_evidence_exchange(prepared, observation_ids))
        included_bytes += prepared.byte_count

    return OperationEvidenceBundle(
        project_id=snapshot.project_id,
        operation_id=snapshot.operation.id,
        exchanges=tuple(included),
        available_exchange_count=available_count,
        included_exchange_count=len(included),
        omitted_duplicate_count=omitted_duplicate_count,
        omitted_by_exchange_limit_count=omitted_by_exchange_limit_count,
        omitted_too_large_count=omitted_too_large_count,
        total_included_bytes=included_bytes,
    )
