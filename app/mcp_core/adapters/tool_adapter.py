#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/7 17:27
@Author  : GGBOND
@File    : tool_adapter.py
@Software: PyCharm
"""
"""
MCP 工具适配器
将 MCP 工具转换为 LangChain Tools
"""
from typing import List, Any
from langchain.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from app.mcp_core.client import get_mcp_client
from app.utils.logger import app_logger


async def create_mcp_tools() -> List[Any]:
    """
    创建所有 MCP 工具的 LangChain 封装

    返回：
    - LangChain Tool 列表
    """

    tools = []

    client = await get_mcp_client()

    async with client:
        available_tools = await client.list_tools()

        app_logger.info(f"📋 发现 {len(available_tools)} 个 MCP 工具")

        for server_name, tool_list in available_tools.items():
            for tool_info in tool_list:
                tool_name = tool_info.get("name")
                tool_desc = tool_info.get("description", "")

                @tool(name=f"{server_name}_{tool_name}", description=tool_desc)
                async def mcp_tool_wrapper(
                        server=server_name,
                        tool=tool_name,
                        **kwargs
                ) -> str:
                    """动态 MCP 工具包装器"""
                    result = await client.invoke_tool(server, tool, kwargs)

                    import json
                    return json.dumps(result, ensure_ascii=False)

                tools.append(mcp_tool_wrapper)

                app_logger.debug(f"  ✅ {server_name}.{tool_name}")

    app_logger.info(f"🎉 创建了 {len(tools)} 个 LangChain 工具")

    return tools
