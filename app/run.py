#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
启动脚本 - Windows 兼容版本（强制 SelectorEventLoop）
"""
import sys
import asyncio

# 必须在导入任何其他模块之前设置！
if sys.platform == "win32":
    # MCP 的 stdio transport 在 Windows 下需要 SelectorEventLoop。
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

import uvicorn

if __name__ == "__main__":
    # 关键：使用 uvicorn.Server 手动运行，而不是 uvicorn.run()
    config = uvicorn.Config(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        loop="none",  # 关键！告诉 uvicorn 不要创建新的事件循环
    )
    server = uvicorn.Server(config)

    # 使用我们创建的 SelectorEventLoop 运行
    loop.run_until_complete(server.serve())
