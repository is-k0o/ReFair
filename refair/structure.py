"""Deterministic V0.2-B1 structural extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID, uuid5

from refair.models.evidence import Observation
from refair.models.normalized import NormalizedExchange
from refair.models.structure import (
    ExactEndpoint,
    HttpOperation,
    MethodAdvertisement,
    MethodAdvertisementSource,
)

STRUCTURAL_VERSION = 1

_ENDPOINT_NAMESPACE = UUID("740d7447-12db-4c69-995d-9c42eb096218")
_OPERATION_NAMESPACE = UUID("cfc8b926-e55d-4092-98f4-30150610355d")
_ADVERTISEMENT_HEADERS = {
    "allow": MethodAdvertisementSource.ALLOW_HEADER,
    "access-control-allow-methods": MethodAdvertisementSource.CORS_ALLOW_METHODS,
}


@dataclass(frozen=True)
class StructuralExtraction:
    observation_id: UUID
    structural_version: int
    endpoint: ExactEndpoint
    operation: HttpOperation
    advertisements: tuple[MethodAdvertisement, ...]


def _identity(namespace: UUID, components: tuple[object, ...]) -> UUID:
    canonical = json.dumps(components, ensure_ascii=True, separators=(",", ":"))
    return uuid5(namespace, canonical)


def _response_headers(raw_response: bytes | None) -> tuple[tuple[str, str], ...]:
    if raw_response is None:
        return ()
    separators = tuple(
        (position, separator)
        for separator in (b"\r\n\r\n", b"\n\n")
        if (position := raw_response.find(separator)) >= 0
    )
    header_bytes = (
        raw_response[: min(separators, key=lambda item: item[0])[0]]
        if separators
        else raw_response
    )
    lines = header_bytes.decode("latin-1").splitlines()
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if separator:
            headers.append((name.strip().lower(), value))
    return tuple(headers)


def _method_advertisements(
    observation: Observation, endpoint_id: UUID
) -> tuple[MethodAdvertisement, ...]:
    advertisements: list[MethodAdvertisement] = []
    for header_name, header_value in _response_headers(observation.raw_response):
        source = _ADVERTISEMENT_HEADERS.get(header_name)
        if source is None:
            continue
        for value in header_value.split(","):
            advertised_method = value.strip()
            if advertised_method:
                advertisements.append(
                    MethodAdvertisement(
                        endpoint_id=endpoint_id,
                        source=source,
                        advertised_method=advertised_method,
                        observation_id=observation.id,
                    )
                )
    return tuple(advertisements)


def extract_structure(
    observation: Observation, normalized: NormalizedExchange
) -> StructuralExtraction:
    """Extract exact endpoint, operation, link, and passive advertisements."""

    if normalized.observation_id != observation.id:
        raise ValueError("normalized exchange does not belong to observation")
    endpoint_id = _identity(
        _ENDPOINT_NAMESPACE,
        (
            str(observation.project_id),
            normalized.scheme,
            normalized.host,
            normalized.port,
            normalized.path,
        ),
    )
    endpoint = ExactEndpoint(
        id=endpoint_id,
        project_id=observation.project_id,
        scheme=normalized.scheme,
        host=normalized.host,
        port=normalized.port,
        path=normalized.path,
    )
    operation = HttpOperation(
        id=_identity(_OPERATION_NAMESPACE, (str(endpoint_id), observation.method)),
        endpoint_id=endpoint_id,
        method=observation.method,
    )
    return StructuralExtraction(
        observation_id=observation.id,
        structural_version=STRUCTURAL_VERSION,
        endpoint=endpoint,
        operation=operation,
        advertisements=_method_advertisements(observation, endpoint_id),
    )
