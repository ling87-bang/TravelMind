"""API contract tests with isolated database and agent dependencies."""

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessageChunk

from app.api.dependencies import get_current_user
from app.main import app
from app.models.base import get_db
from app.models.conversation import Conversation


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self):
        self.conversation = None

    def add(self, value):
        if isinstance(value, Conversation):
            self.conversation = value

    async def commit(self):
        return None

    async def refresh(self, value):
        now = datetime.now(timezone.utc)
        value.id = getattr(value, "id", None) or uuid.uuid4()
        value.extra_info = getattr(value, "extra_info", None) or {}
        value.created_at = getattr(value, "created_at", None) or now
        value.updated_at = getattr(value, "updated_at", None) or now

    async def execute(self, _statement):
        return FakeResult(self.conversation)


@pytest_asyncio.fixture
async def api_client():
    session = FakeSession()
    user = SimpleNamespace(id=uuid.uuid4())

    async def override_db():
        yield session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_conversation(api_client):
    client, _session = api_client
    response = await client.post("/api/v1/conversations", json={"title": "测试会话"})
    assert response.status_code == 200
    assert response.json()["title"] == "测试会话"


@pytest.mark.asyncio
async def test_send_message_validation(api_client):
    client, _session = api_client
    response = await client.post(
        f"/api/v1/chat/stream/{uuid.uuid4()}",
        json={"content": "   "},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_nonexistent_conversation(api_client):
    client, _session = api_client
    response = await client.get(f"/api/v1/conversations/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stream_response(api_client, monkeypatch):
    client, session = api_client
    created = await client.post("/api/v1/conversations", json={})
    conversation_id = created.json()["id"]

    class FakeAgent:
        async def astream_events(self, *_args, **_kwargs):
            yield {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="你好")}}

    async def fake_create_agent():
        return FakeAgent()

    monkeypatch.setattr("app.api.v1.chat.create_travel_agent", fake_create_agent)
    response = await client.post(
        f"/api/v1/chat/stream/{conversation_id}",
        json={"content": "你好"},
    )
    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == ["token", "done"]
    assert session.conversation is not None
