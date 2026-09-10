"""First deterministic ExplorationLead derivation rules."""

from __future__ import annotations

import json
from collections.abc import Iterable
from uuid import UUID, uuid5

from refair.models import (
    ExactEndpoint,
    ExactEndpointGrounding,
    ExplorationLead,
    HttpOperation,
    HttpOperationGrounding,
    JsonArrayItem,
    JsonDirection,
    JsonFieldObservation,
    JsonType,
    MethodAdvertisement,
    MultipartDirection,
    MultipartPartObservation,
    ObservationGrounding,
)

_C2_LEAD_NAMESPACE = UUID("f5577a68-653e-4e1d-b5a9-b6bf6dd6a1f8")


def _lead_id(rule: str, *components: object) -> UUID:
    identity = json.dumps(
        [rule, *components],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return uuid5(_C2_LEAD_NAMESPACE, identity)


def _endpoint_location(endpoint: ExactEndpoint) -> str:
    port = "" if endpoint.port is None else f":{endpoint.port}"
    return f"{endpoint.scheme}://{endpoint.host}{port}{endpoint.path}"


def derive_unobserved_advertised_method_leads(
    *,
    project_id: UUID,
    endpoint: ExactEndpoint,
    operations: Iterable[HttpOperation],
    advertisements: Iterable[MethodAdvertisement],
) -> tuple[ExplorationLead, ...]:
    """Derive one lead for each advertised exact method not yet observed."""

    if endpoint.project_id != project_id:
        raise ValueError("endpoint does not belong to project")

    unique_operations: dict[UUID, HttpOperation] = {}
    for operation in operations:
        if operation.endpoint_id != endpoint.id:
            raise ValueError("operation does not belong to endpoint")
        previous = unique_operations.get(operation.id)
        if previous is not None and previous != operation:
            raise ValueError("conflicting operation facts")
        unique_operations[operation.id] = operation

    witnesses: dict[str, dict[str, UUID]] = {}
    for advertisement in advertisements:
        if advertisement.endpoint_id != endpoint.id:
            raise ValueError("advertisement does not belong to endpoint")
        by_source = witnesses.setdefault(advertisement.advertised_method, {})
        source = advertisement.source.value
        previous = by_source.get(source)
        if previous is None or str(advertisement.observation_id) < str(previous):
            by_source[source] = advertisement.observation_id

    observed_methods = {operation.method for operation in unique_operations.values()}
    location = _endpoint_location(endpoint)
    leads: list[ExplorationLead] = []
    for method in sorted(witnesses):
        if method in observed_methods:
            continue
        by_source = witnesses[method]
        sources = tuple(sorted(by_source))
        witness_ids = tuple(sorted(set(by_source.values()), key=str))
        encoded_method = json.dumps(method, ensure_ascii=True)
        leads.append(
            ExplorationLead(
                id=_lead_id(
                    "advertised-method-unobserved:v1",
                    str(project_id),
                    str(endpoint.id),
                    method,
                ),
                project_id=project_id,
                question=(
                    f"Is advertised method {encoded_method} supported for exact "
                    f"endpoint {location}?"
                ),
                rationale=(
                    f"The exact method was advertised by {', '.join(sources)}, but "
                    "no matching HttpOperation has been observed for this endpoint. "
                    "An advertisement does not confirm support or authorization."
                ),
                grounding=(
                    ExactEndpointGrounding(endpoint_id=endpoint.id),
                    *(
                        ObservationGrounding(observation_id=observation_id)
                        for observation_id in witness_ids
                    ),
                ),
            )
        )
    return tuple(leads)


def _canonical_json_path(path: tuple[str | JsonArrayItem, ...]) -> str:
    components = [
        {"kind": "ARRAY_ITEM"}
        if segment is JsonArrayItem.ITEM
        else {"kind": "KEY", "value": segment}
        for segment in path
    ]
    return json.dumps(components, ensure_ascii=True, separators=(",", ":"))


def _display_json_path(path: tuple[str | JsonArrayItem, ...]) -> str:
    return "$" + "".join(
        "[*]"
        if segment is JsonArrayItem.ITEM
        else f"[{json.dumps(segment, ensure_ascii=True)}]"
        for segment in path
    )


def derive_json_duplicate_key_leads(
    *,
    project_id: UUID,
    operation: HttpOperation,
    fields: Iterable[JsonFieldObservation],
) -> tuple[ExplorationLead, ...]:
    """Derive one lead per observed duplicate-key path occurrence."""

    unique_fields: dict[
        tuple[UUID, JsonDirection, tuple[str | JsonArrayItem, ...], JsonType],
        JsonFieldObservation,
    ] = {}
    for field in fields:
        key = (field.observation_id, field.direction, field.path, field.json_type)
        previous = unique_fields.get(key)
        if previous is not None and previous != field:
            raise ValueError("conflicting JSON field facts")
        unique_fields[key] = field

    grouped: dict[
        tuple[UUID, JsonDirection, tuple[str | JsonArrayItem, ...]], set[str]
    ] = {}
    for field in unique_fields.values():
        if field.duplicate_key_observed:
            key = (field.observation_id, field.direction, field.path)
            grouped.setdefault(key, set()).add(field.json_type.value)

    leads: list[ExplorationLead] = []
    ordered = sorted(
        grouped.items(),
        key=lambda item: (
            str(item[0][0]),
            item[0][1].value,
            _canonical_json_path(item[0][2]),
        ),
    )
    for (observation_id, direction, path), json_types in ordered:
        canonical_path = _canonical_json_path(path)
        display_path = _display_json_path(path)
        type_names = ", ".join(sorted(json_types))
        leads.append(
            ExplorationLead(
                id=_lead_id(
                    "json-duplicate-key:v1",
                    str(project_id),
                    str(operation.id),
                    str(observation_id),
                    direction.value,
                    canonical_path,
                ),
                project_id=project_id,
                question=(
                    "Do components handling this operation interpret duplicate JSON "
                    f"keys consistently at path {display_path}?"
                ),
                rationale=(
                    f"Duplicate JSON object-key syntax was observed in {direction.value} "
                    f"at {display_path}; recorded JSON types: {type_names}. This is a "
                    "structural ambiguity, not confirmation of a parser differential "
                    "or vulnerability."
                ),
                grounding=(
                    ObservationGrounding(observation_id=observation_id),
                    HttpOperationGrounding(operation_id=operation.id),
                ),
            )
        )
    return tuple(leads)


def derive_multipart_disposition_ambiguity_leads(
    *,
    project_id: UUID,
    operation: HttpOperation,
    parts: Iterable[MultipartPartObservation],
) -> tuple[ExplorationLead, ...]:
    """Derive one lead per part with duplicate disposition parameters."""

    unique_parts: dict[
        tuple[UUID, MultipartDirection, int], MultipartPartObservation
    ] = {}
    for part in parts:
        key = (part.observation_id, part.direction, part.part_index)
        previous = unique_parts.get(key)
        if previous is not None and previous != part:
            raise ValueError("conflicting multipart part facts")
        unique_parts[key] = part

    leads: list[ExplorationLead] = []
    for key in sorted(
        unique_parts,
        key=lambda item: (str(item[0]), item[1].value, item[2]),
    ):
        part = unique_parts[key]
        if part.name_parameter_count <= 1 and part.filename_parameter_count <= 1:
            continue
        leads.append(
            ExplorationLead(
                id=_lead_id(
                    "multipart-ambiguous-disposition:v1",
                    str(project_id),
                    str(operation.id),
                    str(part.observation_id),
                    part.direction.value,
                    part.part_index,
                ),
                project_id=project_id,
                question=(
                    "Could components interpret duplicate multipart "
                    "Content-Disposition parameters differently for "
                    f"{part.direction.value} part {part.part_index}?"
                ),
                rationale=(
                    f"The actual part occurrence contains "
                    f"name_parameter_count={part.name_parameter_count} and "
                    f"filename_parameter_count={part.filename_parameter_count}. "
                    "This records structural ambiguity, not a known parser "
                    "differential or vulnerability."
                ),
                grounding=(
                    ObservationGrounding(observation_id=part.observation_id),
                    HttpOperationGrounding(operation_id=operation.id),
                ),
            )
        )
    return tuple(leads)
