import asyncio

import pytest

from refair.policy import ActiveRequestGate


def test_exactly_one_active_agent_operation_runs_at_a_time() -> None:
    async def scenario() -> int:
        gate = ActiveRequestGate()
        in_flight = 0
        peak_in_flight = 0
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def operation(is_first: bool) -> None:
            nonlocal in_flight, peak_in_flight
            async with gate:
                in_flight += 1
                peak_in_flight = max(peak_in_flight, in_flight)
                if is_first:
                    first_entered.set()
                    await release_first.wait()
                await asyncio.sleep(0)
                in_flight -= 1

        first = asyncio.create_task(operation(True))
        await first_entered.wait()
        second = asyncio.create_task(operation(False))
        await asyncio.sleep(0)
        assert in_flight == 1
        release_first.set()
        await asyncio.gather(first, second)
        return peak_in_flight

    assert asyncio.run(scenario()) == 1


def test_gate_refuses_any_limit_other_than_one() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ActiveRequestGate(2)


def test_passive_work_is_not_coupled_to_active_gate() -> None:
    async def scenario() -> tuple[bool, bool]:
        gate = ActiveRequestGate()
        active_entered = asyncio.Event()
        release_active = asyncio.Event()
        passive_finished = asyncio.Event()

        async def active() -> None:
            async with gate:
                active_entered.set()
                await release_active.wait()

        async def passive_browser_ingestion() -> None:
            await active_entered.wait()
            passive_finished.set()

        active_task = asyncio.create_task(active())
        passive_task = asyncio.create_task(passive_browser_ingestion())
        await passive_finished.wait()
        completed_while_active_held_gate = passive_task.done() and not active_task.done()
        release_active.set()
        await asyncio.gather(active_task, passive_task)
        return completed_while_active_held_gate, passive_finished.is_set()

    assert asyncio.run(scenario()) == (True, True)
