"""Transport regressions for quiet and disconnected Agent streams."""
import asyncio
import json

import pytest

from app.core.sse import stream_sse


@pytest.mark.asyncio
async def test_heartbeat_preserves_pending_events_and_done():
    release = asyncio.Event()
    source_cancelled = False

    async def events():
        nonlocal source_cancelled
        try:
            await release.wait()
            yield {"role": "assistant", "content": "行程正文", "code": None}
            yield {"role": "done", "code": 200}
        finally:
            source_cancelled = True

    frames = stream_sse(events(), heartbeat_interval=0.01)
    assert await anext(frames) == ": connected\n\n"
    assert await anext(frames) == ": keep-alive\n\n"
    assert await anext(frames) == ": keep-alive\n\n"
    assert not source_cancelled
    release.set()
    remaining = [frame async for frame in frames]
    payloads = [json.loads(frame[6:]) for frame in remaining if frame.startswith("data: ")]
    assert payloads == [
        {"role": "assistant", "content": "行程正文"}, {"role": "done", "code": 200},
    ]
    assert source_cancelled


@pytest.mark.asyncio
async def test_disconnect_cancels_pending_source_read():
    closed = asyncio.Event()

    async def events():
        try:
            await asyncio.Event().wait()
            yield {"role": "assistant", "content": "unreachable"}
        finally:
            closed.set()

    frames = stream_sse(events(), heartbeat_interval=0.01)
    await anext(frames)
    await anext(frames)
    await frames.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_disconnect_after_frame_closes_source_iterator():
    closed = asyncio.Event()
    async def events():
        try:
            yield {"role": "assistant", "content": "first"}
            await asyncio.Event().wait()
        finally:
            closed.set()
    frames = stream_sse(events())
    await anext(frames)
    await anext(frames)
    await frames.aclose()
    assert closed.is_set()
