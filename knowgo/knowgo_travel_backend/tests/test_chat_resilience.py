"""Failure injection: optional RAG failures must not terminate a chat turn."""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import RateLimitError


def rate_limit():
    return RateLimitError("quota restricted", response=httpx.Response(
        429, request=httpx.Request("POST", "https://model.invalid")), body=None)


def test_query_optimizer_failure_still_retrieves_original_query():
    from app.rag.pipeline import AdvancedRAGPipeline

    pipe = object.__new__(AdvancedRAGPipeline)
    docs = [object()]
    pipe.top_k = 3
    pipe.cache = SimpleNamespace(get=Mock(return_value=None), set=Mock())
    pipe.query_optimizer = SimpleNamespace(optimize=Mock(side_effect=rate_limit()))
    pipe.retriever = SimpleNamespace(retrieve=Mock(return_value=docs))
    pipe.reranker = SimpleNamespace(rerank=Mock(return_value=docs))
    pipe.parent_splitter = SimpleNamespace(get_parent_context=Mock(return_value=docs))
    pipe.context_reorder = SimpleNamespace(reorder=Mock(return_value=docs))
    assert pipe.retrieve("天津旅游攻略") == docs
    pipe.retriever.retrieve.assert_called_once_with(query="天津旅游攻略", queries=["天津旅游攻略"])


def test_reranking_failure_keeps_basic_results():
    from app.rag.reranker import LLMReranker

    ranker = LLMReranker(top_k=1)
    from langchain_core.documents import Document
    docs = [Document(page_content="真实的本地攻略")]
    ranker.structured_model = Mock(invoke=Mock(side_effect=rate_limit()))
    assert ranker.rerank("天津", docs) == docs


def test_rag_singleton_is_initialized_once_under_concurrent_queries(monkeypatch):
    from app.rag import pipeline

    count = []
    marker = object()
    monkeypatch.setattr(pipeline, "_pipeline", None)

    def create():
        if pipeline._pipeline is None:
            count.append(1)
            time.sleep(0.03)
            pipeline._pipeline = marker
        return pipeline._pipeline

    monkeypatch.setattr(pipeline, "_get_or_create_rag_pipeline", create)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: pipeline.get_rag_pipeline(), range(4))) == [marker] * 4
    assert len(count) == 1


def test_unavailable_knowledge_tool_returns_explicit_missing_data(monkeypatch):
    from app.tools import router_query

    monkeypatch.setattr(router_query, "get_rag_pipeline", Mock(side_effect=TimeoutError()))
    result = router_query.query_destination_info.invoke({"destination": "天津"})
    assert "资料暂时无法获取" in result
    assert "不要" in result


@pytest.mark.asyncio
async def test_first_model_chunk_is_visible_before_round_finishes():
    from app.core.event_stream import EventStream
    from app.services.chat_stream_executor import _stream_agent

    release = asyncio.Event()

    class Agent:
        async def astream_events(self, *args, **kwargs):
            # Nested RAG model text must stay internal.
            yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "tools"},
                   "data": {"chunk": SimpleNamespace(content="internal query variants")}}
            yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "model"},
                   "data": {"chunk": SimpleNamespace(content="您好")}}
            await release.wait()
            yield {"event": "on_chat_model_end", "metadata": {"langgraph_node": "model"}, "data": {}}

    stream = EventStream()
    task = asyncio.create_task(_stream_agent(Agent(), {}, {}, stream))
    try:
        event = await asyncio.wait_for(anext(stream.__aiter__()), timeout=1)
        assert event["content"] == "您好"
        assert not task.done()
    finally:
        release.set()
    assert await task == "您好"
    assert stream._queue.empty()  # No reset/replay of the first round.


@pytest.mark.asyncio
async def test_main_model_rate_limit_saves_partial_reply_and_finishes(monkeypatch):
    from app.core.events import make_event
    from app.services import chat_contract_service as chat
    from app.agents.routers.chat_router import RouterResult

    saved = []
    db = SimpleNamespace(add=saved.append, commit=AsyncMock(), rollback=AsyncMock(),
                         execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    monkeypatch.setattr(chat, "get_owned_conversation", AsyncMock(return_value=SimpleNamespace(title="测试")))
    monkeypatch.setattr(chat, "chat_route", AsyncMock(return_value=RouterResult(
        is_itinerary_planning=False, inference_log="test", skills=[])))
    monkeypatch.setattr(chat, "try_direct_ticket_query", AsyncMock(return_value=None))

    async def fail(*args):
        args[-1].push(make_event("assistant", "已收到您的需求。"))
        raise rate_limit()

    monkeypatch.setattr(chat, "execute_turn", fail)
    from app.core.event_stream import EventStream
    stream = EventStream()
    task = asyncio.create_task(chat._produce(db, "user", "conversation", "你好", stream))
    events = [event async for event in stream]
    await task
    error = next(e for e in events if e["role"] == "error")["content"]
    assert error["type"] == "model_rate_limit"
    assert "429" not in error["message"]
    assert saved[-1].content.startswith("已收到您的需求。")
    assert error["message"] in saved[-1].content
    assert saved[-1].extra_info["error"] == error
    assert events[-1]["role"] == "done" and events[-1]["code"] == 500


def test_legacy_keyerror_is_not_shown_as_request():
    from app.utils.service_errors import public_service_error
    assert public_service_error(KeyError("request"))["type"] == "service_error"
