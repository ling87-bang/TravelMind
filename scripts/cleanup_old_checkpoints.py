#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/6 13:50
@Author  : GGBOND
@File    : cleanup_old_checkpoints.py
@Software: PyCharm
"""
import asyncio
from datetime import datetime, timedelta
from app.core.checkpointer import get_checkpointer


async def cleanup_old_checkpoints(days: int = 7):
    """删除 N 天前的检查点"""

    checkpointer = await get_checkpointer()
    cutoff_date = datetime.now() - timedelta(days=days)

    async with checkpointer.pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                DELETE FROM checkpoints
                WHERE created_at < %s
            """, (cutoff_date,))

            deleted_count = cur.rowcount
            await conn.commit()

    print(f"✅ 删除了 {deleted_count} 个过期检查点")


if __name__ == "__main__":
    asyncio.run(cleanup_old_checkpoints(days=7))