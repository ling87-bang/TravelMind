"""Direct ticket and weather queries with deterministic rendering and slot filling."""
import asyncio
import json
import re
from datetime import date, timedelta
from langchain_core.messages import HumanMessage
from app.core.intent import detect_intent
from app.core.events import make_event, ROLE_ASSISTANT
from app.agents.handoffs.travel_agent import get_llm
from app.utils.logger import app_logger

# 交通步骤权威班次清单——由代码直出，不由模型自由生成
_TICKET_SEG_LIMIT = 10
_TICKET_SEAT_ORDER = ("二等座", "硬座", "一等座", "硬卧", "软卧", "无座")
# 高铁/动车车次前缀（G 高铁 / C 城际 / D 动车），用于剔除段内混入的普速
_HS_PREFIX = ("G", "C", "D")


def _row_seat_label(row: dict) -> str:
    """取一行班次的票价文案：紧凑行直接用；原始行从席别字典里按常用度挑一个"""
    if row.get("seat"):
        return row["seat"]
    seats = row.get("seats") or {}
    for k in _TICKET_SEAT_ORDER:
        if k in seats:
            status, price = seats[k]
            return f"{k} {status} {price:g}元"
    return "票价未返回"


def _rows_to_seg(rows: list, seg: dict | None = None) -> dict:
    """ 把一趟查询的班次行归成 {车次: (发车, 车次, 到达, 历时, 票价文案)}

    从 _ticket_table_from_tail 抽出的公共归并逻辑（同车次只留发车最早的一条），
    供"本轮工具返回"与"直查通道的单次 12306 返回"共用，避免两处去重口径分叉；
    seg 传入时按同一规则合并进去（多段/多轮查询共用一张表）
    """
    seg = {} if seg is None else seg
    for r in rows:
        prev = seg.get(r["code"])
        # 同车次只留发车最早的一条（直查与交通步共用这一处去重口径）
        if prev and prev[0] <= r["dep"]:
            continue
        seg[r["code"]] = (r["dep"], r["code"], r["arr"], r["dur"], _row_seat_label(r))
    return seg


def _hs_only(seg: dict) -> dict:
    """ 段内出现 G/C/D 即剔除混入的普速（K/T/Z/纯数字）；整段只有普速则原样保留"""
    if any(v[1][:1] in _HS_PREFIX for v in seg.values()):
        return {k: v for k, v in seg.items() if v[1][:1] in _HS_PREFIX}
    return seg

def _render_ticket_table(segs: dict, only_leg: str = "", header: str = "", tail: str = "") -> str:
    """把「段 → 班次」渲染成 Markdown 表，经过普速过滤和段筛选；无数据返回空串。
    header/tail 可自定义表头与结尾文案。直查通道面向用户时，可替换原本给模型的约束提示，避免内部提示展示在前端。
    """
    segs = {k: _hs_only(v) for k, v in segs.items()}
    if only_leg and "→" in only_leg:
        a, b = only_leg.split("→")
        segs = {
            (f, t): v for (f, t), v in segs.items()
            if (a in f or f in a) and (b in t or b in t)
        }
    if not segs:
        return ""
    lines = [
        "\n\n---\n",
        header or "**以下为系统直出的 12306 真实班次（每段以本表为准，表外车次为无效信息，请勿采纳）**\n",
    ]
    total = 0
    for (frm, to), seg in segs.items():
        items = sorted(seg.values())[:_TICKET_SEG_LIMIT]
        lines.append(f"\n### {frm} → {to}\n\n")
        lines.append("| 车次 | 出发→到达 | 历时 | 票价 |\n|---|---|---|---|\n")
        for dep, code, arr, dur, seat in items:
            lines.append(f"| {code} | {dep}→{arr} | {dur} | {seat} |\n")
        total += len(items)
    if tail:
        lines.append(tail)
    elif only_leg:
        lines.append(f"\n请从以上**真实班次**中选择「{only_leg}」这一段的班次。\n")
    else:
        lines.append("\n请从以上**真实班次**中选择各段班次。\n")
    app_logger.info(f"✅ 直出真实班次清单：{len(segs)} 段 / {total} 趟（表外车次均视为编造）")
    return "".join(lines)


def _ticket_table_from_tail(tail: list, only_leg: str = "") -> str:
    """将本轮查询得到的真实班次按路段分组生成Markdown表格，查询几段就输出几张表。
    兼容12306原始返回格式，起到双保险作用。
    无真实数据返回空字符串，不编造车次。
    only_leg不为空时，仅渲染当前待确认路段；城市名采用宽容匹配，支持站名带有北站/西站等后缀场景。
    """
    from app.tools.mcp_tools import parse_compact_rows, parse_ticket_rows

    segs: dict[tuple, dict] = {}
    for m in tail:
        if getattr(m, "type", None) != "tool":
            continue
        content = getattr(m, "content", "")
        rows = parse_compact_rows(content) or parse_ticket_rows(content)
        if not rows:
            continue
        # 一次工具调用对应一个路段，按首趟车次起讫站分组；同路段多次查询自动合并去重
        _rows_to_seg(rows, segs.setdefault((rows[0]["from"], rows[0]["to"]), {}))
    return _render_ticket_table(segs, only_leg)


# 用于识别单独查高铁/航班的请求，直接调用MCP工具并由代码渲染班次表，不读写状态机
from app.core.intent import DAY_OFFSET as _DAY_OFFSET

_FLIGHT_LIMIT = 10


def _render_flight_table(rows: list[dict]) -> str:
    """航班清单渲染：仅渲染解析出的真实航班，禁止模型编造"""
    if not rows:
        return ""
    lines = [
        "\n\n---\n",
        "**以下为系统直出的实时航班（每班以本表为准，表外航班为无效信息）**\n", "\n",
        "| 航班 | 出发→到达 | 航司 | 准点率 | 航站楼 | 价格 |\n|---|---|---|---|---|---|\n"
    ]
    for r in rows[:_FLIGHT_LIMIT]:
        stop = f"（经停{r['stop']}）" if r.get("stop") else ""
        # 仅使用航班号匹配到的真实票价，无匹配则显示占位符
        price = f"{r['price']:.0f}元" if r.get("price") else "—"
        lines.append(
            f"| {r['code']}{stop} | {r['dep']}→{r['arr']} | {r['airline'][:8]} | "
            f"{r['ontime'] or '—'} | {r['terminal'] or '—'} | {price} |\n"
        )
    lines.append("\n以上为实时航班时刻，票价与余座以航空公司官方渠道为准。\n")
    app_logger.info(f"✅ 直出真实航班清单：{len(rows)} 班（表外航班均视为编造）")
    return "".join(lines)


async def _flight_table(frm: str, to: str, day: str) -> str:
    """航班直查：城市→机场编码→调用VariFlight工具，代码渲染表格。无数据返回空串，不编造航班"""
    from app.tools.mcp_tools import (_CITY_AIRPORT, _CITY_IATA, flight_price_args,
                                     get_flight_tools, parse_flight_prices, parse_flight_rows)
    # 时刻表使用机场码，票价接口使用城市码，两套编码不可混用
    dep, arr = _CITY_AIRPORT.get(frm), _CITY_AIRPORT.get(to)
    if not dep or not arr:
        return ""

    tools = await get_flight_tools()
    tool = next((t for t in tools if t.name.lower() == "searchflightsbydeparr"), None)
    if tool is None:
        return ""

    ptool = next((t for t in tools if t.name.lower() == "getflightpricebycities"), None)
    cdep, carr = _CITY_IATA.get(frm), _CITY_IATA.get(to)

    async def _prices() -> str:
        """单独查询票价，票价失败仅价格列置空，不影响航班基础信息"""
        if ptool is None or not (cdep and carr):
            app_logger.info(f"[直查] 航班票价跳过：ptool={bool(ptool)} 城市码={cdep}/{carr}")
            return ""
        try:
            return await ptool.ainvoke(flight_price_args(ptool, day, cdep, carr))
        except Exception:
            app_logger.warning("[直查] 航班票价查询失败，本次表内价格留空", exc_info=True)
            return ""

    try:
        raw, price_raw = await asyncio.gather(
            tool.ainvoke({"date": day, "dep": dep, "arr": arr}), _prices())
    except Exception:
        app_logger.warning("[直查] 航班查询失败", exc_info=True)
        return ""

    rows = parse_flight_rows(raw)
    # 按航班号匹配票价，航线最低价不可直接当作单班票价
    by_flight = parse_flight_prices(price_raw)[0] if price_raw else {}
    for r in rows:
        r["price"] = float(by_flight.get(str(r.get("code", "")).upper()) or 0)
    return _render_flight_table(rows) if rows else ""


# 直查多轮槽位补全：缓存待收集的起讫城市/日期，最多 _PENDING_TTL 轮，过期释放
_PENDING_QUERY: dict[str, dict] = {}
_PENDING_TTL = 2


def _cities_in(text: str) -> list[str]:
    """按原文出现顺序提取文本内匹配到的城市名称"""
    try:
        from app.data.cities import CITY_PROVINCE
        return sorted({n for n in CITY_PROVINCE if n in text}, key=text.index)
    except Exception:
        return []


_MONTH_DAY_RE = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]")


def _fill_slots(slot: dict, text: str, intent) -> None:
    """填充查询槽位；已存在字段不会被覆盖，一次性完整起讫对可直接覆盖"""
    pair = _parse_city_pair(text)
    if pair:
        slot["frm"], slot["to"] = pair
    else:
        cities = _cities_in(text)
        if cities:
            city = cities[0]
            if "出发" in text or f"从{city}" in text:
                slot["frm"] = city
            elif not slot.get("to"):
                slot["to"] = city
            elif not slot.get("frm"):
                slot["frm"] = city
    if intent.date_word or _MONTH_DAY_RE.search(text):
        slot["date"] = _parse_query_date(text)
    if intent.flight_object:
        slot["kind"] = "flight"
    elif intent.train_object:
        slot["kind"] = "train"


def _missing_slot_text(slot: dict) -> str:
    """根据缺失的槽位生成固定追问文本，不调用LLM"""
    kind = "航班" if slot.get("kind") == "flight" else "班次"
    frm, to = slot.get("frm"), slot.get("to")
    if frm and not to:
        return f"好的，从 **{frm}** 出发。您要到哪个城市？告诉我目的地（和日期）我就直接查{kind}。"
    if to and not frm:
        return f"好的，到 **{to}**。您从哪个城市出发？告诉我出发地（和日期）我就直接查{kind}。"
    return (f"您是想查{kind}吗？只要给我**出发地、到达地和日期**就行，"
            f"例如：「查明天福州到深圳的高铁」。")


def _parse_city_pair(text: str) -> tuple[str, str] | None:
    """解析起讫城市：优先城市库匹配，不足再用正则匹配「X到Y」格式"""
    names: list[str] = []
    try:
        from app.data.cities import CITY_PROVINCE
        names = sorted({n for n in CITY_PROVINCE if n in text}, key=text.index)
    except Exception:
        names = []
    if len(names) >= 2:
        return names[0], names[1]
    m = re.search(r"([\u4e00-\u9fa5]{2,4})\s*(?:到|去|往|至|→)\s*([\u4e00-\u9fa5]{2,4})", text)
    return (m.group(1), m.group(2)) if m else None


def _parse_query_date(text: str) -> str:
    """解析查询日期：优先月日，再相对日期词，无匹配默认取今日"""
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text) or re.search(
        r"(\d{1,2})\s*[-/]\s*(\d{1,2})", text
    )
    if m:
        try:
            return _date(today.year, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            pass
    for word, off in _DAY_OFFSET:
        if word in text:
            return (today + _td(days=off)).isoformat()
    return today.isoformat()


async def try_direct_ticket_query(content: str, stream=None,
                                  conversation_id: str = "") -> str | None:
    """命中交通班次单点查询（高铁/航班）则返回代码渲染的真实班次清单
    确定性门禁规则：
    ① 含票务对象 +（查询动词/日期词）；② 不含规划词汇；
    ③ 可解析起讫城市；④ 对应工具已加载。任一不满足返回 None，交由原通道处理
    """
    intent = detect_intent(content)
    slot = _PENDING_QUERY.get(conversation_id or "")

    # 存在待补槽位，但用户输入包含规划类内容，清空查询状态
    if slot and intent.planning_noise:
        _PENDING_QUERY.pop(conversation_id or "", None)
        slot = None

    if not slot and not intent.looks_like_query:
        app_logger.info(f"[direct] 门禁①②未命中，交回原通道：{content[:30]}")
        return None

    slot = slot or {"kind": "flight" if intent.flight_object else "train"}
    _fill_slots(slot, content, intent)

    # 起讫城市缺失，追问补齐槽位，超过轮数上限则退出直查通道
    if not (slot.get("frm") and slot.get("to")):
        slot["turns"] = slot.get("turns", 0) + 1
        if conversation_id and slot["turns"] <= _PENDING_TTL:
            _PENDING_QUERY[conversation_id] = slot
            app_logger.info(f"[direct] 槽位待补（第 {slot['turns']} 轮）：{slot}")
        else:
            _PENDING_QUERY.pop(conversation_id or "", None)
            return None
        text = _missing_slot_text(slot)
        if stream is not None:
            stream.push(make_event(ROLE_ASSISTANT, text))
        return text

    # 槽位收集完成，清除待查询缓存
    _PENDING_QUERY.pop(conversation_id or "", None)
    frm, to, day = slot["frm"], slot["to"], slot.get("date") or _parse_query_date(content)

    def _emit(text: str) -> str:
        """统一流式事件推送，所有分支出口统一调用，防止事件漏推"""
        if stream is not None:
            stream.push(make_event(ROLE_ASSISTANT, text))
        return text

    _note = ""
    # 航班查询分支，无直飞班次自动降级查询同日高铁
    if intent.flight_object:
        if stream is not None:
            stream.push(make_event(ROLE_ASSISTANT, f"✈️ 正在为您查询 {frm} → {to} 的航班，请稍候…\n"))
        _ftable = await _flight_table(frm, to, day)
        if _ftable:
            app_logger.info(f"✅ 直查通道（航班）：{day} {frm}→{to}（代码渲染，未走状态机）")
            return _emit(f"为您查询到 **{day}** {frm} → {to} 的航班：{_ftable}")
        _note = (f"未查询到 **{day}** {frm} → {to} 的直飞航班（航班数据源该航线暂无直飞班次），"
                 "为您附上同期高铁班次供参考：\n\n")

    # 高铁查询分支
    try:
        from app.tools.mcp_tools import get_train_tools, parse_compact_rows, parse_ticket_rows
        tools = await get_train_tools()
    except Exception:
        return None

    tool = next((t for t in tools if t.name.lower() == "get-tickets"), None)
    if tool is None:
        return None

    if stream is not None:
        stream.push(make_event(ROLE_ASSISTANT, f"🚄 正在为您查询 {frm} → {to} 的高铁班次，请稍候…\n"))
    try:
        raw = await tool.ainvoke({"date": day, "fromStation": frm, "toStation": to})
    except Exception:
        app_logger.warning("[直查] 12306 班次查询失败", exc_info=True)
        return _emit(_note + f"抱歉，**{day} {frm} → {to}** 的实时班次查询失败，请稍后重试或换一站查询。")

    rows = parse_compact_rows(raw) or parse_ticket_rows(raw)
    if not rows:
        return _emit(_note + f"没有查到 **{day} {frm} → {to}** 的可用班次（12306 未返回余票数据），"
                             "您可以换一个日期或邻近车站再试。")

    table = _render_ticket_table(
        {(rows[0]["from"], rows[0]["to"]): _rows_to_seg(rows)},
        header="**以下为 12306 实时班次（数据来源：12306，余票与票价随时变动）**\n",
        tail="\n以上为查询结果，如需预订请在 12306 官方渠道下单。\n",
    )
    if not table:
        return None

    app_logger.info(f"✅ 直查通道：{day} {frm}→{to} 共 {len(rows)} 趟（代码渲染，未走状态机）")
    return _emit(_note + f"为您查询到 **{day}** {frm} → {to} 的班次：{table}")

_WEATHER_CITY_MAX = 3  # 逐城展示上限，防止多城串行拖慢关键路径
def _query_fallback_text(content: str, intent) -> str:
    """闲聊通道识别到交通查询意图时的确定性澄清文案，不调用模型、不编造数据。
    触发场景：存在交通相关意图，但缺少必要查询参数，例如日期、起讫城市。
    只向用户补齐缺失槽位，不直接查询。
    """
    kind = "航班" if (intent.flight_object and not intent.train_object) else "班次"
    pair = _parse_city_pair(content)
    if pair:
        frm, to = pair
        # 成功解析出城市对，仅缺少日期，引导用户补充日期信息
        return (f"您是想查 **{frm} → {to}** 的{kind}吗？"
                f"告诉我日期我就直接给您真实数据，例如「明天」或「10月1日」。")
    return (f"您是想查{kind}吗？我可以直接查 12306 的实时班次与航班时刻，"
            f"只要给我**出发地、到达地和日期**就行，例如：「查明天福州到深圳的高铁」。")

async def try_weather_query(content: str, stream=None, intent=None) -> str | None:
    """闲聊通道识别到天气查询意图时的确定性处理：代码直查高德天气并渲染，不经模型转述。
    命中意图但缺失城市时进行追问；查询失败如实返回，不编造数据；未命中天气意图返回 None。
    intent 可由外部传入避免重复计算，不传则函数内部自行执行意图检测。
    """
    intent = intent or detect_intent(content)
    if not intent.weather_object:
        return None
    cities = _cities_in(content)
    if not cities:
        _ask = ("您想查哪座城市的天气？告诉我城市名就行，"
                "例如：「明天福州天气怎么样」。")
        if stream is not None:
            stream.push(make_event(ROLE_ASSISTANT, _ask))
        return _ask
    try:
        from app.tools.mcp_tools import get_weather_tools
        from app.agents.routers.destination_router import WeatherForecast, _normalize_weather_payload
        tools = await get_weather_tools()
        tool = next((t for t in tools if t.name == "get_weather_forecast"), None)
        if tool is None:
            raise RuntimeError("天气工具未加载")
    except Exception as e:
        app_logger.warning(f"[weather] 天气工具未就绪：{type(e).__name__}: {e}")
        _fail_text = (f"抱歉，暂时没拿到 **{'、'.join(cities[:_WEATHER_CITY_MAX])}** 的天气数据，"
                      "请稍后再问，或换个更规范的城市名。")
        if stream is not None:
            stream.push(make_event(ROLE_ASSISTANT, _fail_text))
        return _fail_text

    # 识别查询日期，存在日期词则只返回目标日期预报，否则返回全部预报
    asked_day = _parse_query_date(content) if (
        intent.date_word or _MONTH_DAY_RE.search(content)) else ""
    blocks: list[str] = []
    failed: list[str] = []

    # 多城市循环查询，受最大城市数量限制；逐个查询并流式推送
    for city in cities[:_WEATHER_CITY_MAX]:
        try:
            payload = _normalize_weather_payload(await tool.ainvoke({"city": city}))
            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            casts = WeatherForecast.model_validate(payload).casts
            if not casts:
                raise RuntimeError("天气返回为空")
        except Exception as e:
            app_logger.warning(f"[weather] 查询失败（不编造）：{city} | {type(e).__name__}: {e}")
            failed.append(city)
            continue
        note = ""
        if asked_day:
            picked = [c for c in casts if c.date == asked_day]
            if picked:
                casts = picked
            else:
                note = f"（**{asked_day}** 超出预报范围（仅支持未来 4 天），以下为近期天气）\n"
        lines = [f"- {c.date}：白天{c.dayweather}，气温{c.nighttemp}~{c.daytemp}°C，"
                 f"{c.daywind}风 {c.daypower} 级" for c in casts]
        block = note + f"**{city}** 天气（数据来源：高德天气）：\n" + "\n".join(lines)
        blocks.append(block)
        if stream is not None:
            stream.push(make_event(ROLE_ASSISTANT, ("\n\n" if len(blocks) > 1 else "") + block))
    if not blocks:
        _fail_text = (f"抱歉，暂时没拿到 **{'、'.join(failed)}** 的天气数据，"
                      "请稍后再问，或换个更规范的城市名。")
        if stream is not None:
            stream.push(make_event(ROLE_ASSISTANT, _fail_text))
        return _fail_text
    text = "\n\n".join(blocks)
    if failed:
        _fail_note = f"（**{'、'.join(failed)}** 暂时没查到）"
        text += f"\n\n{_fail_note}"
        if stream is not None:
            stream.push(make_event(ROLE_ASSISTANT, f"\n\n{_fail_note}"))

    # 存在额外问题时调用模型补充回答，天气基础数据由代码直接输出
    if intent.extra_ask or (content.count("?") + content.count("？")) >= 2:
        extra = await _weather_extra_answer(content)
        if extra:
            text = f"{text}\n\n{extra}"
            if stream is not None:
                stream.push(make_event(ROLE_ASSISTANT, f"\n\n{extra}"))
    return text
# 目的地步天气"归代码"——事实数字不经过模型转述。
# 代码直查并渲染天气，同时剔除模型自写的天气相关文本与工具占位语。
_WEATHER_DROP_RE = re.compile(
    r"℃|°C|天气概况|实时天气|天气信息|请稍等|请稍候|我将查询|正在查询|稍后为您"
)
def _strip_model_weather(text: str) -> str:
    """剔除模型自写的天气行与工具往返占位语（保留城市/景点/美食等其余内容）"""
    kept = [ln for ln in (text or "").splitlines() if not _WEATHER_DROP_RE.search(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

async def _weather_extra_answer(content: str) -> str:
    """补答天气问句里"天气之外"的诉求；无其余诉求、或调用失败时返回空串（绝不影响天气段）"""
    prompt = (
        "用户问题里的天气部分已由系统用权威数据回答完毕。"
        "请只回答其中**与天气无关**的其它诉求（如景点怎么玩、美食推荐、行程安排）；"
        "不要再输出任何天气、气温、降雨相关内容。若没有其它诉求，只回复四个字：无需补充。\n\n"
        f"用户问题：{content}"
    )
    try:
        resp = await get_llm().ainvoke([HumanMessage(content=prompt)])
    except Exception as e:
        app_logger.warning(f"[weather] 补充回答失败（不影响天气段）：{type(e).__name__}: {e}")
        return ""
    text = _strip_model_weather(str(getattr(resp, "content", "") or "")).strip()
    if not text or (len(text) <= 12 and "无需补充" in text):
        return ""
    return text

