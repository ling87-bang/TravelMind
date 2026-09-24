#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/13 16:19
@Author  : GGBOND
@File    : health.py
@Software: PyCharm
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import get_db


router = APIRouter(prefix="/health", tags=["健康检查"])


@router.get("/live")
async def liveness():
    """进程存活检查，不访问外部服务。"""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(db: AsyncSession = Depends(get_db)):
    """数据库就绪检查。"""
    await db.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}
