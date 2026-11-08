"""Database-backed admission, independent producers and reconnectable event streams."""
import asyncio
import time
import uuid
from contextlib import suppress

from fastapi import HTTPException
from sqlalchemy import select, text

from app.core.event_stream import EventStream
from app.models.base import async_session_maker, engine
from app.models.chat_runtime import ChatRun, SkillCard
from app.services.runtime_context import current_run, current_submission
from app.services.skill_card_service import (
    capability, digest, mark_card_history, planning_version, resolve_card, selection_map,
)
from app.utils.logger import app_logger
from app.utils.service_errors import public_service_error

_tasks: dict[str, asyncio.Task] = {}
_live: dict[str, "RunEvents"] = {}
_run_slots = asyncio.Semaphore(8)
_dispatcher: asyncio.Task | None = None


async def lock_conversation(db, conversation_id):
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                     {"key": str(conversation_id)})


def run_data(run: ChatRun) -> dict:
    return {"run_id": str(run.id), "status": run.status, "stage": run.stage,
            "partial_text": run.partial_text, "result": run.result or {},
            "client_request_id": run.request_id}


class RunEvents:
    def __init__(self):
        self.text = ""
        self.result = {}
        self.stage = ""
        self.done = None
        self.subscribers: set[EventStream] = set()

    def push(self, event):
        role, content = event.get("role"), event.get("content")
        if role == "done":
            self.done = event
            return
        if role == "assistant" and isinstance(content, str):
            self.text += content
        elif role == "assistant_reset":
            self.text = ""
        elif role in {"skill_ui", "skill_submitted", "itinerary_plans", "error"}:
            self.result[role] = content
        elif role == "skills":
            self.stage = (content or {}).get("skill_name", "")
        for subscriber in self.subscribers:
            subscriber.push(event)

    def close(self):
        # The owner publishes done only after the durable terminal update.
        pass


async def latest_run(db, conversation_id):
    return (await db.execute(select(ChatRun).where(
        ChatRun.conversation_id == uuid.UUID(str(conversation_id))
    ).order_by(ChatRun.updated_at.desc(), ChatRun.created_at.desc()).limit(1))).scalar_one_or_none()


async def reserve_run(db, user_id: str, conversation_id: str, request_id: str | None,
                      content: str = "", skill: str | None = None,
                      payload: dict | None = None, card_id: str | None = None,
                      options: dict | None = None):
    """Reserve once before opening SSE, so admission errors retain HTTP semantics."""
    request_id = request_id or str(uuid.uuid4())
    await lock_conversation(db, conversation_id)
    previous = (await db.execute(select(ChatRun).where(
        ChatRun.conversation_id == uuid.UUID(conversation_id), ChatRun.request_id == request_id
    ))).scalar_one_or_none()
    request = {"user_id": user_id, "content": content}
    if options:
        request.update(options)
    if skill:
        # Parsing is performed before any state change, including requests from older clients.
        _, _, submit_type, _ = capability(skill)
        parsed = submit_type.model_validate(payload)
        payload = parsed.model_dump()
        payload["segments"] = sorted(payload["segments"], key=lambda item: item["segment_id"])
        for item in payload["segments"]:
            if "trainNo" in item:
                item["trainNo"] = ""
        segments = payload.get("segments", [])
        ids = [item["segment_id"] for item in segments]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "同一行程段不能重复选择")
        from app.capabilities.base.common import get_agent_and_state
        _, _, state = await get_agent_and_state(conversation_id)
        card = await resolve_card(db, conversation_id, skill, card_id, state)
        from app.capabilities.base.payload_cache import submission_payload
        import importlib
        name, candidate_type, _, _ = capability(skill)
        tools = importlib.import_module(f"app.capabilities.{skill}.{name}_agent_tool")
        validate = getattr(tools, "validate_hotel_multi_submission" if name == "hotel"
                           else f"validate_{name}_submission")
        with submission_payload(name, conversation_id, candidate_type.model_validate(card.payload)):
            validation = validate(parsed, cache_key=conversation_id)
        if not validation.ok:
            raise HTTPException(422, validation.msg)
        selection_hash = digest(sorted(segments, key=lambda item: item["segment_id"]))
        if card.submission_hash and card.submission_hash != selection_hash:
            raise HTTPException(409, "该卡片已提交其他选择，请重新获取候选")
        if card.run_id:
            if previous and previous.id != card.run_id:
                raise HTTPException(409, "请求标识已用于其他内容")
            previous = await db.get(ChatRun, card.run_id)
        elif card.planning_version != planning_version(state):
            raise HTTPException(409, "卡片所属规划已更新，请重新获取候选")
        if not card.run_id:
            expected_step = "accommodation_planning" if name == "hotel" else "transport_planning"
            if state.get("current_step") != expected_step:
                raise HTTPException(409, "当前步骤与卡片不匹配，请重新获取候选")
            latest = (await db.execute(select(SkillCard.id).where(
                SkillCard.conversation_id == card.conversation_id, SkillCard.skill_name == skill
            ).order_by(SkillCard.created_at.desc()).limit(1))).scalar_one()
            if latest != card.id:
                raise HTTPException(409, "已有更新的候选卡片，请使用最新卡片")
        request.update(skill=skill, payload=payload, card_id=str(card.id))
    if previous:
        if previous.request_data != request:
            raise HTTPException(409, "请求标识已用于其他内容")
        if previous.status != "failed":
            await db.commit()
            return previous
    active = (await db.execute(select(ChatRun.id).where(
        ChatRun.conversation_id == uuid.UUID(conversation_id), ChatRun.status.in_(["queued", "running"])
    ))).scalar_one_or_none()
    if active:
        raise HTTPException(409, "当前会话正在生成，请等待完成或恢复当前任务")
    run = previous or ChatRun(conversation_id=uuid.UUID(conversation_id), request_id=request_id,
                              request_data=request)
    run.status = "queued"
    run.partial_text = ""
    run.result = {}
    db.add(run)
    await db.flush()
    if skill:
        card.run_id = run.id
        card.submission_hash = selection_hash
        card.submission = payload
    await db.commit()
    launch(str(run.id))
    return run


def launch(run_id: str):
    if run_id not in _tasks:
        task = asyncio.create_task(_run_limited(run_id), name=f"chat-run-{run_id}")
        _tasks[run_id] = task
        task.add_done_callback(lambda completed: _tasks.pop(run_id, None))


async def _run_limited(run_id):
    # Bound concurrent lock, producer and progress connections below the pool capacity.
    async with _run_slots:
        await _execute(run_id)


async def _save_progress(run_id: str, events: RunEvents):
    async with async_session_maker() as db:
        run = await db.get(ChatRun, uuid.UUID(run_id))
        run.partial_text = events.text
        run.stage = events.stage
        run.result = dict(events.result)
        await db.commit()


async def _progress_loop(run_id, events):
    previous_stage = None
    last_saved = 0.0
    while True:
        await asyncio.sleep(0.2)
        if events.stage != previous_stage or time.monotonic() - last_saved >= 1:
            try:
                await _save_progress(run_id, events)
                last_saved = time.monotonic()
                previous_stage = events.stage
            except Exception:
                app_logger.exception("运行进度保存失败，将重试")


async def _apply_card(db, run, events):
    from app.capabilities.base.common import get_agent_and_state
    from app.capabilities.base.payload_cache import submission_payload
    card = await db.get(SkillCard, uuid.UUID(run.request_data["card_id"]))
    name, candidate_type, submit_type, apply = capability(card.skill_name)
    _, _, state = await get_agent_and_state(str(run.conversation_id))
    if card.planning_version != planning_version(state):
        raise HTTPException(409, "行程已改变，请重新获取候选")
    if card.result:
        result = card.result
    else:
        if card.planning_version != planning_version(state):
            raise HTTPException(409, "行程已改变，请重新获取候选")
        expected_step = "accommodation_planning" if name == "hotel" else "transport_planning"
        if state.get("current_step") != expected_step and state.get("last_submission_id") != str(card.id):
            raise HTTPException(409, "当前步骤与卡片不匹配，请重新获取候选")
        token = current_submission.set(str(card.id))
        try:
            with submission_payload(name, str(run.conversation_id), candidate_type.model_validate(card.payload)):
                applied = await apply(str(run.conversation_id), submit_type.model_validate(card.submission))
            if not applied.ok:
                raise HTTPException(422, applied.msg)
            result = applied.model_dump()
        finally:
            current_submission.reset(token)
        card.result = result
        await mark_card_history(db, card)
        await db.commit()
    events.push({"role": "skill_submitted", "content": {
        "card_id": str(card.id), "skill_name": card.skill_name,
        "submitted": {"selections": selection_map(card.submission)},
    }})
    labels = {"train": "车次", "flight": "航班", "driving": "自驾路线", "hotel": "住宿"}
    texts = result.get("texts") or []
    confirmation = f"✅ 已确认您选择的{labels[name]}。\n" + "\n".join(
        f"{index}. {value}" for index, value in enumerate(texts, 1)) + "\n"
    events.push({"role": "assistant", "content": confirmation})
    stage = result.get("next_step") or "accommodation_planning"
    if state.get("last_submission_id") == str(card.id):
        stage = state.get("current_step") or stage
    content = "请基于已确认的住宿直接生成完整行程报告。" if name == "hotel" else "交通已确认，请继续推荐住宿并推进规划。"
    return content, stage, confirmation


async def _execute(run_id):
    events = RunEvents()
    token = current_run.set(run_id)
    progress = None
    connection = None
    status = "failed"
    owned = False
    try:
        # Retain ownership through the durable terminal update, including shutdown paths.
        connection = await engine.connect()
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key, 1))"),
                                           {"key": run_id})
        await connection.commit()
        if not acquired:
            return
        owned = True
        async with async_session_maker() as db:
            run = await db.get(ChatRun, uuid.UUID(run_id))
            if not run or run.status != "queued":
                owned = False
                await connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 1))"), {"key": run_id})
                await connection.commit()
                return
            _live[run_id] = events
            run.status = "running"
            await db.commit()
            progress = asyncio.create_task(_progress_loop(run_id, events))
            request = run.request_data
            content, stage, confirmation = request["content"], request.get("resume_stage"), ""
            if request.get("skill"):
                content, stage, confirmation = await _apply_card(db, run, events)
            events.stage = stage or "routing"
            from app.services.chat_contract_service import _produce
            await _produce(db, request["user_id"], str(run.conversation_id), content, events,
                           force_planning=bool(request.get("skill") or request.get("force_planning")),
                           persist_user=request.get("persist_user", not request.get("skill")),
                           resume_stage=stage, confirmation_text=confirmation)
            status = "succeeded" if events.done and events.done.get("code") == 200 else "failed"
            if (events.result.get("itinerary_plans") or {}).get("status") == "error":
                status = "failed"
                events.push({"role": "error", "content": {"message": "行程清单生成失败，可继续重试。"}})
    except asyncio.CancelledError:
        events.push({"role": "error", "content": {"message": "服务重启，本轮已中断，可继续重试。"}})
    except Exception as error:
        app_logger.exception("后台运行失败")
        message = {"message": error.detail} if isinstance(error, HTTPException) else public_service_error(error)
        events.push({"role": "error", "content": message})
    finally:
        if progress:
            progress.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await progress
        try:
            if not owned:
                return
            async with async_session_maker() as db:
                run = await db.get(ChatRun, uuid.UUID(run_id))
                if run and run.status in {"running", "queued"}:
                    run.status = status
                    run.partial_text = events.text
                    run.stage = events.stage
                    run.result = dict(events.result)
                    await db.commit()
        except Exception:
            status = "failed"
            app_logger.exception("运行终态保存失败")
        finally:
            terminal = {"role": "done", "code": 200 if status == "succeeded" else 500, "content": "finish"}
            for subscriber in events.subscribers:
                subscriber.push(terminal)
                subscriber.close()
            if connection:
                try:
                    if owned:
                        await connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 1))"),
                                                 {"key": run_id})
                        await connection.commit()
                finally:
                    await connection.close()
            current_run.reset(token)
            _live.pop(run_id, None)


def snapshot_events(data):
    yield {"role": "run", "content": {"run_id": data["run_id"], "status": data["status"]}}
    yield {"role": "assistant_reset"}
    if data["partial_text"]:
        yield {"role": "assistant", "content": data["partial_text"]}
    for role, content in data["result"].items():
        yield {"role": role, "content": content}


async def watch_run(run_id):
    """Local listeners get tokens immediately; other workers replay durable snapshots."""
    local = _live.get(run_id)
    if local:
        subscriber = EventStream()
        local.subscribers.add(subscriber)
        snapshot = {"run_id": run_id, "status": "running", "partial_text": local.text,
                    "result": dict(local.result)}
        try:
            for event in snapshot_events(snapshot):
                yield event
            async for event in subscriber:
                yield event
        finally:
            local.subscribers.discard(subscriber)
        return
    previous = None
    while True:
        if run_id in _live:
            async for event in watch_run(run_id):
                yield event
            return
        async with async_session_maker() as db:
            run = await db.get(ChatRun, uuid.UUID(run_id))
            if not run:
                return
            data = run_data(run)
        if data != previous:
            for event in snapshot_events(data):
                yield event
            previous = data
        if data["status"] in {"succeeded", "failed"}:
            yield {"role": "done", "code": 200 if data["status"] == "succeeded" else 500}
            return
        await asyncio.sleep(0.25)


async def recover_runs():
    """Only reclaim runs whose process lock is free; other app workers remain owners."""
    async with async_session_maker() as db:
        runs = (await db.execute(select(ChatRun).where(ChatRun.status.in_(["queued", "running"])))).scalars().all()
        for run in runs:
            async with engine.connect() as connection:
                free = await connection.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key, 1))"),
                                               {"key": str(run.id)})
                if not free:
                    continue
                await db.refresh(run)
                if run.status == "running":
                    run.status = "failed"
                    run.result = {**(run.result or {}), "error": {"message": "服务重启，本轮已中断，可继续重试。"}}
                await connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 1))"),
                                         {"key": str(run.id)})
                await connection.commit()
        await db.commit()
        for run in runs:
            if run.status == "queued":
                launch(str(run.id))
    global _dispatcher
    if _dispatcher is None or _dispatcher.done():
        _dispatcher = asyncio.create_task(_dispatch_queued(), name="chat-run-dispatcher")


async def _dispatch_queued():
    # Recover the commit-to-launch gap, including a request disconnecting just after commit.
    last_recovery = time.monotonic()
    while True:
        await asyncio.sleep(1)
        try:
            if time.monotonic() - last_recovery >= 30:
                await recover_runs()
                last_recovery = time.monotonic()
            async with async_session_maker() as db:
                identifiers = (await db.execute(select(ChatRun.id).where(
                    ChatRun.status == "queued"
                ).order_by(ChatRun.created_at).limit(100))).scalars().all()
            for identifier in identifiers:
                launch(str(identifier))
        except Exception:
            app_logger.exception("待运行任务扫描失败，将重试")


async def shutdown_runs():
    global _dispatcher
    if _dispatcher:
        _dispatcher.cancel()
        await asyncio.gather(_dispatcher, return_exceptions=True)
        _dispatcher = None
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
