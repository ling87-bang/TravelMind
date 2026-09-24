#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/6 16:09
@Author  : GGBOND
@File    : travel_agent.py
@Software: PyCharm
"""
import asyncio
import sys
import uuid
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.agents.handoffs.travel_agent import create_travel_agent
from langchain_core.messages import HumanMessage, AIMessage

async def run_interactive_chat():
    """
    运行持续对话的测试循环
    """
    travel_agent = await create_travel_agent()

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    print(f"=== 开始测试 (会话 ID: {thread_id}) ===")
    print("输入 'q', 'quit', 'exit' 退出对话")
    print("-" * 50)

    while True:
        try:
            user_input = input("\nUser (你): ").strip()

            if not user_input:
                continue

            if user_input.lower() in ["q", "quit", "exit"]:
                print("结束对话。")
                break

            inputs = {"messages": [HumanMessage(content=user_input)]}

            print("\nAssistant (Agent): ", end="", flush=True)

            last_message_id = None

            async for event in travel_agent.astream(inputs, config=config, stream_mode="values"):
                messages = event.get("messages", [])

                if messages:
                    last_msg = messages[-1]

                    if last_msg.id == last_message_id:
                        continue

                    last_message_id = last_msg.id

                    if isinstance(last_msg, AIMessage) and last_msg.content:
                        print(last_msg.content)

        except Exception as e:
            print(f"\n❌ 发生错误: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_interactive_chat())
