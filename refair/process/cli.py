"""Command-line processor for pending deterministic derived state."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from refair.config import load_config
from refair.normalization import NORMALIZER_VERSION, normalize_observation
from refair.storage import SQLiteRepository
from refair.structure import STRUCTURAL_VERSION, extract_structure

NORMALIZATION_BATCH_SIZE = 250
STRUCTURAL_BATCH_SIZE = 250


@dataclass(frozen=True)
class PhaseResult:
    processed: int
    pending: int
    warnings: int


@dataclass(frozen=True)
class ProcessResult:
    normalization: PhaseResult
    structure: PhaseResult


def process_normalization_pending(repository: SQLiteRepository) -> PhaseResult:
    processed = 0
    warning_count = 0
    while batch := repository.list_pending_observations(
        target_normalizer_version=NORMALIZER_VERSION,
        limit=NORMALIZATION_BATCH_SIZE,
    ):
        for observation in batch:
            exchange = normalize_observation(observation)
            repository.add_normalized_exchange(exchange)
            processed += 1
            warning_count += len(exchange.warnings)
    return PhaseResult(
        processed=processed,
        pending=repository.count_pending_observations(
            target_normalizer_version=NORMALIZER_VERSION
        ),
        warnings=warning_count,
    )


def process_structure_pending(repository: SQLiteRepository) -> PhaseResult:
    processed = 0
    while batch := repository.list_pending_structural_inputs(
        target_structural_version=STRUCTURAL_VERSION,
        limit=STRUCTURAL_BATCH_SIZE,
    ):
        for observation, normalized in batch:
            extraction = extract_structure(observation, normalized)
            if repository.add_structural_extraction(extraction):
                processed += 1
    return PhaseResult(
        processed=processed,
        pending=repository.count_pending_structural_observations(
            target_structural_version=STRUCTURAL_VERSION
        ),
        warnings=0,
    )


def process_pending(repository: SQLiteRepository) -> ProcessResult:
    return ProcessResult(
        normalization=process_normalization_pending(repository),
        structure=process_structure_pending(repository),
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
    print(f"normalized_processed: {result.normalization.processed}")
    print(f"normalization_pending: {result.normalization.pending}")
    print(f"structural_processed: {result.structure.processed}")
    print(f"structural_pending: {result.structure.pending}")
    print(f"warnings: {result.normalization.warnings + result.structure.warnings}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        return run(arguments)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
        print(f"refair-process: {error}", file=sys.stderr)
        return 1
