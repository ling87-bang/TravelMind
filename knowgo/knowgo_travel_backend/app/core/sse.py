"""Serialize SSE events and keep idle model/tool streams connected."""
import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import suppress
from typing import Any


def serialize_event(event: dict[str, Any]) -> str:
    """Encode a business event without optional null fields."""
    frame = {key: value for key, value in event.items() if value is not None}
    return f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"


async def stream_sse(
    events: AsyncIterable[dict[str, Any]], heartbeat_interval: float = 10.0,
) -> AsyncIterator[str]:
    """Send comments during idle periods without cancelling the pending event."""
    iterator = aiter(events)
    pending = None
    try:
        # Flush response headers before routing, model inference or external I/O.
        yield ": connected\n\n"
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(iterator))
            ready, _ = await asyncio.wait({pending}, timeout=heartbeat_interval)
            if not ready:
                # SSE comments are ignored by the frontend's data-frame parser.
                yield ": keep-alive\n\n"
                continue
            completed, pending = pending, None
            try:
                event = completed.result()
            except StopAsyncIteration:
                break
            yield serialize_event(event)
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()

