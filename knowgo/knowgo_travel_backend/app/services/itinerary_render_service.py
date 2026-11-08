"""Render confirmed selections, itinerary days and budget into chat Markdown."""
import asyncio
from app.core.event_stream import EventStream
from app.core.events import make_event, ROLE_ASSISTANT
from app.utils.logger import app_logger

_SLOT_RENDER = (("morning", "上午"), ("noon", "中午"), ("afternoon", "下午"), ("evening", "晚上"))
_INVITE_TAIL = "\n\n如需调整（天数/预算/目的地等），直接告诉我即可。"

def _render_confirmed_block(transport_legs: list | None, hotels: list | None) -> str:
    """已确认的交通与住宿摘要块（数据来自状态机已落库的
    selected_transport_legs / selected_hotels）。两者都空则整块不出——
    本地零段游玩、或品牌兜底缺失时不硬凑。
    """
    legs = [str(x).strip() for x in (transport_legs or []) if str(x).strip()]
    stays = [str(x).strip() for x in (hotels or []) if str(x).strip()]
    if not legs and not stays:
        return ""
    out = ["### 已确认的交通与住宿\n"]
    if legs:
        out.append("\n- **交通**\n")
        out.extend(f"  - {t}\n" for t in legs)
    if stays:
        out.append("\n- **住宿**\n")
        out.extend(f"  - {h}\n" for h in stays)
    return "".join(out)

def _render_itinerary_body(
    days: list, budget: dict, req: dict,
    transport_legs: list | None = None, hotels: list | None = None,
) -> str:
    """结构化逐日行程 → Markdown 正文 + 预算明细（全部为代码生成，数字不来自模型）。
    transport_legs / hotels：状态机已确认并落库的交通段与住宿
    （selected_transport_legs / selected_hotels）。
    """
    if not days:
        return ""
    confirmed = _render_confirmed_block(transport_legs, hotels)
    lines = [confirmed + "\n", "### 详细行程\n"] if confirmed else ["### 详细行程\n"]

    for d in days:
        city = str(d.get("city") or "").strip()
        lines.append(f"\n#### Day {d.get('day')}{' ' + city if city else ''}\n")
        for key, label in _SLOT_RENDER:
            items = [str(x).strip() for x in (d.get(key) or []) if str(x).strip()]
            lines.append(f"\n- **{label}**\n")
            lines.extend(f"  - {it}\n" for it in items)
        # 当天小贴士
        tips = [str(x).strip() for x in (d.get("tips") or []) if str(x).strip()]
        if tips:
            lines.append("\n- **小贴士**：\n")
            lines.extend(f"  - {t}\n" for t in tips)

    b = budget or {}
    if b.get("total"):
        people = int(req.get("adult_count") or 0) + int(req.get("children_count") or 0) or 1
        total = float(b.get("total") or 0)
        per = total / people
        # 与 _build_budget（自动确认模式）口径完全一致：表头人数/晚数/间数/城数 + 各分项括号说明
        day_count = int(req.get("travel_days") or 0) or len(days) or 1
        nights = max(day_count - 1, 0)
        rooms = max((people + 1) // 2, 1)      # ceil(people/2)，避免新增 math 依赖
        city_count = len({str(d.get("city")).strip() for d in days
                          if str(d.get("city") or "").strip()}) or 1
        transport_cost = float(b.get("transport") or 0)
        accommodation_cost = float(b.get("accommodation") or 0)
        food_cost = float(b.get("food") or 0)
        attractions_cost = float(b.get("attractions") or 0)
        misc_cost = float(b.get("misc") or 0)
        total_legs = len(transport_legs or [])
        transport_detail = (f"（{total_legs} 段 × 各段票价）" if total_legs
                            else "（本地游玩，无城际交通段）")
        hotel_price = (accommodation_cost / (nights * rooms)) if nights * rooms else 0.0
        hotel_quoted = bool(hotels) and not any(
            any(word in str(h) for word in ("实时报价不可用", "仅选定品牌", "具体门店到店"))
            for h in hotels or []
        )
        hotel_detail = (f"各城参考均价 {hotel_price:.0f} 元" if hotel_quoted
                        else f"各城估算 {hotel_price:.0f} 元/晚（实时房价不可用）")
        bmax = req.get("budget_max")
        if isinstance(bmax, (int, float)) and bmax > 0:
            verdict = "人均未超预算" if per <= bmax else "人均超预算（建议下调住宿档位或减天数）"
        else:
            verdict = "未提供预算上限，仅作参考"
        # 门票说明须与金额来源一致：来源字段缺席（如快速通道预算）时回退估算文案，
        # 否则模型传了真实票价时，金额按真实价算、说明却仍写"按 200元/人/天 估算"，数字与说明自相矛盾
        ticket_note = (f"（参考门票合计 {attractions_cost / people:.0f} 元/人，以景区公示为准）"
                       if b.get("attractions_source") in {"real", "reference"} else "（按 200元/人/天 估算）")
        lines.append(
            f"\n### 预算明细（{people} 人 / {nights} 晚 / {rooms} 间房 / {city_count} 城）\n"
            f"- **交通费用**：{transport_cost:.2f} 元"
            f"（人均 {transport_cost / people:.0f} 元，{transport_detail}）\n"
            f"- **住宿费用**：{accommodation_cost:.2f} 元"
            f"（{rooms} 间 × {nights} 晚 × {hotel_detail}）\n"
            f"- **餐饮费用**：{food_cost:.2f} 元（150元/人/天）\n"
            f"- **景点门票**：{attractions_cost:.2f} 元{ticket_note}\n"
            f"- **其他杂费**：{misc_cost:.2f} 元\n"
            f"**总计：{total:.2f} 元（{people} 人总价）｜人均：{per:.2f} 元**\n"
            f"预算结论：{verdict}\n"
        )
    else:
        app_logger.warning("⚠️ 行程渲染时缺少预算数据，本轮不输出预算明细（避免编造数字）")
    return "".join(lines).strip()

# 代码渲染正文分段推送，模拟流式观感
_RENDER_CHUNK = 48
_RENDER_DELAY = 0.025

async def _push_rendered(stream: EventStream, text: str) -> None:
    """把代码渲染的长正文按小段渐进推送（文本短于一段时零额外延迟）"""
    for i in range(0, len(text), _RENDER_CHUNK):
        stream.push(make_event(ROLE_ASSISTANT, text[i:i + _RENDER_CHUNK]))
        if i + _RENDER_CHUNK < len(text):
            await asyncio.sleep(_RENDER_DELAY)

