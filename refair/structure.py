"""Deterministic structural extraction from immutable observation evidence."""

from __future__ import annotations

import gzip
import json
import zlib
from dataclasses import dataclass
from io import BytesIO
from uuid import UUID, uuid5

from refair.models.evidence import Observation
from refair.models.normalized import BodyKind, NormalizedExchange
from refair.models.structure import (
    ExactEndpoint,
    HttpOperation,
    JsonArrayItem,
    JsonDirection,
    JsonDocument,
    JsonFieldObservation,
    JsonParseStatus,
    JsonPath,
    JsonType,
    MethodAdvertisement,
    MethodAdvertisementSource,
)
from refair.normalization import _split_headers_and_body

# If normalization changes scheme/host/port/path, structural identity must be
# invalidated with a STRUCTURAL_VERSION bump and a deliberate derived-state rebuild.
STRUCTURAL_VERSION = 3

MAX_JSON_BODY_BYTES = 1_048_576
MAX_JSON_DECODED_BYTES = 1_048_576
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 10_000

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
    json_documents: tuple[JsonDocument, ...]
    json_fields: tuple[JsonFieldObservation, ...]


@dataclass(frozen=True)
class _JsonObject:
    pairs: tuple[tuple[str, object], ...]


class _JsonLimitExceeded(Exception):
    pass


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


def _json_type(value: object) -> JsonType:
    if isinstance(value, _JsonObject):
        return JsonType.OBJECT
    if isinstance(value, list):
        return JsonType.ARRAY
    if isinstance(value, str):
        return JsonType.STRING
    if isinstance(value, bool):
        return JsonType.BOOLEAN
    if isinstance(value, (int, float)):
        return JsonType.NUMBER
    if value is None:
        return JsonType.NULL
    raise TypeError("unsupported decoded JSON value")


def _reject_nonstandard_constant(_value: str) -> object:
    raise ValueError("non-standard JSON constant")


def _walk_json(
    observation_id: UUID, direction: JsonDirection, root: object
) -> tuple[JsonFieldObservation, ...]:
    facts: dict[tuple[JsonPath, JsonType], bool] = {}
    stack: list[tuple[object, JsonPath, int, bool]] = [(root, (), 0, False)]
    nodes = 0
    while stack:
        value, path, depth, duplicate = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise _JsonLimitExceeded

        value_type = _json_type(value)
        if path:
            key = (path, value_type)
            facts[key] = facts.get(key, False) or duplicate

        if isinstance(value, _JsonObject):
            seen: set[str] = set()
            duplicates: set[str] = set()
            for name, _ in value.pairs:
                if name in seen:
                    duplicates.add(name)
                seen.add(name)
            for name, child in reversed(value.pairs):
                stack.append((child, (*path, name), depth + 1, name in duplicates))
        elif isinstance(value, list):
            for child in reversed(value):
                stack.append(
                    (child, (*path, JsonArrayItem.ITEM), depth + 1, False)
                )

    return tuple(
        JsonFieldObservation(
            observation_id=observation_id,
            direction=direction,
            path=path,
            json_type=value_type,
            duplicate_key_observed=duplicate,
        )
        for (path, value_type), duplicate in sorted(
            facts.items(), key=lambda item: (_canonical_path(item[0][0]), item[0][1])
        )
    )


def _canonical_path(path: JsonPath) -> str:
    return json.dumps(
        [None if segment is JsonArrayItem.ITEM else segment for segment in path],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _content_encoding(header_bytes: bytes) -> str | None:
    values: list[str] = []
    for line in header_bytes.decode("latin-1").splitlines()[1:]:
        name, separator, value = line.partition(":")
        if separator and name.strip().lower() == "content-encoding":
            values.append(value)
    if not values:
        return "identity"
    if len(values) != 1:
        return None

    tokens = [token.strip().lower() for token in values[0].split(",")]
    if not tokens or any(not token for token in tokens):
        return None
    non_identity = [token for token in tokens if token != "identity"]
    if not non_identity:
        return "identity"
    if len(non_identity) == 1 and non_identity[0] in {"gzip", "x-gzip"}:
        return "gzip"
    return None


def _decode_gzip(body: bytes) -> tuple[bytes | None, JsonParseStatus | None]:
    try:
        with gzip.GzipFile(fileobj=BytesIO(body), mode="rb") as stream:
            decoded = stream.read(MAX_JSON_DECODED_BYTES + 1)
    except (EOFError, MemoryError, OSError, OverflowError, zlib.error):
        return None, JsonParseStatus.CONTENT_DECODING_FAILED
    if len(decoded) > MAX_JSON_DECODED_BYTES:
        return None, JsonParseStatus.SKIPPED_TOO_LARGE
    return decoded, None


def _extract_json_document(
    observation_id: UUID,
    direction: JsonDirection,
    raw_message: bytes,
) -> tuple[JsonDocument, tuple[JsonFieldObservation, ...]]:
    headers, body = _split_headers_and_body(
        raw_message, direction.value.lower(), []
    )
    if len(body) > MAX_JSON_BODY_BYTES:
        return (
            JsonDocument(
                observation_id=observation_id,
                direction=direction,
                parse_status=JsonParseStatus.SKIPPED_TOO_LARGE,
            ),
            (),
        )
    encoding = _content_encoding(headers)
    if encoding is None:
        return (
            JsonDocument(
                observation_id=observation_id,
                direction=direction,
                parse_status=JsonParseStatus.UNSUPPORTED_CONTENT_ENCODING,
            ),
            (),
        )
    if encoding == "gzip":
        body, decode_status = _decode_gzip(body)
        if decode_status is not None:
            return (
                JsonDocument(
                    observation_id=observation_id,
                    direction=direction,
                    parse_status=decode_status,
                ),
                (),
            )
        assert body is not None
    if len(body) > MAX_JSON_DECODED_BYTES:
        return (
            JsonDocument(
                observation_id=observation_id,
                direction=direction,
                parse_status=JsonParseStatus.SKIPPED_TOO_LARGE,
            ),
            (),
        )
    try:
        root = json.loads(
            body,
            object_pairs_hook=lambda pairs: _JsonObject(tuple(pairs)),
            parse_constant=_reject_nonstandard_constant,
        )
    except (MemoryError, RecursionError):
        status = JsonParseStatus.LIMIT_EXCEEDED
    except (UnicodeDecodeError, ValueError):
        status = JsonParseStatus.MALFORMED
    else:
        try:
            fields = _walk_json(observation_id, direction, root)
        except _JsonLimitExceeded:
            status = JsonParseStatus.LIMIT_EXCEEDED
        else:
            return (
                JsonDocument(
                    observation_id=observation_id,
                    direction=direction,
                    parse_status=JsonParseStatus.PARSED,
                    root_type=_json_type(root),
                ),
                fields,
            )
    return (
        JsonDocument(
            observation_id=observation_id,
            direction=direction,
            parse_status=status,
        ),
        (),
    )


def _json_structure(
    observation: Observation, normalized: NormalizedExchange
) -> tuple[tuple[JsonDocument, ...], tuple[JsonFieldObservation, ...]]:
    documents: list[JsonDocument] = []
    fields: list[JsonFieldObservation] = []
    candidates = (
        (
            JsonDirection.REQUEST,
            normalized.request_body_kind,
            observation.raw_request,
        ),
        (
            JsonDirection.RESPONSE,
            normalized.response_body_kind,
            observation.raw_response,
        ),
    )
    for direction, body_kind, raw_message in candidates:
        if body_kind is not BodyKind.JSON or raw_message is None:
            continue
        document, document_fields = _extract_json_document(
            observation.id, direction, raw_message
        )
        documents.append(document)
        fields.extend(document_fields)
    return tuple(documents), tuple(fields)


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
    json_documents, json_fields = _json_structure(observation, normalized)
    return StructuralExtraction(
        observation_id=observation.id,
        structural_version=STRUCTURAL_VERSION,
        endpoint=endpoint,
        operation=operation,
        advertisements=_method_advertisements(observation, endpoint_id),
        json_documents=json_documents,
        json_fields=json_fields,
    )
