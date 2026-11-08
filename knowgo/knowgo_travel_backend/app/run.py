"""
启动脚本 - Windows 兼容版本（强制 SelectorEventLoop）
"""
import sys
import asyncio
import selectors
import os
for output in (sys.stdout, sys.stderr):
    if hasattr(output, "reconfigure"):
        output.reconfigure(encoding="utf-8", errors="replace")
# 限制OpenBLAS单线程运行，解决内存抢占问题
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
# 必须在导入任何其他模块之前设置！
if sys.platform == "win32":
    # 先设置策略，避免替换策略后丢失已经创建的循环。
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn
from app.config import settings

if __name__ == "__main__":
    # 关键：使用 uvicorn.Server 手动运行，而不是 uvicorn.run()
    config = uvicorn.Config(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        loop="none",  # 关键！告诉 uvicorn 不要创建新的事件循环
    )
    server = uvicorn.Server(config)

    # 使用我们创建的 SelectorEventLoop 运行
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        loop.close()
