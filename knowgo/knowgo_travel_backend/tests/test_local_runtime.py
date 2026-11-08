"""Regression checks for local startup and complete chat persistence; no network."""
import sys
import asyncio
from app.core.event_stream import EventStream
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_auxiliary_model_falls_back_to_primary(monkeypatch):
    from app.utils import llm_factory

    monkeypatch.setattr(llm_factory.settings, "deepseek_api_key", "")
    marker = object()
    factory = lambda streaming: marker if streaming is False else None
    monkeypatch.setattr(llm_factory, "get_qwen_llm", factory)
    assert llm_factory.get_deepseek_llm() is marker


@pytest.mark.asyncio
async def test_empty_mcp_result_is_cached():
    from app.mcp_core.client import MCPClientManager

    manager = MCPClientManager()
    manager._configs = {}
    manager._tools = []
    manager._load_tools = AsyncMock(side_effect=AssertionError("must not reload"))
    assert await manager.get_tools() == []
    manager._load_tools.assert_not_called()


def test_local_mcp_uses_project_python():
    from app.mcp_core.client import MCPClientManager

    assert MCPClientManager.SERVER_CONFIGS["weather"]["command"] == sys.executable
    assert MCPClientManager.SERVER_CONFIGS["search"]["command"] == sys.executable


@pytest.mark.asyncio
async def test_map_enrichment_without_key_makes_no_request(monkeypatch):
    from app.services import itinerary_plan_service as plans

    monkeypatch.setattr(plans.settings, "amap_api_key", "")
    def forbidden_client(*args, **kwargs):
        raise AssertionError("must not call unconfigured map provider")
    monkeypatch.setattr(plans.httpx, "AsyncClient", forbidden_client)
    await plans._enrich_spots(SimpleNamespace(itinerary_list=[]))


def test_structured_plan_preserves_all_time_slots():
    from app.services.itinerary_plan_service import _plan_from_structure

    plan = _plan_from_structure([{"day": 1, "city": "天津", "morning": ["上午安排"],
                                 "noon": ["午餐安排"], "afternoon": ["下午安排"],
                                 "evening": ["晚间返程"]}])
    assert "天津" in plan.title
    assert [item.time for item in plan.itinerary_list[0].itinerary] == ["上午", "中午", "下午", "晚上"]
    assert "晚间返程" in plan.itinerary_list[0].itinerary[-1].scenic_spots_list[0].toponym_desc


@pytest.mark.asyncio
async def test_unconfigured_remote_services_are_not_loaded(monkeypatch):
    from app.mcp_core.client import MCPClientManager

    for key in ("MCP_ENABLED_SERVERS", "AMAP_API_KEY", "TAVILY_API_KEY",
                "VARIFLIGHT_API_KEY", "AIGOHOTEL_MCP_API", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    manager = MCPClientManager()
    manager._configs = None
    manager._load_tools = AsyncMock(return_value=[])
    await manager.initialize()
    assert set(manager._configs) == {"12306-mcp"}


@pytest.mark.asyncio
async def test_summary_failure_preserves_message_before_done(monkeypatch):
    from app.agents.routers.chat_router import RouterResult
    from app.services import chat_contract_service as chat
    from app.services import express_planning_service as express

    saved = []
    commits = []

    class Database:
        def add(self, message):
            saved.append(message)

        async def commit(self):
            commits.append(len(saved))

        async def execute(self, statement):
            return SimpleNamespace(all=lambda: [])

    full_text = "保留完整旅行规划正文。" * 250
    monkeypatch.setattr(chat, "get_owned_conversation", AsyncMock(return_value=SimpleNamespace(title="测试")))
    monkeypatch.setattr(chat, "chat_route", AsyncMock(return_value=RouterResult(
        is_itinerary_planning=True, inference_log="test", skills=[])))
    monkeypatch.setattr(chat, "try_direct_ticket_query", AsyncMock(return_value=None))
    monkeypatch.setattr(express, "try_express_planning", AsyncMock(return_value=None))
    monkeypatch.setattr(chat, "execute_turn", AsyncMock(return_value=(full_text, None, False, None)))
    monkeypatch.setattr(chat, "compress_content", AsyncMock(side_effect=TimeoutError("summary unavailable")))

    stream = EventStream()
    task = asyncio.create_task(chat._produce(Database(), "test-user", "test-conversation", "安排一次旅行", stream))
    events = []
    async for event in stream:
        events.append(event)
        if event["role"] == "done":
            assert len(saved) == 2
            assert commits == [1, 2]
            assert saved[-1].content == full_text
            assert saved[-1].extra_info["original"] == full_text
    await task
    assert not any(event["role"] == "error" for event in events)
    assert events[-1]["role"] == "done"
