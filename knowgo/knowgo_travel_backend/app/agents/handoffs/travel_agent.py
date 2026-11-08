"""
Handoffs 主 Agent
一个 Agent + 中间件实现整个旅行规划流程
"""
from app.tools.mcp_tools import get_all_mcp_tools, apply_train_arg_guard
from app.tools.router_query import query_destination_info
from langchain.agents import create_agent
from app.core.state import TravelState
from app.core.checkpointer import get_checkpointer
from app.core.store import get_store
from app.utils.llm_factory import get_qwen_llm
from app.core.middleware import create_step_config_middleware
from app.tools.memory_tools import (
    update_travel_style_tool,
    update_dietary_restriction_tool,
    update_food_preference_tool,
    add_travel_record_tool,
    update_accommodation_preference_tool  # 住宿步骤中间件需要此工具
)
from app.tools.state_transition import (
    record_requirement_tool,
    select_destination_tool,
    select_transport_tool,
    select_accommodation_tool,
    select_food_tool,
    select_accommodation_food_tool,  # 一站式住宿+餐饮确认（总工具表注册，防 unknown tool names）
    generate_itinerary_tool,
    submit_itinerary_body_tool,  # 结构化行程提交（正文由代码渲染）
    summarize_budget_tool,
    ALL_ROLLBACK_TOOLS
)
from app.utils.logger import app_logger


def get_llm():
    """获取配置好的千问模型"""
    return get_qwen_llm()


async def create_travel_agent():
    """
    创建 Handoffs 旅行规划 Agent

    返回：
        编译好的 Agent（可直接调用）
    """

    app_logger.info("创建 Travel Agent（带审批）...")

    llm = get_llm()
    all_mcp_tools = await get_all_mcp_tools()
    # 注册侧同源套用车票守卫。ToolNode 是按名到本注册表取对象执行的，
    # 若此处不套，middleware 注入侧的 _ArgGuardTool 会被绕过（参数兜底/结果裁剪全失效）
    all_mcp_tools = apply_train_arg_guard(all_mcp_tools)

    step_config_middleware = await create_step_config_middleware()

    all_tools = [
        record_requirement_tool,
        select_destination_tool,
        select_transport_tool,
        select_accommodation_tool,
        select_food_tool,
        select_accommodation_food_tool,  # 一站式住宿+餐饮确认
        generate_itinerary_tool,
        submit_itinerary_body_tool,
        summarize_budget_tool,
        *ALL_ROLLBACK_TOOLS,
        query_destination_info,
        *all_mcp_tools,
        update_travel_style_tool,
        update_dietary_restriction_tool,
        update_food_preference_tool,
        add_travel_record_tool,
        update_accommodation_preference_tool  # 住宿步骤中间件需要此工具住宿步骤中间件按名引用此工具
    ]

    checkpointer = await get_checkpointer()
    store = await get_store()
    agent = create_agent(
        model=llm,
        tools=all_tools,
        state_schema=TravelState,
        middleware=[step_config_middleware],
        checkpointer=checkpointer,
        store=store
    )

    app_logger.info("✅ Travel Agent（带审批）创建完成")

    return agent
