# ========== 多阶段构建 ==========

# 阶段1：构建依赖
FROM python:3.12-slim AS builder

WORKDIR /build

# 安装 UV 包管理器
RUN pip install --no-cache-dir uv

# 复制依赖文件
COPY requirements.txt constraints.txt ./

# 创建虚拟环境并安装依赖
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip install --no-cache-dir -c constraints.txt -r requirements.txt

# 阶段2：运行时镜像
FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        curl && \
    rm -rf /var/lib/apt/lists/*

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 复制应用代码
COPY app /app/app
COPY scripts /app/scripts
COPY data /app/data

# 设置环境变量
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV APP_PORT=8000

# 暴露端口（使用环境变量）
EXPOSE ${APP_PORT}

# 健康检查（注意：healthcheck 中不能直接使用环境变量，需要在 docker-compose 中配置）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${APP_PORT}/ || exit 1

# 启动命令（使用环境变量）
CMD uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT}
