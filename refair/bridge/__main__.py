"""Command-line entry point for the passive collector."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from refair.bridge.collector import create_app
from refair.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ReFair passive collector")
    parser.add_argument("--config", default="config.example.yaml")
    arguments = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    config = load_config(arguments.config)
    app = create_app(config)
    uvicorn.run(
        app,
        host=config.bridge.bind_address,
        port=config.bridge.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
