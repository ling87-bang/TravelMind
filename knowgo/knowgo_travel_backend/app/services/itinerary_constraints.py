"""Keep itinerary cities and sightseeing times consistent with confirmed travel."""
import json
import re
from copy import deepcopy
from datetime import date

_CLOCK = re.compile(r"(?<!\d)([0-2]?\d):([0-5]\d)")
_SLOTS = {"morning": 9 * 60, "noon": 12 * 60, "afternoon": 14 * 60, "evening": 18 * 60}
_SLOT_RANGES = {"morning": (0, 720), "noon": (720, 840), "afternoon": (840, 1080), "evening": (1080, 1440)}
_FLEXIBLE = {
    "morning": "在{city}选择顺路的文化街区慢行，建议预留1~2小时，按当日开放情况调整",
    "noon": "12:30 在{city}就近选择当地小吃或连锁餐馆用午餐，人均约50元仅作预算参考",
    "afternoon": "14:30 在{city}安排轻松的街区散步或展馆参观，具体场馆与预约提前核实",
    "evening": "18:30 在{city}就近享用晚餐并返回已选住宿休息，注意核对次日交通时间",
}
_TRANSIT = ("乘车", "乘坐", "高铁", "列车", "候车", "到站", "抵达", "返程", "返回", "回到", "车站", "安检", "途中")


def constraints_for(state: dict) -> dict:
    from app.tools.state_transition import _day_city_map, _expected_legs
    from app.core.middleware import _leg_query_date

    req = state.get("user_requirement") or {}
    cities = _day_city_map(state)
    legs = _expected_legs(state)
    transfers = []
    try:
        start = date.fromisoformat(req.get("departure_date") or "")
    except ValueError:
        return {"cities": cities, "transfers": []}
    for index, route in enumerate(legs):
        selected = (state.get("leg_selections") or {}).get(route, "")
        clocks = list(_CLOCK.finditer(selected))
        if len(clocks) < 2:
            continue
        departure, arrival = [int(m[1]) * 60 + int(m[2]) for m in clocks[:2]]
        try:
            day = (date.fromisoformat(_leg_query_date(state, legs, index)) - start).days + 1
        except ValueError:
            continue
        origin, destination = route.split("→", 1)
        is_return = destination == req.get("departure_city")
        transfers.append({"day": day, "route": route, "selected": selected,
                          "departure": departure, "arrival": arrival,
                          "origin": origin, "destination": destination, "is_return": is_return})
        if is_return and arrival >= departure and arrival < 12 * 60 and 0 < day <= len(cities):
            cities[day - 1] = destination
    return {"cities": cities, "transfers": transfers}


def schedule_prompt(constraints: dict) -> str:
    return ("\n\n【已确认日程约束，优先于通用示例】\n"
            + json.dumps(constraints, ensure_ascii=False)
            + "\ncities 按第1天起逐日列出，应与每天的 city 一致，不能自行均分城市。"
              "departure/arrival 为当天零点起分钟数，selected 为真实到发时间。"
              "出发和换城当天，到达前只安排乘车与接驳，至少留30分钟后再安排游览或用餐。"
              "返程当天离开目的地后，不再安排该目的地景点；早班返程后可写抵家休整。"
              "早班返程的末日晚间写已完成返程并休息，不要改成傍晚乘同一班车。"
              "未查询核验的景点门票和酒店单价必须标注参考或估算。")


def schedule_errors(days: list[dict], constraints: dict) -> list[str]:
    errors = []
    cities = constraints.get("cities") or []
    by_day = {int(day.get("day") or 0): day for day in days}
    for number, day in by_day.items():
        for slot, (start, end) in _SLOT_RANGES.items():
            for item in day.get(slot) or []:
                item = str(item)
                clocks = list(_CLOCK.finditer(item))
                clock = clocks[0] if clocks else None
                if clock and not start <= int(clock[1]) * 60 + int(clock[2]) < end:
                    # A confirmed train can occupy several slots. For example,
                    # 13:36-19:22 belongs in both afternoon and evening; comparing
                    # every copy only against its departure causes endless repairs.
                    if len(clocks) >= 2 and any(word in item for word in _TRANSIT):
                        dep = int(clocks[0][1]) * 60 + int(clocks[0][2])
                        arr = int(clocks[1][1]) * 60 + int(clocks[1][2])
                        if arr >= dep and dep < end and arr >= start:
                            continue
                    errors.append(f"第{number}天{slot}的活动时刻应归入正确时段")
    for number, expected in enumerate(cities, 1):
        actual = str((by_day.get(number) or {}).get("city") or "").strip().removesuffix("市")
        if actual and actual != expected.removesuffix("市"):
            errors.append(f"第{number}天城市应为{expected}，与已确认住宿和交通日期一致")
    for transfer in constraints.get("transfers") or []:
        day = by_day.get(transfer["day"]) or {}
        for slot, default_time in _SLOTS.items():
            for item in day.get(slot) or []:
                item = str(item)
                clock = _CLOCK.search(item)
                when = int(clock[1]) * 60 + int(clock[2]) if clock else default_time
                if any(word in item for word in _TRANSIT):
                    continue
                if not transfer["is_return"] and transfer["arrival"] >= transfer["departure"]:
                    if when < transfer["arrival"] + 30:
                        errors.append(f"第{transfer['day']}天{slot}不能在{transfer['selected']}到达并接驳前安排目的地活动")
                elif transfer["is_return"] and when >= transfer["departure"]:
                    if transfer["origin"] in item or any(w in item for w in ("景点", "摩天轮", "参观", "游览", "漫步")):
                        errors.append(f"第{transfer['day']}天已按{transfer['selected']}返程，不能继续安排目的地游览")
    return list(dict.fromkeys(errors))[:5]


def repair_schedule(days: list[dict], constraints: dict) -> list[dict]:
    """Reuse city-matching activities, reserving confirmed travel windows in code.

    This is basic itinerary consistency, not a connection or ticket-booking guarantee.
    Missing content uses explicit flexible arrangements rather than invented venues.
    """
    cities = constraints.get("cities") or []
    if not cities:
        return days
    used = set()
    out = []
    for index, city in enumerate(cities):
        matching = [i for i, day in enumerate(days) if i not in used
                    and str(day.get("city") or "").removesuffix("市") == city.removesuffix("市")]
        picked = index if index in matching else (matching[0] if matching else None)
        row = deepcopy(days[picked]) if picked is not None else {}
        if picked is not None:
            used.add(picked)
        row.update(day=index + 1, city=city)
        buckets = {slot: [] for slot in _SLOTS}
        for old_slot in _SLOTS:
            for item in row.get(old_slot) or []:
                clock = _CLOCK.search(str(item))
                target = old_slot
                if clock:
                    when = int(clock[1]) * 60 + int(clock[2])
                    target = next((slot for slot, (start, end) in _SLOT_RANGES.items()
                                   if start <= when < end), old_slot)
                buckets[target].append(str(item))
        for slot in _SLOTS:
            row[slot] = buckets[slot] or [_FLEXIBLE[slot].format(city=city)]
        row.setdefault("tips", [f"{city}的具体营业时间与预约要求请提前在官方渠道核实"])
        out.append(row)
    for transfer in constraints.get("transfers") or []:
        number = transfer["day"]
        if not 0 < number <= len(out):
            continue
        row = out[number - 1]
        dep, arr = transfer["departure"], transfer["arrival"]
        travel = (f"按已确认班次乘车：{transfer['route']} {transfer['selected']}；"
                  "提前1小时到站安检，抵达后预留至少30分钟接驳")
        if transfer["is_return"] and arr >= dep and arr < 12 * 60:
            home = transfer["destination"]
            row.update(city=home,
                       morning=[travel],
                       noon=[f"已返抵{home}，就近安排午餐并整理行李，餐饮费用按实际消费为准"],
                       afternoon=[f"在{home}家中或住处休整，本次旅程已结束，不再安排跨城活动"],
                       evening=[f"本日早间已完成返程，晚间在{home}休息并整理旅行记录，本次行程结束"])
            continue
        if arr < dep:
            continue  # Overnight travel requires a separate multi-day treatment.
        earliest = ((arr + 30 + 29) // 30) * 30
        for slot, default_time in _SLOTS.items():
            if not transfer["is_return"] and _SLOT_RANGES[slot][1] <= dep:
                # Before an evening departure, the traveller is still at the
                # origin. Copying the evening train into every earlier slot
                # creates an invalid schedule that no subsequent repair fixes.
                row[slot] = [
                    f"在{transfer['origin']}安排出发前准备与就近用餐，核对前往"
                    f"{transfer['destination']}的已确认车次，整理行李并提前到站候车"
                ]
                continue
            items = []
            for item in row.get(slot) or []:
                item = str(item)
                clock = _CLOCK.search(item)
                when = int(clock[1]) * 60 + int(clock[2]) if clock else default_time
                if transfer["is_return"] and when >= dep:
                    items = [travel + "；完成返程后返回住处休息"]
                    break
                if not transfer["is_return"] and not any(word in item for word in _TRANSIT):
                    if default_time < arr:
                        items = [travel]
                        break
                    if when < earliest and clock:
                        item = item[:clock.start()] + f"{earliest // 60:02d}:{earliest % 60:02d}" + item[clock.end():]
                items.append(item)
            row[slot] = items or [travel]
    return out
