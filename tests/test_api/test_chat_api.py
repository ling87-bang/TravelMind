#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/14 14:29
@Author  : GGBOND
@File    : test_chat_api.py
@Software: PyCharm
"""
import json

import pytest
import pytest_asyncio

"""
聊天 API 测试
"""
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest_asyncio.fixture
async def client():
    """创建测试客户端"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_conversation(client: AsyncClient):
    """测试创建会话"""

    response = await client.post(
        "/api/v1/conversations/",
        json={"title": "测试会话"},
        headers={"X-User-ID": "test_user"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert "conv_" in data["data"]["id"]


@pytest.mark.asyncio
async def test_send_message_validation(client: AsyncClient):
    """测试消息验证"""

    # 空消息应该失败
    response = await client.post(
        "/api/v1/chat/",
        json={
            "conversation_id": "conv_test",
            "message": "   ",  # 只有空格
            "stream": False
        },
        headers={"X-User-ID": "test_user"}
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_nonexistent_conversation(client: AsyncClient):
    """测试获取不存在的会话"""

    response = await client.get(
        "/api/v1/conversations/conv_nonexistent",
        headers={"X-User-ID": "test_user"}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stream_response(client: AsyncClient):
    """测试流式响应"""

    # 先创建会话
    create_resp = await client.post(
        "/api/v1/conversations/",
        json={},
        headers={"X-User-ID": "test_user"}
    )
    conv_id = create_resp.json()["data"]["id"]

    # 发送流式请求
    async with client.stream(
            "POST",
            "/api/v1/chat/",
            json={
                "conversation_id": conv_id,
                "message": "你好",
                "stream": True
            },
            headers={"X-User-ID": "test_user"}
    ) as response:
        events = []
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue

            if line.startswith("data:"):
                # 剥离第一个 "data:"
                content = line.split(":", 1)[1].strip()

                # 再次检查并剥离可能存在的第二个 "data:" (修复双重前缀问题)
                if content.startswith("data:"):
                    content = content.split(":", 1)[1].strip()

                if content and content != "[DONE]":
                    try:
                        events.append(json.loads(content))
                    except json.JSONDecodeError:
                        print(f"Skipping invalid JSON: {content}")

        # 验证事件序列
        event_types = [e["type"] for e in events]
        assert "start" in event_types
        assert "done" in event_types