/**
 * 聊天切片：对话列表、消息、加载状态、SSE 流式异步 thunk
 *
 * 性能优化点：
 * 1. SSE 增量分段：正文最多每 50ms 更新一次 Redux，切会话仍能持续接收；
 * 2. 滚动使用 requestAnimationFrame 节流，最多每帧滚动一次；
 * 3. 切换会话时校验 currentConversationId，防止慢请求覆盖新会话数据。
 */
import {
  createAsyncThunk,
  createSlice,
  type PayloadAction,
} from '@reduxjs/toolkit';
import {
  ApiError,
  createConversationApi,
  fetchConversationApi,
  fetchConversationsApi,
  fetchHistoryApi,
  fetchItineraryPlanApi,
  notifyUnauthorized,
  streamChatApi,
  submitSkillApi,
  fetchRunApi,
  retryRunApi,
  clearPendingRequest,
  updateConversationStatusApi,
} from '@/lib/api';
import type {
  ChatMessage,
  Conversation,
  HistoryMessage,
  ItineraryPlanEvent,
  MessageRole,
  SkillEvent,
  SkillUiFrame,
  StreamEvent,
  ChatRunStatus,
} from '@/lib/types';
import type { PersistedChat } from '@/lib/persist';
import { streamErrorMessage, streamResultText } from '@/lib/stream-result';
import { setPlanLoading, showToast, showPlan } from './uiSlice';
import type { RootState, AppDispatch } from '../index';


/** 类型化 thunk 工厂：让 getState() 推断为 RootState、dispatch 兼容 AppDispatch */
const createTypedAsyncThunk = createAsyncThunk.withTypes<{
  state: RootState;
  dispatch: AppDispatch;
}>();

export interface ChatState {
  recoveredRun: ChatRunStatus | null;
  /** 全部对话（行程）列表 */
  conversations: Conversation[];
  /** 当前会话 ID */
  currentConversationId: string | null;
  /** 当前会话的消息数组 */
  messages: ChatMessage[];
  /** 发送加载锁：防止重复点击重复请求 */
  isLoading: boolean;
  /** SSE 流式进行中 */
  isStreaming: boolean;
  /** 正在流式渲染的助手消息 id */
  streamingMessageId: string | null;
  streamingConversationId: string | null;
  /** 切走会话时暂存正在接收的消息，返回后继续展示。 */
  backgroundStreamMessages: ChatMessage[];
  /** 流式过程中的状态提示文本（skills 执行步骤 / 清单生成中） */
  statusText: string | null;
  /** 全新会话是否已提交旅行需求表单：未提交时禁用输入（表单作为聊天门禁） */
  requirementSubmitted: boolean;
}

const initialState: ChatState = {
  recoveredRun: null,
  conversations: [],
  currentConversationId: null,
  messages: [],
  isLoading: false,
  isStreaming: false,
  streamingMessageId: null,
  streamingConversationId: null,
  backgroundStreamMessages: [],
  statusText: null,
  requirementSubmitted: false,
};


/** 消息滚动容器引用：由 ChatMain 注册 */
export const scrollContainerRef: { current: HTMLDivElement | null } = {
  current: null,
};

let pendingScroll = false;
/** rAF 节流滚动到底部 */
export function requestScroll(): void {
  const container = scrollContainerRef.current;
  if (!container || pendingScroll) return;
  pendingScroll = true;
  requestAnimationFrame(() => {
    const el = scrollContainerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    pendingScroll = false;
  });
}

/** 消息 id 生成器（自增序列，保证并发下唯一） */
let msgSeq = 0;
function genId(prefix: string): string {
  msgSeq += 1;
  return `${prefix}-${Date.now()}-${msgSeq}`;
}

function historyMessages(messages: HistoryMessage[]): ChatMessage[] {
  return messages.map((m: HistoryMessage) => ({
            id: m.id || genId('msg'),
            role: m.role,
            // assistant 的 content 是压缩摘要，全文在 extra_info.original
            content:
              m.role === 'assistant'
                ? m.extra_info?.original || m.content
                : m.content,
            // 历史 itinerary_plans 成功帧回放 → 恢复「查看行程清单」入口
            planId:
              m.extra_info?.itinerary_plans?.status === 'success' &&
              m.extra_info.itinerary_plans.id
                ? m.extra_info.itinerary_plans.id
                : undefined,
            // 历史卡片帧回放 → 刷新/切会话后卡片仍内嵌在消息里重渲染
            skillUi:
              m.extra_info?.skill_ui?.skill_name
                ? m.extra_info.skill_ui
                : undefined,
            kind: 'normal' as const,
            createdAt: Date.now(),
          }));
}

/** 流式累积结果：正文 / 错误文案 / 清单 id / 内嵌卡片载荷（流结束后统一提交回 redux） */
interface StreamAcc {
  cardAccepted?: boolean;
  fullText: string;
  errorText: string;
  planId?: string;
  planPending?: boolean;
  skillUi?: SkillUiFrame;
  /** 当前是否处于等待态（配合静默计时器做去重，避免重复派发） */
  waiting: boolean;
}

/** 等待动画静默阈值：流式分片间歇超过该时长即回到等待态展示波浪动画 */
const WAIT_QUIET_MS = 900;

/**
 * 统一事件处理 ：按 knowgo_travel 事件 Schema 分发
 * assistant → 增量追加；skills / itinerary_plans → 状态提示；
 * error → 保留正文并记录错误；end(code=401) → 全局登出
 */
function handleStreamEvent(
  evt: StreamEvent,
  acc: StreamAcc,
  dispatch: AppDispatch,
  msgId: string,
): void {
  switch (evt.role) {
    case 'skill_submitted': {
      const content = evt.content as NonNullable<ChatRunStatus['result']['skill_submitted']>;
      if (content?.card_id) {
        acc.cardAccepted = true;
        dispatch(markCardSubmitted(content));
      }
      break;
    }
    case 'assistant': {
      const chunk = typeof evt.content === 'string' ? evt.content : '';
      if (chunk) {
        acc.fullText += chunk;
      }
      break;
    }
    case 'assistant_reset': {
      // 后端择优回退：本回合已显示的内容作废，清空累计文本，
      // 随后由新的 assistant 帧重新推送（避免"旁白 + 正文"两份同时在屏幕上）
      acc.fullText = '';
      break;
    }
    case 'skills': {
      // 技能执行状态 → 状态提示条（如「行程规划：正在为您制定行程」）
      const s = evt.content as SkillEvent | undefined;
      if (s?.title) dispatch(setStreamStatus(`${s.title}：${s.desc ?? ''}`));
      break;
    }
    case 'skill_ui': {
      // 结构化 UI Skill 载荷（如多段高铁选择卡片）→ 挂到本条消息上，
      // 由 MessageItem 按 skill_name 查注册表内嵌渲染；此处不做任何业务校验
      const frame = evt.content as SkillUiFrame | undefined;
      if (frame?.skill_name) acc.skillUi = frame;
      break;
    }
    case 'itinerary_plans': {
      const c = evt.content as ItineraryPlanEvent | undefined;
      if (c?.status === 'success' && c.id) {
        acc.planId = c.id;
        dispatch(setStreamStatus(null));
      } else if (c?.status === 'pending') {
        // pending 帧：立即标记消息"清单生成中"，流式期间即显示生成中按钮
        acc.planPending = true;
        dispatch(setMsgPlanPending(msgId));
        dispatch(setStreamStatus(c.message || '行程清单生成中...'));
      } else if (c?.status === 'error') {
        acc.planPending = false;
        dispatch(setStreamStatus(null));
        dispatch(showToast({ message: c.message || '行程清单生成失败', type: 'error' }));
      }
      break;
    }
    case 'error': {
      acc.errorText = streamErrorMessage(evt.content);
      break;
    }
    case 'end': {
      if (evt.code === 401) notifyUnauthorized();
      break;
    }
    default:
      break;
  }
}

/**
 * 消费 SSE 响应流 ：逐行解析 data: 统一事件帧
 * 缓冲半行 + 流式 UTF-8 解码，避免中文多字节字符被网络分包截断
 */
async function consumeStream(
  res: Response,
  msgId: string,
  dispatch: AppDispatch,
): Promise<StreamAcc> {
  const reader = res.body?.getReader();
  if (!reader) {
    const empty: StreamAcc = {
      fullText: '',
      errorText: '连接中断，请稍后重试。',
      waiting: false,
    };
    dispatch(commitStreamMessage({ id: msgId, content: empty.errorText }));
    return empty;
  }
  const decoder = new TextDecoder();
  const acc: StreamAcc = { fullText: '', errorText: '', planPending: false, waiting: true };
  let buffer = '';
  let receivedDone = false;
  let lastPaint = 0;
  let paintTimer: ReturnType<typeof setTimeout> | null = null;
  const paint = () => {
    paintTimer = null;
    dispatch(updateStreamContent({ id: msgId, content: acc.fullText }));
    requestScroll();
    lastPaint = Date.now();
  };

  // 等待波浪动画：分片流动即退出等待态；静默超过阈值（模型思考/skill 执行）即回到等待态
  let quietTimer: ReturnType<typeof setTimeout> | null = null;
  const armQuietTimer = () => {
    if (quietTimer) clearTimeout(quietTimer);
    quietTimer = setTimeout(() => {
      quietTimer = null;
      if (acc.waiting) return;
      acc.waiting = true;
      dispatch(setMsgWaiting({ id: msgId, waiting: true }));
    }, WAIT_QUIET_MS);
  };
  const stopQuietTimer = () => {
    if (quietTimer) {
      clearTimeout(quietTimer);
      quietTimer = null;
    }
  };

  try {
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? ''; // 半行留待下一包拼接

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data:')) continue;
      const data = trimmed.slice(5).trim();
      if (!data || data === '[DONE]') continue;
      try {
        const evt = JSON.parse(data) as StreamEvent;
        // 正文分片到达：立即退出等待态（隐藏波浪动画）并重置静默计时
        if (evt.role === 'assistant' && typeof evt.content === 'string' && evt.content) {
          if (acc.waiting) {
            acc.waiting = false;
            dispatch(setMsgWaiting({ id: msgId, waiting: false }));
          }
          armQuietTimer();
        }
        handleStreamEvent(evt, acc, dispatch, msgId);
        if (evt.role === 'done' || evt.role === 'end') {
          receivedDone = true;
          if (evt.code && evt.code >= 400 && !acc.errorText) {
            acc.errorText = '本轮未完成，请稍后重试。';
          }
        }
        if (evt.role === 'assistant' || evt.role === 'assistant_reset') {
          if (Date.now() - lastPaint >= 50) {
            if (paintTimer) clearTimeout(paintTimer);
            paint();
          } else if (!paintTimer) {
            paintTimer = setTimeout(paint, 50 - (Date.now() - lastPaint));
          }
        }
      } catch {
        /* 单帧解析失败不中断整条流 */
      }
    }
  }
  if (!receivedDone && !acc.errorText) acc.errorText = '连接提前结束，本轮未完成，请重试。';
  } catch {
    acc.errorText ||= '连接中断，本轮未完成，请稍后重试。';
  } finally {
    stopQuietTimer();
    if (paintTimer) clearTimeout(paintTimer);
    reader.releaseLock();
  }

  // 完成后切换到 Markdown 渲染；失败保留部分正文，并追加可读提示。
  dispatch(
    commitStreamMessage({
      id: msgId,
      content: streamResultText(acc.fullText, acc.errorText),
      planId: acc.planId,
      skillUi: acc.skillUi,
    }),
  );
  return acc;
}


/** 获取全部对话列表：GET /api/v1/conversations */
export const fetchConversationsThunk = createTypedAsyncThunk(
  'chat/fetchConversations',
  async (_: void, { dispatch }) => {
    try {
      const convs = await fetchConversationsApi();
      dispatch(setConversations(convs));
    } catch (e) {
      // 401 由全局鉴权处理器统一登出并提示，此处不重复弹窗
      if (e instanceof ApiError && e.status === 401) return;
      dispatch(
        showToast({ message: e instanceof Error ? e.message : '获取行程失败', type: 'error' }),
      );
    }
  },
);

/** 新建对话：POST /api/v1/conversations */
export const createConversationThunk = createTypedAsyncThunk(
  'chat/createConversation',
  async (_: void, { dispatch }) => {
    try {
      const conv = await createConversationApi();
      dispatch(setCurrentConversationId(conv.id));
      dispatch(clearMessages());
      // 与原生逻辑一致：创建后刷新列表
      dispatch(fetchConversationsThunk());
      dispatch(showToast({ message: '新行程已创建' }));
    } catch (e) {
      dispatch(showToast({ message: '创建失败', type: 'error' }));
    }
  },
);

/** 切换对话：加载标题 + 历史消息 */
export const switchConversationThunk = createTypedAsyncThunk(
  'chat/switchConversation',
  async (id: string, { getState, dispatch }) => {
    dispatch(setCurrentConversationId(id));
    // 活跃流已经有本地消息；重新读历史会用新 ID 覆盖正在接收的气泡。
    if (getState().chat.isStreaming && getState().chat.streamingConversationId === id) return;

    // 获取标题（失败静默，不阻塞历史加载）
    try {
      const conv = await fetchConversationApi(id);
      if (getState().chat.currentConversationId !== id) return;
      dispatch(updateConversation(conv));
    } catch {
      /* 忽略标题加载失败 */
    }

    // 获取历史消息
    try {
      const msgs = await fetchHistoryApi(id);
      if (getState().chat.currentConversationId !== id) return;
      if (getState().chat.isStreaming && getState().chat.streamingConversationId === id) return;
      dispatch(
        setMessages(
          historyMessages(msgs),
        ),
      );
    } catch {
      if (getState().chat.currentConversationId !== id) return;
      dispatch(clearMessages());
    }
  },
);

/** 发送消息：POST /api/v1/messages（SSE 流式）自动建会话 → 发送锁 → 用户消息 → 加载占位 → 流式消费 / 兜底渲染 → 释放锁。 */
export const updateConversationStatusThunk = createTypedAsyncThunk(
  'chat/updateConversationStatus',
  async ({ id, status }: { id: string; status: 'active' | 'archived' }, { dispatch, getState }) => {
    const conversation = await updateConversationStatusApi(id, status);
    dispatch(updateConversation(conversation));
    if (status === 'archived' && getState().chat.currentConversationId === id) {
      dispatch(setCurrentConversationId(null));
    }
    return conversation;
  },
);

export const sendMessageThunk = createTypedAsyncThunk(
  'chat/sendMessage',
  async (content: string, { getState, dispatch }) => {
    const trimmed = content.trim();
    const { auth, chat } = getState() as RootState;
    if (!trimmed || chat.isLoading) return;

    // —— 无会话时先自动创建 ——
    let conversationId = chat.currentConversationId;
    if (!conversationId) {
      await dispatch(createConversationThunk()).unwrap();
      conversationId = (getState() as RootState).chat.currentConversationId;
      if (!conversationId) return;
    }

    // —— 发送锁 + 用户消息 + 助手加载占位 ——
    dispatch(setIsLoading(true));
    dispatch(
      appendMessage({
        id: genId('msg'),
        role: 'user',
        content: trimmed,
        kind: 'normal',
        createdAt: Date.now(),
      }),
    );
    const msgId = genId('msg');
    dispatch(startStreaming(msgId));

    try {
      const res = await streamChatApi(conversationId, trimmed);
      const isStream =
        res.ok && res.headers.get('content-type')?.includes('event-stream');

      if (!res.ok) {
        // SSE 端点业务错误（如 404 会话不存在）：解析 {detail}/{msg} 后走统一错误展示
        let detail = '请求失败';
        try {
          const body = (await res.json()) as { detail?: string; msg?: string };
          detail = body.detail || body.msg || detail;
        } catch {
          /* 非 JSON 响应体 */
        }
        throw new ApiError(res.status, detail);
      }

      if (isStream) {
        const result = await consumeStream(res, msgId, dispatch);
        if (!result.errorText) clearPendingRequest(conversationId);
        dispatch(fetchConversationsThunk());
      } else {
        // 非流式：直接渲染返回内容（data.content || data.message || JSON.stringify(data)）
        try {
          const data = (await res.json()) as { content?: string; message?: string };
          dispatch(
            commitStreamMessage({
              id: msgId,
              content: data.content || data.message || JSON.stringify(data),
            }),
          );
        } catch {
          dispatch(
            commitStreamMessage({
              id: msgId,
              content: '连接中断，请稍后重试。',
            }),
          );
        }
      }
    } catch (e) {
      // 网络异常 / 401：移除加载占位，追加错误提示
      const fallback =
        e instanceof ApiError && e.status !== 0 ? e.detail : '连接中断，请稍后重试。';
      dispatch(
        commitStreamMessage({
          id: msgId,
          content: fallback,
        }),
      );
    } finally {
      dispatch(setIsLoading(false));
      // 兜底复位流式态：请求异常时也必须解除发送锁，
      // 若 isStreaming 一直为 true，输入框会被永久禁用
      dispatch(endStreaming());
    }
  },
);

/**
 * 提交聊天内嵌 UI Skill 的选择结果（多段高铁卡片）：
 * POST /api/v1/skills/train_multi_segment/submit
 * 
 * 后端落库后**直接续跑后续编排**并以 SSE 下发，故这里与发送消息复用同一套事件管道：
 * 新建一条助手气泡接收住宿/餐饮等后续回复（此前只回 JSON，提交完流程就停住）。
 * 返回值 { ok, msg }：ok=false 时卡片保持展开，用户可重新选择后再次提交。
 */
export const submitTrainSkillThunk = createTypedAsyncThunk(
  'chat/submitSkill',
  async (
    args: { skillName: string; payload: unknown; cardId?: string },
    { getState, dispatch },
  ): Promise<{ ok: boolean; msg: string }> => {
    const conversationId = getState().chat.currentConversationId;
    if (!conversationId) return { ok: false, msg: '会话不存在，请刷新后重试' };
    if (getState().chat.isLoading) return { ok: false, msg: '当前回复尚未结束，请稍候' };
    // 加发送锁：续跑期间输入框整体禁用（等待中还能输入、抢发新一轮都不合理）
    dispatch(setIsLoading(true));
    const msgId = genId('msg');
    dispatch(startStreaming(msgId));
    try {
      // 按 skill_name 路由到对应提交端点（高铁/住宿各自落库并续跑后续编排）
      const res = await submitSkillApi(args.skillName, conversationId, args.payload, args.cardId);
      if (!res.ok) {
        // 非流式错误（如 404 会话不存在 / 422 参数非法）：解析 {detail}/{msg} 后如实告知
        let detail = '提交失败，请重试';
        try {
          const body = (await res.json()) as { detail?: string; msg?: string };
          detail = body.detail || body.msg || detail;
        } catch {
          /* 非 JSON 响应体 */
        }
        dispatch(commitStreamMessage({ id: msgId, content: detail }));
        return { ok: false, msg: detail };
      }
      const acc = await consumeStream(res, msgId, dispatch);
      if (!acc.errorText) clearPendingRequest(conversationId);
      return { ok: Boolean(acc.cardAccepted) || !acc.errorText, msg: acc.errorText };
    } catch (e) {
      const msg = e instanceof Error ? e.message : '提交失败，请重试';
      dispatch(commitStreamMessage({ id: msgId, content: msg }));
      return { ok: false, msg };
    } finally {
      dispatch(setIsLoading(false));
      dispatch(endStreaming()); // 兜底复位，防止异常路径把输入框锁死
    }
  },
);

/** Refresh recovery is independent of the original HTTP connection. */
export const recoverRunThunk = createTypedAsyncThunk(
  'chat/recoverRun',
  async (conversationId: string, { getState, dispatch }) => {
    if (getState().chat.isStreaming) return;
    try {
      const run = await fetchRunApi(conversationId);
      if (getState().chat.currentConversationId !== conversationId || getState().chat.isStreaming) return;
      const previous = getState().chat.recoveredRun;
      if (!run) return;
      const active = run.status === 'queued' || run.status === 'running';
      dispatch(setRecoveredRun(run));
      dispatch(setIsLoading(active));
      if (run.result.skill_submitted) dispatch(markCardSubmitted(run.result.skill_submitted));
      if (active) {
        const id = `run-${run.run_id}`;
        const messages = getState().chat.messages.filter(message => message.id !== id);
        dispatch(setMessages([...messages, { id, role: 'assistant', kind: 'normal',
          content: run.partial_text || '正在继续生成行程…', createdAt: Date.now(),
          skillUi: run.result.skill_ui,
          planId: run.result.itinerary_plans?.status === 'success' ? run.result.itinerary_plans.id : undefined,
        }]));
      } else if (previous?.run_id !== run.run_id || previous?.status !== run.status) {
        const history = await fetchHistoryApi(conversationId);
        if (getState().chat.currentConversationId !== conversationId || getState().chat.isStreaming) return;
        const messages = historyMessages(history);
        if (run.status === 'failed' && !history.some(message => message.run_id === run.run_id && message.role === 'assistant')) {
          messages.push({ id: `run-${run.run_id}`, role: 'assistant', kind: 'normal', createdAt: Date.now(),
            content: streamResultText(run.partial_text, streamErrorMessage(run.result.error)) });
        }
        dispatch(setMessages(messages));
        if (run.status === 'succeeded') clearPendingRequest(conversationId);
      }
    } catch { /* A later poll resumes transient network errors. */ }
  },
);

export const retryRunThunk = createTypedAsyncThunk(
  'chat/retryRun',
  async (_: void, { getState, dispatch }) => {
    const conversationId = getState().chat.currentConversationId;
    if (!conversationId || getState().chat.isLoading) return;
    dispatch(setIsLoading(true));
    dispatch(setRecoveredRun(null));
    const messageId = genId('msg');
    dispatch(startStreaming(messageId));
    try {
      const result = await consumeStream(await retryRunApi(conversationId), messageId, dispatch);
      if (!result.errorText) clearPendingRequest(conversationId);
    } catch (error) {
      dispatch(commitStreamMessage({ id: messageId, content: error instanceof Error ? error.message : '恢复失败，请重试' }));
    } finally {
      dispatch(setIsLoading(false));
      dispatch(endStreaming());
      dispatch(recoverRunThunk(conversationId));
    }
  },
);

/** 行程清单查看：GET /api/v1/itinerary-plans/{planId} → 打开弹窗 后端已保证落库即增强后完整数据（），单次拉取即可，无轮询 */
export const fetchPlanThunk = createTypedAsyncThunk(
  'chat/fetchPlan',
  async (planId: string, { dispatch }) => {
    dispatch(setPlanLoading(true));
    try {
      const plan = await fetchItineraryPlanApi(planId);
      dispatch(showPlan(plan));
    } catch (e) {
      dispatch(
        showToast({
          message: e instanceof Error ? e.message : '行程清单加载失败',
          type: 'error',
        }),
      );
    } finally {
      dispatch(setPlanLoading(false));
    }
  },
);


const chatSlice = createSlice({
  name: 'chat',
  initialState,
  reducers: {
    markCardSubmitted(state, action: PayloadAction<NonNullable<ChatRunStatus['result']['skill_submitted']>>) {
      for (const message of [...state.messages, ...state.backgroundStreamMessages]) {
        const frame = message.skillUi;
        if (frame && (frame.card_id === action.payload.card_id ||
            (!frame.card_id && frame.skill_name === action.payload.skill_name))) {
          frame.card_id = action.payload.card_id;
          frame.submitted = action.payload.submitted;
        }
      }
    },
    setRecoveredRun(state, action: PayloadAction<ChatRunStatus | null>) {
      state.recoveredRun = action.payload;
    },
    resetChat() {
      return initialState;
    },
    setConversations(state, action: PayloadAction<Conversation[]>) {
      state.conversations = action.payload;
    },
    /** 更新单个对话（标题），用于切换会话后回填 */
    updateConversation(state, action: PayloadAction<Conversation>) {
      const conv = action.payload;
      const idx = state.conversations.findIndex((c) => c.id === conv.id);
      if (idx >= 0) state.conversations[idx] = conv;
      else state.conversations.unshift(conv);
    },
    setCurrentConversationId(state, action: PayloadAction<string | null>) {
      if (state.currentConversationId === action.payload) return;
      if (state.isStreaming && state.currentConversationId === state.streamingConversationId) {
        state.backgroundStreamMessages = state.messages;
      }
      state.currentConversationId = action.payload;
      state.recoveredRun = null;
      state.messages = state.isStreaming && action.payload === state.streamingConversationId
        ? state.backgroundStreamMessages : [];
      if (action.payload === state.streamingConversationId) state.backgroundStreamMessages = [];
      state.statusText = null;
      // 会话切换时同步表单门禁：选中已有会话放行输入；回到全新会话重新要求填表单
      state.requirementSubmitted = action.payload !== null;
    },
    /** 表单提交成功：放行当前全新会话的输入框 */
    markRequirementSubmitted(state) {
      state.requirementSubmitted = true;
    },
    setMessages(state, action: PayloadAction<ChatMessage[]>) {
      state.messages = action.payload;
    },
    clearMessages(state) {
      state.messages = [];
      state.statusText = null; // 切换会话时清空状态提示
    },
    appendMessage(state, action: PayloadAction<ChatMessage>) {
      state.messages.push(action.payload);
    },
    removeMessage(state, action: PayloadAction<string>) {
      state.messages = state.messages.filter((m) => m.id !== action.payload);
    },
    setIsLoading(state, action: PayloadAction<boolean>) {
      state.isLoading = action.payload;
    },
    /** 更新流式状态提示（skills 执行步骤 / 行程清单生成中） */
    setStreamStatus(state, action: PayloadAction<string | null>) {
      if (state.currentConversationId === state.streamingConversationId) state.statusText = action.payload;
    },
    /** 开始流式：追加空助手消息并记录 id */
    startStreaming(state, action: PayloadAction<string>) {
      state.isStreaming = true;
      state.streamingMessageId = action.payload;
      state.streamingConversationId = state.currentConversationId;
      state.statusText = null; // 新一轮开始，清空上一轮状态提示
      state.messages.push({
        id: action.payload,
        role: 'assistant',
        content: '',
        kind: 'normal',
        waiting: true, // 等待波浪动画：首个正文分片到达前保持等待态
        createdAt: Date.now(),
      });
    },
    /** 模型等待态切换：气泡内波浪动画的显隐（模型思考 / 分片间歇静默时展示） */
    setMsgWaiting(state, action: PayloadAction<{ id: string; waiting: boolean }>) {
      const messages = state.currentConversationId === state.streamingConversationId
        ? state.messages : state.backgroundStreamMessages;
      const msg = messages.find((m) => m.id === action.payload.id);
      if (msg) msg.waiting = action.payload.waiting;
    },
    updateStreamContent(state, action: PayloadAction<{ id: string; content: string }>) {
      const messages = state.currentConversationId === state.streamingConversationId
        ? state.messages : state.backgroundStreamMessages;
      const msg = messages.find((m) => m.id === action.payload.id);
      if (msg) msg.content = action.payload.content;
    },
    /** 流式结束：提交最终文本（含 planId），清理流式状态 */
    commitStreamMessage(
      state,
      action: PayloadAction<{
        id: string;
        content: string;
        planId?: string;
        skillUi?: SkillUiFrame;
      }>,
    ) {
      const messages = state.currentConversationId === state.streamingConversationId
        ? state.messages : state.backgroundStreamMessages;
      const msg = messages.find((m) => m.id === action.payload.id);
      if (msg) {
        msg.content = action.payload.content;
        msg.waiting = false;
        msg.planPending = false;
        if (action.payload.planId) {
          msg.planId = action.payload.planId;
          msg.planPending = false; // success 帧已到，"生成中"态结束
        }
        // 内嵌 UI Skill 载荷：流结束后随消息一起落到 redux，供 MessageItem 渲染卡片。
        // 后端每轮都会重推同技能卡片（用户刷新页面后卡片消失，靠重推自愈），
        // 故此处只保留最新一张——清掉历史消息上的同技能载荷，避免同一会话叠出多张相同卡片
        if (action.payload.skillUi) {
          msg.skillUi = action.payload.skillUi;
          const skillName = action.payload.skillUi.skill_name;
          for (const m of messages) {
            if (m.id !== action.payload.id && m.skillUi?.skill_name === skillName) {
              delete m.skillUi;
            }
          }
        }
      }
      state.isStreaming = false;
      state.streamingMessageId = null;
      state.streamingConversationId = null;
      state.backgroundStreamMessages = [];
      state.statusText = null; // 流结束，清空状态提示
    },
    /** 兜底结束流式态：异常路径未走 commitStreamMessage 时复位，避免输入框被锁死 */
    endStreaming(state) {
      state.isStreaming = false;
      state.streamingMessageId = null;
      state.streamingConversationId = null;
      state.backgroundStreamMessages = [];
      state.statusText = null;
    },
    /** 清单生成中标记：pending 帧到达即打标，流式期间即可显示"生成中"按钮 */
    setMsgPlanPending(state, action: PayloadAction<string>) {
      const messages = state.currentConversationId === state.streamingConversationId
        ? state.messages : state.backgroundStreamMessages;
      const msg = messages.find((m) => m.id === action.payload);
      if (msg) msg.planPending = true;
    },
    /** 从 localStorage 恢复已持久化的对话列表 */
    rehydrateChat(state, action: PayloadAction<PersistedChat>) {
      if (state.conversations.length === 0 && action.payload.conversations.length > 0) {
        state.conversations = action.payload.conversations;
        state.currentConversationId = action.payload.currentConversationId;
      }
    },
  },
});

export const {
  markCardSubmitted,
  setRecoveredRun,
  resetChat,
  setConversations,
  updateConversation,
  setCurrentConversationId,
  markRequirementSubmitted,
  setMessages,
  clearMessages,
  appendMessage,
  removeMessage,
  setIsLoading,
  setStreamStatus,
  startStreaming,
  setMsgWaiting,
  updateStreamContent,
  commitStreamMessage,
  endStreaming,
  setMsgPlanPending,
  rehydrateChat,
} = chatSlice.actions;
export default chatSlice.reducer;
