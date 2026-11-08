from app.services.itinerary_constraints import constraints_for, schedule_errors, repair_schedule


def test_daily_cities_follow_confirmed_stays_and_early_return():
    state = {
        "user_requirement": {"departure_city": "北京", "destination": "上海、天津",
                             "departure_date": "2026-10-23", "travel_days": 4},
        "selected_destination": "上海、天津",
        "leg_selections": {"北京→上海": "G1 06:30-11:24 二等座598元",
                           "上海→天津": "G532 06:31-11:41 二等座529元",
                           "天津→北京": "C2602 05:33-06:12 二等座54.5元"},
    }
    constraints = constraints_for(state)
    assert constraints["cities"] == ["上海", "天津", "天津", "北京"]
    assert [leg["day"] for leg in constraints["transfers"]] == [1, 2, 4]


def test_sightseeing_before_confirmed_arrival_is_rejected():
    constraints = {"cities": ["上海"], "transfers": [
        {"day": 1, "departure": 390, "arrival": 684, "is_return": False,
         "selected": "G1 06:30-11:24", "origin": "北京", "destination": "上海"}]}
    errors = schedule_errors([{"day": 1, "city": "上海", "morning": ["09:00 外滩漫步"]}], constraints)
    assert errors and "到达并接驳前" in errors[0]
    assert not schedule_errors([{"day": 1, "city": "上海", "morning": ["06:30 乘坐G1高铁"],
                                 "noon": ["12:30 南京路午餐"]}], constraints)


def test_sightseeing_after_early_return_is_rejected():
    constraints = {"cities": ["北京"], "transfers": [
        {"day": 1, "departure": 333, "arrival": 372, "is_return": True,
         "selected": "C2602 05:33-06:12", "origin": "天津", "destination": "北京"}]}
    errors = schedule_errors([{"day": 1, "city": "北京", "afternoon": ["14:00 天津博物馆参观"]}], constraints)
    assert errors and "不能继续安排" in errors[0]


def test_repair_reuses_city_activities_and_reserves_travel_windows():
    constraints = {"cities": ["上海", "天津", "天津", "北京"], "transfers": [
        {"day": 1, "departure": 390, "arrival": 684, "is_return": False,
         "route": "北京→上海", "selected": "G1 06:30-11:24", "origin": "北京", "destination": "上海"},
        {"day": 2, "departure": 391, "arrival": 701, "is_return": False,
         "route": "上海→天津", "selected": "G532 06:31-11:41", "origin": "上海", "destination": "天津"},
        {"day": 4, "departure": 333, "arrival": 372, "is_return": True,
         "route": "天津→北京", "selected": "C2602 05:33-06:12", "origin": "天津", "destination": "北京"}]}
    days = [{"day": i + 1, "city": city, "morning": ["09:00 市区游览"],
             "noon": ["12:00 午餐"], "afternoon": ["14:00 参观博物馆"],
             "evening": ["18:00 返回酒店"], "tips": ["提前预约"]}
            for i, city in enumerate(["上海", "上海", "天津", "天津"])]
    repaired = repair_schedule(days, constraints)
    assert [day["city"] for day in repaired] == constraints["cities"]
    assert "G1" in repaired[0]["morning"][0]
    assert "G532" in repaired[1]["morning"][0]
    assert "12:30" in repaired[1]["noon"][0]
    assert "C2602" in repaired[3]["morning"][0]
    assert "完成返程" in repaired[3]["evening"][0]
    assert not schedule_errors(repaired, constraints)


def test_repair_places_clock_times_in_correct_slots():
    constraints = {"cities": ["上海"], "transfers": []}
    days = [{"day": 1, "city": "上海", "morning": ["09:00 外滩漫步"],
             "noon": ["14:00 参观上海博物馆"], "afternoon": ["18:00 豫园附近晚餐"],
             "evening": ["返回酒店休息"]}]
    assert schedule_errors(days, constraints)
    repaired = repair_schedule(days, constraints)
    assert repaired[0]["afternoon"] == ["14:00 参观上海博物馆"]
    assert "18:00 豫园附近晚餐" in repaired[0]["evening"]
    assert "12:30" in repaired[0]["noon"][0]
    assert not schedule_errors(repaired, constraints)


def test_afternoon_return_spanning_evening_passes_after_repair():
    constraints = {"cities": ["西安"], "transfers": [
        {"day": 1, "departure": 816, "arrival": 1162, "is_return": True,
         "route": "西安→天津", "selected": "G1702 13:36-19:22 二等座557元",
         "origin": "西安", "destination": "天津"}]}
    days = [{"day": 1, "city": "西安", "morning": ["09:00 西安市区散步"],
             "noon": ["12:30 午餐后取回行李"], "afternoon": ["14:00 西安景点参观"],
             "evening": ["18:00 西安夜市游览"], "tips": ["提前到站"]}]
    repaired = repair_schedule(days, constraints)
    assert "G1702" in repaired[0]["afternoon"][0]
    assert "G1702" in repaired[0]["evening"][0]
    assert not schedule_errors(repaired, constraints)


def test_transit_interval_does_not_allow_an_unrelated_slot():
    constraints = {"cities": ["天津"], "transfers": []}
    days = [{"day": 1, "city": "天津", "evening": ["乘坐高铁 06:30-11:24 前往天津"]}]
    assert schedule_errors(days, constraints)


def test_evening_outbound_keeps_earlier_slots_at_origin():
    constraints = {"cities": ["上海"], "transfers": [
        {"day": 1, "departure": 1164, "arrival": 1431, "is_return": False,
         "route": "北京→上海", "selected": "G35 19:24-23:51 二等座598元",
         "origin": "北京", "destination": "上海"}]}
    days = [{"day": 1, "city": "上海", "morning": ["09:00 上海外滩漫步"],
             "noon": ["12:30 上海就近午餐"], "afternoon": ["14:00 上海博物馆参观"],
             "evening": ["18:00 上海夜景游览"], "tips": ["核对车次"]}]
    repaired = repair_schedule(days, constraints)
    for slot in ("morning", "noon", "afternoon"):
        assert "在北京" in repaired[0][slot][0]
        assert "19:24" not in repaired[0][slot][0]
    assert "G35" in repaired[0]["evening"][0]
    assert not schedule_errors(repaired, constraints)
