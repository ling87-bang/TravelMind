/** 统一处理鉴权、业务错误和流式请求。 */
import type { AuthResponse, ChatRunStatus, Conversation, HistoryMessage, TravelItinerary } from './types';

/** API 基础地址存储键（对应原 #apiBase 输入框） */
const API_BASE_KEY = 'zhixing_api_base';
const DEFAULT_API_BASE = process.env.NEXT_PUBLIC_API_BASE || '';

/** 获取 API 基础地址：优先读取用户配置，回退默认值 */
export function getApiBase(): string {
  try {
    return (localStorage.getItem(API_BASE_KEY) || DEFAULT_API_BASE).trim().replace(/\/+$/, '');
  } catch {
    return DEFAULT_API_BASE;
  }
}

/** 设置 API 基础地址（AuthOverlay 折叠面板写入） */
export function setApiBase(base: string): void {
  try {
    localStorage.setItem(API_BASE_KEY, base);
  } catch {
    /* localStorage 不可用时静默失败 */
  }
}

// —— 鉴权 token：由 store 层通过 setToken 同步，避免 api 层直接依赖 redux ——
let accessToken = '';
export function setToken(token: string): void {
  accessToken = token;
}
export function getToken(): string {
  return accessToken;
}

// —— 401 统一处理：store 层注册登出回调 ——
let unauthorizedHandler: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}
/** SSE end 帧 code=401 时复用同一登出回调 */
export function notifyUnauthorized(): void {
  unauthorizedHandler?.();
}

/** 统一的业务错误类型：携带 HTTP 状态码与后端 detail */
export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/**
 * 核心请求函数
 * @param path 接口路径（如 /api/v1/users/login）
 * @param options fetch 选项，会自动补充 Content-Type 与 Authorization 头
 */
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  if (accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`);
  }

  let response: Response;
  try {
    response = await fetch(`${getApiBase()}${path}`, { ...options, headers });
  } catch {
    // 网络层异常（断网 / 服务未启动）
    throw new ApiError(0, '网络连接异常');
  }

  if (response.status === 401) {
    // 鉴权失效：触发全局登出
    unauthorizedHandler?.();
    throw new ApiError(401, '登录已过期，请重新登录');
  }

  if (!response.ok) {
    let detail = '请求失败';
    try {
      // 兼容两种错误体：v1 路由 {detail}（FastAPI）与契约路由 {code,msg,data}
      const body = (await response.json()) as { detail?: string; message?: string; msg?: string };
      detail = body.detail || body.message || body.msg || detail;
    } catch {
      /* 响应体非 JSON，使用默认错误文案 */
    }
    throw new ApiError(response.status, detail);
  }

  const body = (await response.json()) as unknown;
  // 后端统一 {code,msg,data} 包裹 ：此处只解一层，调用方拿到的仍是原 T 结构
  if (body && typeof body === 'object' && 'code' in body && 'data' in body) {
    return (body as { data: T }).data;
  }
  return body as T;
}


/** 用户注册：POST /api/v1/users/register */
export function registerApi(username: string, email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>('/api/v1/users/register', {
    method: 'POST',
    body: JSON.stringify({ username, email, password }),
  });
}

/** 用户登录：POST /api/v1/users/login */
export function loginApi(username: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>('/api/v1/users/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

/** 获取全部对话列表：GET /api/v1/conversations */
export async function fetchConversationsApi(): Promise<Conversation[]> {
  const data = await request<Conversation[] | { conversations?: Conversation[] }>(
    '/api/v1/conversations',
  );
  // 兼容后端返回数组或 { conversations: [] } 两种结构
  return Array.isArray(data) ? data : data.conversations || [];
}

/** 新建对话：POST /api/v1/conversations（title 固定「新对话」，后端按首条消息自动命名 ） */
export function createConversationApi(): Promise<Conversation> {
  return request<Conversation>('/api/v1/conversations', {
    method: 'POST',
    body: JSON.stringify({ title: '新对话' }),
  });
}

/** 获取单个对话（标题）：GET /api/v1/conversations/{id} */
export function fetchConversationApi(id: string): Promise<Conversation> {
  return request<Conversation>(`/api/v1/conversations/${id}`);
}

export function updateConversationStatusApi(id: string, status: 'active' | 'archived'): Promise<Conversation> {
  return request<Conversation>(`/api/v1/conversations/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  });
}

/** 获取对话历史消息：GET /api/v1/messages?conversation_id=xxx → {conversation, messages} */
export async function fetchHistoryApi(id: string): Promise<HistoryMessage[]> {
  const data = await request<HistoryMessage[] | { messages?: HistoryMessage[] }>(
    `/api/v1/messages?conversation_id=${encodeURIComponent(id)}`,
  );
  return Array.isArray(data) ? data : data.messages || [];
}

/**
 * 流式接口统一请求：POST + JSON body，返回原始 Response 供调用方读取 body 流。
 * 401 复用全局登出回调（与 request 一致）。
 */
async function postStream(path: string, body: unknown): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${getApiBase()}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, '网络连接异常');
  }
  if (res.status === 401) {
    unauthorizedHandler?.();
    throw new ApiError(401, '登录已过期，请重新登录');
  }
  if (!res.ok) {
    let detail = '请求失败，请稍后重试';
    try {
      const error = await res.json() as { detail?: unknown; msg?: unknown };
      const message = error.detail || error.msg;
      if (typeof message === 'string') detail = message;
    } catch { /* 保留默认错误提示 */ }
    throw new ApiError(res.status, detail);
  }
  return res;
}

/**
 * SSE 流式聊天：POST /api/v1/messages （资源式扁平契约，体 {conversation_id, content}）
 * 返回原始 Response，由调用方读取 body 流并增量解析统一事件帧。
 */
export function streamChatApi(
  conversationId: string,
  content: string,
): Promise<Response> {
  return postStream('/api/v1/messages', { conversation_id: conversationId, content,
    client_request_id: pendingRequestId(conversationId, content) });
}

/** 行程清单：GET /api/v1/itinerary-plans/{planId}（data 直接为 TravelItinerary） */
export async function fetchItineraryPlanApi(
  planId: string,
): Promise<TravelItinerary> {
  return request<TravelItinerary>(`/api/v1/itinerary-plans/${planId}`);
}

/** 全国城市列表：GET /api/v1/cities?q=xxx（出发地单选 / 目的地多选数据源，空 q 返回全量） */
export interface CityOption {
  name: string;
  province: string;
}
export function fetchCitiesApi(q = ''): Promise<CityOption[]> {
  return request<CityOption[]>(
    `/api/v1/cities${q ? `?q=${encodeURIComponent(q)}` : ''}`,
  );
}

/**
 * 聊天内嵌 UI Skill 的提交路径表：新增一个 Skill 只需在此登记一条，
 * 提交逻辑（chatSlice 的 submitSkillThunk / MessageItem）完全复用，无需再写分支。
 */
const SKILL_SUBMIT_PATHS: Record<string, string> = {
  train_multi_segment: '/api/v1/skills/train_multi_segment/submit',
  hotel_multi_segment: '/api/v1/skills/hotel_multi_segment/submit',
  // 多段航班提交端点（thunk 按 skill_name 取路径，无需额外分支）
  flight_multi_segment: '/api/v1/skills/flight_multi_segment/submit',
  // 多段自驾确认端点（只读确认，同样按 skill_name 取路径）
  driving_multi_segment: '/api/v1/skills/driving_multi_segment/submit',
};

/**
 * 提交聊天内嵌 UI Skill 的选择结果（多段高铁 / 多段住宿通用）：
 * POST /api/v1/skills/{skill_name}/submit
 * 
 * 后端据此写入段账本（高铁 leg_selections / 住宿 selected_hotels）并把状态机推进到下一步，
 * **随后直接续跑后续编排**，故与 /api/v1/messages 一样返回 SSE 流（原始 Response，
 * 由 chatSlice 的事件管道消费）；只做基础合法性校验（段/候选是否存在），
 * **不做任何日期时间校验**。
 */
export function submitSkillApi(
  skillName: string,
  conversationId: string,
  payload: unknown,
  cardId?: string,
): Promise<Response> {
  const path = SKILL_SUBMIT_PATHS[skillName];
  if (!path) throw new Error(`未登记的 UI Skill：${skillName}`);
  return postStream(path, { conversation_id: conversationId, payload, card_id: cardId,
    client_request_id: pendingRequestId(conversationId, JSON.stringify({ skillName, cardId, payload })) });
}

/** Preserve request identity across a dropped response and a browser refresh. */
function pendingRequestId(conversationId: string, signature: string): string {
  const key = `knowgo:pending:${conversationId}`;
  const requestId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  try {
    const previous = JSON.parse(localStorage.getItem(key) || 'null');
    if (previous?.signature === signature && previous?.requestId) return previous.requestId;
    localStorage.setItem(key, JSON.stringify({ signature, requestId }));
  } catch { /* Storage is optional for a live request. */ }
  return requestId;
}

export function clearPendingRequest(conversationId: string): void {
  try { localStorage.removeItem(`knowgo:pending:${conversationId}`); } catch { /* Storage is optional. */ }
}

export async function fetchRunApi(conversationId: string): Promise<ChatRunStatus | null> {
  const run = await request<ChatRunStatus | null>(`/api/v1/conversations/${conversationId}/run`);
  return run?.run_id ? run : null;
}

export function retryRunApi(conversationId: string): Promise<Response> {
  return postStream(`/api/v1/conversations/${conversationId}/run/retry`, {});
}
