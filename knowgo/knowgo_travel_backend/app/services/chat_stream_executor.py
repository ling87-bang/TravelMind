"""对话流式执行器：LangGraph 框架流式"""
import asyncio
import json
import re
from datetime import date, timedelta

from langchain_core.messages import HumanMessage

from app.core.events import make_event, ROLE_ASSISTANT, ROLE_ASSISTANT_RESET
from app.core.intent import detect_intent
from app.core.event_stream import EventStream
from app.agents.handoffs.travel_agent import create_travel_agent, get_llm
from app.utils.logger import app_logger

async def _stream_plain(content: str, stream: EventStream) -> str:
    """闲聊路径：直连 LLM 流式，不走规划 Agent"""
    chunks: list[str] = []
    async for chunk in get_llm().astream([HumanMessage(content=content)]):
        if chunk.content:
            chunks.append(chunk.content)
            stream.push(make_event(ROLE_ASSISTANT, chunk.content))
    return "".join(chunks)


from app.services.travel_query_service import (
    try_direct_ticket_query, try_weather_query, _strip_model_weather,
    _ticket_table_from_tail, _query_fallback_text, _WEATHER_CITY_MAX,
)

from app.services.model_stream_service import _stream_agent


_ACCOMMODATION_LABELS: dict[str, str] = {
    "star_hotel": "星级酒店",
    "economy_hotel": "经济酒店",
    "hostel": "特色民宿",
    "youth_hostel": "青年旅社",
}

# 各档位常见连锁品牌，仅作参考，不含实时门店与价格
_COMMON_HOTELS: dict[str, list[str]] = {
    "economy_hotel": ["如家", "汉庭", "7 天", "锦江之星", "格林豪泰", "莫泰"],
    "star_hotel": ["希尔顿", "万豪", "洲际", "香格里拉", "凯悦", "雅高"],
    "hostel": ["花间堂", "途家民宿", "小猪民宿", "美团民宿"],
    "youth_hostel": ["国际青年旅舍（YHA）", "万里路青年旅舍", "如家青旅"],
}


def _match_hotel_choice(text: str, requirement: dict | None) -> str:
    """识别用户住宿选择，命中品牌名/委托语返回选中品牌，未命中返回空串
    两类命中：①品牌清单内品牌；②委托语。委托语自动选用户已勾选档位的首个品牌
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    for brand in [b for v in _COMMON_HOTELS.values() for b in v]:
        if brand.replace(" ", "") in raw.replace(" ", ""):
            return brand
    if detect_intent(raw).hotel_accept:
        selected = list((requirement or {}).get("accommodation_types") or [])
        types = [t for t in selected if t in _COMMON_HOTELS] or ["economy_hotel"]
        return _COMMON_HOTELS[types[0]][0]
    return ""


def _build_hotel_fallback(requirement: dict | None, auto_confirm: bool = False,
                          chosen: str = "") -> str:
    """酒店服务不可用时生成降级推荐文案，同时用于流式推送与落库
    自动确认模式不再向用户索要品牌，直接按档位安排；普通模式等待用户选择
    """
    selected = list((requirement or {}).get("accommodation_types") or [])
    types = [t for t in selected if t in _COMMON_HOTELS] or list(_COMMON_HOTELS)
    lines = [
        "\n\n---\n",
        "⚠️ 实时酒店报价服务当前不可用，无法展示具体门店与实时价格。\n",
        "以下按您的住宿类型，列出该档位市面上常见的连锁品牌，供您参考（具体门店与价格到店/官网再定）：\n",
    ]
    for t in types:
        lines.append(f"- **{_ACCOMMODATION_LABELS[t]}**：{'、'.join(_COMMON_HOTELS[t])}\n")
    if chosen:
        lines.append(
            f"✅ 已记录您的住宿选择：**{chosen}**（具体门店与实时价格到店择优选同档位），"
            f"并继续规划后续行程。"
        )
    elif auto_confirm:
        lines.append("已按以上该档位的常见品牌为您直接安排住宿（不指定具体门店与实时价格，到店择优选同档位），并继续规划后续行程。")
    else:
        lines.append("请回复您想选的品牌（或「按推荐来」），我将按该档位继续安排行程。")
    return "".join(lines)


async def _has_hotel_search_tool() -> bool:
    """校验是否存在酒店查询工具；异常时返回False，进入降级兜底"""
    try:
        from app.tools.mcp_tools import get_hotel_tools
        tools = await get_hotel_tools()
    except Exception:
        return False
    return any(
        any(hint in getattr(t, "name", "").lower() for hint in ("searchhotels", "find-hotels"))
        for t in tools
    )

_TRANSPORT_LIST_RE = re.compile(
    r"[GDCKTZ]\d{1,4}\b"                       # 车次号（G330 / D50 / K507 / T10 / Z162）
    r"|\b[A-Z0-9]{2}\d{3,4}\b"                 # 航班号（CA4105 / 3U8885）
    r"|经济舱|公务舱|头等舱|商务座|一等座|二等座|硬座|硬卧|软卧|机票|航班"
    r"|历时|耗时|过路费|自驾|路程|里程"
    r"|\d+(?:\.\d+)?\s*元"
    # 模型自写的"残行"——只有时刻没有车次号，旧正则认不出来（实测
    # "- 出发-到达时刻：07:21 - 14:31" 三条与代码直出表并存，用户看到两套互相矛盾的班次）
    r"|出发\s*[-—~～至]?\s*到达|到达时刻|发车时刻"
    r"|\d{1,2}:\d{2}\s*[-—~～至]\s*\d{1,2}:\d{2}"
)
_TRANSPORT_HEAD_RE = re.compile(
    r"^#{2,4}\s*[①②③④⑤⑥⑦⑧⑩]"                # #### ① 重庆 → 石家庄
    r"|^#{2,4}\s*交通(方式)?查询结果"
    r"|^\s*[-*•]\s*\*\*(高铁|航班|自驾|火车|飞机)\*\*"
)
_TRANSPORT_LEAD = "已为您查询到以下真实班次，请选择各段的交通方式：\n"
_TRANSPORT_LEAD_MIN = 8


def _zero_leg_guard(vals: dict) -> tuple[bool, str]:
    """ 零段直通判定——出发地与目的地同城（本地游玩）时没有任何城际段

    返回 (是否零段, 出发地城市)。出发地/目的地缺失、或存在城际段时返回 (False, 出发地)，
    单目的地与多目的地均按同一口径（_expected_legs 已按"相邻节点城市不同"计算）
    """
    from app.services.express_planning_service import _split_cities
    from app.tools.state_transition import _expected_legs
    dep = str(((vals.get("user_requirement") or {}).get("departure_city")) or "").strip()
    if not dep or not _split_cities(vals.get("selected_destination") or ""):
        return False, dep
    return (not _expected_legs(vals)), dep  # 段数口径只认第 11 章 _expected_legs


def _leg_context(state: dict) -> tuple[str, int, int]:
    """ 取"当前待确认段" → (段文本, 序号从1起, 总段数)。

    多目的地段级循环专用；单目的地/未进入段级循环时返回 ("", 0, 0)，
    调用方据此走老逻辑，单城行为完全不变
    """
    from app.tools.state_transition import _expected_legs
    try:
        legs = _expected_legs(state)
    except Exception:
        return "", 0, 0
    if not legs:
        return "", 0, 0
    cur = max(0, min(int(state.get("leg_cursor") or 0), len(legs) - 1))
    return legs[cur], cur + 1, len(legs)


def _strip_transport_lists(text: str) -> str:
    """剔除交通步骤正文里模型自编的班次/航班/自驾清单行及其小标题，只留引导语"""
    kept: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped and (
            _TRANSPORT_LIST_RE.search(stripped) or _TRANSPORT_HEAD_RE.match(stripped)
        ):
            continue
        kept.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return cleaned.replace("以上选项", "以下班次")

# 结构化数据经过Pydantic校验，正文与预算表由本函数确定性拼接。
_BODY_READY_MARK = "天行程结构"
from app.services.itinerary_render_service import _render_itinerary_body, _push_rendered, _INVITE_TAIL


def _queried_cities(tail: list) -> list[str]:
    """从本轮 query_destination_info 的调用参数里取城市
    多城"排序确认轮"selected_destination为空时，作为城市来源
    """
    out: list[str] = []
    for m in tail:
        for tc in (getattr(m, "tool_calls", None) or []):
            if (tc.get("name") if isinstance(tc, dict) else None) != "query_destination_info":
                continue
            city = str(((tc.get("args") or {}) if isinstance(tc, dict) else {}).get("destination") or "").strip()
            if city and city not in out:
                out.append(city)
    return out

async def _weather_paragraph(cities: list[str], start: str, days: int) -> str:
    """代码直查并段落式渲染行程窗口内天气。
    返回空字符串表示查询失败/无可用预报，绝不编造天气。
    """
    cities = [c.strip() for c in cities if c.strip()][:_WEATHER_CITY_MAX]
    if not cities:
        return ""
    from app.tools.mcp_tools import get_weather_tools
    from app.agents.routers.destination_router import WeatherForecast, _normalize_weather_payload

    tools = await get_weather_tools()
    tool = next((t for t in tools if t.name == "get_weather_forecast"), None)
    if tool is None:
        app_logger.warning("天气工具未加载，本轮不追加天气（不编造）")
        return ""
    try:
        begin = date.fromisoformat(start) if start else None
    except ValueError:
        begin = None
    end = begin + timedelta(days=days - 1) if (begin and days > 0) else None
    parts: list[str] = []
    for city in cities:
        try:
            payload = _normalize_weather_payload(await tool.ainvoke({"city": city}))
            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            casts = WeatherForecast.model_validate(payload).casts
        except Exception as e:
            app_logger.warning(f"天气渲染跳过 {city}：{type(e).__name__}: {e}")
            continue
        if begin and end:
            in_window = [c for c in casts if begin <= date.fromisoformat(c.date) <= end]
            casts = in_window or casts[:max(days, 1)]
        else:
            casts = casts[:days] if days > 0 else casts
        if not casts:
            continue
        # 渲染天气文本块
        header = (
            f"出发日期前后（{begin.isoformat()}至{end.isoformat()}）的天气概况如下："
            if begin and end else f"未来 {len(casts)} 天天气概况如下："
        )
        rows = [
            f"- {c.date}: 白天{c.dayweather}，气温{c.nighttemp}~{c.daytemp}°C" for c in casts
        ]
        note = ""
        if begin and end:
            missing = days - len(casts)
            if missing == days:
                note = f"（{begin.isoformat()} 起超出可预报范围，暂提供未来 {len(casts)} 天，临近出发再查）"
            elif missing > 0:
                note = f"（后 {missing} 天超出可预报范围，临近出发再查）"
        block = "\n".join([header] + rows + ([note] if note else []))
        parts.append(f"### {city}\n{block}" if len(cities) > 1 else block)
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n\n（数据来源：高德天气）"

# 住宿轮外部酒店服务不可用，代码层确定性兜底逻辑
# 触发条件：①工具返回降级标记；②酒店查询工具加载失败。兜底仅输出品牌档位，不编造门店与价格
_DEGRADE_MARK = "【外部服务暂时不可用】"
_ACCOMMODATION_STEP = "accommodation_planning"

# 交通规划步骤采用整块缓冲，回合结束一次性推送，避免大量班次逐字流式造成卡顿
_TRANSPORT_STEP = "transport_planning"

# 行程生成步骤改为代码确定性推进，减少一轮用户确认交互
_ITINERARY_STEP = "itinerary_generation"

# 需要整块缓冲的步骤：一次性推送结果，防止流式重置造成前端内容闪变
_BUFFER_ALL_STEPS = {
    _TRANSPORT_STEP,
    _ITINERARY_STEP,
}
# 住宿、餐饮确认轮也加入缓冲集合，防止前端内容闪变
_BUFFER_ALL_STEPS |= {_ACCOMMODATION_STEP, "food_planning"}

def _covers_all_days(text: str, days: int) -> bool:
    """校验正文是否完整覆盖第1~days天，复用落库守卫的天数解析器，统一口径"""
    if days <= 0 or not text:
        return False
    from app.tools.state_transition import _day_numbers  # 局部导入，防止循环引用
    have = _day_numbers(text)
    return all(d in have for d in range(1, days + 1))

async def execute_turn(
        user_id: str, conversation_id: str, content: str, planning: bool, stream: EventStream
) -> tuple[str, str | None, bool, list | None]:
    """Continue automatic selections until the next user action or a full itinerary."""
    if not planning:
        return await _execute_turn_once(user_id, conversation_id, content, planning, stream)

    agent = await create_travel_agent()
    config = {"configurable": {"thread_id": conversation_id}, "recursion_limit": 60}
    try:
        state = (await agent.aget_state(config)).values or {}
    except Exception:
        state = {}
    prefixes: list[str] = []
    seen_steps: set[str] = set()
    notices = {
        "transport_planning": "目的地已确认，正在查询并选择交通…\n",
        "accommodation_planning": "交通已自动确认，正在选择各站住宿…\n",
        "food_planning": "住宿已确认，正在安排餐饮并生成行程…\n",
        "itinerary_generation": "交通与住宿已自动确认，正在生成完整行程报告…\n",
    }
    for _ in range(4):
        previous_step = state.get("current_step")
        result = await _execute_turn_once(user_id, conversation_id, content, True, stream)
        text, step, committed, body = result
        try:
            # Post-processing tools update the checkpoint after the model finishes.
            # Their new stage must be read back rather than trusting the old tuple.
            state = (await agent.aget_state(config)).values or {}
        except Exception:
            return "\n\n".join([*prefixes, text]), step, committed, body
        next_step = state.get("current_step")
        zero_stay_progress = ((state.get("user_requirement") or {}).get("travel_days") == 1
                              and next_step == "itinerary_generation" and next_step != previous_step)
        if ((not state.get("auto_confirm") and not zero_stay_progress) or body
                or next_step not in notices or next_step == previous_step):
            return "\n\n".join([*prefixes, text]), step, committed, body
        if next_step in seen_steps:
            raise RuntimeError("自动规划未能继续推进，请调整需求后重试。已确认的选择仍保留。")
        seen_steps.add(next_step)
        if text:
            prefixes.append(text)
        stream.push(make_event(ROLE_ASSISTANT, notices[next_step]))
        content = (
            "请继续已授权的自动规划。保留已确认的需求、交通和住宿，完成剩余步骤并生成完整行程报告。"
            "若当前已到行程生成阶段，请提交完整的结构化逐日行程，不要停在确认或预告文字。"
        )
        app_logger.info(f"自动规划续跑：{previous_step} → {next_step}")
    raise RuntimeError("自动规划达到续跑上限，已确认的选择仍保留，请稍后继续。")


async def _execute_turn_once(
        user_id: str, conversation_id: str, content: str, planning: bool, stream: EventStream
) -> tuple[str, str | None, bool, list | None]:
    """执行阶段：规划请求走 LangGraph Agent，闲聊直连 LLM
    返回 (完整回复文本, 状态机当前步骤, 本轮是否落库, 逐日结构化行程)：
    - current_step 供服务层做行程清单生成的步骤门禁
    - committed 表示本轮是否真正调用了 generate_itinerary_tool 落库
    - itinerary_body 为已过 Pydantic 校验的逐日结构，供清单生成兜底构造
    - 闲聊路径无状态机，后三项返回 None/False/None
    """
    if planning:
        agent = await create_travel_agent()
        input_data = {"messages": [HumanMessage(content=content)], "user_id": user_id}
        # LangGraph recursion_limit 上调，给多轮工具往返留余量
        config = {"configurable": {"thread_id": conversation_id}, "recursion_limit": 60}
        try:
            pre_snap = await agent.aget_state(config)
            turn_step = (pre_snap.values or {}).get("current_step")
            if turn_step == _ACCOMMODATION_STEP:
                from app.capabilities.hotel_multi_segment.hotel_flow import advance_zero_stay
                if await advance_zero_stay(conversation_id):
                    pre_snap = await agent.aget_state(config)
                    turn_step = (pre_snap.values or {}).get("current_step")
            buffer_all = turn_step in _BUFFER_ALL_STEPS
        except Exception:
            buffer_all = False
            turn_step = None

        # 交通方式切换：用户对话内修改交通方式，预先落库更新状态
        _switch_note = ""
        if turn_step == _TRANSPORT_STEP:
            from app.services.transport_switch_service import apply_transport_switch
            _switched = await apply_transport_switch(agent, config, pre_snap.values or {}, content)
            if _switched:
                _switch_note = f"好的，已按您的要求把交通方式改为「{_switched}」。"

        text = await _stream_agent(
            agent, input_data, config, stream,
            buffer_all=buffer_all,
            defer_push=buffer_all,
        )

        # 回合结束读取最终状态快照
        try:
            snap = await agent.aget_state(config)
            state_step = (snap.values or {}).get("current_step")
        except Exception:
            app_logger.warning("读取状态机 current_step 失败，本轮禁止生成行程清单")
            state_step = None

        # 判断本轮是否执行行程落库
        committed = False
        tail: list = []
        try:
            msgs = (snap.values or {}).get("messages") or []
            for m in reversed(msgs):
                if getattr(m, "type", None) == "human":
                    break
                tail.append(m)
            committed = any(
                getattr(m, "type", None) == "tool" and "已生成" in str(getattr(m, "content", ""))
                for m in tail
            )
        except Exception:
            committed = False

        # 获取结构化行程 body
        try:
            body = (snap.values or {}).get("itinerary_body") or None
        except Exception:
            body = None

        # 后处理①：行程步骤自动推进，去除用户确认轮
        if state_step == _ITINERARY_STEP and not committed:
            _days = int(((snap.values or {}).get("user_requirement") or {}).get("travel_days") or 0)
            if _covers_all_days(text, _days):
                try:
                    await agent.aupdate_state(config, {
                        "itinerary": [{"day_number": d} for d in range(1, _days + 1)],
                        "current_step": "budget_summarization",
                    })
                    state_step = "budget_summarization"
                    app_logger.info(
                        f"✅ 行程正文已覆盖第1~{_days}天，代码层推进到预算步（去掉确认轮）"
                    )
                except Exception:
                    app_logger.warning("正文达标但状态推进失败，本轮按原步骤处理", exc_info=True)

        # 后处理②：同城本地游玩，零城际段，直接跳到住宿步骤
        _zero_leg, _dep0 = _zero_leg_guard(snap.values or {})
        _zero_note = ""
        if state_step == _TRANSPORT_STEP and _zero_leg:
            from app.capabilities.train_multi_segment.train_flow import advance_zero_leg
            if await advance_zero_leg(conversation_id):
                _zero_note = (f"出发地与目的地同为{_dep0}，本次为本地游玩，无需城际交通段，"
                              "直接为您安排住宿。")
                text = _zero_note
                state_step = _ACCOMMODATION_STEP

        if state_step == _ACCOMMODATION_STEP:
            from app.capabilities.hotel_multi_segment.hotel_flow import advance_zero_stay
            if await advance_zero_stay(conversation_id):
                snap = await agent.aget_state(config)
                state_step = (snap.values or {}).get("current_step")
                text = "当天往返无需住宿，继续生成行程报告。"

        # 后处理③：住宿服务降级兜底
        if state_step == _ACCOMMODATION_STEP:
            degraded = any(
                getattr(m, "type", None) == "tool" and _DEGRADE_MARK in str(getattr(m, "content", ""))
                for m in tail
            )
            if degraded or not await _has_hotel_search_tool():
                _vals = snap.values or {}
                _last_human = ""
                for _m in reversed(_vals.get("messages") or []):
                    if getattr(_m, "type", None) == "human":
                        _last_human = str(getattr(_m, "content", "") or "")
                        break
                _chosen = _match_hotel_choice(_last_human, _vals.get("user_requirement"))
                if _chosen:
                    from app.tools.state_transition import _multi_city_names
                    _cities = _multi_city_names(_vals)
                    _rec = list(_vals.get("selected_hotels") or [])
                    for _c in (_cities or []):
                        if not any(_c in _r for _r in _rec):
                            _rec.append(f"{_c}：{_chosen}（具体门店到店选定）")
                    _upd: dict = {
                        "selected_hotels": _rec,
                        "current_step": "food_planning",
                    }
                    _types = list((_vals.get("user_requirement") or {}).get("accommodation_types") or [])
                    if _types:
                        _upd["selected_accommodation_types"] = _types
                    try:
                        await agent.aupdate_state(config, _upd)
                        state_step = "food_planning"
                        app_logger.warning(
                            f"✅ 住宿轮降级且用户已选「{_chosen}」：代码层直接推进到餐饮步"
                        )
                    except Exception:
                        app_logger.warning("住宿轮降级推进失败，本轮仍按住宿步处理", exc_info=True)
                recommendation = _build_hotel_fallback(
                    _vals.get("user_requirement"),
                    bool(_vals.get("auto_confirm")),
                    _chosen,
                )
                if not buffer_all:
                    stream.push(make_event(ROLE_ASSISTANT, recommendation))
                text += recommendation
                app_logger.warning(
                    f"⚠️ 住宿轮{'酒店服务降级' if degraded else '无酒店查询工具'}："
                    f"已补推常见连锁酒店品牌推荐（防止会话卡死）"
                )

        # 后处理④：多段住宿卡片逻辑
        if state_step == _ACCOMMODATION_STEP:
            from app.capabilities.hotel_multi_segment.hotel_flow import (
                auto_select_hotels,
                build_lead_text,
                emit_hotel_skill_ui,
                hotel_card_mode,
            )
            _hv = snap.values or {}
            if hotel_card_mode(_hv):
                if _hv.get("auto_confirm"):
                    _hp = await auto_select_hotels(conversation_id, _hv)
                    if _hp:
                        text = ("已按您表单中的住宿档位为您选定各站住宿：\n"
                                + "\n".join(f"- {t}" for t in _hp))
                else:
                    _hold, _hst = await emit_hotel_skill_ui(stream, conversation_id, _hv)
                    if _hst == "pushed":
                        text = build_lead_text(_hold)
                    elif _hst == "empty" and buffer_all:
                        text = ("⚠️ 各站住宿实时候选暂时查询不到（酒店数据源未返回结果），"
                                "请稍后在对话中重新查询。")
            if _zero_note and _zero_note not in text:
                text = f"{_zero_note}\n\n{text}"

        # 后处理⑤：目的地天气交由代码渲染，剔除模型生成天气文本
        if state_step == "destination_recommendation":
            _vals = snap.values or {}
            _req = _vals.get("user_requirement") or {}
            from app.services.express_planning_service import _split_cities
            _cities = _split_cities(
                str(_vals.get("selected_destination") or _req.get("destination") or "")
            )
            if not _cities:
                _cities = _queried_cities(tail)
            stripped = _strip_model_weather(text)
            if stripped != text:
                app_logger.warning("⚠️ 已剔除模型自写天气行/占位语（改由代码渲染）")
            text = stripped
            if turn_step == "destination_recommendation":
                app_logger.info(
                    f"⏭️ 目的地步非进入轮：跳过天气块追加（{_cities} 已在进入轮渲染过）"
                )
            else:
                block = await _weather_paragraph(
                    _cities, str(_req.get("departure_date") or ""), int(_req.get("travel_days") or 0)
                )
                if block:
                    _tail_q = ""
                    _lines = text.rstrip().splitlines()
                    if _lines and ("是否确认选择" in _lines[-1] or _lines[-1].strip().startswith("请问")):
                        _tail_q = _lines.pop()
                        text = "\n".join(_lines).rstrip()
                    text = f"{text}\n\n{block}" if text else block
                    if _tail_q:
                        text = f"{text}\n\n{_tail_q}"
                    app_logger.info(
                        f"✅ 天气已由代码渲染（{_cities} / 行程 {_req.get('travel_days')} 天，版式：引导句+每天一行）"
                    )
                    if not buffer_all:
                        await _push_rendered(stream, f"\n\n{block}")
                else:
                    app_logger.warning(f"⚠️ 代码渲染天气为空（城市={_cities}，不追加、不编造）")

        # 后处理⑥：交通清单代码接管，高铁/航班/自驾卡片
        if state_step == _TRANSPORT_STEP:
            leg = _leg_context(snap.values or {})[0]
            _vals = snap.values or {}
            from app.capabilities.train_multi_segment.train_flow import multi_leg_train
            from app.capabilities.flight_multi_segment.flight_flow import flight_card_mode
            from app.capabilities.driving_multi_segment.driving_flow import driving_card_mode
            _card_mode = bool(
                multi_leg_train(_vals) or flight_card_mode(_vals) or driving_card_mode(_vals)
            )
            pool = list(tail)
            try:
                pool += [
                    m for m in (snap.values or {}).get("messages") or []
                    if getattr(m, "type", None) == "tool"
                ]
            except Exception:
                pass
            table = _ticket_table_from_tail(pool, only_leg=leg)
            from app.tools.mcp_tools import parse_compact_rows, parse_ticket_rows
            fresh = any(
                getattr(m, "type", None) == "tool"
                and bool(parse_compact_rows(getattr(m, "content", "") or "")
                         or parse_ticket_rows(getattr(m, "content", "") or ""))
                for m in tail
            )
            if table and fresh and not _card_mode:
                cleaned = _strip_transport_lists(text)
                if cleaned != text:
                    if len(cleaned) < _TRANSPORT_LEAD_MIN:
                        cleaned = _TRANSPORT_LEAD
                    text = cleaned + table
                    app_logger.warning(
                        f"⚠️ 交通步骤正文含模型自编清单，已剔除并改用代码直出（{len(text)} 字）"
                    )
                else:
                    text += table
                app_logger.info("✅ 交通步骤已追加代码层权威班次清单（清单内为唯一有效车次）")
            elif table and _card_mode:
                app_logger.info(
                    "🚄 卡片模式：不直出 12306 班次表（班次已在选择卡片内，避免两套数据重复）"
                )

            # 航班不可达城市自动降级为高铁
            _fallback_note = ""
            from app.capabilities.flight_multi_segment.flight_flow import (
                flight_card_mode as _flight_card_mode,
                flight_unreachable_cities,
            )
            _no_air = flight_unreachable_cities(_vals) if _flight_card_mode(_vals) else []
            if _no_air:
                from app.services.transport_switch_service import switch_transport_mode
                try:
                    _vals = await switch_transport_mode(agent, config, _vals, "train")
                    _fallback_note = (
                        f"「{'、'.join(_no_air)}」暂无可直达的航班数据（该城市无民用机场或机场码暂未收录），"
                        f"已自动为您改查高铁；如仍希望乘机，可回复「换个目的地」重新挑选。"
                    )
                    app_logger.warning(
                        f"✈️→🚄 航班结构性不可查（无机场码城市：{_no_air}），已降级为高铁并交由高铁分支接管"
                    )
                except Exception as exc:
                    app_logger.warning(f"⚠️ 航班降级高铁落库失败（本轮按航班分支继续）：{exc}")

            # 多段高铁卡片
            if multi_leg_train(_vals):
                from app.capabilities.train_multi_segment.train_flow import (
                    emit_train_skill_ui,
                    auto_select_train_legs,
                    build_lead_text as build_train_lead_text
                )
                if _vals.get("auto_confirm"):
                    _picked = await auto_select_train_legs(conversation_id, _vals)
                    if _picked and buffer_all:
                        text = ("已按「耗时 + 价格」综合最优为您选定各段高铁：\n"
                                + "\n".join(f"- {t}" for t in _picked))
                        state_step = _ACCOMMODATION_STEP
                else:
                    _payload, _st = await emit_train_skill_ui(stream, conversation_id, _vals)
                    if _st == "pushed" and buffer_all:
                        text = build_train_lead_text(_payload)
                    elif _st == "empty" and buffer_all:
                        text = ("⚠️ 实时高铁班次暂时查询不到（12306 数据源本次未返回余票）。"
                                "您可以稍后发送「重新查询」，或直接回复「改航班」/「改自驾」，"
                                "我会立即按新的交通方式为您查询。")
            elif flight_card_mode(_vals):
                # 多段航班卡片
                from app.capabilities.flight_multi_segment.flight_flow import (
                    auto_select_flights,
                    build_lead_text as build_flight_lead_text,
                    emit_flight_skill_ui,
                )
                if _vals.get("auto_confirm"):
                    _fpicked = await auto_select_flights(conversation_id, _vals)
                    if _fpicked and buffer_all:
                        text = ("已按「价格最优」为您选定各段航班：\n"
                                + "\n".join(f"- {t}" for t in _fpicked))
                        state_step = _ACCOMMODATION_STEP
                else:
                    _fpayload, _fst = await emit_flight_skill_ui(stream, conversation_id, _vals)
                    if _fst == "pushed" and buffer_all:
                        text = build_flight_lead_text(_fpayload)
                    elif _fst == "empty" and buffer_all:
                        text = ("⚠️ 实时航班暂时查询不到（航班数据源本次未返回结果）。"
                                "您可以稍后发送「重新查询」，或直接回复「改高铁」/「改自驾」，"
                                "我会立即按新的交通方式为您查询。")
            elif driving_card_mode(_vals):
                # 多段自驾卡片
                from app.capabilities.driving_multi_segment.driving_flow import (
                    auto_select_driving,
                    build_lead_text as build_driving_lead_text,
                    emit_driving_skill_ui,
                )
                if _vals.get("auto_confirm"):
                    _dpicked = await auto_select_driving(conversation_id, _vals)
                    if _dpicked and buffer_all:
                        text = ("已按高德默认策略为您确认各段自驾路线：\n"
                                + "\n".join(f"- {t}" for t in _dpicked))
                        state_step = _ACCOMMODATION_STEP
                else:
                    _dpayload, _dst = await emit_driving_skill_ui(stream, conversation_id, _vals)
                    if _dst == "pushed" and buffer_all:
                        text = build_driving_lead_text(_dpayload)
                    elif _dst == "empty" and buffer_all:
                        text = ("⚠️ 实时自驾路线暂时查询不到（高德数据源本次未返回结果）。"
                                "您可以稍后发送「重新查询」，或直接回复「改高铁」/「改自驾」，"
                                "我会立即按新的交通方式为您查询。")
            # 追加交通切换/降级提示
            if buffer_all:
                for _note in (_fallback_note, _switch_note):
                    if _note and _note not in text:
                        text = f"{_note}\n\n{text}" if text.strip() else _note

        # 后处理⑦：结构化行程由代码渲染正文，追加预算与邀请话术
        code_rendered = False
        if any(
            getattr(m, "type", None) == "tool" and _BODY_READY_MARK in str(getattr(m, "content", ""))
            for m in tail
        ):
            try:
                vals = snap.values or {}
            except Exception:
                vals = {}
            rendered = _render_itinerary_body(
                vals.get("itinerary_body") or [],
                vals.get("budget") or {},
                vals.get("user_requirement") or {},
                vals.get("selected_transport_legs") or [],
                vals.get("selected_hotels") or [],
            )
            if rendered:
                rendered_text = rendered + _INVITE_TAIL
                prefix = text.strip()
                _req = vals.get("user_requirement") or {}
                _lead = (f"已按 {int(_req.get('travel_days') or 0)} 天行程生成如下安排："
                         if _req.get("travel_days") else "")
                text = f"{_lead}\n\n{rendered_text}" if _lead else rendered_text
                if prefix:
                    app_logger.warning(f"⚠️ 行程正文分支丢弃模型旁白 {len(prefix)} 字（正文仅由代码渲染）")
                state_step = "budget_summarization"
                code_rendered = buffer_all
                if not buffer_all:
                    stream.push(make_event(ROLE_ASSISTANT_RESET))   
                    await _push_rendered(stream, f"\n\n{text}")
                app_logger.info(
                    f"✅ 行程正文已由代码渲染（{len(rendered_text)} 字），引导语由代码生成"
                    f"（共 {len(text)} 字）"
                )

        # 缓冲模式：回合结束一次性推送最终文本
        if buffer_all and text.strip():
            if code_rendered:
                await _push_rendered(stream, text)
            else:
                stream.push(make_event(ROLE_ASSISTANT, text))
        return text, state_step, committed, body

    # 闲聊分支：天气/交通查询意图都走确定性兜底，不交给裸模型
    _intent = detect_intent(content)
    if _intent.weather_object:
        _wx = await try_weather_query(content, stream, intent=_intent)
        app_logger.info(f"[weather] 闲聊通道改推代码直查天气：{content[:30]}")
        return _wx, None, False, None
    if _intent.transport_object:
        _wx = await try_direct_ticket_query(content, stream)
        if _wx:
            app_logger.info(f"[fallback] 闲聊通道改推确定性直查结果：{content[:30]}")
            return _wx, None, False, None
        _reply = _query_fallback_text(content, _intent)
        stream.push(make_event(ROLE_ASSISTANT, _reply))
        return _reply, None, False, None
    return await _stream_plain(content, stream), None, False, None
