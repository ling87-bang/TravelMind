#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/16 16:39
@Author  : GGBOND
@File    : users
@Software: PyCharm
"""
"""
用户模型
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class User(Base):
    """用户表"""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    # 用户偏好（JSON 格式）
    preferences: Mapped[dict] = mapped_column(JSON, default=dict,nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now()
    )

    # 关系
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )