"""Pure operation-centric context projection and deterministic bounding."""

from __future__ import annotations

import json
from collections.abc import Callable, Hashable, Iterable
from datetime import timezone
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from refair.models import (
    ActorOutcome,
    ContextByteString,
    ContextFormField,
    ContextHypothesis,
    ContextJsonArrayItem,
    ContextJsonField,
    ContextJsonKey,
    ContextMultipartPart,
    ContextMultipartPartName,
    ContextObservationRef,
    ContextSection,
    ContextSectionCoverage,
    ExactEndpoint,
    ExactEndpointGrounding,
    ExplorationLead,
    HttpOperation,
    HttpOperationGrounding,
    JsonArrayItem,
    MethodAdvertisement,
    MultipartPartObservation,
    OperationContextSnapshot,
    OperationFormDocumentOutcome,
    OperationFormField,
    OperationJsonDocumentOutcome,
    OperationJsonField,
    OperationMultipartDocumentOutcome,
    OperationQueryShape,
    RequestRepresentation,
    ResponseRepresentation,
)

_MAX_CONFIGURED_SERIALIZED_BYTES = 16_777_216
_T = TypeVar("_T")
_P = TypeVar("_P", bound=BaseModel)


class ContextLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_sibling_operations: int = Field(default=16, ge=0)
    max_method_advertisements: int = Field(default=32, ge=0)
    max_observation_refs: int = Field(default=16, ge=0)
    max_query_shapes: int = Field(default=32, ge=0)
    max_request_representations: int = Field(default=16, ge=0)
    max_response_representations: int = Field(default=32, ge=0)
    max_actor_outcomes: int = Field(default=32, ge=0)
    max_json_document_outcomes: int = Field(default=16, ge=0)
    max_json_fields: int = Field(default=128, ge=0)
    max_form_document_outcomes: int = Field(default=16, ge=0)
    max_form_fields: int = Field(default=128, ge=0)
    max_multipart_document_outcomes: int = Field(default=16, ge=0)
    max_multipart_parts: int = Field(default=64, ge=0)
    max_exploration_leads: int = Field(default=32, ge=0)
    max_hypotheses: int = Field(default=32, ge=0)
    max_anchor_serialized_bytes: int = Field(
        default=16_384, gt=0, le=_MAX_CONFIGURED_SERIALIZED_BYTES
    )
    max_item_serialized_bytes: int = Field(
        default=4_096, gt=0, le=_MAX_CONFIGURED_SERIALIZED_BYTES
    )


DEFAULT_CONTEXT_LIMITS = ContextLimits()


class OperationContextInput(BaseModel):
    """Caller-selected facts; this contract makes no database completeness claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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
    json_fields: tuple[OperationJsonField, ...] = ()
    form_document_outcomes: tuple[OperationFormDocumentOutcome, ...] = ()
    form_fields: tuple[OperationFormField, ...] = ()
    multipart_document_outcomes: tuple[OperationMultipartDocumentOutcome, ...] = ()
    multipart_parts: tuple[MultipartPartObservation, ...] = ()
    exploration_leads: tuple[ExplorationLead, ...] = ()
    hypotheses: tuple[ContextHypothesis, ...] = ()


def _serialized_size(model: BaseModel) -> int:
    serialized = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return len(serialized.encode("utf-8"))


def _deduplicate(
    values: Iterable[_T],
    *,
    identity: Callable[[_T], Hashable],
    label: str,
) -> tuple[_T, ...]:
    unique: dict[Hashable, _T] = {}
    for value in values:
        key = identity(value)
        previous = unique.get(key)
        if previous is not None and previous != value:
            raise ValueError(f"conflicting {label} facts")
        unique[key] = value
    return tuple(unique.values())


def _bounded_section(
    *,
    section: ContextSection,
    values: Iterable[_T],
    identity: Callable[[_T], Hashable],
    sort_key: Callable[[_T], object],
    project: Callable[[_T], _P],
    limit: int,
    max_item_serialized_bytes: int,
    label: str,
) -> tuple[tuple[_P, ...], ContextSectionCoverage]:
    unique = _deduplicate(values, identity=identity, label=label)
    ordered = sorted(unique, key=sort_key)
    eligible: list[_P] = []
    omitted_too_large = 0
    for value in ordered:
        projected = project(value)
        if _serialized_size(projected) > max_item_serialized_bytes:
            omitted_too_large += 1
        else:
            eligible.append(projected)
    included = tuple(eligible[:limit])
    coverage = ContextSectionCoverage(
        section=section,
        available_count=len(unique),
        included_count=len(included),
        omitted_too_large_count=omitted_too_large,
        omitted_by_limit_count=len(eligible) - len(included),
    )
    return included, coverage


def _project_bytes(value: bytes) -> ContextByteString:
    encoded: list[str] = []
    for byte in value:
        if 0x20 <= byte <= 0x7E and byte != 0x5C:
            encoded.append(chr(byte))
        elif byte == 0x5C:
            encoded.append("\\\\")
        else:
            encoded.append(f"\\x{byte:02x}")
    return ContextByteString(value="".join(encoded))


def _json_path_key(path: tuple[str | JsonArrayItem, ...]) -> str:
    return json.dumps(
        [
            {"kind": "ARRAY_ITEM"}
            if segment is JsonArrayItem.ITEM
            else {"kind": "KEY", "value": segment}
            for segment in path
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _project_json_field(field: OperationJsonField) -> ContextJsonField:
    path = tuple(
        ContextJsonArrayItem()
        if segment is JsonArrayItem.ITEM
        else ContextJsonKey(value=segment)
        for segment in field.path
    )
    return ContextJsonField(
        direction=field.direction,
        path=path,
        json_type=field.json_type,
        observation_count=field.observation_count,
        duplicate_key_observed=field.duplicate_key_observed,
    )


def _project_form_field(field: OperationFormField) -> ContextFormField:
    return ContextFormField(
        direction=field.direction,
        field_name=_project_bytes(field.field_name),
        observation_count=field.observation_count,
        total_occurrence_count=field.total_occurrence_count,
        total_assigned_occurrence_count=field.total_assigned_occurrence_count,
        max_occurrence_count=field.max_occurrence_count,
    )


def _project_multipart_part(part: MultipartPartObservation) -> ContextMultipartPart:
    return ContextMultipartPart(
        observation_id=part.observation_id,
        direction=part.direction,
        part_index=part.part_index,
        disposition_type=part.disposition_type,
        content_type=part.content_type,
        name_parameter_count=part.name_parameter_count,
        filename_parameter_count=part.filename_parameter_count,
        filename_empty_count=part.filename_empty_count,
        filename_nonempty_count=part.filename_nonempty_count,
        names=tuple(
            ContextMultipartPartName(
                name=_project_bytes(name.name),
                occurrence_count=name.occurrence_count,
            )
            for name in part.names
        ),
    )


def _optional_text_key(value: str | None) -> tuple[bool, str]:
    return value is not None, value or ""


def _optional_int_key(value: int | None) -> tuple[bool, int]:
    return value is not None, value or 0


def _observation_sort_key(ref: ContextObservationRef) -> tuple[object, ...]:
    observed_at = ref.observed_at.astimezone(timezone.utc)
    return (
        -observed_at.toordinal(),
        -observed_at.hour,
        -observed_at.minute,
        -observed_at.second,
        -observed_at.microsecond,
        str(ref.observation_id),
    )


def compile_operation_context(
    source: OperationContextInput,
    limits: ContextLimits = DEFAULT_CONTEXT_LIMITS,
) -> OperationContextSnapshot:
    """Compile a bounded snapshot of unique facts supplied by the caller.

    Coverage counts are relative to this input after logical deduplication and
    anchor exclusion. They are not project-wide or database-wide totals.
    """

    if source.endpoint.project_id != source.project_id:
        raise ValueError("endpoint does not belong to context project")
    if source.operation.endpoint_id != source.endpoint.id:
        raise ValueError("anchor operation does not belong to endpoint")
    if _serialized_size(source.endpoint) > limits.max_anchor_serialized_bytes:
        raise ValueError("anchor endpoint exceeds serialized byte limit")
    if _serialized_size(source.operation) > limits.max_anchor_serialized_bytes:
        raise ValueError("anchor operation exceeds serialized byte limit")

    siblings: list[HttpOperation] = []
    for sibling in source.sibling_operations:
        if sibling.endpoint_id != source.endpoint.id:
            raise ValueError("sibling operation does not belong to endpoint")
        if sibling.id == source.operation.id:
            if sibling != source.operation:
                raise ValueError("conflicting anchor operation in sibling facts")
            continue
        siblings.append(sibling)

    for advertisement in source.method_advertisements:
        if advertisement.endpoint_id != source.endpoint.id:
            raise ValueError("method advertisement does not belong to endpoint")
    for ref in source.observation_refs:
        if ref.project_id != source.project_id:
            raise ValueError("observation reference does not belong to project")
    for lead in source.exploration_leads:
        if lead.project_id != source.project_id:
            raise ValueError("exploration lead does not belong to project")
        grounded = any(
            (
                isinstance(item, HttpOperationGrounding)
                and item.operation_id == source.operation.id
            )
            or (
                isinstance(item, ExactEndpointGrounding)
                and item.endpoint_id == source.endpoint.id
            )
            for item in lead.grounding
        )
        if not grounded:
            raise ValueError("exploration lead is not grounded in anchor operation")
    for hypothesis in source.hypotheses:
        if hypothesis.project_id != source.project_id:
            raise ValueError("context hypothesis does not belong to project")

    coverage: list[ContextSectionCoverage] = []

    def bound(**kwargs):
        values, section_coverage = _bounded_section(
            max_item_serialized_bytes=limits.max_item_serialized_bytes,
            **kwargs,
        )
        coverage.append(section_coverage)
        return values

    sibling_operations = bound(
        section=ContextSection.SIBLING_OPERATIONS,
        values=siblings,
        identity=lambda item: item.id,
        sort_key=lambda item: (item.method, str(item.id)),
        project=lambda item: item,
        limit=limits.max_sibling_operations,
        label="sibling operation",
    )
    method_advertisements = bound(
        section=ContextSection.METHOD_ADVERTISEMENTS,
        values=source.method_advertisements,
        identity=lambda item: (
            item.advertised_method,
            item.source,
            item.observation_id,
        ),
        sort_key=lambda item: (
            item.advertised_method,
            item.source.value,
            str(item.observation_id),
        ),
        project=lambda item: item,
        limit=limits.max_method_advertisements,
        label="method advertisement",
    )
    observation_refs = bound(
        section=ContextSection.OBSERVATION_REFS,
        values=source.observation_refs,
        identity=lambda item: item.observation_id,
        sort_key=_observation_sort_key,
        project=lambda item: item,
        limit=limits.max_observation_refs,
        label="observation reference",
    )
    query_shapes = bound(
        section=ContextSection.QUERY_SHAPES,
        values=source.query_shapes,
        identity=lambda item: (item.query_present, item.query_parameter_names),
        sort_key=lambda item: (item.query_present, item.query_parameter_names),
        project=lambda item: item,
        limit=limits.max_query_shapes,
        label="query shape",
    )
    request_representations = bound(
        section=ContextSection.REQUEST_REPRESENTATIONS,
        values=source.request_representations,
        identity=lambda item: (item.content_type, item.body_kind),
        sort_key=lambda item: (*_optional_text_key(item.content_type), item.body_kind.value),
        project=lambda item: item,
        limit=limits.max_request_representations,
        label="request representation",
    )
    response_representations = bound(
        section=ContextSection.RESPONSE_REPRESENTATIONS,
        values=source.response_representations,
        identity=lambda item: (item.response_status, item.content_type, item.body_kind),
        sort_key=lambda item: (
            *_optional_int_key(item.response_status),
            *_optional_text_key(item.content_type),
            item.body_kind.value,
        ),
        project=lambda item: item,
        limit=limits.max_response_representations,
        label="response representation",
    )
    actor_outcomes = bound(
        section=ContextSection.ACTOR_OUTCOMES,
        values=source.actor_outcomes,
        identity=lambda item: (item.actor_id, item.provenance, item.response_status),
        sort_key=lambda item: (
            *_optional_text_key(item.actor_id),
            item.provenance.value,
            *_optional_int_key(item.response_status),
        ),
        project=lambda item: item,
        limit=limits.max_actor_outcomes,
        label="actor outcome",
    )
    json_document_outcomes = bound(
        section=ContextSection.JSON_DOCUMENT_OUTCOMES,
        values=source.json_document_outcomes,
        identity=lambda item: (item.direction, item.parse_status, item.root_type),
        sort_key=lambda item: (
            item.direction.value,
            item.parse_status.value,
            item.root_type.value if item.root_type is not None else "",
        ),
        project=lambda item: item,
        limit=limits.max_json_document_outcomes,
        label="JSON document outcome",
    )
    json_fields = bound(
        section=ContextSection.JSON_FIELDS,
        values=source.json_fields,
        identity=lambda item: (item.direction, item.path, item.json_type),
        sort_key=lambda item: (
            item.direction.value,
            _json_path_key(item.path),
            item.json_type.value,
        ),
        project=_project_json_field,
        limit=limits.max_json_fields,
        label="JSON field",
    )
    form_document_outcomes = bound(
        section=ContextSection.FORM_DOCUMENT_OUTCOMES,
        values=source.form_document_outcomes,
        identity=lambda item: (item.direction, item.parse_status),
        sort_key=lambda item: (item.direction.value, item.parse_status.value),
        project=lambda item: item,
        limit=limits.max_form_document_outcomes,
        label="FORM document outcome",
    )
    form_fields = bound(
        section=ContextSection.FORM_FIELDS,
        values=source.form_fields,
        identity=lambda item: (item.direction, item.field_name),
        sort_key=lambda item: (item.direction.value, item.field_name),
        project=_project_form_field,
        limit=limits.max_form_fields,
        label="FORM field",
    )
    multipart_document_outcomes = bound(
        section=ContextSection.MULTIPART_DOCUMENT_OUTCOMES,
        values=source.multipart_document_outcomes,
        identity=lambda item: (item.direction, item.parse_status),
        sort_key=lambda item: (item.direction.value, item.parse_status.value),
        project=lambda item: item,
        limit=limits.max_multipart_document_outcomes,
        label="multipart document outcome",
    )
    multipart_parts = bound(
        section=ContextSection.MULTIPART_PARTS,
        values=source.multipart_parts,
        identity=lambda item: (item.observation_id, item.direction, item.part_index),
        sort_key=lambda item: (
            str(item.observation_id),
            item.direction.value,
            item.part_index,
        ),
        project=_project_multipart_part,
        limit=limits.max_multipart_parts,
        label="multipart part",
    )
    exploration_leads = bound(
        section=ContextSection.EXPLORATION_LEADS,
        values=source.exploration_leads,
        identity=lambda item: item.id,
        sort_key=lambda item: str(item.id),
        project=lambda item: item,
        limit=limits.max_exploration_leads,
        label="exploration lead",
    )
    hypotheses = bound(
        section=ContextSection.HYPOTHESES,
        values=source.hypotheses,
        identity=lambda item: item.id,
        sort_key=lambda item: str(item.id),
        project=lambda item: item,
        limit=limits.max_hypotheses,
        label="context hypothesis",
    )

    return OperationContextSnapshot(
        project_id=source.project_id,
        endpoint=source.endpoint,
        operation=source.operation,
        sibling_operations=sibling_operations,
        method_advertisements=method_advertisements,
        observation_refs=observation_refs,
        query_shapes=query_shapes,
        request_representations=request_representations,
        response_representations=response_representations,
        actor_outcomes=actor_outcomes,
        json_document_outcomes=json_document_outcomes,
        json_fields=json_fields,
        form_document_outcomes=form_document_outcomes,
        form_fields=form_fields,
        multipart_document_outcomes=multipart_document_outcomes,
        multipart_parts=multipart_parts,
        exploration_leads=exploration_leads,
        hypotheses=hypotheses,
        coverage=tuple(coverage),
    )
