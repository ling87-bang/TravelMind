#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/5 16:58
@Author  : GGBOND
@File    : test_checkpointer
@Software: PyCharm
"""
import asyncio
import sys
from langchain_community.chat_models import ChatTongyi
from app.config import settings
from app.core.checkpointer import get_checkpointer
from langchain.agents import create_agent

# 设置 WindowsSelectorEventLoopPolicy
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def create_travel_agent():
    """创建带 Checkpointer 的 Agent"""

    # 获取 Checkpointer
    checkpointer = await get_checkpointer()

    # 编译 Agent
    agent = create_agent(
        model=ChatTongyi(
            model=settings.qwen_model_name,
            api_key=settings.dashscope_api_key,
            temperature=settings.qwen_temperature
        ),
        checkpointer=checkpointer  # 关键：传入 Checkpointer
    )

    return agent

async def main():
    agent = await create_travel_agent()

    # 配置（thread_id 用于会话隔离）
    config = {
        "configurable": {
            "thread_id": "user_123_session_4567"
        }
    }

    # 第一轮对话
    response1 = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "我想去西安旅游"}]},
        config
    )

    print(response1["messages"][-1].content)

    # 第二轮对话（Agent 会自动读取 thread_id 对应的历史状态）
    response2 = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "推荐几个景点"}]},
        config
    )

    print(response2["messages"][-1].content)

if __name__ == '__main__':
    asyncio.run(main())