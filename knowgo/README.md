# KnowGo 本地联调工作区

KnowGo 是旅行助手升级版，包含 Next.js 前端、FastAPI / LangGraph 后端，以及本地 PostgreSQL、Redis 配置。支持多段交通与住宿卡片、提交去重、断线恢复和会话归档。演示步骤见 `DEMO_GUIDE.md`。

## 目录

- `knowgo_travel_backend/`：新版 FastAPI / LangGraph 后端。
- `frontend/`：从提供的新版 Next.js 前端复制的工作副本。后续前端修改以本目录为准。
- `compose.yaml`：本演示独立的 PostgreSQL、Redis，使用独立数据卷。
- `scripts/`：依赖安装和本地配置工具。
- `.runtime/`：启动日志与当前脚本创建的进程 ID。

## 首次启动（Windows）

先启动 Docker Desktop，准备 Python 3.12、uv、Node.js 和 npm，在本目录执行：

```powershell
.\start-demo.cmd -Install
```

后续启动：

```powershell
.\start-demo.cmd -Production
```

- 前端：<http://127.0.0.1:3000>
- API 文档：<http://127.0.0.1:8000/docs>
- 后端就绪检查：<http://127.0.0.1:8000/api/v1/health/ready>
- PostgreSQL：`127.0.0.1:15433`
- Redis：`127.0.0.1:16379`

停止应用及本工作区容器（保留数据库数据）：

```powershell
powershell -ExecutionPolicy Bypass -File .\stop-demo.ps1
```

## 配置

在 `knowgo_travel_backend/.env` 中填写你自己的主模型 `DASHSCOPE_API_KEY`。环境变量模板位于 `knowgo_travel_backend/.env.example`。

DeepSeek 辅助模型未配置时复用主模型。地图、天气、航班和实时酒店数据需要各服务自己的 Key；未配置时不应宣称已获取实时结果。公开 12306 服务无需项目私有 Key，但需要网络及对应日期已开售。

所有实际 `.env` 文件均为本地配置，不提交 Git。

## 功能与验证

- 已完成：安装依赖，修复登录状态恢复，前端类型检查与生产构建。
- 已完成：后端本地回归测试，包括消息保存、无地图密钥降级、行程城市与交通时段校准。
- 支持注册登录、需求表单、目的地确认、多段高铁选择、多站住宿选择和逐日清单生成。
- 已完成：刷新恢复登录、当前会话、卡片提交状态和清单入口。
- 未验收：真实航班、实时报价酒店、天气、地图与完整 RAG 链路；相关服务密钥尚未配置。
- 自动化完整 HTTP 流程使用模型与第三方数据替身，真实服务需使用自己的配置和已开售日期联调。

生产模式不显示开发工具与热更新提示；修改前端后请重新执行 `npm run build` 再启动生产服务。

## 持久化提交与断线恢复

卡片候选、提交结果及运行记录保存在 PostgreSQL。新卡片携带 `card_id`，客户端发送 `client_request_id` 支持重复请求去重。同卡片的相同选择返回原运行结果，冲突选择返回 409。

页面断开后继续生成。重新进入会话后每两秒查询运行状态，完成后恢复持久化消息与清单，失败时显示继续生成入口。会话可通过“更多 → 归档会话 → 确认归档”移入“已归档”，也可从归档列表恢复。部分输出每秒保存一次，阶段变化与终态及时保存。每个后端进程最多并发执行 8 个生成任务，排队任务由数据库调度器补发。进程重启后，中断任务标记为可重试，排队任务继续执行。

后端启动会自动执行幂等增量迁移，也可在 `knowgo_travel_backend/` 中手动运行下面的命令。

```powershell
.\.venv\Scripts\python.exe -m scripts.migrate_chat_runtime
```

迁移新增 `chat_runs`、`skill_cards`，为消息和清单增加可空 `run_id` 与去重索引。旧卡片在当前步骤与段布局可以确认时补建快照，版本无法确认时提示重新获取候选。升级后重启前后端进程。

### 回归验证

在 `knowgo_travel_backend/` 中运行下面的命令。数据库集成测试使用每项独立的临时 schema，结束后自动清理。模型与外部服务在集成测试中使用替身。

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:LANGSMITH_TRACING='false'
$env:LANGCHAIN_TRACING_V2='false'
$env:KNOWGO_TEST_DATABASE='1'
.\.venv\Scripts\python.exe -m pytest -p pytest_asyncio.plugin -q tests
```

在 `frontend/` 中运行下面的命令。

```powershell
node --test tests/chat-stream.test.cjs
.\node_modules\.bin\tsc.cmd --noEmit --incremental false
npm run build
```
