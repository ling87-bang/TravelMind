"""Local process and dependency readiness checks."""
import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.models.base import engine
from app.mcp_core.client import MCPClientManager

router = APIRouter(prefix="/health", tags=["健康检查"])


@router.get("/live")
async def live():
    return {"status": "alive"}


@router.get("/ready")
async def ready():
    async def check_database():
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(check_database(), timeout=5)
    except Exception:
        return JSONResponse({"status": "not_ready", "database": "unavailable"}, status_code=503)
    mcp = MCPClientManager._instance
    return {
        "status": "ready", "database": "connected",
        "mcp_tool_count": len((mcp._tools or []) if mcp else []),
    }
