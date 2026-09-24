"""
数据库初始化脚本（完整版）
"""
import asyncio
import sys
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore
from app.config import settings
from app.utils.logger import app_logger
# 导入模型包，确保所有 ORM 模型先注册到 Base.metadata。
import app.models  # noqa: F401
from app.models.base import init_db


async def init_database():
    """初始化所有数据库表"""

    db_url = settings.database_url
    app_logger.info(f"连接数据库: {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")

    try:
        async with AsyncConnectionPool(conninfo=db_url, min_size=2, max_size=10) as pool:
            app_logger.info("启用 pgvector 扩展...")
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                    await conn.commit()
            app_logger.info("✅ pgvector 扩展已就绪")

        app_logger.info("初始化业务表...")
        await init_db()
        app_logger.info("✅ 业务表创建成功")

        async with AsyncConnectionPool(conninfo=db_url, min_size=2, max_size=10) as pool:
            app_logger.info("初始化 Checkpointer 表...")
            async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
                await checkpointer.setup()
            app_logger.info("✅ Checkpointer 表创建成功")

            app_logger.info("初始化 Store 表...")
            async with AsyncPostgresStore.from_conn_string(db_url) as store:
                await store.setup()
            app_logger.info("✅ Store 表创建成功")

        app_logger.info("数据库初始化完成！")

    except Exception as e:
        app_logger.error(f"❌ 数据库初始化失败: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(init_database())
