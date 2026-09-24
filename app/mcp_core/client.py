#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/8 17:37
@Author  : GGBOND
@File    : search_server.py
@Software: PyCharm
"""
"""
MCP 客户端管理器
统一管理所有 MCP 服务连接
"""
import asyncio
import os
import sys
from typing import Optional, List
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from app.utils.logger import app_logger

load_dotenv()


class MCPClientManager:
    """
    MCP 客户端管理器（单例模式）
    """

    _instance: Optional['MCPClientManager'] = None
    _client: Optional[MultiServerMCPClient] = None
    _tools: Optional[List] = None
    _lock = asyncio.Lock()

    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))

    ENV_VARS = os.environ.copy()
    ENV_VARS["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + ENV_VARS.get("PYTHONPATH", "")

    SERVER_CONFIGS = {
        "weather": {
            "command": sys.executable,
            "args": ["-m", "app.mcp_core.servers.weather_server"],
            "transport": "stdio",
            "env": ENV_VARS,
        },
        "search": {
            "command": sys.executable,
            "args": ["-m", "app.mcp_core.servers.search_server"],
            "transport": "stdio",
            "env": ENV_VARS,
        },

        "amap": {
            "url": f"https://mcp.amap.com/mcp?key={os.getenv('AMAP_API_KEY', '')}",
            "transport": "http",
        },
        "12306-mcp": {
            "url": "https://mcp.api-inference.modelscope.net/215d3cfb299e47/mcp",
            "transport": "streamable_http",
        },
        "VariFlight-Aviation": {
            "url": f"https://ai.variflight.com/servers/aviation/mcp/?api_key={os.getenv('VARIFLIGHT_API_KEY', '')}",
            "transport": "streamable_http",
        },
        "aigohotel-mcp": {
            "url": "https://mcp.aigohotel.com/mcp",
            "transport": "streamable_http",
            "headers": {
                "Authorization": f"Bearer {os.getenv('AIGOHOTEL_MCP_API')}",
                "Content-Type": "application/json"
            }
        },
    }

    @classmethod
    async def get_instance(cls, servers: List[str] = None) -> 'MCPClientManager':
        """获取单例实例"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance.initialize(servers=servers)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例（用于测试）"""
        cls._instance = None

    async def initialize(self, servers: List[str] = None):
        """
        初始化 MCP 客户端

        Args:
            servers: 要启用的服务列表，默认启用所有
        """
        if self._client is not None:
            app_logger.warning("⚠️ MCP 客户端已初始化，跳过")
            return

        # 本地运行默认只启用自建服务，避免缺少第三方 Key 时阻塞主对话。
        if servers is None:
            servers = ["weather", "search"]
            external_enabled = os.getenv("MCP_ENABLE_EXTERNAL", "false").lower() in {
                "1", "true", "yes", "on"
            }
            if external_enabled:
                servers.append("12306-mcp")
                if os.getenv("AMAP_API_KEY"):
                    servers.append("amap")
                if os.getenv("VARIFLIGHT_API_KEY"):
                    servers.append("VariFlight-Aviation")
                if os.getenv("AIGOHOTEL_MCP_API"):
                    servers.append("aigohotel-mcp")
        configs = {k: v for k, v in self.SERVER_CONFIGS.items() if k in servers}

        app_logger.info(f"🔗 初始化 MCP: {list(configs.keys())}")

        self._client = MultiServerMCPClient(configs)

        try:
            self._tools = await self._client.get_tools()
            app_logger.info(f"✅ 已加载 {len(self._tools)} 个 MCP 工具")
        except Exception as e:
            app_logger.warning(f"⚠️ 预加载工具失败: {e}")
            self._tools = []

    async def close(self):
        """关闭客户端"""
        if self._client:
            self._client = None
            self._tools = None
            app_logger.info("🔒 MCP 客户端已关闭")

    async def get_tools(self) -> List:
        """
        获取所有 MCP 工具

        Returns:
            LangChain 工具列表
        """
        if self._client is None:
            raise RuntimeError("MCP 客户端未初始化，请先调用 initialize()")

        if self._tools is not None:
            return self._tools

        self._tools = await self._client.get_tools()
        return self._tools


async def get_mcp_client(servers: List[str] = None) -> MCPClientManager:
    """获取 MCP 客户端管理器实例"""
    return await MCPClientManager.get_instance(servers)
