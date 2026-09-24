#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/6 17:04
@Author  : GGBOND
@File    : test_handoffs_flow.py
@Software: PyCharm
"""
"""
测试 Handoffs 完整流程
"""
import pytest
import asyncio
import uuid
from app.agents.handoffs.travel_agent import create_travel_agent
from app.core.state import create_initial_state


@pytest.mark.asyncio
async def test_requirement_collection_step():
    """测试步骤 1：需求收集"""

    # 创建 Agent
    agent = await create_travel_agent()

    # 配置
    thread_id = f"test_{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    # 初始状态
    initial_state = create_initial_state(
        user_id="test_user",
        session_id="test_session"
    )

    # 第 1 轮：用户发起对话
    print("\n=== 第 1 轮：用户发起 ===")
    response1 = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "我想规划一次旅行"}]},
        config
    )
    print(f"Agent: {response1['messages'][-1].content}")

    # 第 2 轮：用户提供需求
    print("\n=== 第 2 轮：用户提供需求 ===")
    response2 = await agent.ainvoke(
        {"messages": [
            {"role": "user", "content":
                "出发日期是2025-08-01，一共5天，2个成人1个儿童，"
                "预算每人4000-6000元，喜欢文化探索和美食，没有特殊需求"}
        ]},
        config
    )
    print(f"Agent: {response2['messages'][-1].content}")

    # 验证状态
    assert response2.get("current_step") == "destination_recommendation", \
        "应该跳转到目的地推荐步骤"
    assert response2.get("user_requirement") is not None, \
        "用户需求应该已记录"

    print("\n✅ 需求收集步骤测试通过")


@pytest.mark.asyncio
async def test_destination_selection_step():
    """测试步骤 2：目的地推荐"""

    agent = await create_travel_agent()
    thread_id = f"test_{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    # 跳过步骤 1，直接设置状态
    initial_state = create_initial_state("test_user", "test_session")
    initial_state["current_step"] = "destination_recommendation"
    initial_state["user_requirement"] = {
        "departure_date": "2025-08-01",
        "travel_days": 5,
        "adult_count": 2,
        "children_count": 1,
        "budget_min": 4000,
        "budget_max": 6000,
        "budget_level": "comfort",
        "travel_styles": ["culture", "food"],
        "special_needs": None
    }

    # 请求推荐
    print("\n=== 请求目的地推荐 ===")
    response = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "请推荐几个目的地"}]},
        config
    )
    print(f"Agent: {response['messages'][-1].content}")

    # 用户选择
    print("\n=== 用户选择西安 ===")
    response2 = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "我选择西安"}]},
        config
    )
    print(f"Agent: {response2['messages'][-1].content}")

    # 验证状态
    assert response2.get("selected_destination") == "西安", \
        "目的地应该是西安"
    assert response2.get("current_step") == "transport_planning", \
        "应该跳转到交通规划步骤"

    print("\n✅ 目的地推荐步骤测试通过")


@pytest.mark.asyncio
async def test_complete_flow():
    """测试完整流程（模拟真实用户操作）"""

    agent = await create_travel_agent()
    thread_id = f"test_{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    # 定义对话序列
    conversations = [
        "我想规划一次旅行",
        "出发日期2025-08-01，5天，2成人1儿童，预算4000-6000/人，喜欢文化和美食",
        "我选择西安",
        "选择高铁",
        "选择星级酒店和特色民宿",
        "选择特色美食和本地小吃",
        "行程看起来不错，确认生成",
        "预算可以接受",
        "确认生成订单"
    ]

    for i, user_message in enumerate(conversations, 1):
        print(f"\n=== 第 {i} 轮对话 ===")
        print(f"User: {user_message}")

        response = await agent.ainvoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config
        )

        print(f"Agent: {response['messages'][-1].content}")
        print(f"Current Step: {response.get('current_step', 'N/A')}")

    # 最终验证
    final_response = response
    assert final_response.get("order_id") is not None, \
        "应该生成订单号"

    print("\n完整流程测试通过！")


if __name__ == "__main__":
    # 运行测试
    asyncio.run(test_requirement_collection_step())
    asyncio.run(test_destination_selection_step())
    asyncio.run(test_complete_flow())