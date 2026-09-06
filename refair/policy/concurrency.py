"""Concurrency guard for agent-generated active operations only."""

from __future__ import annotations

import asyncio
from types import TracebackType

MAX_AGENT_INFLIGHT_REQUESTS = 1


class ActiveRequestGate:
    """An async context manager intended to wrap only the future active executor."""

    def __init__(self, limit: int = MAX_AGENT_INFLIGHT_REQUESTS) -> None:
        if limit != MAX_AGENT_INFLIGHT_REQUESTS:
            raise ValueError("ReFair V0 requires exactly one agent request in flight")
        self._semaphore = asyncio.Semaphore(limit)

    async def __aenter__(self) -> ActiveRequestGate:
        await self._semaphore.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._semaphore.release()
