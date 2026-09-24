#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/21 15:32
@Author  : GGBOND
@File    : example1_long_term_memory_demo
@Software: PyCharm
"""

"""
示例1：长期记忆完整演示（可直接运行）
- 写入用户画像：旅行风格 / 饮食禁忌 / 饮食偏好
- 写入出行历史：旅行记录 + 住宿偏好
- 打印 format_memory_for_prompt 的最终结果
"""
import sys
import asyncio
# 设置 WindowsSelectorEventLoopPolicy
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.store import get_user_memory_service


async def run_demo():
    service = await get_user_memory_service()

    user_id = "449bfbbe-bdcb-473d-b7a9-67120f783df0"

    print("\n==============================")
    print("✅ Step A: 保存用户画像（profile）")
    print("==============================")

    await service.update_travel_styles(user_id, ["culture", "food"])
    await service.update_dietary_restrictions(user_id, ["seafood-allergy"])
    await service.update_food_preferences(user_id, ["spicy", "local-cuisine"])

    profile = await service.get_user_profile(user_id)
    print("当前画像：", profile.model_dump())

    print("\n==============================")
    print("✅ Step B: 保存出行历史（history）")
    print("==============================")

    await service.add_completed_trip(
        user_id=user_id,
        destination="西安",
        start_date="2025-08-01",
        end_date="2025-08-05",
        visited_attractions=["兵马俑", "华清宫", "大雁塔", "西安城墙"]
    )

    await service.update_accommodation_preference(
        user_id=user_id,
        preferred_types=["star_hotel", "hostel"],
        avg_budget=350.0
    )

    history = await service.get_travel_history(user_id)
    print("当前出行历史：", history.model_dump())

    print("\n==============================")
    print("✅ Step C: 打印注入提示词的长期记忆（format_memory_for_prompt）")
    print("==============================")

    memory_text = await service.format_memory_for_prompt(user_id)
    print(memory_text if memory_text else "(暂无长期记忆)")

    print("\n🎉 示例1完成：你已经验证了 Store 写入 + 读取 + 格式化注入文本全链路")


if __name__ == "__main__":
    asyncio.run(run_demo())



import asyncio

from app.config import settings
from app.core.store import get_user_memory_service, store_lifespan
from app.core.checkpointer import get_checkpointer
from langchain.agents import create_agent
from langchain_community.chat_models import ChatTongyi


async def create_travel_agent(user_id: str):
    """创建只使用长期记忆（Store）的 Agent"""

    # 1️⃣ 从 Store 读取长期记忆
    service = await get_user_memory_service()
    memory_prompt = await service.format_memory_for_prompt(user_id)

    # 2️⃣ 构建 system prompt（长期记忆注入点）
    system_prompt = (
        "你是一个旅行规划助手。\n"
        "请结合用户的历史偏好和出行记录，进行个性化推荐。\n"
        "如果用户去过某些目的地或景点，请尽量避免重复推荐。\n"
    )

    if memory_prompt:
        system_prompt = f"{system_prompt}\n\n{memory_prompt}"

    # 3️⃣ 创建 Agent（官网最新版 create_agent）
    agent = create_agent(
        model=ChatTongyi(
            model=settings.qwen_model_name,
            api_key=settings.dashscope_api_key,
            temperature=settings.qwen_temperature,
        ),
        system_prompt=system_prompt,  # ✅ 长期记忆
    )

    return agent


async def main():
    user_id = "demo_user_001"  # 必须与 Store 中一致

    agent = await create_travel_agent(user_id)

    # 单轮或多轮都可以（这里只做最简单演示）
    response = await agent.ainvoke({
        "messages": [
            {"role": "user", "content": "根据我的偏好，推荐一个适合的国内旅行目的地"}
        ]
    })

    print(response["messages"][-1].content)



if __name__ == "__main__":
    async def _run():
        async with store_lifespan():
            await main()

    asyncio.run(_run())

