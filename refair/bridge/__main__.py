"""Command-line entry point for the passive collector."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

import uvicorn

from refair.bridge.collector import create_app
from refair.config import load_config


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the ReFair passive collector")
    parser.add_argument("--config", default="config.example.yaml")
    arguments = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    config = load_config(arguments.config)
    app = create_app(config)
    print("ReFair passive collector")
    print(f"bind: {config.bridge.bind_address}:{config.bridge.port}")
    print(f"database: {config.bridge.sqlite_path}")
    print("actors:")
    for actor_id, actor in config.actors.items():
        print(f"  {actor_id} -> listener {actor.listener}")
    uvicorn.run(
        app,
        host=config.bridge.bind_address,
        port=config.bridge.port,
        log_level="info",
        use_colors=False,
    )


if __name__ == "__main__":
    main()
