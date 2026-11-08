'use client';

import { memo, useCallback, useState } from 'react';
import { useDispatch } from 'react-redux';
import type { AppDispatch } from '@/store';
import type { ChatMessage, SkillUiFrame } from '@/lib/types';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  fetchPlanThunk,
  submitTrainSkillThunk,
} from '@/store/slices/chatSlice';
import { showToast } from '@/store/slices/uiSlice';
import { UI_SKILL_REGISTRY, type UiSkillProps } from '../skills/train-multi-segment/skillRegistry';
import type { TrainMultiSegmentSkillData } from '../skills/train-multi-segment/types';
import LoadingDots from '../common/LoadingDots';

type AnySkillComponent = React.ComponentType<
  UiSkillProps<unknown, unknown> & { data: unknown }
>;

/**
 * 聊天流内嵌 UI Skill 块：按帧里的 skill_name 查注册表取组件渲染。
 * 未知 skill_name 直接不渲染（后端加新技能而前端未发布时不影响对话）。
 */
function SkillBlock({ frame }: { frame: SkillUiFrame }) {
  const dispatch = useDispatch<AppDispatch>();
  const [submitting, setSubmitting] = useState(false);
  // 持久化提交状态用于刷新和切换会话后的恢复，本轮提交成功由本地状态立即锁定。
  // 或刷新后，卡片仍为锁定态且预览能复原各段选中项；本轮刚提交的由本地 done 立即接管，
  // 两者只需其一为真即视为已提交
  const persisted = frame.submitted;
  const [done, setDone] = useState(() => Boolean(persisted));

  // 按 skill_name 提交后继续消费 SSE，让后端恢复规划并生成后续回复。
  // 后端落库后直接续跑后续编排（住宿→餐饮→行程），回复以新的助手气泡接着出现在对话里；
  // 此前只回 JSON，提交完流程就停住
  const handleSubmit = useCallback(
    async (payload: unknown) => {
      if (submitting) return;
      setSubmitting(true);
      try {
        const res = await dispatch(
          submitTrainSkillThunk({ skillName: frame.skill_name, payload, cardId: frame.card_id }),
        ).unwrap();
        if (res.ok) setDone(true);
        if (res.msg) dispatch(showToast({ message: res.msg, type: 'error' }));
      } finally {
        setSubmitting(false);
      }
    },
    [dispatch, submitting, frame.skill_name, frame.card_id],
  );

  const key = frame.skill_name as keyof typeof UI_SKILL_REGISTRY;
  if (!(key in UI_SKILL_REGISTRY)) return null;
  const SkillComp = UI_SKILL_REGISTRY[key] as unknown as AnySkillComponent;
  // 提交成功后不再卸载卡片：保留已提交状态在对话流里展示（用户要求，视觉更连贯）；
  // 交由外层 pointer-events 锁 + 组件 submitted 标记共同禁用交互，避免重复提交

  return (
    <div className={`mt-3${submitting || done || persisted ? ' pointer-events-none' : ''}`}>
      <SkillComp
        data={frame.data as TrainMultiSegmentSkillData}
        onSubmit={handleSubmit}
        submitting={submitting}
        submitted={done || Boolean(persisted)}
        initialSelections={persisted?.selections}
      />
    </div>
  );
}

interface MessageItemProps {
  message: ChatMessage;
  /** 是否为当前流式渲染中的助手消息（由 ChatMain 判定） */
  isStreamingTarget: boolean;
}

/**
 * 单条消息（用户 / AI 气泡）
 * - 原版信纸边框装饰：助手消息左侧金色标线 + 左上圆角 2px
 * - 消息入场 slideIn 滑入动画（transform 硬件加速 + will-change）
 * - 流式期间以纯文本增量展示，结束后统一渲染 Markdown。
 */
function MessageItem({ message, isStreamingTarget }: MessageItemProps) {
  const dispatch = useDispatch<AppDispatch>();
  const isUser = message.role === 'user';

  // 点击查看行程清单：GET /api/v1/itinerary-plans/{planId} → PlanModal
  const handleViewPlan = useCallback(() => {
    if (message.planId) dispatch(fetchPlanThunk(message.planId));
  }, [dispatch, message.planId]);


  // 加载占位：只渲染三点动画
  if (message.kind === 'loading') {
    return (
      <div className="animate-slide-in flex max-w-[800px] gap-4 will-change-transform">
        <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-primary text-sm text-white">
          <i className="fa-solid fa-robot" />
        </div>
        <div className="relative rounded-xl border border-black/5 bg-white px-6 py-4 text-[15px] leading-[1.8] shadow-bubble">
          <LoadingDots />
        </div>
      </div>
    );
  }

  return (
    <div
      className={`animate-slide-in flex max-w-[800px] gap-4 will-change-transform ${
        isUser ? 'flex-row-reverse self-end' : ''
      }`}
    >
      <div
        className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full text-sm text-white ${
          isUser ? 'bg-accent' : 'bg-primary'
        }`}
      >
        <i className={`fa-solid ${isUser ? 'fa-user' : 'fa-robot'}`} />
      </div>

      <div
        className={`relative rounded-xl px-6 py-4 text-[15px] leading-[1.8] shadow-bubble ${
          isUser
            ? 'rounded-tr-[2px] bg-primary text-white'
            : 'rounded-tl-[2px] border border-black/5 bg-white text-text-main before:absolute before:bottom-[15px] before:left-0 before:top-[15px] before:w-[3px] before:rounded-[0_2px_2px_0] before:bg-accent before:opacity-50 before:content-[""]'
        }`}
      >
        <div>
          {isStreamingTarget ? <span className="whitespace-pre-wrap">{message.content}</span> : (
            isUser ? <span className="whitespace-pre-wrap">{message.content}</span> : (
              <div className="markdown-body">
                <Markdown remarkPlugins={[remarkGfm]} skipHtml>{message.content}</Markdown>
              </div>
            )
          )}
        </div>

        {isStreamingTarget && message.waiting && <LoadingDots />}

        {(message.planId || message.planPending) && (
          <button
            type="button"
            onClick={handleViewPlan}
            disabled={!message.planId}
            className={`mt-3 flex items-center gap-1.5 rounded-sm border px-3 py-1.5 text-[13px] font-semibold transition-colors duration-200 ${
              message.planId
                ? 'cursor-pointer border-accent bg-transparent text-accent hover:bg-accent hover:text-white'
                : 'cursor-progress border-accent/40 bg-accent/5 text-accent/70'
            }`}
          >
            {message.planId ? (
              <>
                <i className="fa-regular fa-map" /> 查看行程清单
              </>
            ) : (
              <>
                <i className="fa-solid fa-spinner fa-spin" /> 行程清单生成中，可先看正文
              </>
            )}
          </button>
        )}

        {message.skillUi && !isStreamingTarget && <SkillBlock frame={message.skillUi} />}
      </div>
    </div>
  );
}

export default memo(MessageItem);
