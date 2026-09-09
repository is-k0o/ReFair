"""Minimal deterministic structural application models."""

from __future__ import annotations

from enum import Enum, StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from refair.models.evidence import ObservationProvenance
from refair.models.normalized import BodyKind


class ExactEndpoint(BaseModel):
    """An exact observed HTTP surface, independent of method and query."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    project_id: UUID
    scheme: str
    host: str
    port: int | None = Field(default=None, ge=1, le=65535)
    path: str


class HttpOperation(BaseModel):
    """An exact observed method on one ExactEndpoint."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    endpoint_id: UUID
    method: str = Field(min_length=1)


class MethodAdvertisementSource(StrEnum):
    ALLOW_HEADER = "ALLOW_HEADER"
    CORS_ALLOW_METHODS = "CORS_ALLOW_METHODS"


class MethodAdvertisement(BaseModel):
    """An evidence-backed server advertisement, not verified capability."""

    model_config = ConfigDict(frozen=True)

    endpoint_id: UUID
    source: MethodAdvertisementSource
    advertised_method: str = Field(min_length=1)
    observation_id: UUID


class JsonDirection(StrEnum):
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"


class JsonType(StrEnum):
    OBJECT = "OBJECT"
    ARRAY = "ARRAY"
    STRING = "STRING"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    NULL = "NULL"


class JsonParseStatus(StrEnum):
    PARSED = "PARSED"
    MALFORMED = "MALFORMED"
    SKIPPED_TOO_LARGE = "SKIPPED_TOO_LARGE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    UNSUPPORTED_CONTENT_ENCODING = "UNSUPPORTED_CONTENT_ENCODING"
    CONTENT_DECODING_FAILED = "CONTENT_DECODING_FAILED"


class JsonArrayItem(Enum):
    """Typed JSON path segment representing any array element."""

    ITEM = "ARRAY_ITEM"


JsonPath = tuple[str | JsonArrayItem, ...]


class JsonDocument(BaseModel):
    """Value-free parse result for one JSON request or response body."""

    model_config = ConfigDict(frozen=True)

    observation_id: UUID
    direction: JsonDirection
    parse_status: JsonParseStatus
    root_type: JsonType | None = None

    @model_validator(mode="after")
    def root_type_matches_parse_status(self) -> Self:
        if (self.parse_status is JsonParseStatus.PARSED) != (
            self.root_type is not None
        ):
            raise ValueError("root type must be present exactly when JSON was parsed")
        return self


class JsonFieldObservation(BaseModel):
    """One evidence-backed JSON path/type fact without its scalar value."""

    model_config = ConfigDict(frozen=True)

    observation_id: UUID
    direction: JsonDirection
    path: JsonPath = Field(min_length=1)
    json_type: JsonType
    duplicate_key_observed: bool = False


class OperationJsonDocumentOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: JsonDirection
    parse_status: JsonParseStatus
    root_type: JsonType | None = None
    observation_count: int = Field(ge=1)


class OperationJsonField(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: JsonDirection
    path: JsonPath = Field(min_length=1)
    json_type: JsonType
    observation_count: int = Field(ge=1)
    duplicate_key_observed: bool = False


class FormDirection(StrEnum):
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"


class FormParseStatus(StrEnum):
    PARSED = "PARSED"
    SKIPPED_TOO_LARGE = "SKIPPED_TOO_LARGE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    UNSUPPORTED_CONTENT_ENCODING = "UNSUPPORTED_CONTENT_ENCODING"
    CONTENT_DECODING_FAILED = "CONTENT_DECODING_FAILED"


class FormDocument(BaseModel):
    """Value-free parse result for one URL-encoded request or response body."""

    model_config = ConfigDict(frozen=True)

    observation_id: UUID
    direction: FormDirection
    parse_status: FormParseStatus


class FormFieldObservation(BaseModel):
    """Occurrences of one decoded byte-level form field name."""

    model_config = ConfigDict(frozen=True)

    observation_id: UUID
    direction: FormDirection
    field_name: bytes
    occurrence_count: int = Field(ge=1)
    assigned_occurrence_count: int = Field(ge=0)

    @model_validator(mode="after")
    def assigned_occurrences_do_not_exceed_total(self) -> Self:
        if self.assigned_occurrence_count > self.occurrence_count:
            raise ValueError("assigned occurrences cannot exceed total occurrences")
        return self


class OperationFormDocumentOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: FormDirection
    parse_status: FormParseStatus
    observation_count: int = Field(ge=1)


class OperationFormField(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: FormDirection
    field_name: bytes
    observation_count: int = Field(ge=1)
    total_occurrence_count: int = Field(ge=1)
    total_assigned_occurrence_count: int = Field(ge=0)
    max_occurrence_count: int = Field(ge=1)

    @model_validator(mode="after")
    def aggregate_counts_are_consistent(self) -> Self:
        if self.total_assigned_occurrence_count > self.total_occurrence_count:
            raise ValueError("assigned occurrences cannot exceed total occurrences")
        if self.max_occurrence_count > self.total_occurrence_count:
            raise ValueError("maximum occurrences cannot exceed total occurrences")
        return self


class MultipartDirection(StrEnum):
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"


class MultipartParseStatus(StrEnum):
    PARSED = "PARSED"
    MALFORMED = "MALFORMED"
    SKIPPED_TOO_LARGE = "SKIPPED_TOO_LARGE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    UNSUPPORTED_CONTENT_ENCODING = "UNSUPPORTED_CONTENT_ENCODING"
    CONTENT_DECODING_FAILED = "CONTENT_DECODING_FAILED"


class MultipartDocument(BaseModel):
    """Value-free parse result for one multipart/form-data body."""

    model_config = ConfigDict(frozen=True)

    observation_id: UUID
    direction: MultipartDirection
    parse_status: MultipartParseStatus


class MultipartPartName(BaseModel):
    """One exact observed multipart name parameter value."""

    model_config = ConfigDict(frozen=True)

    name: bytes
    occurrence_count: int = Field(ge=1)


class MultipartPartObservation(BaseModel):
    """Selected structural metadata for one actual multipart part occurrence."""

    model_config = ConfigDict(frozen=True)

    observation_id: UUID
    direction: MultipartDirection
    part_index: int = Field(ge=0)
    disposition_type: str | None = None
    content_type: str | None = None
    name_parameter_count: int = Field(ge=0)
    filename_parameter_count: int = Field(ge=0)
    filename_empty_count: int = Field(ge=0)
    filename_nonempty_count: int = Field(ge=0)
    names: tuple[MultipartPartName, ...] = ()

    @model_validator(mode="after")
    def parameter_counts_are_consistent(self) -> Self:
        if self.filename_parameter_count != (
            self.filename_empty_count + self.filename_nonempty_count
        ):
            raise ValueError("filename counts must sum to parameter count")
        if sum(item.occurrence_count for item in self.names) != (
            self.name_parameter_count
        ):
            raise ValueError("name occurrences must sum to parameter count")
        if len({item.name for item in self.names}) != len(self.names):
            raise ValueError("multipart part names must be unique")
        return self


class OperationMultipartDocumentOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: MultipartDirection
    parse_status: MultipartParseStatus
    observation_count: int = Field(ge=1)


class OperationQueryShape(BaseModel):
    model_config = ConfigDict(frozen=True)

    query_present: bool
    query_parameter_names: tuple[str, ...]
    observation_count: int = Field(ge=1)


class RequestRepresentation(BaseModel):
    model_config = ConfigDict(frozen=True)

    content_type: str | None
    body_kind: BodyKind
    observation_count: int = Field(ge=1)


class ResponseRepresentation(BaseModel):
    model_config = ConfigDict(frozen=True)

    response_status: int | None = Field(default=None, ge=100, le=599)
    content_type: str | None
    body_kind: BodyKind
    observation_count: int = Field(ge=1)


class ActorOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor_id: str | None
    provenance: ObservationProvenance
    response_status: int | None = Field(default=None, ge=100, le=599)
    observation_count: int = Field(ge=1)
