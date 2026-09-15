"""Bounded, redacted HTTP evidence around a human-selected operation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from refair.models import Observation, ObservationProvenance, OperationContextSnapshot
from refair.storage import ObservationMetadata, SQLiteRepository


class EvidenceLimits(BaseModel):
    """Bounds that omit an oversized unique exchange whole, never truncate RAW."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_exchanges: int = Field(default=8, ge=0)
    max_workflow_exchanges: int = Field(default=24, ge=0)
    workflow_before_seconds: int = Field(default=300, ge=0)
    workflow_after_seconds: int = Field(default=300, ge=0)
    max_request_bytes: int = Field(default=32 * 1024, ge=0)
    max_response_bytes: int = Field(default=128 * 1024, ge=0)
    max_total_bytes: int = Field(default=512 * 1024, ge=0)


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


class HttpEvidenceOccurrence(BaseModel):
    """One stored observation occurrence retained through exact deduplication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: UUID
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


class HttpEvidenceExchange(BaseModel):
    """One exact redacted exchange, possibly representing duplicate observations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: Literal["ANCHOR_OPERATION", "WORKFLOW_CONTEXT"]
    occurrences: tuple[HttpEvidenceOccurrence, ...] = Field(min_length=1)
    actor_id: str | None
    provenance: ObservationProvenance
    method: str = Field(min_length=1)
    url: str = Field(min_length=1)
    response_status: int | None = Field(default=None, ge=100, le=599)
    request: HttpEvidenceMessage
    response: HttpEvidenceMessage | None

    @field_validator("occurrences")
    @classmethod
    def occurrences_are_canonical(
        cls, value: tuple[HttpEvidenceOccurrence, ...]
    ) -> tuple[HttpEvidenceOccurrence, ...]:
        identifiers = tuple(item.observation_id for item in value)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("observation occurrences must have unique IDs")
        canonical = tuple(
            sorted(
                value,
                key=lambda item: (
                    item.observed_at.astimezone(timezone.utc),
                    str(item.observation_id),
                ),
            )
        )
        if canonical != value:
            raise ValueError("observation occurrences must be chronological")
        return value

    @property
    def observation_ids(self) -> tuple[UUID, ...]:
        """Convenience projection; occurrences are the serialized source of truth."""

        return tuple(item.observation_id for item in self.occurrences)


class OperationEvidenceBundle(BaseModel):
    """One anchor operation plus bounded adjacent same-actor workflow context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_version: Literal[1] = 1
    reusable_credentials_redacted: Literal[True] = True
    application_values_included: Literal[True] = True
    project_id: UUID
    operation_id: UUID
    exchanges: tuple[HttpEvidenceExchange, ...]
    anchor_observation_count: int = Field(ge=0)
    workflow_candidate_observation_count: int = Field(ge=0)
    available_observation_count: int = Field(ge=0)
    unique_exchange_count: int = Field(ge=0)
    included_exchange_count: int = Field(ge=0)
    omitted_duplicate_count: int = Field(ge=0)
    omitted_by_exchange_limit_count: int = Field(ge=0)
    omitted_too_large_count: int = Field(ge=0)
    omitted_static_asset_count: int = Field(ge=0)
    omitted_outside_authority_count: int = Field(ge=0)
    omitted_by_workflow_limit_count: int = Field(ge=0)
    total_included_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def coverage_is_consistent(self) -> Self:
        if self.included_exchange_count != len(self.exchanges):
            raise ValueError("included exchange count must match exchanges")
        if self.available_observation_count != (
            self.unique_exchange_count + self.omitted_duplicate_count
        ):
            raise ValueError("observation coverage must match exact deduplication")
        if self.unique_exchange_count != (
            self.included_exchange_count
            + self.omitted_by_exchange_limit_count
            + self.omitted_too_large_count
        ):
            raise ValueError("unique exchange coverage counts must be complete")
        selected_workflow_count = (
            self.available_observation_count - self.anchor_observation_count
        )
        if selected_workflow_count < 0:
            raise ValueError("anchor observations cannot exceed available observations")
        if self.workflow_candidate_observation_count != (
            selected_workflow_count
            + self.omitted_static_asset_count
            + self.omitted_outside_authority_count
            + self.omitted_by_workflow_limit_count
        ):
            raise ValueError("workflow candidate coverage counts must be complete")
        identifiers = [
            occurrence.observation_id
            for exchange in self.exchanges
            for occurrence in exchange.occurrences
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
    scope: Literal["ANCHOR_OPERATION", "WORKFLOW_CONTEXT"]
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
        prepared.scope,
        observation.actor_id,
        observation.provenance.value,
        observation.method,
        observation.url,
        observation.response_status,
        prepared.request.redacted_bytes,
        prepared.response.redacted_bytes if prepared.response is not None else None,
    )


def _to_evidence_exchange(
    prepared: _PreparedExchange, occurrences: list[HttpEvidenceOccurrence]
) -> HttpEvidenceExchange:
    observation = prepared.observation
    return HttpEvidenceExchange(
        scope=prepared.scope,
        occurrences=tuple(
            sorted(
                occurrences,
                key=lambda item: (
                    item.observed_at.astimezone(timezone.utc),
                    str(item.observation_id),
                ),
            )
        ),
        actor_id=observation.actor_id,
        provenance=observation.provenance,
        method=observation.method,
        url=observation.url,
        response_status=observation.response_status,
        request=prepared.request.message,
        response=(prepared.response.message if prepared.response is not None else None),
    )


_STATIC_RESPONSE_CONTENT_TYPES = frozenset(
    {
        "text/css",
        "text/javascript",
        "application/javascript",
        "application/x-javascript",
        "application/wasm",
    }
)
_STATIC_URL_EXTENSIONS = frozenset(
    {
        ".css",
        ".js",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".webp",
        ".avif",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
    }
)


def _effective_authority(
    scheme: str, hostname: str | None, port: int | None
) -> tuple[str, str, int | None] | None:
    normalized_scheme = scheme.lower()
    if not normalized_scheme or not hostname:
        return None
    effective_port = port
    if effective_port is None:
        if normalized_scheme == "http":
            effective_port = 80
        elif normalized_scheme == "https":
            effective_port = 443
    return normalized_scheme, hostname.lower(), effective_port


def _url_authority(url: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(url)
        return _effective_authority(parsed.scheme, parsed.hostname, parsed.port)
    except ValueError:
        return None


def _workflow_candidate_is_static(
    repository: SQLiteRepository, metadata: ObservationMetadata
) -> bool:
    normalized = repository.get_normalized_exchange(metadata.id)
    if normalized is not None:
        content_type = (normalized.response_content_type or "").lower()
        return content_type.startswith(("image/", "audio/", "video/", "font/")) or (
            content_type in _STATIC_RESPONSE_CONTENT_TYPES
        )
    try:
        path = urlsplit(metadata.url).path
    except ValueError:
        return False
    return PurePosixPath(path.lower()).suffix in _STATIC_URL_EXTENSIONS


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise RuntimeError("stored evidence timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _candidate_distance(
    metadata: ObservationMetadata, anchor_start: datetime, anchor_end: datetime
) -> timedelta:
    observed_at = _utc(metadata.observed_at)
    if observed_at < anchor_start:
        return anchor_start - observed_at
    if observed_at > anchor_end:
        return observed_at - anchor_end
    return timedelta(0)


def _prepare_observation(
    observation: Observation,
    scope: Literal["ANCHOR_OPERATION", "WORKFLOW_CONTEXT"],
) -> _PreparedExchange:
    _utc(observation.observed_at)
    return _PreparedExchange(
        scope=scope,
        observation=observation,
        request=_prepare_message(observation.raw_request),
        response=(
            _prepare_message(observation.raw_response)
            if observation.raw_response is not None
            else None
        ),
    )


def assemble_operation_evidence(
    repository: SQLiteRepository,
    snapshot: OperationContextSnapshot,
    *,
    limits: EvidenceLimits = DEFAULT_EVIDENCE_LIMITS,
) -> OperationEvidenceBundle:
    """Assemble anchor RAW plus a metadata-selected temporal workflow neighborhood."""

    if not repository.read_only:
        raise ValueError("operation evidence assembly requires a read-only repository")

    anchor_references = snapshot.observation_refs
    anchor_ids = {reference.observation_id for reference in anchor_references}
    visible_actor_ids = tuple(
        sorted(
            {
                reference.actor_id
                for reference in anchor_references
                if reference.actor_id is not None
            }
        )
    )
    workflow_candidates: list[ObservationMetadata] = []
    omitted_outside_authority_count = 0
    omitted_static_asset_count = 0
    omitted_by_workflow_limit_count = 0

    if anchor_references:
        anchor_start = min(_utc(item.observed_at) for item in anchor_references)
        anchor_end = max(_utc(item.observed_at) for item in anchor_references)
        window_start = anchor_start - timedelta(seconds=limits.workflow_before_seconds)
        window_end = anchor_end + timedelta(seconds=limits.workflow_after_seconds)
        metadata_rows = repository.observation_metadata_window(
            project_id=snapshot.project_id,
            start=window_start,
            end=window_end,
            actor_ids=visible_actor_ids,
        )
        for metadata in metadata_rows:
            observed_at = _utc(metadata.observed_at)
            actor_allowed = (
                metadata.actor_id in visible_actor_ids
                if visible_actor_ids
                else metadata.actor_id is None
            )
            if (
                metadata.id in anchor_ids
                or metadata.project_id != snapshot.project_id
                or not actor_allowed
                or observed_at < window_start
                or observed_at > window_end
            ):
                continue
            workflow_candidates.append(metadata)

        anchor_authority = _effective_authority(
            snapshot.endpoint.scheme,
            snapshot.endpoint.host,
            snapshot.endpoint.port,
        )
        eligible: list[ObservationMetadata] = []
        for metadata in workflow_candidates:
            if anchor_authority is None or _url_authority(metadata.url) != anchor_authority:
                omitted_outside_authority_count += 1
                continue
            if _workflow_candidate_is_static(repository, metadata):
                omitted_static_asset_count += 1
                continue
            eligible.append(metadata)

        eligible.sort(
            key=lambda item: (
                _candidate_distance(item, anchor_start, anchor_end),
                _utc(item.observed_at),
                str(item.id),
            )
        )
        selected_workflow = eligible[: limits.max_workflow_exchanges]
        omitted_by_workflow_limit_count = len(eligible) - len(selected_workflow)
        selected_workflow.sort(key=lambda item: (_utc(item.observed_at), str(item.id)))
    else:
        selected_workflow = []

    grouped: dict[
        tuple[object, ...], tuple[_PreparedExchange, list[HttpEvidenceOccurrence]]
    ] = {}

    def add_prepared(prepared: _PreparedExchange) -> None:
        identity = _exchange_identity(prepared)
        occurrence = HttpEvidenceOccurrence(
            observation_id=prepared.observation.id,
            observed_at=prepared.observation.observed_at,
        )
        existing = grouped.get(identity)
        if existing is None:
            grouped[identity] = (prepared, [occurrence])
        else:
            existing[1].append(occurrence)

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
        add_prepared(_prepare_observation(observation, "ANCHOR_OPERATION"))

    for metadata in selected_workflow:
        observation = repository.get_observation(metadata.id)
        if observation is None:
            raise RuntimeError(
                f"workflow observation is missing from storage: {metadata.id}"
            )
        if observation.id != metadata.id:
            raise RuntimeError("loaded observation ID conflicts with workflow metadata")
        if observation.project_id != snapshot.project_id:
            raise RuntimeError(
                "loaded workflow observation project conflicts with operation snapshot"
            )
        add_prepared(_prepare_observation(observation, "WORKFLOW_CONTEXT"))

    available_observation_count = len(anchor_references) + len(selected_workflow)
    omitted_duplicate_count = sum(
        len(occurrences) - 1 for _, occurrences in grouped.values()
    )
    included: list[HttpEvidenceExchange] = []
    included_bytes = 0
    included_anchor_exchanges = 0
    omitted_too_large_count = 0
    omitted_by_exchange_limit_count = 0
    for prepared, occurrences in grouped.values():
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
        if (
            prepared.scope == "ANCHOR_OPERATION"
            and included_anchor_exchanges >= limits.max_exchanges
        ):
            omitted_by_exchange_limit_count += 1
            continue
        if included_bytes + prepared.byte_count > limits.max_total_bytes:
            omitted_too_large_count += 1
            continue
        included.append(_to_evidence_exchange(prepared, occurrences))
        included_bytes += prepared.byte_count
        if prepared.scope == "ANCHOR_OPERATION":
            included_anchor_exchanges += 1

    return OperationEvidenceBundle(
        project_id=snapshot.project_id,
        operation_id=snapshot.operation.id,
        exchanges=tuple(included),
        anchor_observation_count=len(anchor_references),
        workflow_candidate_observation_count=len(workflow_candidates),
        available_observation_count=available_observation_count,
        unique_exchange_count=len(grouped),
        included_exchange_count=len(included),
        omitted_duplicate_count=omitted_duplicate_count,
        omitted_by_exchange_limit_count=omitted_by_exchange_limit_count,
        omitted_too_large_count=omitted_too_large_count,
        omitted_static_asset_count=omitted_static_asset_count,
        omitted_outside_authority_count=omitted_outside_authority_count,
        omitted_by_workflow_limit_count=omitted_by_workflow_limit_count,
        total_included_bytes=included_bytes,
    )
