-- ============================================================================
-- KnowGo 旅行规划后端 —— PostgreSQL 业务表初始化脚本
--
-- 覆盖范围：4 张业务表（与 app/models 下的 ORM 模型一一对应）
--   users            用户表        app/models/user.py
--   conversation     会话表        app/models/conversation.py
--   message          消息表        app/models/message.py
--   itinerary_plans  行程清单表    app/models/itinerary_plan.py
--
-- 执行方式（任选其一）：
--   psql -h <host> -p 5432 -U <user> -d <db> -f init.sql
--   pgAdmin：连到目标库后打开本文件，全选执行
--
-- 使用说明：
--   1. 幂等：全部 IF NOT EXISTS，可重复执行；只建缺失的表/索引，不改动已有数据；
--      线上库若已由应用建过这些表，执行本脚本为无操作（no-op）
--   2. 不建库、不建扩展：目标库需先存在（即 .env 中 POSTGRES_DB 指向的库）
--   3. 主键 id / plan_id 由应用侧 Python 生成（uuid4），列上无 DEFAULT；
--      时间列同理，由应用写入 now()，列上无 DEFAULT——与模型定义严格一致
--   4. LangGraph 基础设施表（checkpoints / checkpoint_blobs / checkpoint_writes /
--      checkpoint_migrations / store / store_migrations）不在此文件中，它们由
--      langgraph 库的 setup() 在首次运行时自动建表并按库版本迁移，不要手写
--
-- 生成依据：由 app/models 模型经 SQLAlchemy 的 PostgreSQL 方言导出，
-- 并与线上库 information_schema / pg_indexes 实测结构逐列核对一致
-- ============================================================================


-- ========== 1. 用户表 ==========
CREATE TABLE IF NOT EXISTS users (
    id            UUID NOT NULL,                 -- 主键：应用侧 uuid4 生成
    username      VARCHAR(50),                   -- 通用账密登录用（可空）
    email         VARCHAR(100),                  -- 邮箱登录用（可空）
    password_hash VARCHAR(255),                  -- 密码哈希（可空）
    preferences   JSON,                          -- 用户偏好（唯一可空列）
    created_at    TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at    TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email);


-- ========== 2. 会话表 ==========
CREATE TABLE IF NOT EXISTS conversation (
    id          UUID NOT NULL,
    user_id     UUID NOT NULL,                   -- 归属用户
    title       VARCHAR(200) NOT NULL,
    status      VARCHAR(20) NOT NULL,            -- active / archived / deleted（软删只改这一列）
    extra_info  JSON NOT NULL,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at  TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_conversation_user_id ON conversation (user_id);
CREATE INDEX IF NOT EXISTS ix_conversation_status ON conversation (status);


-- ========== 3. 行程清单表 ==========
CREATE TABLE IF NOT EXISTS itinerary_plans (
    plan_id         VARCHAR(36) NOT NULL,        -- 业务层生成的 plan_id（UUID 字符串）
    conversation_id UUID NOT NULL,               -- 对应前端契约的 sessionId（LangGraph thread_id）
    plan_data       JSON NOT NULL,               -- 完整结构化行程（title + itinerary_list）
    status          INTEGER NOT NULL,            -- 1 成功 / 2 失败
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (plan_id),
    FOREIGN KEY (conversation_id) REFERENCES conversation (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_itinerary_plans_conversation_id ON itinerary_plans (conversation_id);
CREATE INDEX IF NOT EXISTS ix_itinerary_plans_plan_id ON itinerary_plans (plan_id);  -- 与主键重复，保持与模型定义（index=True）一致


-- ========== 4. 消息表 ==========
CREATE TABLE IF NOT EXISTS message (
    id              UUID NOT NULL,
    conversation_id UUID NOT NULL,
    role            VARCHAR(20) NOT NULL,        -- user / assistant / system
    content         TEXT NOT NULL,               -- 消息正文（超长时仅存摘要，全文在 extra_info.original）
    extra_info      JSON NOT NULL,               -- 含 skill_ui 卡片帧、摘要全文等
    created_at      TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (conversation_id) REFERENCES conversation (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_message_conversation_id ON message (conversation_id);
CREATE INDEX IF NOT EXISTS ix_message_role ON message (role);
CREATE INDEX IF NOT EXISTS ix_message_created_at ON message (created_at);