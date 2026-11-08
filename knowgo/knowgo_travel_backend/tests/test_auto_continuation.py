"""Automatic selections must continue into report generation in the same turn."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.event_stream import EventStream


@pytest.mark.asyncio
async def test_auto_hotel_postprocessing_continues_until_report(monkeypatch):
    from app.services import chat_stream_executor as executor

    state = {"current_step": "transport_planning", "auto_confirm": True}
    agent = SimpleNamespace(aget_state=AsyncMock(side_effect=lambda config: SimpleNamespace(values=dict(state))))
    monkeypatch.setattr(executor, "create_travel_agent", AsyncMock(return_value=agent))
    body = [{"day": 1, "city": "上海"}]

    async def once(user_id, conversation_id, content, planning, stream):
        if state["current_step"] == "transport_planning":
            state["current_step"] = "accommodation_planning"
            return "车次已选定", "accommodation_planning", False, None
        if state["current_step"] == "accommodation_planning":
            state["current_step"] = "itinerary_generation"
            # Reproduce the old return value after auto_select_hotels updates state.
            return "住宿已选定", "accommodation_planning", False, None
        state["current_step"] = "budget_summarization"
        return "完整行程报告", "budget_summarization", True, body

    run_once = AsyncMock(side_effect=once)
    monkeypatch.setattr(executor, "_execute_turn_once", run_once)
    text, step, committed, result_body = await executor.execute_turn("user", "conversation", "规划", True, EventStream())
    assert run_once.await_count == 3
    assert text.endswith("完整行程报告")
    assert (step, committed, result_body) == ("budget_summarization", True, body)


@pytest.mark.asyncio
async def test_manual_selection_remains_waiting_for_user(monkeypatch):
    from app.services import chat_stream_executor as executor

    state = {"current_step": "accommodation_planning", "auto_confirm": False}
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values=state)))
    monkeypatch.setattr(executor, "create_travel_agent", AsyncMock(return_value=agent))
    result = ("请在卡片选择住宿", "accommodation_planning", False, None)
    run_once = AsyncMock(return_value=result)
    monkeypatch.setattr(executor, "_execute_turn_once", run_once)
    assert await executor.execute_turn("user", "conversation", "确认", True, EventStream()) == result
    run_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_selection_without_progress_does_not_loop(monkeypatch):
    from app.services import chat_stream_executor as executor

    state = {"current_step": "transport_planning", "auto_confirm": True}
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values=state)))
    monkeypatch.setattr(executor, "create_travel_agent", AsyncMock(return_value=agent))
    result = ("没有可用班次，请调整日期", "transport_planning", False, None)
    run_once = AsyncMock(return_value=result)
    monkeypatch.setattr(executor, "_execute_turn_once", run_once)
    assert await executor.execute_turn("user", "conversation", "规划", True, EventStream()) == result
    run_once.assert_awaited_once()
