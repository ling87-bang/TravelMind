"""Persist UI candidates before publishing, and validate the exact submitted snapshot."""
import hashlib
import importlib
import json
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.models.base import async_session_maker
from app.models.chat_runtime import SkillCard
from app.models.message import Message
from app.services.runtime_context import current_run


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def planning_version(state: dict) -> str:
    return digest({"requirement": state.get("user_requirement"),
                   "destination": state.get("selected_destination")})


def capability(skill: str):
    names = {
        "train_multi_segment": ("train", "TrainMultiSegmentPayload", "TrainSubmitPayload", "apply_train_selection"),
        "hotel_multi_segment": ("hotel", "HotelMultiSegmentPayload", "HotelMultiSegmentSubmitPayload", "apply_hotel_selection"),
        "flight_multi_segment": ("flight", "FlightMultiSegmentPayload", "FlightSubmitPayload", "apply_flight_selection"),
        "driving_multi_segment": ("driving", "DrivingMultiSegmentPayload", "DrivingSubmitPayload", "apply_driving_selection"),
    }
    if skill not in names:
        raise HTTPException(404, "未知卡片类型")
    name, candidate_type, submit_type, apply_name = names[skill]
    module = f"app.capabilities.{skill}"
    schema = importlib.import_module(f"{module}.{name}_skill_schema")
    flow = importlib.import_module(f"{module}.{name}_flow")
    return name, getattr(schema, candidate_type), getattr(schema, submit_type), getattr(flow, apply_name)


async def publish_card(stream, conversation_id: str, state: dict, skill: str, payload):
    frame = {"skill_name": skill, "data": payload.model_dump(by_alias=True)}
    if current_run.get():
        async with async_session_maker() as db:
            card = SkillCard(conversation_id=uuid.UUID(conversation_id), skill_name=skill,
                             planning_version=planning_version(state), payload=frame["data"])
            db.add(card)
            await db.commit()
            frame["card_id"] = str(card.id)
    stream.push({"role": "skill_ui", "content": frame})


def selection_map(payload: dict) -> dict[str, str]:
    return {str(item["segment_id"]): str(item.get("option_id") or item.get("hotel_id")
                                        or item.get("brand_id") or "confirmed")
            for item in payload.get("segments", [])}


async def resolve_card(db, conversation_id: str, skill: str, card_id: str | None, state: dict):
    """Legacy recovery requires a matching current segment layout and the latest card."""
    if card_id:
        try:
            identifier = uuid.UUID(card_id)
        except ValueError:
            raise HTTPException(422, "卡片标识格式不正确")
        card = await db.get(SkillCard, identifier)
        if not card or str(card.conversation_id) != conversation_id or card.skill_name != skill:
            raise HTTPException(404, "卡片不存在")
        return card

    latest = (await db.execute(select(SkillCard).where(
        SkillCard.conversation_id == uuid.UUID(conversation_id), SkillCard.skill_name == skill
    ).order_by(SkillCard.created_at.desc()).limit(1))).scalar_one_or_none()
    if latest:
        if latest.run_id:
            return latest
        raise HTTPException(409, "卡片缺少标识，请刷新页面并使用最新卡片")

    messages = (await db.execute(select(Message).where(
        Message.conversation_id == uuid.UUID(conversation_id), Message.role == "assistant"
    ).order_by(Message.created_at.desc()).limit(30))).scalars().all()
    source = next((message for message in messages
                   if ((message.extra_info or {}).get("skill_ui") or {}).get("skill_name") == skill), None)
    frame = (source.extra_info or {}).get("skill_ui") if source else None
    latest_message = (await db.execute(select(Message.id).where(
        Message.conversation_id == uuid.UUID(conversation_id)
    ).order_by(Message.created_at.desc()).limit(1))).scalar_one_or_none()
    expected_step = "accommodation_planning" if skill == "hotel_multi_segment" else "transport_planning"
    if (not frame or latest_message != source.id or frame.get("submitted")
            or state.get("current_step") != expected_step):
        raise HTTPException(409, "无法确认历史卡片所属规划，请重新获取候选")
    name, candidate_type, _, _ = capability(skill)
    try:
        candidate = candidate_type.model_validate(frame.get("data"))
    except ValidationError:
        raise HTTPException(409, "历史卡片数据不完整，请重新获取候选")
    flow = importlib.import_module(f"app.capabilities.{skill}.{name}_flow")
    expected = flow._segments(state) if name == "hotel" else flow._legs_with_dates(state)
    actual = candidate.segments
    def route(segment):
        raw = segment.model_dump(by_alias=True)
        return tuple(raw.get(key) for key in (
            ("city", "checkInDate", "checkOutDate", "guestCount", "priceLevel")
            if name == "hotel" else ("from", "to", "date")))
    # Compare normalized schema fields for both historical and current segments.
    if name == "hotel":
        matches = [route(item) for item in actual] == [route(item) for item in expected]
    else:
        matches = [(item.from_, item.to, item.date) for item in actual] == [
            (item.from_, item.to, item.travel_date) for item in expected]
    if not actual or not matches:
        raise HTTPException(409, "历史卡片与当前行程不匹配，请重新获取候选")
    card = SkillCard(conversation_id=uuid.UUID(conversation_id), skill_name=skill,
                     planning_version=planning_version(state), payload=frame["data"])
    db.add(card)
    await db.flush()
    return card


async def mark_card_history(db, card: SkillCard):
    messages = (await db.execute(select(Message).where(
        Message.conversation_id == card.conversation_id, Message.role == "assistant"
    ).order_by(Message.created_at.desc()))).scalars().all()
    for message in messages:
        extra = dict(message.extra_info or {})
        frame = extra.get("skill_ui") or {}
        if frame.get("card_id") == str(card.id) or (
            not frame.get("card_id") and frame.get("skill_name") == card.skill_name
            and frame.get("data") == card.payload
        ):
            extra["skill_ui"] = {**frame, "card_id": str(card.id),
                                 "submitted": {"selections": selection_map(card.submission or {})}}
            message.extra_info = extra
            break
