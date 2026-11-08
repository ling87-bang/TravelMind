"""
会话管理 API
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.models.base import get_db
from app.models.user import User
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationCreate, ConversationUpdate, ConversationResponse
from app.services import conversation_service  # 会话业务逻辑收敛到服务层
from app.api.dependencies import get_current_user

router = APIRouter(prefix="/conversations", tags=["会话管理"])


@router.post("", response_model=ConversationResponse)
async def create_conversation(
        data: ConversationCreate,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """创建新会话"""
    conversation = await conversation_service.create_conversation(db, user.id, data.title)  # 走服务层
    return ConversationResponse.from_orm(conversation)


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """获取用户的所有会话"""
    conversations = await conversation_service.list_conversations(db, user.id)
    return [ConversationResponse.from_orm(c) for c in conversations]

@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
        conversation_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """获取会话详情"""

    conversation = await conversation_service.get_owned_conversation(db, conversation_id, user.id)

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在"
        )

    return ConversationResponse.from_orm(conversation)


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
        conversation_id: str,
        data: ConversationUpdate,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """更新会话"""

    conversation = await conversation_service.get_owned_conversation(db, conversation_id, user.id)

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在"
        )

    # 更新字段
    if data.title is not None:  # is not None：区分"没传"和"传了空串"
        conversation.title = data.title
    if data.status is not None:
        conversation.status = data.status

    await db.commit()
    await db.refresh(conversation)

    return ConversationResponse.from_orm(conversation)


@router.delete("/{conversation_id}")
async def delete_conversation(
        conversation_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """删除会话（软删除）"""

    conversation = await conversation_service.get_owned_conversation(db, conversation_id, user.id)

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在"
        )

    conversation.status = "deleted"  # 软删
    await db.commit()

    return {"message": "会话已删除"}

@router.get("/{conversation_id}/run")
async def get_conversation_run(conversation_id: str, user: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db)):
    from app.services.chat_run_service import latest_run, run_data
    conv = await conversation_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    run = await latest_run(db, conversation_id)
    return run_data(run) if run else None


@router.post("/{conversation_id}/run/retry")
async def retry_conversation_run(conversation_id: str, user: User = Depends(get_current_user),
                                 db: AsyncSession = Depends(get_db)):
    from fastapi.responses import StreamingResponse
    from app.core.sse import stream_sse
    from app.api.v1.messages import _SSE_HEADERS
    from app.services.chat_run_service import latest_run, reserve_run, watch_run
    conv = await conversation_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    run = await latest_run(db, conversation_id)
    if not run:
        raise HTTPException(404, "没有可恢复的任务")
    request = run.request_data
    run = await reserve_run(db, str(user.id), conversation_id, run.request_id,
                            request["content"], request.get("skill"), request.get("payload"),
                            request.get("card_id"), options={key: request[key] for key in (
                                "force_planning", "persist_user", "resume_stage") if key in request})
    return StreamingResponse(stream_sse(watch_run(str(run.id))), media_type="text/event-stream",
                             headers=_SSE_HEADERS)
