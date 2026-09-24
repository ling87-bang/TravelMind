#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/5 13:51
@Author  : GGBOND
@File    : test_llm.py
@Software: PyCharm
"""
"""
测试千问 LLM 连接
"""
import os
from dotenv import load_dotenv
from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import HumanMessage

load_dotenv()


def test_qwen_connection():
    """测试千问模型连接"""

    print("🔗 测试千问模型连接...")

    try:
        model = ChatTongyi(
            model="qwen-max",
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            temperature=0.7
        )

        response = model.invoke([
            HumanMessage(content="你好，请用一句话介绍你自己。")
        ])

        print(f"✅ 连接成功！模型回复：\n{response.content}")

    except Exception as e:
        print(f"❌ 连接失败：{e}")
        raise


if __name__ == "__main__":
    test_qwen_connection()
