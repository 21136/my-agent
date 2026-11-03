from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from work_card_update_coalescer import ProjectWorkCardUpdateCoalescer


def test_first_update_deadline_is_not_extended_and_uses_latest_publisher():
    async def run():
        coalescer = ProjectWorkCardUpdateCoalescer(delay_seconds=0.06)
        sent = []
        started = asyncio.get_running_loop().time()
        coalescer.schedule("project-a", lambda: sent.append(("old", asyncio.get_running_loop().time())))
        await asyncio.sleep(0.04)
        coalescer.schedule("project-a", lambda: sent.append(("latest", asyncio.get_running_loop().time())))
        await asyncio.sleep(0.035)
        assert [name for name, _ in sent] == ["latest"]
        assert sent[0][1] - started < 0.095
        coalescer.close()
    asyncio.run(run())


def test_projects_have_independent_windows_and_immediate_update_does_not_wait():
    async def run():
        coalescer = ProjectWorkCardUpdateCoalescer(delay_seconds=0.08)
        sent = []
        coalescer.schedule("project-a", lambda: sent.append("a"))
        coalescer.schedule("project-b", lambda: sent.append("b"))
        coalescer.schedule("project-a", lambda: sent.append("a-immediate"), immediate=True)
        assert sent == ["a-immediate"]
        await asyncio.sleep(0.1)
        assert sent == ["a-immediate", "b"]
        coalescer.close()
    asyncio.run(run())


def test_close_cancels_pending_publish_without_running_callback():
    async def run():
        coalescer = ProjectWorkCardUpdateCoalescer(delay_seconds=0.01)
        sent = []
        coalescer.schedule("project-a", lambda: sent.append("published"))
        coalescer.close()
        await asyncio.sleep(0.03)
        assert sent == []
    asyncio.run(run())


def test_schedule_can_resume_with_persisted_remaining_delay():
    async def run():
        coalescer = ProjectWorkCardUpdateCoalescer(delay_seconds=30.0)
        sent = []
        coalescer.schedule("project-a", lambda: sent.append("ready"), delay_seconds=0.01)
        await asyncio.sleep(0.03)
        assert sent == ["ready"]
        coalescer.close()
    asyncio.run(run())
