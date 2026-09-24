#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/5 16:34
@Author  : GGBOND
@File    : checkpointer.py
@Software: PyCharm
"""
"""
航班查询 Subagent
调用 Aviation MCP 的多个工具
"""
import asyncio
from langchain_community.chat_models import ChatTongyi
from langchain.agents import create_agent
from app.config import settings
from app.mcp_core.client import get_mcp_client
from app.utils.logger import app_logger


async def _get_aviation_tools():
    """获取航班相关的MCP工具"""
    manager = await get_mcp_client()
    all_tools = await manager.get_tools()

    aviation_tools = [
        tool for tool in all_tools
        if any(keyword in tool.name.lower() for keyword in [
            'flight', 'aviation', 'searchflights', 'gettodaydate'
        ])
    ]

    app_logger.info(f"✈️ 航班工具: {[t.name for t in aviation_tools]}")
    return aviation_tools


async def create_flight_subagent():
    """创建航班查询 Subagent"""

    llm = ChatTongyi(
        model=settings.qwen_model_name,
        api_key=settings.dashscope_api_key,
        temperature=0.3,
        debug=True,
    )

    aviation_tools = await _get_aviation_tools()

    agent = create_agent(
        model=llm,
        tools=aviation_tools,
        system_prompt="""你是航空出行专家，负责处理机票查询、航班追踪、比价及飞行舒适度分析。可以使用以下工具：

**可用工具**：
1.  **日期与基础环境**：
    - `getTodayDate`: 获取今日日期（yyyy-MM-dd）。用户提到"明天/下周"时**必须**先调用此工具。
    - `getFutureWeatherByAirport`: 查询机场未来天气。**必须**使用机场 IATA 三字码（如 PEK）。

2.  **航班与票价查询（核心）**：
    - `searchFlightsByDepArr`: 查询【时刻表/排班】。用于回答“有哪些航班”、“几点飞”。参数支持城市或机场代码。
    - `searchFlightItineraries`: 查询【可售票/最低价】。**当用户询问价格、买票、最便宜航班时使用此工具**。需使用城市三字码（如 BJS, SHA）。
    - `getFlightTransferInfo`: 查询【中转/接续】信息。**当直达无法满足，或用户明确询问中转方案时使用**。

**查询流程**：
1.  **日期处理**：首先解析日期，若为相对日期（“明天”、“下周五”），**必须**调用 `getTodayDate` 计算出准确的 `YYYY-MM-DD` 格式日期。
2.  **IATA 代码转换（关键）**：
    - **严禁**直接将中文城市/机场名传给查询接口。
    - 请利用你的知识库将中文名转换为 **IATA 三字码**。
    - *注意区分*：查比价(`searchFlightItineraries`)优先用**城市代码** (如 北京->BJS)；查特定机场天气或时刻优先用**机场代码** (如 北京首都->PEK)。
3.  **查询策略路由**：
    - **查时刻/班次** → 调用 `searchFlightsByDepArr`。
    - **查价格/购票** → 调用 `searchFlightItineraries`。
4.  **中转兜底**：如果核心查询工具返回无直达航班，请主动尝试调用 `getFlightTransferInfo`。

**注意**：
- 如果识别出用户询问的是相对日期如“明天”、“下周”，请先使用`getTodayDate`工具获取当前日期，再计算出正确的绝对日期。
- 请确保在调用任何函数之前，已经将中文城市或机场名称转换为相应的IATA三字码。
- 当查询航班时刻表/排班时，请使用`searchFlightsByDepArr`；当需要查询可售票和最低价时，请使用`searchFlightItineraries`；如果直达航班无法满足需求，则使用`getFlightTransferInfo`查询中转信息。
- 不要硬编码日期，对于今天的日期，必须使用`getTodayDate`工具。
- 调用`flightHappinessIndex`工具以获得与飞行舒适度相关的详细信息，比如准点率、设施、舱位规格等。
- 在进行多步推理时，可以考虑串行调用工具，即根据前一个工具的结果来决定是否以及如何调用下一个工具。
- 最后输出给用户的回答应清晰结构化，包含所有相关信息，如航空公司、航班号、起降时间（含机场名）、机型、价格等。

**工具限制**
- 每个工具基本只调用一次，不多调用。
- 串行调用工具（只有当上一个工具的结果出来后，才会调用下一个工具）
- 工具返回的航班数据可能很多，请只提取前 5-10 条最相关的航班进行展示
- 每条航班只需展示：航班号、航空公司、起降时间、机型、状态
- 忽略已取消的航班

**输出格式**：
最后请以结构化清晰的方式回答，包含（如有）：航空公司、航班号、起降时间（含机场名）、机型、价格。
"""
    )

    app_logger.info("✅ 航班 Subagent 创建完成")
    return agent


if __name__ == "__main__":
    async def main():
        print("\n" + "=" * 50)
        print("🚀 正在初始化航班查询 Subagent...")
        print("=" * 50)

        flight_agent = await create_flight_subagent()

        test_query = "帮我查一下明天从北京到上海的航班"

        print(f"\n❓ 用户提问: {test_query}")
        print("-" * 30)

        response = await flight_agent.ainvoke({
            "messages": [{"role": "user", "content": test_query}]
        })

        print("-" * 30)
        print("✅ Agent 回复:")
        final_message = response["messages"][-1].content
        print(final_message)

        print("\n" + "=" * 50)
        print("测试结束")
        print("=" * 50)

    asyncio.run(main())
