"""Typed UI submissions share durable admission, validation and SSE resumption."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.api.v1.messages import _SSE_HEADERS
from app.core.sse import stream_sse
from app.models.base import get_db
from app.models.user import User
from app.services.conversation_service import get_owned_conversation
from app.services.chat_run_service import reserve_run, watch_run
from app.capabilities.train_multi_segment.train_skill_schema import TrainSubmitPayload
from app.capabilities.hotel_multi_segment.hotel_skill_schema import HotelMultiSegmentSubmitPayload
from app.capabilities.flight_multi_segment.flight_skill_schema import FlightSubmitPayload
from app.capabilities.driving_multi_segment.driving_skill_schema import DrivingSubmitPayload

router = APIRouter(prefix="/skills", tags=["Agent Skill"])


class SubmitRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    card_id: str | None = None
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128)


class TrainSubmitRequest(SubmitRequest):
    payload: TrainSubmitPayload


class HotelSubmitRequest(SubmitRequest):
    payload: HotelMultiSegmentSubmitPayload


class FlightSubmitRequest(SubmitRequest):
    payload: FlightSubmitPayload


class DrivingSubmitRequest(SubmitRequest):
    payload: DrivingSubmitPayload


async def _submit(skill, req, user, db):
    conv = await get_owned_conversation(db, req.conversation_id, user.id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    run = await reserve_run(db, str(user.id), str(conv.id), req.client_request_id,
                            skill=skill, payload=req.payload.model_dump(), card_id=req.card_id)
    return StreamingResponse(stream_sse(watch_run(str(run.id))), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


@router.post("/train_multi_segment/submit")
async def submit_train_multi_segment(req: TrainSubmitRequest,
                                      user: User = Depends(get_current_user),
                                      db: AsyncSession = Depends(get_db)):
    return await _submit("train_multi_segment", req, user, db)


@router.post("/hotel_multi_segment/submit")
async def submit_hotel_multi_segment(req: HotelSubmitRequest,
                                      user: User = Depends(get_current_user),
                                      db: AsyncSession = Depends(get_db)):
    return await _submit("hotel_multi_segment", req, user, db)


@router.post("/flight_multi_segment/submit")
async def submit_flight_multi_segment(req: FlightSubmitRequest,
                                      user: User = Depends(get_current_user),
                                      db: AsyncSession = Depends(get_db)):
    return await _submit("flight_multi_segment", req, user, db)


@router.post("/driving_multi_segment/submit")
async def submit_driving_multi_segment(req: DrivingSubmitRequest,
                                      user: User = Depends(get_current_user),
                                      db: AsyncSession = Depends(get_db)):
    return await _submit("driving_multi_segment", req, user, db)
