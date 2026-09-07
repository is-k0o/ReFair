"""Small, read-only command-line inspector for immutable observations."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from urllib.parse import urlsplit
from uuid import UUID

from refair.config import load_config
from refair.models import Observation, ObservationProvenance
from refair.storage import SQLiteRepository

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 1000
MAX_PATH_DISPLAY = 120
MAX_RAW_PREVIEW_BYTES = 4096


def _bounded_positive(value: str, *, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{label} must be an integer") from error
    if not 1 <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"{label} must be between 1 and {maximum}"
        )
    return parsed


def _list_limit(value: str) -> int:
    return _bounded_positive(value, maximum=MAX_LIST_LIMIT, label="limit")


def _preview_length(value: str) -> int:
    return _bounded_positive(
        value, maximum=MAX_RAW_PREVIEW_BYTES, label="raw preview length"
    )


def _http_status(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("HTTP status must be an integer") from error
    if not 100 <= parsed <= 599:
        raise argparse.ArgumentTypeError("HTTP status must be between 100 and 599")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refair-inspect",
        description="Read-only inspection of ReFair observations.",
        epilog=(
            "Raw evidence may contain credentials, cookies, session tokens, "
            "personal data, or other sensitive content. Raw preview is bounded "
            "and must be requested explicitly."
        ),
    )
    parser.add_argument("--config", default="config.example.yaml")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("summary", help="show aggregate observation counts")

    list_parser = commands.add_parser("list", help="list recent observations")
    list_parser.add_argument("--limit", type=_list_limit, default=DEFAULT_LIST_LIMIT)
    list_parser.add_argument("--actor")
    list_parser.add_argument(
        "--provenance", choices=[item.value for item in ObservationProvenance]
    )
    list_parser.add_argument("--method", type=str.upper)
    list_parser.add_argument("--status", type=_http_status)
    list_parser.add_argument(
        "--full-url",
        action="store_true",
        help="show complete stored URLs, including query strings",
    )

    show_parser = commands.add_parser("show", help="show one observation's metadata")
    show_parser.add_argument("observation_id", type=UUID)
    show_parser.add_argument(
        "--raw-preview",
        type=_preview_length,
        metavar="BYTES",
        help=(
            f"show at most BYTES of each raw message (maximum "
            f"{MAX_RAW_PREVIEW_BYTES}; may expose credentials or personal data)"
        ),
    )
    return parser


def _display_location(url: str, *, full_url: bool) -> str:
    if full_url:
        return url
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or "<unknown-host>"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
    except ValueError:
        return "<invalid-url>"
    path = parsed.path or "/"
    if len(path) > MAX_PATH_DISPLAY:
        path = f"{path[: MAX_PATH_DISPLAY - 3]}..."
    return f"{host}{path}"


def _print_summary(repository: SQLiteRepository) -> None:
    summary = repository.observation_summary()
    print(f"Database: {repository.path}")
    print(f"Observations: {summary.total}")
    print("\nBy actor:")
    if summary.by_actor:
        for actor, count in summary.by_actor.items():
            print(f"  {actor}: {count}")
    else:
        print("  (none)")
    print("\nBy provenance:")
    if summary.by_provenance:
        for provenance, count in summary.by_provenance.items():
            print(f"  {provenance}: {count}")
    else:
        print("  (none)")


def _print_list(repository: SQLiteRepository, arguments: argparse.Namespace) -> None:
    provenance = (
        ObservationProvenance(arguments.provenance)
        if arguments.provenance is not None
        else None
    )
    observations = repository.recent_observation_metadata(
        limit=arguments.limit,
        actor_id=arguments.actor,
        provenance=provenance,
        method=arguments.method,
        response_status=arguments.status,
    )
    for observation in observations:
        location = _display_location(observation.url, full_url=arguments.full_url)
        status = observation.response_status or "-"
        actor = observation.actor_id or "-"
        print(
            f"{observation.observed_at.isoformat()}  {actor}  "
            f"{observation.provenance.value}  {observation.method}  {status}  "
            f"{location}  req={observation.request_size}B  "
            f"resp={observation.response_size}B"
        )


def _print_show(observation: Observation, raw_preview: int | None) -> None:
    print(f"ID: {observation.id}")
    print(f"Project ID: {observation.project_id}")
    print(f"Timestamp: {observation.observed_at.isoformat()}")
    print(f"Actor: {observation.actor_id or '-'}")
    print(f"Provenance: {observation.provenance.value}")
    print(f"Method: {observation.method}")
    print(f"URL: {observation.url}")
    print(f"Status: {observation.response_status or '-'}")
    print(f"Request bytes: {len(observation.raw_request)}")
    print(f"Response bytes: {len(observation.raw_response or b'')}")

    if raw_preview is not None:
        print(
            "\nWARNING: raw evidence may contain credentials, cookies, tokens, "
            "personal data, or other sensitive content."
        )
        print(
            f"Raw request preview ({min(raw_preview, len(observation.raw_request))}/"
            f"{len(observation.raw_request)} bytes): "
            f"{observation.raw_request[:raw_preview]!r}"
        )
        response = observation.raw_response or b""
        print(
            f"Raw response preview ({min(raw_preview, len(response))}/"
            f"{len(response)} bytes): {response[:raw_preview]!r}"
        )


def run(arguments: argparse.Namespace) -> int:
    config = load_config(arguments.config)
    repository = SQLiteRepository(config.bridge.sqlite_path, read_only=True)
    if arguments.command == "summary":
        _print_summary(repository)
        return 0
    if arguments.command == "list":
        _print_list(repository, arguments)
        return 0

    observation = repository.get_observation(arguments.observation_id)
    if observation is None:
        print(f"Observation not found: {arguments.observation_id}", file=sys.stderr)
        return 1
    _print_show(observation, arguments.raw_preview)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return run(arguments)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"refair-inspect: {error}", file=sys.stderr)
        return 1
