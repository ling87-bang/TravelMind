import pytest


@pytest.mark.asyncio
async def test_long_term_memory_full_flow(memory_service, test_user_id):
    """
    完整长期记忆 E2E 测试（合并版）：
    - 画像：旅行风格/饮食禁忌/饮食偏好
    - 历史：添加一次旅行记录、汇总 visited_attractions
    - 住宿：偏好类型 + 平均预算
    - 输出：format_memory_for_prompt 包含关键字段
    """
    service = memory_service
    user_id = test_user_id

    # ========== 1) 写入画像偏好 ==========
    await service.update_travel_styles(user_id, ["culture", "food"])
    await service.update_dietary_restrictions(user_id, ["seafood-allergy"])
    await service.update_food_preferences(user_id, ["spicy", "local-cuisine"])

    profile = await service.get_user_profile(user_id)
    assert "culture" in profile.travel_styles
    assert "food" in profile.travel_styles
    assert "seafood-allergy" in profile.dietary_restrictions
    assert "spicy" in profile.food_preferences
    assert profile.updated_at is not None

    # ========== 2) 写入出行历史 ==========
    await service.add_completed_trip(
        user_id=user_id,
        destination="西安",
        start_date="2025-08-01",
        end_date="2025-08-05",
        visited_attractions=["兵马俑", "华清宫", "大雁塔", "西安城墙"],
    )

    history = await service.get_travel_history(user_id)
    assert len(history.completed_trips) == 1
    assert history.completed_trips[0].destination == "西安"
    assert "兵马俑" in history.visited_attractions
    assert history.updated_at is not None

    # ========== 3) 写入住宿偏好 ==========
    await service.update_accommodation_preference(
        user_id=user_id,
        preferred_types=["star_hotel", "hostel"],
        avg_budget=350.0,
    )

    history2 = await service.get_travel_history(user_id)
    assert "star_hotel" in history2.accommodation_preference.preferred_types
    assert "hostel" in history2.accommodation_preference.preferred_types
    assert history2.accommodation_preference.avg_budget_per_night is not None

    # ========== 4) 验证格式化 prompt ==========
    memory_text = await service.format_memory_for_prompt(user_id)
    print("\n=== 格式化的长期记忆（E2E 合并版） ===")
    print(memory_text)

    assert "用户历史偏好" in memory_text

    # 画像
    assert "旅行风格" in memory_text
    assert "culture" in memory_text
    assert "food" in memory_text

    assert "饮食禁忌" in memory_text
    assert "seafood-allergy" in memory_text

    assert "饮食偏好" in memory_text
    assert "spicy" in memory_text
    assert "local-cuisine" in memory_text

    # 历史
    assert "去过的目的地" in memory_text
    assert "西安" in memory_text

    assert "去过的景点" in memory_text
    assert "兵马俑" in memory_text

    # 住宿
    assert "住宿偏好" in memory_text
    assert "star_hotel" in memory_text
    assert "hostel" in memory_text

    assert "住宿预算" in memory_text

    print("\n✅ 完整长期记忆 full-flow 测试通过")


from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult


class EchoChatModel(BaseChatModel):
    """
    最小可运行假模型：
    - 不调用任何外部服务
    - 直接回显 system prompt，便于断言 memory 是否注入
    """

    @property
    def _llm_type(self) -> str:
        return "echo-chat-model"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        sys = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        content = f"[ECHO_SYSTEM]\n{sys}"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


@pytest.mark.asyncio
async def test_agent_memory_injection_smoke(memory_service, test_user_id):
    """
    “Agent 连接”最小冒烟测试：
    1) 写入长期记忆
    2) format_memory_for_prompt 得到 memory_text
    3) 注入 system prompt
    4) 假模型回显 system prompt
    5) 断言：输出里包含长期记忆关键内容
    """
    service = memory_service
    user_id = test_user_id

    # 1) 准备长期记忆
    await service.update_travel_styles(user_id, ["adventure"])
    await service.update_dietary_restrictions(user_id, ["vegetarian"])
    await service.add_completed_trip(
        user_id=user_id,
        destination="成都",
        start_date="2025-06-01",
        end_date="2025-06-03",
        visited_attractions=["大熊猫基地", "宽窄巷子"],
    )

    # 2) 取出格式化后的 user_memory
    memory_text = await service.format_memory_for_prompt(user_id)
    assert "adventure" in memory_text
    assert "vegetarian" in memory_text
    assert "成都" in memory_text

    # 3) 构建注入后的 prompt（模拟 middleware 注入效果）
    system_prompt = f"""你是一个旅行规划助手。
请参考用户长期记忆进行个性化回答：

{memory_text}
"""
    user_question = "我下次想去一个不太累、适合吃吃喝喝的地方，有推荐吗？"

    # 4) 用假模型跑一次
    model = EchoChatModel()
    result = model.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_question),
    ])

    content = result.content
    print("\n=== Agent Memory Smoke Output ===")
    print(content)

    # 5) 断言注入成功
    assert "用户历史偏好" in content
    assert "adventure" in content
    assert "vegetarian" in content
    assert "成都" in content
    assert "大熊猫基地" in content

    print("\n✅ Agent 长期记忆注入冒烟测试通过")
