#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/5 13:30
@Author  : GGBOND
@File    : config
@Software: PyCharm
"""
import os

"""
配置管理模块
使用 pydantic-settings 管理环境变量
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from functools import lru_cache

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Settings(BaseSettings):
    """应用配置"""

    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    debug: bool = Field(default=False, alias="DEBUG")
    jwt_secret_key: str = Field(
        default="local-development-only-change-me",
        alias="JWT_SECRET_KEY"
    )

    dashscope_api_key: str = Field(alias="DASHSCOPE_API_KEY")
    qwen_model_name: str = Field(default="qwen-max", alias="QWEN_MODEL_NAME")
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="QWEN_BASE_URL"
    )
    qwen_temperature: float = 0.7
    qwen_max_tokens: int = 8000

    langsmith_api_key: str = Field(default="", alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="travel-planner-dev", alias="LANGSMITH_PROJECT")
    langsmith_tracing: bool = Field(default=True, alias="LANGSMITH_TRACING")
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com",
        alias="LANGSMITH_ENDPOINT"
    )

    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(alias="POSTGRES_DB")
    postgres_user: str = Field(alias="POSTGRES_USER")
    postgres_password: str = Field(alias="POSTGRES_PASSWORD")

    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")
    redis_password: str = Field(default="", alias="REDIS_PASSWORD")

    amap_api_key: str = Field(default="", alias="AMAP_API_KEY")
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    mcp_enable_external: bool = Field(default=False, alias="MCP_ENABLE_EXTERNAL")

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    @property
    def database_url(self) -> str:
        """生成 PostgreSQL 连接字符串"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """生成 Redis 连接字符串"""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    """获取配置单例（缓存）"""
    return Settings()


settings = get_settings()
