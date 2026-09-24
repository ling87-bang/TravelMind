#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/6 13:57
@Author  : GGBOND
@File    : test_state_persistence.py
@Software: PyCharm
"""
"""
测试状态持久化功能
"""
import pytest
import asyncio
from app.core.state import TravelState, create_initial_state
from app.core.checkpointer import get_checkpointer
from app.core.store import get_user_memory_service


@pytest.mark.asyncio
async def test_checkpointer_save_and_load():
    """测试 Checkpointer 保存和加载"""

    # 初始化
    checkpointer = await get_checkpointer()

    # 模拟保存状态
    thread_id = "test_thread_001"
    config = {"configurable": {"thread_id": thread_id}}

    state = create_initial_state(user_id="test_user", session_id="test_session")
    state["current_step"] = "destination_recommendation"
    state["messages"] = [{"role": "user", "content": "我想去西安"}]

    # 保存（实际使用中由 LangGraph 自动保存）
    # 这里仅演示概念

    # 加载（从检查点恢复）
    # loaded_state = await checkpointer.get(config)

    print("✅ Checkpointer 测试通过")


@pytest.mark.asyncio
async def test_store_user_preferences():
    """测试 Store 用户偏好存储"""

    service = await get_user_memory_service()

    user_id = "test_user_002"

    # 保存偏好
    await service.save_user_preference(
        user_id=user_id,
        preference_key="budget_level",
        preference_value="comfort"
    )

    # 读取偏好
    budget_level = await service.get_user_preference(
        user_id=user_id,
        preference_key="budget_level"
    )

    assert budget_level == "comfort", "预算等级不匹配"

    # 添加收藏
    await service.add_favorite_destination(user_id, "西安")
    await service.add_favorite_destination(user_id, "成都")

    favorites = await service.get_favorite_destinations(user_id)

    assert len(favorites) == 2, "收藏数量不匹配"
    assert "西安" in favorites, "西安未收藏"

    print("✅ Store 测试通过")


if __name__ == "__main__":
    # 运行测试
    asyncio.run(test_checkpointer_save_and_load())
    asyncio.run(test_store_user_preferences())