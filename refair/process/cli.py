"""Command-line processor for pending deterministic derived state."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from refair.config import load_config
from refair.normalization import normalize_observation
from refair.storage import SQLiteRepository


@dataclass(frozen=True)
class ProcessResult:
    processed: int
    pending: int
    warnings: int


def process_pending(repository: SQLiteRepository) -> ProcessResult:
    processed = 0
    warning_count = 0
    for observation in repository.list_pending_observations():
        exchange = normalize_observation(observation)
        repository.add_normalized_exchange(exchange)
        processed += 1
        warning_count += len(exchange.warnings)
    return ProcessResult(
        processed=processed,
        pending=repository.count_pending_observations(),
        warnings=warning_count,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refair-process",
        description="Process pending ReFair observations into deterministic derived state.",
    )
    parser.add_argument("--config", default="config.example.yaml")
    return parser


def run(arguments: argparse.Namespace) -> int:
    config = load_config(arguments.config)
    repository = SQLiteRepository(config.bridge.sqlite_path)
    repository.initialize()
    result = process_pending(repository)
    print(f"processed: {result.processed}")
    print(f"pending: {result.pending}")
    print(f"warnings: {result.warnings}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        return run(arguments)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
        print(f"refair-process: {error}", file=sys.stderr)
        return 1
