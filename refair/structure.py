"""Deterministic structural extraction from immutable observation evidence."""

from __future__ import annotations

import gzip
import json
import zlib
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from uuid import UUID, uuid5

from refair.models.evidence import Observation
from refair.models.normalized import BodyKind, NormalizedExchange
from refair.models.structure import (
    ExactEndpoint,
    FormDirection,
    FormDocument,
    FormFieldObservation,
    FormParseStatus,
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
    MultipartDirection,
    MultipartDocument,
    MultipartParseStatus,
    MultipartPartName,
    MultipartPartObservation,
)
from refair.normalization import _split_headers_and_body

# If normalization changes scheme/host/port/path, structural identity must be
# invalidated with a STRUCTURAL_VERSION bump and a deliberate derived-state rebuild.
STRUCTURAL_VERSION = 5

MAX_JSON_BODY_BYTES = 1_048_576
MAX_JSON_DECODED_BYTES = 1_048_576
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 10_000
MAX_FORM_BODY_BYTES = 1_048_576
MAX_FORM_DECODED_BYTES = 1_048_576
MAX_FORM_FIELDS = 10_000
MAX_FORM_FIELD_NAME_BYTES = 16_384
MAX_MULTIPART_BODY_BYTES = 1_048_576
MAX_MULTIPART_DECODED_BYTES = 1_048_576
MAX_MULTIPART_PARTS = 1_000
MAX_MULTIPART_BOUNDARY_BYTES = 1_024
MAX_MULTIPART_TOP_HEADER_BYTES = 65_536
MAX_MULTIPART_PART_HEADER_BYTES = 65_536
MAX_MULTIPART_NAME_BYTES = 16_384
MAX_MULTIPART_DISPOSITION_PARAMETERS = 256

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
    form_documents: tuple[FormDocument, ...]
    form_fields: tuple[FormFieldObservation, ...]
    multipart_documents: tuple[MultipartDocument, ...]
    multipart_parts: tuple[MultipartPartObservation, ...]


@dataclass(frozen=True)
class _JsonObject:
    pairs: tuple[tuple[str, object], ...]


class _JsonLimitExceeded(Exception):
    pass


class _FormLimitExceeded(Exception):
    pass


class _MultipartMalformed(Exception):
    pass


class _MultipartLimitExceeded(Exception):
    pass


class _RepresentationDecodeStatus(Enum):
    SKIPPED_TOO_LARGE = "SKIPPED_TOO_LARGE"
    UNSUPPORTED_CONTENT_ENCODING = "UNSUPPORTED_CONTENT_ENCODING"
    CONTENT_DECODING_FAILED = "CONTENT_DECODING_FAILED"


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


def _decode_gzip(
    body: bytes, *, max_decoded_bytes: int
) -> tuple[bytes | None, _RepresentationDecodeStatus | None]:
    try:
        with gzip.GzipFile(fileobj=BytesIO(body), mode="rb") as stream:
            decoded = stream.read(max_decoded_bytes + 1)
    except (EOFError, MemoryError, OSError, OverflowError, zlib.error):
        return None, _RepresentationDecodeStatus.CONTENT_DECODING_FAILED
    if len(decoded) > max_decoded_bytes:
        return None, _RepresentationDecodeStatus.SKIPPED_TOO_LARGE
    return decoded, None


def _decode_representation(
    raw_message: bytes,
    *,
    role: str,
    max_body_bytes: int,
    max_decoded_bytes: int,
) -> tuple[bytes, bytes | None, _RepresentationDecodeStatus | None]:
    headers, body = _split_headers_and_body(raw_message, role, [])
    if len(body) > max_body_bytes:
        return headers, None, _RepresentationDecodeStatus.SKIPPED_TOO_LARGE
    encoding = _content_encoding(headers)
    if encoding is None:
        return (
            headers,
            None,
            _RepresentationDecodeStatus.UNSUPPORTED_CONTENT_ENCODING,
        )
    if encoding == "gzip":
        decoded, status = _decode_gzip(
            body, max_decoded_bytes=max_decoded_bytes
        )
        return headers, decoded, status
    if len(body) > max_decoded_bytes:
        return headers, None, _RepresentationDecodeStatus.SKIPPED_TOO_LARGE
    return headers, body, None


def _extract_json_document(
    observation_id: UUID,
    direction: JsonDirection,
    raw_message: bytes,
) -> tuple[JsonDocument, tuple[JsonFieldObservation, ...]]:
    _, body, decode_status = _decode_representation(
        raw_message,
        role=direction.value.lower(),
        max_body_bytes=MAX_JSON_BODY_BYTES,
        max_decoded_bytes=MAX_JSON_DECODED_BYTES,
    )
    if decode_status is not None:
        return (
            JsonDocument(
                observation_id=observation_id,
                direction=direction,
                parse_status=JsonParseStatus(decode_status.value),
            ),
            (),
        )
    assert body is not None
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


def _decode_form_field_name(value: bytes) -> bytes:
    decoded = bytearray()
    index = 0
    hexadecimal = b"0123456789abcdefABCDEF"
    while index < len(value):
        byte = value[index]
        if byte == ord("+"):
            decoded.append(ord(" "))
            index += 1
        elif (
            byte == ord("%")
            and index + 2 < len(value)
            and value[index + 1] in hexadecimal
            and value[index + 2] in hexadecimal
        ):
            decoded.append(int(value[index + 1 : index + 3], 16))
            index += 3
        else:
            decoded.append(byte)
            index += 1
        if len(decoded) > MAX_FORM_FIELD_NAME_BYTES:
            raise _FormLimitExceeded
    return bytes(decoded)


def _extract_form_document(
    observation_id: UUID,
    direction: FormDirection,
    raw_message: bytes,
) -> tuple[FormDocument, tuple[FormFieldObservation, ...]]:
    _, body, decode_status = _decode_representation(
        raw_message,
        role=direction.value.lower(),
        max_body_bytes=MAX_FORM_BODY_BYTES,
        max_decoded_bytes=MAX_FORM_DECODED_BYTES,
    )
    if decode_status is not None:
        return (
            FormDocument(
                observation_id=observation_id,
                direction=direction,
                parse_status=FormParseStatus(decode_status.value),
            ),
            (),
        )
    assert body is not None

    counts: dict[bytes, list[int]] = {}
    occurrences = 0
    start = 0
    try:
        while start <= len(body):
            end = body.find(b"&", start)
            if end < 0:
                end = len(body)
                final = True
            else:
                final = False
            segment = body[start:end]
            if segment:
                occurrences += 1
                if occurrences > MAX_FORM_FIELDS:
                    raise _FormLimitExceeded
                equals = segment.find(b"=")
                assigned = equals >= 0
                encoded_name = segment if not assigned else segment[:equals]
                field_name = _decode_form_field_name(encoded_name)
                field_counts = counts.setdefault(field_name, [0, 0])
                field_counts[0] += 1
                field_counts[1] += int(assigned)
            if final:
                break
            start = end + 1
    except _FormLimitExceeded:
        return (
            FormDocument(
                observation_id=observation_id,
                direction=direction,
                parse_status=FormParseStatus.LIMIT_EXCEEDED,
            ),
            (),
        )

    fields = tuple(
        FormFieldObservation(
            observation_id=observation_id,
            direction=direction,
            field_name=field_name,
            occurrence_count=field_counts[0],
            assigned_occurrence_count=field_counts[1],
        )
        for field_name, field_counts in sorted(counts.items())
    )
    return (
        FormDocument(
            observation_id=observation_id,
            direction=direction,
            parse_status=FormParseStatus.PARSED,
        ),
        fields,
    )


def _form_structure(
    observation: Observation, normalized: NormalizedExchange
) -> tuple[tuple[FormDocument, ...], tuple[FormFieldObservation, ...]]:
    documents: list[FormDocument] = []
    fields: list[FormFieldObservation] = []
    candidates = (
        (
            FormDirection.REQUEST,
            normalized.request_body_kind,
            observation.raw_request,
        ),
        (
            FormDirection.RESPONSE,
            normalized.response_body_kind,
            observation.raw_response,
        ),
    )
    for direction, body_kind, raw_message in candidates:
        if body_kind is not BodyKind.FORM or raw_message is None:
            continue
        document, document_fields = _extract_form_document(
            observation.id, direction, raw_message
        )
        documents.append(document)
        fields.extend(document_fields)
    return tuple(documents), tuple(fields)


_TOKEN_FORBIDDEN = frozenset(b'()<>@,;:\\"/[]?={} \t')


def _is_ascii_token(value: bytes) -> bool:
    return bool(value) and all(
        33 <= byte <= 126 and byte not in _TOKEN_FORBIDDEN for byte in value
    )


def _semicolon_components(value: bytes) -> tuple[bytes, ...]:
    components: list[bytes] = []
    start = 0
    quoted = False
    escaped = False
    for index, byte in enumerate(value):
        if byte in {ord("\r"), ord("\n")}:
            raise _MultipartMalformed
        if quoted:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                quoted = False
        elif byte == ord('"'):
            quoted = True
        elif byte == ord(";"):
            components.append(value[start:index])
            start = index + 1
    if quoted or escaped:
        raise _MultipartMalformed
    components.append(value[start:])
    return tuple(components)


def _parameter_value(value: bytes) -> tuple[bytes, bool]:
    value = value.strip(b" \t")
    if not value.startswith(b'"'):
        if not _is_ascii_token(value):
            raise _MultipartMalformed
        return value, False

    decoded = bytearray()
    escaped = False
    closing_index: int | None = None
    for index in range(1, len(value)):
        byte = value[index]
        if escaped:
            decoded.append(byte)
            escaped = False
        elif byte == ord("\\"):
            escaped = True
        elif byte == ord('"'):
            closing_index = index
            break
        else:
            decoded.append(byte)
    if escaped or closing_index is None:
        raise _MultipartMalformed
    if value[closing_index + 1 :].strip(b" \t"):
        raise _MultipartMalformed
    return bytes(decoded), True


def _parameterized_header_value(
    value: bytes, *, max_parameters: int
) -> tuple[bytes, tuple[tuple[bytes, bytes, bool], ...]]:
    components = _semicolon_components(value)
    token = components[0].strip(b" \t").lower()
    parameter_components = components[1:]
    if len(parameter_components) > max_parameters:
        raise _MultipartLimitExceeded
    parameters: list[tuple[bytes, bytes, bool]] = []
    for component in parameter_components:
        name, separator, raw_value = component.partition(b"=")
        name = name.strip(b" \t").lower()
        if not separator or not _is_ascii_token(name):
            raise _MultipartMalformed
        decoded, quoted = _parameter_value(raw_value)
        parameters.append((name, decoded, quoted))
    return token, tuple(parameters)


def _media_type(value: bytes) -> str:
    type_name, separator, subtype = value.partition(b"/")
    if not separator or not _is_ascii_token(type_name) or not _is_ascii_token(subtype):
        raise _MultipartMalformed
    try:
        return f"{type_name.decode('ascii').lower()}/{subtype.decode('ascii').lower()}"
    except UnicodeDecodeError as error:
        raise _MultipartMalformed from error


def _top_content_type(headers: bytes) -> bytes:
    if len(headers) > MAX_MULTIPART_TOP_HEADER_BYTES:
        raise _MultipartLimitExceeded
    if b"\r" in headers.replace(b"\r\n", b""):
        raise _MultipartMalformed
    values: list[bytes] = []
    for line in headers.replace(b"\r\n", b"\n").split(b"\n")[1:]:
        name, separator, value = line.partition(b":")
        if separator and name.strip().lower() == b"content-type":
            values.append(value)
    if len(values) != 1:
        raise _MultipartMalformed
    token, parameters = _parameterized_header_value(
        values[0], max_parameters=MAX_MULTIPART_DISPOSITION_PARAMETERS
    )
    if _media_type(token) != "multipart/form-data":
        raise _MultipartMalformed
    boundaries = [
        (value, quoted)
        for name, value, quoted in parameters
        if name == b"boundary"
    ]
    if len(boundaries) != 1:
        raise _MultipartMalformed
    boundary, quoted = boundaries[0]
    if len(boundary) > MAX_MULTIPART_BOUNDARY_BYTES:
        raise _MultipartLimitExceeded
    if not boundary or b"\r" in boundary or b"\n" in boundary:
        raise _MultipartMalformed
    if not quoted and not _is_ascii_token(boundary):
        raise _MultipartMalformed
    return boundary


def _multipart_part_blobs(body: bytes, boundary: bytes) -> tuple[bytes, ...]:
    marker = b"--" + boundary
    parts: list[bytes] = []
    active = False
    part_start = 0
    line_start = 0
    while line_start <= len(body):
        newline = body.find(b"\n", line_start)
        if newline < 0:
            line = body[line_start:]
            next_line = len(body) + 1
        else:
            line = body[line_start:newline]
            if line.endswith(b"\r"):
                line = line[:-1]
            next_line = newline + 1
        if line in {marker, marker + b"--"}:
            closing = line == marker + b"--"
            if not active:
                if closing:
                    return ()
                active = True
            else:
                parts.append(body[part_start:line_start])
                if len(parts) > MAX_MULTIPART_PARTS:
                    raise _MultipartLimitExceeded
                if closing:
                    return tuple(parts)
            part_start = next_line
        if newline < 0:
            break
        line_start = next_line
    raise _MultipartMalformed


def _part_header_block(part: bytes) -> bytes:
    if part.startswith(b"\r\n"):
        return b""
    if part.startswith(b"\n"):
        return b""
    separators = tuple(
        (position, separator)
        for separator in (b"\r\n\r\n", b"\n\n")
        if (position := part.find(separator)) >= 0
    )
    if not separators:
        if len(part) > MAX_MULTIPART_PART_HEADER_BYTES:
            raise _MultipartLimitExceeded
        raise _MultipartMalformed
    position, _ = min(separators, key=lambda item: item[0])
    if position > MAX_MULTIPART_PART_HEADER_BYTES:
        raise _MultipartLimitExceeded
    return part[:position]


def _selected_part_headers(headers: bytes) -> dict[bytes, list[bytes]]:
    if len(headers) > MAX_MULTIPART_PART_HEADER_BYTES:
        raise _MultipartLimitExceeded
    if b"\r" in headers.replace(b"\r\n", b""):
        raise _MultipartMalformed
    selected = {b"content-disposition": [], b"content-type": []}
    for line in headers.replace(b"\r\n", b"\n").split(b"\n"):
        if not line:
            continue
        if line.startswith((b" ", b"\t")):
            raise _MultipartMalformed
        name, separator, value = line.partition(b":")
        normalized_name = name.strip().lower()
        if not separator or not _is_ascii_token(normalized_name):
            raise _MultipartMalformed
        if normalized_name in selected:
            selected[normalized_name].append(value)
    return selected


def _multipart_part(
    observation_id: UUID,
    direction: MultipartDirection,
    part_index: int,
    part: bytes,
) -> MultipartPartObservation:
    selected = _selected_part_headers(_part_header_block(part))
    disposition_values = selected[b"content-disposition"]
    content_type_values = selected[b"content-type"]
    if len(disposition_values) > 1 or len(content_type_values) > 1:
        raise _MultipartMalformed

    disposition_type: str | None = None
    name_parameter_count = 0
    names: dict[bytes, int] = {}
    filename_parameter_count = 0
    filename_empty_count = 0
    filename_nonempty_count = 0
    if disposition_values:
        token, parameters = _parameterized_header_value(
            disposition_values[0],
            max_parameters=MAX_MULTIPART_DISPOSITION_PARAMETERS,
        )
        if not _is_ascii_token(token):
            raise _MultipartMalformed
        try:
            disposition_type = token.decode("ascii").lower()
        except UnicodeDecodeError as error:
            raise _MultipartMalformed from error
        for name, value, _ in parameters:
            if name == b"name":
                name_parameter_count += 1
                if len(value) > MAX_MULTIPART_NAME_BYTES:
                    raise _MultipartLimitExceeded
                names[value] = names.get(value, 0) + 1
            elif name == b"filename":
                filename_parameter_count += 1
                if value:
                    filename_nonempty_count += 1
                else:
                    filename_empty_count += 1

    content_type: str | None = None
    if content_type_values:
        token, _ = _parameterized_header_value(
            content_type_values[0],
            max_parameters=MAX_MULTIPART_DISPOSITION_PARAMETERS,
        )
        content_type = _media_type(token)

    return MultipartPartObservation(
        observation_id=observation_id,
        direction=direction,
        part_index=part_index,
        disposition_type=disposition_type,
        content_type=content_type,
        name_parameter_count=name_parameter_count,
        filename_parameter_count=filename_parameter_count,
        filename_empty_count=filename_empty_count,
        filename_nonempty_count=filename_nonempty_count,
        names=tuple(
            MultipartPartName(name=name, occurrence_count=count)
            for name, count in sorted(names.items())
        ),
    )


def _extract_multipart_document(
    observation_id: UUID,
    direction: MultipartDirection,
    raw_message: bytes,
) -> tuple[MultipartDocument, tuple[MultipartPartObservation, ...]]:
    headers, body, decode_status = _decode_representation(
        raw_message,
        role=direction.value.lower(),
        max_body_bytes=MAX_MULTIPART_BODY_BYTES,
        max_decoded_bytes=MAX_MULTIPART_DECODED_BYTES,
    )
    if decode_status is not None:
        return (
            MultipartDocument(
                observation_id=observation_id,
                direction=direction,
                parse_status=MultipartParseStatus(decode_status.value),
            ),
            (),
        )
    assert body is not None
    try:
        boundary = _top_content_type(headers)
        parts = tuple(
            _multipart_part(observation_id, direction, index, part)
            for index, part in enumerate(_multipart_part_blobs(body, boundary))
        )
    except _MultipartLimitExceeded:
        status = MultipartParseStatus.LIMIT_EXCEEDED
    except _MultipartMalformed:
        status = MultipartParseStatus.MALFORMED
    else:
        return (
            MultipartDocument(
                observation_id=observation_id,
                direction=direction,
                parse_status=MultipartParseStatus.PARSED,
            ),
            parts,
        )
    return (
        MultipartDocument(
            observation_id=observation_id,
            direction=direction,
            parse_status=status,
        ),
        (),
    )


def _multipart_structure(
    observation: Observation, normalized: NormalizedExchange
) -> tuple[tuple[MultipartDocument, ...], tuple[MultipartPartObservation, ...]]:
    documents: list[MultipartDocument] = []
    parts: list[MultipartPartObservation] = []
    candidates = (
        (
            MultipartDirection.REQUEST,
            normalized.request_body_kind,
            normalized.request_content_type,
            observation.raw_request,
        ),
        (
            MultipartDirection.RESPONSE,
            normalized.response_body_kind,
            normalized.response_content_type,
            observation.raw_response,
        ),
    )
    for direction, body_kind, content_type, raw_message in candidates:
        if (
            body_kind is not BodyKind.MULTIPART
            or content_type != "multipart/form-data"
            or raw_message is None
        ):
            continue
        document, document_parts = _extract_multipart_document(
            observation.id, direction, raw_message
        )
        documents.append(document)
        parts.extend(document_parts)
    return tuple(documents), tuple(parts)


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
    form_documents, form_fields = _form_structure(observation, normalized)
    multipart_documents, multipart_parts = _multipart_structure(
        observation, normalized
    )
    return StructuralExtraction(
        observation_id=observation.id,
        structural_version=STRUCTURAL_VERSION,
        endpoint=endpoint,
        operation=operation,
        advertisements=_method_advertisements(observation, endpoint_id),
        json_documents=json_documents,
        json_fields=json_fields,
        form_documents=form_documents,
        form_fields=form_fields,
        multipart_documents=multipart_documents,
        multipart_parts=multipart_parts,
    )
