#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/7 17:29
@Author  : GGBOND
@File    : test_weather_mcp.py
@Software: PyCharm
"""
import pytest

"""测试天气 MCP Server"""
import asyncio
from app.mcp_core.servers.weather_server import get_weather_forecast

@pytest.mark.asyncio
async def test():
    print("=== 测试西安天气 (adcode: 610100) ===")
    result = await get_weather_forecast("610100")
    print(result)

    print("\n=== 测试北京天气 (adcode: 110000) ===")
    result1 = await get_weather_forecast("110000")
    print(result1)


if __name__ == "__main__":
    asyncio.run(test())