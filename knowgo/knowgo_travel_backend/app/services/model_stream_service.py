"""Model event streaming and output cleanup shared by chat execution."""
import re
from app.core.event_stream import EventStream
from app.core.events import make_event, ROLE_ASSISTANT, ROLE_ASSISTANT_RESET
from app.utils.logger import app_logger

# 协议泄漏过滤：拦截模型输出内部工具协议标记，防止污染对话历史，避免输出中断
_LEAK_HINTS = ("✿", "你可以在回复中插入以下命令", "✿FUNCTION✿", "✿ARGS✿")

def _is_protocol_leak(text: str) -> bool:
    """判断流式文本片段是否包含模型内部工具协议泄漏标记"""
    return any(h in text for h in _LEAK_HINTS)

# 拦截模型将系统提示、工具使用规范原样输出到用户正文的情况，支持单句过滤
_LEAK_SENTENCE_HITS = (
    "将json格式结果转换为用户可读的信息",
    "请记住，只有在收集到足够信息后才能调用工具",
    "当需要调用工具时，你必须以如下格式输出",
    "以如下格式输出",
    "请勿修改或删除上方已定义好的函数",
    "请确保调用的工具名称和参数正确",
    "使用这些工具可以帮助你更好地完成任务",
    "请根据你的判断决定是否调用工具",
)

# 正则匹配工具调用痕迹，拦截工具名称、参数JSON片段泄漏
_LEAK_TOOL_TRACE_RE = re.compile(r"_tool|我将使用以下参数调用|我将调用工具|\[\{")

# 块级泄漏起始标记，用于识别整块工具说明、参数列表区域
_LEAK_BLOCK_START = ("## 注意：", "## 注意:", "## 注意事项：", "## 注意事项:")

def _is_trace_block_start(line: str) -> bool:
    """判断该行是否为工具泄漏块的起始行"""
    return any(line.startswith(m) for m in _LEAK_BLOCK_START) or bool(
        _LEAK_TOOL_TRACE_RE.search(line)
    )

def _strip_leak_blocks(text: str) -> str:
    """整段移除系统指令块与工具参数块，正常正文不受影响；无命中直接返回原文"""
    if not text:
        return text
    lines = text.splitlines(keepends=True)
    if not any(_is_trace_block_start(line.strip()) for line in lines):
        return text
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if not skipping:
            if _is_trace_block_start(stripped):
                skipping = True
                continue
            out.append(line)
            continue
        # 处于丢弃状态：空行、列表项、编号行全部丢弃；遇到普通正文行则退出丢弃模式
        if not stripped or stripped[0] in "-*·" or stripped[:1].isdigit():
            continue
        skipping = False
        out.append(line)
    return "".join(out)

def _is_leak_sentence(sentence: str) -> bool:
    """判断单句是否属于泄漏内容：匹配预设泄漏句式，或包含工具调用痕迹"""
    return (
        any(h in sentence for h in _LEAK_SENTENCE_HITS)
        or bool(_LEAK_TOOL_TRACE_RE.search(sentence))
    )


# 移除模型擅自输出的清单生成类承诺文案，保证前端清单UI状态和文本描述保持一致
_CLAIM_SENTENCE_RE = re.compile(
    r"行程清单(已生成|生成完毕|已为您生成|已上传)"
    r"|(已|已经)(按此方案|为您)?生成.{0,8}行程清单"
    r"|(您)?(可以|可在|请)在下方(查看|查看详情|点击查看)"
)

def _strip_claim_sentences(text: str) -> str:
    """剔除模型自行宣称「清单已生成、可在下方查看」类话术，清单状态以清单帧为准"""
    if not text:
        return text
    sentences = re.split(r"(?<=[。！？!?\n])", text)
    kept = [s for s in sentences if not _CLAIM_SENTENCE_RE.search(s)]
    return "".join(kept) if len(kept) != len(sentences) else text

# 将确认类收尾问句替换为引导调整的友好文案，避免重复确认造成用户困惑
_INVITE_BLOCK_RE = re.compile(
    r"请确认(以上|上述|此)[^。！？!?\n]{0,14}(是否符合您的需求|是否满足(您的)?(需求|要求)|是否合适|是否满意|是否需要调整)[。！!？]?"
    r"|(以上|上述)(的)?(行程|方案|安排)[^。！？!?\n]{0,10}是否符合您的需求[。！!？]?"
    r"|是否(需要|要)(进一步)?调整(一下)?[吗呢]?[？?](?=$|\n)"
)
_INVITE_TEXT = "如需调整（天数/预算/目的地等），直接告诉我即可。"

def _rewrite_closing_invite(text: str) -> str:
    """将阻塞式确认问句改写为无阻塞邀请语句"""
    return _INVITE_BLOCK_RE.sub(_INVITE_TEXT, text) if text else text

def _scrub_leak_text(text: str) -> str:
    """清洗回复文本：移除系统指令、工具参数泄漏、虚假清单承诺，改写确认类收尾句。
    执行顺序：块级清洗 → 清单承诺句子剔除 → 收尾句改写 → 单句泄漏过滤。
    无泄漏内容时原样返回；调用方可通过返回值对比判断是否需要重置推送。
    """
    if not text:
        return text
    stripped = _strip_leak_blocks(text)
    # 剔除清单相关虚假承诺话术
    stripped = _strip_claim_sentences(stripped)
    # 改写确认类结尾句子
    stripped = _rewrite_closing_invite(stripped)
    # 分句过滤单句泄漏
    sentences = re.split(r"(?<=[。！？!?\n])", stripped)
    kept = [s for s in sentences if not _is_leak_sentence(s)]
    return "".join(kept) if len(kept) != len(sentences) else stripped


# 去除模型重复粘贴的完整行程正文与预算表；短文本不参与去重，无重复直接返回原文
_DEDUPE_MIN_LEN = 200

def _dedupe_texts(rounds: list[str]) -> list[str]:
    """轮次文本去重，支持两种重复场景：
    1. 两段文本完全相同
    2. 后一段为完整版，前一段是截断版本（后段包含前段）
    长文本（≥200字）判定子串重复，保留时间靠后的完整版；短文本不参与去重。
    """
    kept: list[str] = []
    kept_long: list[str] = []
    for t in reversed(rounds):
        drop = False
        if len(t) >= _DEDUPE_MIN_LEN:
            for p in kept_long:
                if p in t or t in p:
                    drop = True
                    break
        if not drop:
            kept.append(t)
            if len(t) >= _DEDUPE_MIN_LEN:
                kept_long.append(t)
    kept.reverse()
    return kept

# 对普通散文段落做相似度去重；表格、列表、编号行不参与去重
_DEDUPE_PARA_MIN = 25
_DEDUPE_COMMON = 14

def _norm_para(text: str) -> str:
    return re.sub(r"\s+", "", text or "")

def _is_prose_para(para: str) -> bool:
    """判断是否为普通散文段落；列表、表格、标题行不参与折叠"""
    p = para.strip()
    return bool(p) and p[0] not in "-|*#·•0123456789"

def _common_len(a: str, b: str) -> int:
    from difflib import SequenceMatcher
    m = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return m.size

# 预告等待话术正则，同一回合只保留一次预告
_NOTICE_RE = re.compile(r"(请稍等|稍等片刻|请稍候|正在为您|马上为您|我将计算|我将为您)")

def _fold_restatements(text: str, state: dict) -> str:
    """移除重复重述散文段落与重复等待预告语。
    state：跨段落共享状态，保存已见过的散文段落、预告标记。
    表格/列表/编号行全部保留，不参与折叠。
    """
    seen: list[str] = state.setdefault("paras", [])
    kept: list[str] = []
    for para in re.split(r"\n\s*\n", text or ""):
        key = _norm_para(para)
        prose = bool(key) and _is_prose_para(para)
        is_notice = prose and bool(_NOTICE_RE.search(para))

        if len(key) >= _DEDUPE_PARA_MIN and prose:
            if any(_common_len(key, s) >= _DEDUPE_COMMON for s in seen):
                app_logger.info(f"去重：删除换措辞重述段（{len(key)} 字）")
                continue
            seen.append(key)
        elif is_notice and state.get("notice"):
            app_logger.info(f"去重：删除重复预告语（{len(key)} 字）")
            continue
        if is_notice:
            state["notice"] = True
        kept.append(para)
    return "\n\n".join(kept)

def _chunk_has_tool_call(chunk) -> bool:
    """判断流式块是否携带工具调用增量，用于识别工具执行轮"""
    if getattr(chunk, "tool_call_chunks", None):
        return True
    kwargs = getattr(chunk, "additional_kwargs", None) or {}
    return bool(kwargs.get("tool_calls"))

async def _stream_agent(
        agent, input_data: dict, config: dict, stream: EventStream,
        buffer_all: bool = False,
        defer_push: bool = False,
) -> str:
    """框架流式：astream_events 捕获 LLM token，工具调用由 Agent 自主决策
    buffer_all=True：卡片或结构化行程使用校验后的完整结果。
    普通文本允许首轮实时推送；检索工具内部的模型事件不作为正文展示。
    """
    rounds: list[str] = []        # 每一轮的完整文本
    round_buf: list[str] = []     # 当前轮的 token 缓冲
    pushed: list[str] = []        # 已实时推送给前端的 token
    round_meta: list[tuple[int, bool]] = []   # 诊断：每轮 (字数, 是否工具轮)
    round_had_tool = False        # 当前轮是否出现工具调用
    leaky = False                 # 是否检测到协议泄漏句（需回合末复位重推）
    seen_state: dict = {"paras": [], "notice": False}   # 跨轮折叠状态（重述段 / 预告语）

    async for event in agent.astream_events(input_data, config=config, version="v2"):
        etype = event.get("event")
        if etype == "on_tool_start":
            stream.push(make_event("skills", {
                "skill_name": event.get("name", "tool"),
                "title": "处理中", "desc": "正在执行查询或更新规划步骤…",
            }))
        # 工具内部模型产出的查询变体/评分不是助手正文。
        node = (event.get("metadata") or {}).get("langgraph_node")
        if node is not None and node != "model":
            continue
        if etype == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk is None:
                continue
            if _chunk_has_tool_call(chunk):
                round_had_tool = True
            content = getattr(chunk, "content", None)
            if content:
                if _is_protocol_leak(content):
                    continue
                round_buf.append(content)
                if not buffer_all:
                    pushed.append(content)
                    stream.push(make_event(ROLE_ASSISTANT, content))
        elif etype == "on_chat_model_end":
            text = "".join(round_buf)
            round_buf.clear()
            cleaned = _scrub_leak_text(text)
            cleaned = _fold_restatements(cleaned, seen_state)
            if cleaned != text:
                leaky = True
            if cleaned.strip():
                rounds.append(cleaned)
            round_meta.append((len(text), round_had_tool))
            round_had_tool = False

    # 兜底处理未收到 end 事件的残留片段
    if round_buf:
        text = "".join(round_buf)
        cleaned = _scrub_leak_text(text)
        cleaned = _fold_restatements(cleaned, seen_state)
        if cleaned != text:
            leaky = True
        if cleaned.strip():
            rounds.append(cleaned)
            round_meta.append((len(cleaned), round_had_tool))

    rounds = _dedupe_texts(rounds)
    if not rounds:
        return ""

    if defer_push:
        return "".join(rounds)

    shown = "".join(pushed)
    app_logger.info(f"🧩 轮次明细（字数, 是否工具轮）：{round_meta}，已推送 {len(shown)} 字")

    # 出口1：全程没有实时推送，回合末整块推送
    if not shown:
        text = "".join(rounds)
        stream.push(make_event(ROLE_ASSISTANT, text))
        app_logger.info(f"🔇 首轮未实时推送，回合末补推 {len(text)} 字")
        return text

    # 出口2：检测泄漏/重述，复位并重推清洗后完整文本
    if leaky and shown:
        text = "".join(rounds)
        stream.push(make_event(ROLE_ASSISTANT_RESET))
        stream.push(make_event(ROLE_ASSISTANT, text))
        app_logger.info(f"↩️ 检测到泄漏/重述内容，撤销已推送 {len(shown)} 字，改推清洗后 {len(text)} 字")
        return text

    # 出口3：首轮为空或工具旁白，保留已推送内容，不回退
    if round_meta[0][0] == 0 or round_meta[0][1]:
        app_logger.info(f"✅ 首轮无可保留正文（0 字或工具轮旁白），保留已推送 {len(shown)} 字（不回退，防内容丢失）")
        return shown

    # 首轮已实时推送；仅在清洗结果确有变化时复位，避免重复播放整篇。
    text = "".join(rounds)
    if text != shown:
        stream.push(make_event(ROLE_ASSISTANT_RESET))
        stream.push(make_event(ROLE_ASSISTANT, text))
    return text


