"""Pure deterministic normalization of immutable raw observations."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import unquote_plus, urlsplit

from refair.models.evidence import Observation
from refair.models.normalized import BodyKind, NormalizedExchange

NORMALIZER_VERSION = 1


@dataclass(frozen=True)
class _BodyMetadata:
    content_type: str | None
    kind: BodyKind
    size: int
    digest: str | None


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _split_headers_and_body(
    raw_message: bytes, role: str, warnings: list[str]
) -> tuple[bytes, bytes]:
    separators = tuple(
        (position, separator)
        for separator in (b"\r\n\r\n", b"\n\n")
        if (position := raw_message.find(separator)) >= 0
    )
    if not separators:
        warnings.append(f"{role}_header_body_separator_missing")
        return raw_message, b""
    position, separator = min(separators, key=lambda item: item[0])
    return raw_message[:position], raw_message[position + len(separator) :]


def _content_type_from_headers(
    header_bytes: bytes, role: str, warnings: list[str]
) -> str | None:
    lines = header_bytes.decode("latin-1").splitlines()
    if not lines or not lines[0].strip():
        warnings.append(f"{role}_start_line_missing")
        header_lines: list[str] = []
    else:
        start_line = lines[0].strip()
        header_lines = lines[1:]
        if role == "request":
            parts = start_line.split()
            if len(parts) < 3 or not parts[-1].upper().startswith("HTTP/"):
                warnings.append("request_start_line_unrecognized")
        elif not start_line.upper().startswith("HTTP/"):
            warnings.append("response_start_line_unrecognized")

    values: list[str] = []
    malformed = False
    for line in header_lines:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            malformed = True
            continue
        if name.strip().lower() == "content-type":
            values.append(value.strip())
    if malformed:
        warnings.append(f"{role}_malformed_header")
    if len(values) > 1:
        warnings.append(f"{role}_multiple_content_type_headers")
    if not values:
        return None
    media_type = values[0].partition(";")[0].strip().lower()
    if not media_type:
        warnings.append(f"{role}_empty_content_type")
        return None
    return media_type


def _body_kind(content_type: str | None, body: bytes) -> BodyKind:
    if not body:
        return BodyKind.EMPTY
    if content_type == "application/json" or (
        content_type is not None and content_type.endswith("+json")
    ):
        return BodyKind.JSON
    if content_type == "application/x-www-form-urlencoded":
        return BodyKind.FORM
    if content_type is not None and content_type.startswith("multipart/"):
        return BodyKind.MULTIPART
    if content_type is not None and content_type.startswith("text/"):
        return BodyKind.TEXT
    return BodyKind.OTHER


def _message_metadata(
    raw_message: bytes | None, role: str, warnings: list[str]
) -> _BodyMetadata:
    if raw_message is None:
        warnings.append(f"{role}_message_missing")
        return _BodyMetadata(None, BodyKind.EMPTY, 0, None)
    headers, body = _split_headers_and_body(raw_message, role, warnings)
    content_type = _content_type_from_headers(headers, role, warnings)
    return _BodyMetadata(
        content_type=content_type,
        kind=_body_kind(content_type, body),
        size=len(body),
        digest=_digest(body) if body else None,
    )


def normalize_observation(observation: Observation) -> NormalizedExchange:
    """Project one Observation without modifying or copying its raw evidence."""

    warnings: list[str] = []
    raw_query = observation.url.partition("#")[0].partition("?")
    query_text = raw_query[2] if raw_query[1] else ""
    query_names = (
        tuple(unquote_plus(item.partition("=")[0]) for item in query_text.split("&"))
        if query_text
        else ()
    )
    query_digest = (
        _digest(query_text.encode("utf-8", errors="surrogatepass"))
        if raw_query[1]
        else None
    )

    try:
        parsed = urlsplit(observation.url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        invalid_port = False
        try:
            port = parsed.port
        except ValueError:
            port = None
            invalid_port = True
            warnings.append("url_port_invalid")
        if port is not None and not 1 <= port <= 65535:
            port = None
            invalid_port = True
            warnings.append("url_port_invalid")
        if port is None and not invalid_port:
            port = {"http": 80, "https": 443}.get(scheme)
        if not scheme:
            warnings.append("url_scheme_missing")
        if not host:
            warnings.append("url_host_missing")
    except ValueError:
        scheme = ""
        host = ""
        port = None
        path = "/"
        warnings.append("url_unparseable")

    request = _message_metadata(observation.raw_request, "request", warnings)
    response = _message_metadata(observation.raw_response, "response", warnings)
    return NormalizedExchange(
        observation_id=observation.id,
        normalizer_version=NORMALIZER_VERSION,
        scheme=scheme,
        host=host,
        port=port,
        path=path,
        query_parameter_names=query_names,
        raw_query_sha256=query_digest,
        request_content_type=request.content_type,
        request_body_kind=request.kind,
        request_body_size=request.size,
        request_body_sha256=request.digest,
        response_content_type=response.content_type,
        response_body_kind=response.kind,
        response_body_size=response.size,
        response_body_sha256=response.digest,
        warnings=tuple(warnings),
    )
