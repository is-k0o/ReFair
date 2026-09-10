"""Operation-centric bounded context contracts for C3 snapshot version 1."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from refair.models.analytic import ExplorationLead
from refair.models.evidence import HypothesisStatus, ObservationProvenance
from refair.models.structure import (
    ActorOutcome,
    ExactEndpoint,
    FormDirection,
    HttpOperation,
    JsonDirection,
    JsonType,
    MethodAdvertisement,
    MultipartDirection,
    OperationFormDocumentOutcome,
    OperationJsonDocumentOutcome,
    OperationMultipartDocumentOutcome,
    OperationQueryShape,
    RequestRepresentation,
    ResponseRepresentation,
)


class ContextObservationRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: UUID
    project_id: UUID
    observed_at: datetime
    actor_id: str | None = None
    provenance: ObservationProvenance
    response_status: int | None = Field(default=None, ge=100, le=599)

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


class ContextByteString(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    encoding: Literal["ASCII_BACKSLASH_HEX"] = "ASCII_BACKSLASH_HEX"
    value: str


class ContextJsonKey(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["KEY"] = "KEY"
    value: str


class ContextJsonArrayItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ARRAY_ITEM"] = "ARRAY_ITEM"


ContextJsonPathSegment = Annotated[
    ContextJsonKey | ContextJsonArrayItem,
    Field(discriminator="kind"),
]


class ContextJsonField(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: JsonDirection
    path: tuple[ContextJsonPathSegment, ...] = Field(min_length=1)
    json_type: JsonType
    observation_count: int = Field(ge=1)
    duplicate_key_observed: bool = False


class ContextFormField(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: FormDirection
    field_name: ContextByteString
    observation_count: int = Field(ge=1)
    total_occurrence_count: int = Field(ge=1)
    total_assigned_occurrence_count: int = Field(ge=0)
    max_occurrence_count: int = Field(ge=1)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> Self:
        if self.total_assigned_occurrence_count > self.total_occurrence_count:
            raise ValueError("assigned occurrences cannot exceed total occurrences")
        if self.max_occurrence_count > self.total_occurrence_count:
            raise ValueError("maximum occurrences cannot exceed total occurrences")
        return self


class ContextMultipartPartName(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: ContextByteString
    occurrence_count: int = Field(ge=1)


class ContextMultipartPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: UUID
    direction: MultipartDirection
    part_index: int = Field(ge=0)
    disposition_type: str | None = None
    content_type: str | None = None
    name_parameter_count: int = Field(ge=0)
    filename_parameter_count: int = Field(ge=0)
    filename_empty_count: int = Field(ge=0)
    filename_nonempty_count: int = Field(ge=0)
    names: tuple[ContextMultipartPartName, ...] = ()

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
        identities = {(item.name.encoding, item.name.value) for item in self.names}
        if len(identities) != len(self.names):
            raise ValueError("multipart part names must be unique")
        return self


class ContextHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    project_id: UUID
    statement: str = Field(min_length=1)
    status: HypothesisStatus
    supporting_witness_observation_ids: tuple[UUID, ...] = ()
    contradicting_witness_observation_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def witnesses_are_present_and_disjoint(self) -> Self:
        supporting = set(self.supporting_witness_observation_ids)
        contradicting = set(self.contradicting_witness_observation_ids)
        if not supporting and not contradicting:
            raise ValueError("context hypothesis requires at least one witness")
        if supporting.intersection(contradicting):
            raise ValueError("a witness cannot both support and contradict")
        return self


class ContextSection(StrEnum):
    SIBLING_OPERATIONS = "SIBLING_OPERATIONS"
    METHOD_ADVERTISEMENTS = "METHOD_ADVERTISEMENTS"
    OBSERVATION_REFS = "OBSERVATION_REFS"
    QUERY_SHAPES = "QUERY_SHAPES"
    REQUEST_REPRESENTATIONS = "REQUEST_REPRESENTATIONS"
    RESPONSE_REPRESENTATIONS = "RESPONSE_REPRESENTATIONS"
    ACTOR_OUTCOMES = "ACTOR_OUTCOMES"
    JSON_DOCUMENT_OUTCOMES = "JSON_DOCUMENT_OUTCOMES"
    JSON_FIELDS = "JSON_FIELDS"
    FORM_DOCUMENT_OUTCOMES = "FORM_DOCUMENT_OUTCOMES"
    FORM_FIELDS = "FORM_FIELDS"
    MULTIPART_DOCUMENT_OUTCOMES = "MULTIPART_DOCUMENT_OUTCOMES"
    MULTIPART_PARTS = "MULTIPART_PARTS"
    EXPLORATION_LEADS = "EXPLORATION_LEADS"
    HYPOTHESES = "HYPOTHESES"


class ContextSectionCoverage(BaseModel):
    """Coverage relative to unique caller-supplied facts, not database totals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: ContextSection
    available_count: int = Field(ge=0)
    included_count: int = Field(ge=0)
    omitted_too_large_count: int = Field(ge=0)
    omitted_by_limit_count: int = Field(ge=0)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> Self:
        if self.available_count != (
            self.included_count
            + self.omitted_too_large_count
            + self.omitted_by_limit_count
        ):
            raise ValueError("coverage counts must sum to available count")
        return self


_SECTION_ATTRIBUTES = {
    ContextSection.SIBLING_OPERATIONS: "sibling_operations",
    ContextSection.METHOD_ADVERTISEMENTS: "method_advertisements",
    ContextSection.OBSERVATION_REFS: "observation_refs",
    ContextSection.QUERY_SHAPES: "query_shapes",
    ContextSection.REQUEST_REPRESENTATIONS: "request_representations",
    ContextSection.RESPONSE_REPRESENTATIONS: "response_representations",
    ContextSection.ACTOR_OUTCOMES: "actor_outcomes",
    ContextSection.JSON_DOCUMENT_OUTCOMES: "json_document_outcomes",
    ContextSection.JSON_FIELDS: "json_fields",
    ContextSection.FORM_DOCUMENT_OUTCOMES: "form_document_outcomes",
    ContextSection.FORM_FIELDS: "form_fields",
    ContextSection.MULTIPART_DOCUMENT_OUTCOMES: "multipart_document_outcomes",
    ContextSection.MULTIPART_PARTS: "multipart_parts",
    ContextSection.EXPLORATION_LEADS: "exploration_leads",
    ContextSection.HYPOTHESES: "hypotheses",
}


class OperationContextSnapshot(BaseModel):
    """A bounded view of supplied facts, not complete application truth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_version: Literal[1] = 1
    scope: Literal["ANCHOR_OPERATION_SAME_ENDPOINT_V1"] = (
        "ANCHOR_OPERATION_SAME_ENDPOINT_V1"
    )
    raw_messages_included: Literal[False] = False
    credential_material_included: Literal[False] = False
    scalar_application_values_included: Literal[False] = False

    project_id: UUID
    endpoint: ExactEndpoint
    operation: HttpOperation
    sibling_operations: tuple[HttpOperation, ...] = ()
    method_advertisements: tuple[MethodAdvertisement, ...] = ()
    observation_refs: tuple[ContextObservationRef, ...] = ()
    query_shapes: tuple[OperationQueryShape, ...] = ()
    request_representations: tuple[RequestRepresentation, ...] = ()
    response_representations: tuple[ResponseRepresentation, ...] = ()
    actor_outcomes: tuple[ActorOutcome, ...] = ()
    json_document_outcomes: tuple[OperationJsonDocumentOutcome, ...] = ()
    json_fields: tuple[ContextJsonField, ...] = ()
    form_document_outcomes: tuple[OperationFormDocumentOutcome, ...] = ()
    form_fields: tuple[ContextFormField, ...] = ()
    multipart_document_outcomes: tuple[OperationMultipartDocumentOutcome, ...] = ()
    multipart_parts: tuple[ContextMultipartPart, ...] = ()
    exploration_leads: tuple[ExplorationLead, ...] = ()
    hypotheses: tuple[ContextHypothesis, ...] = ()
    coverage: tuple[ContextSectionCoverage, ...]

    @model_validator(mode="after")
    def coverage_is_complete_and_matches_sections(self) -> Self:
        expected = tuple(ContextSection)
        actual = tuple(item.section for item in self.coverage)
        if actual != expected:
            raise ValueError("coverage must contain every section in canonical order")
        for item in self.coverage:
            attribute = _SECTION_ATTRIBUTES[item.section]
            if item.included_count != len(getattr(self, attribute)):
                raise ValueError("coverage included count must match snapshot section")
        return self
