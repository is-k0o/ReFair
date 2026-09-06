"""Content-first identity for static response assets."""

from __future__ import annotations

import gzip
import hashlib
import zlib

from pydantic import BaseModel, ConfigDict, Field


class Asset(BaseModel):
    """One immutable content version and all URLs where it was observed."""

    model_config = ConfigDict(frozen=True)

    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    body: bytes
    observed_urls: tuple[str, ...] = ()


def normalize_response_body(body: bytes, content_encoding: str | None = None) -> bytes:
    """Return identity bytes, decompressing standard HTTP encodings when declared.

    No textual rewriting is performed: whitespace and line endings are content.
    """

    encoding = (content_encoding or "identity").strip().lower()
    if encoding in {"", "identity"}:
        return body
    if encoding in {"gzip", "x-gzip"}:
        return gzip.decompress(body)
    if encoding == "deflate":
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    raise ValueError(f"unsupported content encoding for asset identity: {content_encoding}")


def content_sha256(body: bytes, content_encoding: str | None = None) -> str:
    normalized = normalize_response_body(body, content_encoding)
    return hashlib.sha256(normalized).hexdigest()
