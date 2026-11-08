"""Opt-in PostgreSQL regressions. Each test creates and removes its own isolated schema.

Run with KNOWGO_TEST_DATABASE=1 against the configured local PostgreSQL.
Models and network services are replaced at the planning boundary.
"""
import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(os.getenv("KNOWGO_TEST_DATABASE") != "1", reason="opt-in isolated PostgreSQL tests")


@pytest_asyncio.fixture
async def database(monkeypatch):
    from app.models import Base, User, Conversation
    from app.models.base import engine as admin_engine
    from app.config import settings
    from app.models.migrations import migrate_chat_runtime
    from app.services import chat_run_service as runs, skill_card_service as cards
    from app.agents.handoffs import travel_agent

    schema = "knowgo_test_" + uuid.uuid4().hex
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(settings.database_url.replace("postgresql://", "postgresql+asyncpg://"),
                                 connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await migrate_chat_runtime(connection)
            await migrate_chat_runtime(connection)
        monkeypatch.setattr(runs, "engine", engine)
        monkeypatch.setattr(runs, "async_session_maker", factory)
        monkeypatch.setattr(cards, "async_session_maker", factory)
        state = {"current_step": "transport_planning", "selected_destination": "天津",
                 "user_requirement": {"departure_city": "北京", "destination": "天津",
                                      "departure_date": "2026-12-01", "travel_days": 2,
                                      "adult_count": 2, "children_count": 0, "transport_types": ["train"]}}

        class Agent:
            async def aget_state(self, config):
                return SimpleNamespace(values=dict(state))

            async def aupdate_state(self, config, updates):
                state.update(updates)

        monkeypatch.setattr(travel_agent, "create_travel_agent", AsyncMock(return_value=Agent()))
        async with factory() as db:
            user = User(id=uuid.uuid4(), username="runtime-test", email="runtime@example.com", password_hash="unused", preferences={})
            conversation = Conversation(id=uuid.uuid4(), user_id=user.id, title="test", status="active")
            db.add(user)
            await db.flush()
            db.add(conversation)
            await db.commit()
        yield factory, user, conversation, state
    finally:
        await runs.shutdown_runs()
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


def candidate():
    return {"skill_name": "train_multi_segment", "segments": [
        {"segment_id": "s1", "from": "北京", "to": "天津", "date": "2026-12-01", "train_list": []},
        {"segment_id": "s2", "from": "天津", "to": "北京", "date": "2026-12-02", "train_list": []},
    ]}


async def add_card(factory, conversation, state):
    from app.models.chat_runtime import SkillCard
    from app.services.skill_card_service import planning_version
    async with factory() as db:
        card = SkillCard(conversation_id=conversation.id, skill_name="train_multi_segment",
                         planning_version=planning_version(state), payload=candidate())
        db.add(card)
        await db.commit()
        return card


@pytest.mark.asyncio
async def test_parallel_same_request_reserves_once_and_active_run_blocks_other_requests(database, monkeypatch):
    from app.services import chat_run_service as runs
    from app.models.chat_runtime import ChatRun
    factory, user, conversation, _ = database
    launched = []
    monkeypatch.setattr(runs, "launch", launched.append)
    async def reserve(key):
        async with factory() as db:
            return await runs.reserve_run(db, str(user.id), str(conversation.id), key, "hello")
    first, second = await asyncio.gather(reserve("same"), reserve("same"))
    assert first.id == second.id
    assert launched == [str(first.id)]
    with pytest.raises(HTTPException) as error:
        await reserve("another")
    assert error.value.status_code == 409
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ChatRun)) == 1


@pytest.mark.asyncio
async def test_persisted_card_survives_cache_loss_and_duplicate_submission_does_not_resume(database, monkeypatch):
    from app.services import chat_run_service as runs, chat_contract_service as chat
    from app.capabilities.train_multi_segment import train_agent_tool
    from app.models.chat_runtime import SkillCard
    factory, user, conversation, state = database
    card = await add_card(factory, conversation, state)
    train_agent_tool._PAYLOAD_CACHE._data.clear()
    produce = AsyncMock(side_effect=lambda *args, **kwargs: args[4].push({"role": "done", "code": 200}))
    monkeypatch.setattr(chat, "_produce", produce)
    async def submit(key):
        async with factory() as db:
            return await runs.reserve_run(db, str(user.id), str(conversation.id), key,
                                          skill=card.skill_name, payload={"segments": []}, card_id=str(card.id))
    first = await submit("first")
    await runs._tasks[str(first.id)]
    second = await submit("second")
    assert first.id == second.id
    assert produce.await_count == 1
    assert state["current_step"] == "accommodation_planning"
    assert state["last_submission_id"] == str(card.id)
    async with factory() as db:
        saved = await db.get(SkillCard, card.id)
        assert saved.result["ok"]


@pytest.mark.asyncio
async def test_checkpoint_success_followed_by_card_save_failure_is_retryable(database, monkeypatch):
    from app.services import chat_run_service as runs, chat_contract_service as chat
    from app.models.chat_runtime import ChatRun
    factory, user, conversation, state = database
    card = await add_card(factory, conversation, state)
    original = runs.mark_card_history
    monkeypatch.setattr(runs, "mark_card_history", AsyncMock(side_effect=RuntimeError("injected save failure")))
    async def submit():
        async with factory() as db:
            return await runs.reserve_run(db, str(user.id), str(conversation.id), "retry",
                                          skill=card.skill_name, payload={"segments": []}, card_id=str(card.id))
    first = await submit()
    await runs._tasks[str(first.id)]
    assert state["last_submission_id"] == str(card.id)
    async with factory() as db:
        assert (await db.get(ChatRun, first.id)).status == "failed"
    monkeypatch.setattr(runs, "mark_card_history", original)
    monkeypatch.setattr(chat, "_produce", AsyncMock(side_effect=lambda *args, **kwargs: args[4].push({"role": "done", "code": 200})))
    second = await submit()
    await runs._tasks[str(second.id)]
    assert second.id == first.id
    async with factory() as db:
        assert (await db.get(ChatRun, first.id)).status == "succeeded"


@pytest.mark.asyncio
async def test_disconnect_keeps_producer_alive_and_result_can_be_replayed(database, monkeypatch):
    from app.services import chat_run_service as runs, chat_contract_service as chat
    from app.models.message import Message
    factory, user, conversation, _ = database
    release = asyncio.Event()
    started = asyncio.Event()
    async def produce(db, user_id, conversation_id, content, stream, **kwargs):
        await chat._store_message(db, conversation_id, "user", content)
        stream.push({"role": "assistant", "content": "partial"})
        started.set()
        await release.wait()
        await chat._store_message(db, conversation_id, "assistant", "partial complete")
        stream.push({"role": "assistant", "content": " complete"})
        stream.push({"role": "done", "code": 200})
    monkeypatch.setattr(chat, "_produce", produce)
    async with factory() as db:
        run = await runs.reserve_run(db, str(user.id), str(conversation.id), "disconnect", "hello")
    await started.wait()
    iterator = runs.watch_run(str(run.id))
    await anext(iterator)
    await iterator.aclose()
    assert not runs._tasks[str(run.id)].done()
    release.set()
    await runs._tasks[str(run.id)]
    replay = [event async for event in runs.watch_run(str(run.id))]
    assert replay[-1]["code"] == 200
    assert any(event.get("content") == "partial complete" for event in replay)
    async with factory() as db:
        messages = (await db.execute(select(Message).where(Message.run_id == run.id))).scalars().all()
        assert len(messages) == 2


@pytest.mark.asyncio
async def test_stale_card_rejected(database, monkeypatch):
    from app.services import chat_run_service as runs
    from app.services.skill_card_service import resolve_card
    from app.models.message import Message
    factory, user, conversation, state = database
    old_card = await add_card(factory, conversation, state)
    await add_card(factory, conversation, state)
    async with factory() as db:
        with pytest.raises(HTTPException) as error:
            await runs.reserve_run(db, str(user.id), str(conversation.id), "old",
                                   skill=old_card.skill_name, payload={"segments": []}, card_id=str(old_card.id))
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_legacy_card_is_backfilled_and_unknown_version_rejected(database):
    from app.services.skill_card_service import resolve_card
    from app.models.message import Message
    factory, _, conversation, state = database
    async with factory() as db:
        db.add(Message(conversation_id=conversation.id, role="assistant", content="choose",
                       extra_info={"skill_ui": {"skill_name": "train_multi_segment", "data": candidate()}}))
        await db.commit()
        card = await resolve_card(db, str(conversation.id), "train_multi_segment", None, state)
        assert card.id
        await db.rollback()
        db.add(Message(conversation_id=conversation.id, role="user", content="new requirements"))
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await resolve_card(db, str(conversation.id), "train_multi_segment", None, state)
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_selection_order_is_idempotent_and_conflicting_selection_is_rejected(database, monkeypatch):
    from app.services import chat_run_service as runs
    from app.models.chat_runtime import SkillCard
    factory, user, conversation, state = database
    card = await add_card(factory, conversation, state)
    option = dict(trainNo="G1", startTime="08:00", endTime="09:00", startStation="北京", endStation="天津",
                  duration="01:00", price=50, seatType="二等座", ticketStatus="有票", option_id="one")
    async with factory() as db:
        saved = await db.get(SkillCard, card.id)
        payload = candidate()
        for segment in payload["segments"]:
            segment["train_list"] = [option, {**option, "option_id": "two"}]
        saved.payload = payload
        await db.commit()
    monkeypatch.setattr(runs, "launch", lambda _: None)
    choices = [{"segment_id": "s1", "option_id": "one"}, {"segment_id": "s2", "option_id": "one"}]
    async def submit(items):
        async with factory() as db:
            return await runs.reserve_run(db, str(user.id), str(conversation.id), str(uuid.uuid4()),
                                          skill=card.skill_name, payload={"segments": items}, card_id=str(card.id))
    first = await submit(choices)
    second = await submit(list(reversed(choices)))
    assert first.id == second.id
    with pytest.raises(HTTPException) as error:
        await submit([{**choices[0], "option_id": "two"}, choices[1]])
    assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["flight", "driving", "hotel"])
async def test_other_capabilities_use_durable_snapshot_and_confirmation(database, monkeypatch, name):
    from app.services import chat_run_service as runs, chat_contract_service as chat
    from app.services.skill_card_service import planning_version
    from app.models.chat_runtime import SkillCard, ChatRun
    factory, user, conversation, state = database
    skill = name + "_multi_segment"
    payload = {"skill_name": skill, "segments": []}
    selections = []
    if name == "hotel":
        state["current_step"] = "accommodation_planning"
        payload["segments"] = [dict(segment_id="h1", city="天津", checkInDate="2026-12-01",
                                     checkOutDate="2026-12-02", guestCount=2, priceLevel="经济型", mode="brand",
                                     hotel_list=[], brand_list=[dict(brand_id="b1", brandName="汉庭")])]
        selections = [dict(segment_id="h1", mode="brand", brand_id="b1")]
    else:
        for segment in candidate()["segments"]:
            base = {key: value for key, value in segment.items() if key != "train_list"}
            if name == "driving":
                base.update(ok=True, distance_km=120, duration_text="02:00")
                selection = dict(segment_id=base["segment_id"])
            else:
                base["flight_list"] = [dict(flight_no="CA1", dep_time="08:00", arr_time="09:00",
                                           dep_airport="PEK", arr_airport="TSN", price=200, option_id="flight-one")]
                selection = dict(segment_id=base["segment_id"], option_id="flight-one")
            payload["segments"].append(base)
            selections.append(selection)
    async with factory() as db:
        card = SkillCard(conversation_id=conversation.id, skill_name=skill,
                         planning_version=planning_version(state), payload=payload)
        db.add(card)
        await db.commit()
        monkeypatch.setattr(chat, "_produce", AsyncMock(side_effect=lambda *args, **kwargs: args[4].push({"role": "done", "code": 200})))
        run = await runs.reserve_run(db, str(user.id), str(conversation.id), name,
                                     skill=skill, payload={"segments": selections}, card_id=str(card.id))
    await runs._tasks[str(run.id)]
    async with factory() as db:
        saved = await db.get(ChatRun, run.id)
        assert saved.status == "succeeded"
        assert saved.result["skill_submitted"]["card_id"] == str(card.id)
    assert state["last_submission_id"] == str(card.id)


@pytest.mark.asyncio
async def test_startup_reclaims_orphans_and_dispatches_committed_queue(database, monkeypatch):
    from app.services import chat_run_service as runs, chat_contract_service as chat
    from app.models.chat_runtime import ChatRun
    factory, user, conversation, _ = database
    monkeypatch.setattr(chat, "_produce", AsyncMock(side_effect=lambda *args, **kwargs: args[4].push({"role": "done", "code": 200})))
    async with factory() as db:
        orphan = ChatRun(conversation_id=conversation.id, request_id="orphan", status="running",
                         request_data={"user_id": str(user.id), "content": "hello"})
        db.add(orphan)
        await db.commit()
    await runs.recover_runs()
    async with factory() as db:
        assert (await db.get(ChatRun, orphan.id)).status == "failed"
        queued = ChatRun(conversation_id=conversation.id, request_id="queued", status="queued",
                         request_data={"user_id": str(user.id), "content": "hello"})
        db.add(queued)
        await db.commit()
    async with asyncio.timeout(5):
        while True:
            async with factory() as db:
                if (await db.get(ChatRun, queued.id)).status == "succeeded":
                    break
            await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_active_run_uniqueness_is_enforced_by_database(database):
    from sqlalchemy.exc import IntegrityError
    from app.models.chat_runtime import ChatRun
    factory, user, conversation, _ = database
    async with factory() as db:
        for index in range(2):
            db.add(ChatRun(conversation_id=conversation.id, request_id=str(index),
                           request_data={"user_id": str(user.id), "content": "hello"}))
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_http_invalid_ids_and_deleted_conversation_resources(database):
    from app.main import app
    from app.models.base import get_db
    from app.api.dependencies import get_current_user
    from app.models.itinerary_plan import ItineraryPlan
    factory, user, conversation, _ = database
    async def db_dependency():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for method in ("GET", "PATCH", "DELETE"):
                response = await client.request(method, "/api/v1/conversations/invalid", json={} if method == "PATCH" else None)
                assert response.status_code == 422
            plan_id = str(uuid.uuid4())
            async with factory() as db:
                db.add(ItineraryPlan(plan_id=plan_id, conversation_id=conversation.id, plan_data={}))
                await db.commit()
            assert (await client.delete(f"/api/v1/conversations/{conversation.id}")).status_code == 200
            paths = [f"/api/v1/conversations/{conversation.id}", f"/api/v1/conversations/{conversation.id}/run",
                     f"/api/v1/messages?conversation_id={conversation.id}", f"/api/v1/itinerary-plans/{plan_id}"]
            for path in paths:
                assert (await client.get(path)).status_code == 404
            response = await client.post("/api/v1/messages", json={"conversation_id": str(conversation.id), "content": "hello"})
            assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_archive_restore_http_roundtrip_preserves_messages_and_checks_ownership(database):
    from app.main import app
    from app.models.base import get_db
    from app.api.dependencies import get_current_user
    from app.models.message import Message
    from app.models.user import User
    factory, user, conversation, _ = database
    async def db_dependency():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with factory() as db:
            db.add(Message(conversation_id=conversation.id, role='user', content='保留的消息'))
            await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            endpoint = f'/api/v1/conversations/{conversation.id}'
            response = await client.patch(endpoint, json={'status': 'archived'})
            assert response.status_code == 200
            assert response.json()['data']['status'] == 'archived'
            conversations = (await client.get('/api/v1/conversations')).json()['data']
            assert conversations[0]['status'] == 'archived'
            history = (await client.get(f'/api/v1/messages?conversation_id={conversation.id}')).json()['data']
            assert history['messages'][0]['content'] == '保留的消息'
            response = await client.patch(endpoint, json={'status': 'active'})
            assert response.status_code == 200 and response.json()['data']['status'] == 'active'
            app.dependency_overrides[get_current_user] = lambda: User(id=uuid.uuid4())
            assert (await client.patch(endpoint, json={'status': 'archived'})).status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_multicity_train_hotel_plan_http_flow_and_refresh(database, monkeypatch):
    """Exercise actual admission, selection, persistence and HTTP contracts with stubbed models/providers."""
    from app.main import app
    from app.api.dependencies import get_current_user
    from app.models.base import get_db
    from app.services import chat_contract_service as chat, itinerary_plan_service as plans
    from app.services.skill_card_service import publish_card
    from app.capabilities.train_multi_segment.train_skill_schema import TrainMultiSegmentPayload
    from app.capabilities.hotel_multi_segment.hotel_skill_schema import HotelMultiSegmentPayload
    from app.services.planning_math import stay_windows, day_cities
    factory, user, conversation, state = database
    state["selected_destination"] = "天津、上海"
    state["user_requirement"].update(destination="天津、上海", travel_days=4)
    routes = [("北京", "天津", "2026-12-01"), ("天津", "上海", "2026-12-02"), ("上海", "北京", "2026-12-04")]
    train_card = TrainMultiSegmentPayload.model_validate({"segments": [
        {"segment_id": f"s{index}", "from": origin, "to": destination, "date": date,
         "train_list": [dict(trainNo=f"G{index}", startTime="08:00", endTime="10:00", startStation=origin,
                             endStation=destination, duration="02:00", price=100, seatType="二等座",
                             ticketStatus="有票", option_id=f"option-{index}")]}
        for index, (origin, destination, date) in enumerate(routes, 1)]})
    hotels = HotelMultiSegmentPayload.model_validate({"segments": [
        dict(segment_id=f"h{index}", city=city, checkInDate=check_in, checkOutDate=check_out,
             guestCount=2, priceLevel="经济型", mode="brand", brand_list=[dict(brand_id=f"brand-{index}", brandName="汉庭")])
        for index, (city, check_in, check_out) in enumerate(stay_windows("2026-12-01", 4, ["天津", "上海"]), 1)]})
    monkeypatch.setattr(plans, "_enrich_spots", AsyncMock())
    monkeypatch.setattr(plans, "_enrich_routes", AsyncMock())
    monkeypatch.setattr(plans, "schedule_quality_check", lambda _: None)

    async def produce(db, user_id, conversation_id, content, stream, **kwargs):
        if kwargs.get("persist_user"):
            await chat._store_message(db, conversation_id, "user", content)
            await publish_card(stream, conversation_id, state, "train_multi_segment", train_card)
            extra = {"skill_ui": stream.result["skill_ui"]}
            text = "请选择三段高铁"
        elif state["current_step"] == "accommodation_planning":
            await publish_card(stream, conversation_id, state, "hotel_multi_segment", hotels)
            extra = {"skill_ui": stream.result["skill_ui"]}
            text = kwargs["confirmation_text"] + "请选择两站住宿"
        else:
            days = [dict(day=index, city=city, morning=[f"{city}文化街区"], noon=["午餐"],
                         afternoon=["博物馆"], evening=["晚餐与休息"])
                    for index, city in enumerate(day_cities(4, ["天津", "上海"]), 1)]
            plan_id = await plans.generate_plan(db, conversation_id, "四天行程", structure=days)
            # Retry generation for this run updates the same plan.
            assert await plans.generate_plan(db, conversation_id, "四天行程", structure=days) == plan_id
            frame = {"status": "success", "id": plan_id, "message": "行程清单已生成"}
            stream.push({"role": "itinerary_plans", "content": frame})
            extra = {"itinerary_plans": frame}
            text = kwargs["confirmation_text"] + "完整四天行程"
            state["current_step"] = "budget_summarization"
        stream.push({"role": "assistant", "content": text})
        await chat._store_message(db, conversation_id, "assistant", text, {**extra, "original": text})
        stream.push({"role": "done", "code": 200})

    monkeypatch.setattr(chat, "_produce", produce)
    async def db_dependency():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = lambda: user
    import json
    def events(response):
        assert response.status_code == 200
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert frames[-1]["role"] == "done" and frames[-1]["code"] == 200
        return frames
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            initial = events(await client.post("/api/v1/messages", json={
                "conversation_id": str(conversation.id), "content": "安排四天多城市旅行", "client_request_id": "initial"}))
            card = next(frame["content"] for frame in initial if frame["role"] == "skill_ui")
            transport = events(await client.post("/api/v1/skills/train_multi_segment/submit", json={
                "conversation_id": str(conversation.id), "card_id": card["card_id"], "client_request_id": "train",
                "payload": {"segments": [dict(segment_id=f"s{index}", option_id=f"option-{index}") for index in range(1, 4)]}}))
            hotel = next(frame["content"] for frame in transport if frame["role"] == "skill_ui")
            report = events(await client.post("/api/v1/skills/hotel_multi_segment/submit", json={
                "conversation_id": str(conversation.id), "card_id": hotel["card_id"], "client_request_id": "hotel",
                "payload": {"segments": [dict(segment_id=f"h{index}", mode="brand", brand_id=f"brand-{index}") for index in (1, 2)]}}))
            plan_id = next(frame["content"]["id"] for frame in report if frame["role"] == "itinerary_plans")
            plan = (await client.get(f"/api/v1/itinerary-plans/{plan_id}")).json()["data"]
            assert len(plan["itinerary_list"]) == 4
            history = (await client.get(f"/api/v1/messages?conversation_id={conversation.id}")).json()["data"]["messages"]
            assert len(history) == 4
            cards = [(message.get("extra_info") or {}).get("skill_ui") for message in history]
            assert sum(bool(frame and frame.get("submitted")) for frame in cards) == 2
            latest = (await client.get(f"/api/v1/conversations/{conversation.id}/run")).json()["data"]
            assert latest["status"] == "succeeded"
            assert latest["result"]["itinerary_plans"]["id"] == plan_id
    finally:
        app.dependency_overrides.clear()
