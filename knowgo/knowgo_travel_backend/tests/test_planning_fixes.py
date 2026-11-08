"""Regression cases for shared arithmetic, candidate isolation and input limits."""
import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.planning_math import budget_costs, stay_nights


@pytest.mark.parametrize("days,stations,expected", [(1, 1, [0]), (4, 2, [1, 2]), (2, 4, [1, 0, 0, 0]), (5, 1, [4])])
def test_overnights_cover_exactly_trip_length(days, stations, expected):
    assert stay_nights(days, stations) == expected
    assert sum(expected) == days - 1


def test_budget_weights_each_city_and_estimates_only_unknown_nights():
    costs = budget_costs(5, 2, ["天津", "上海"], [50], 3, {"天津": 100, "上海": 500})
    assert costs["accommodation"] == 1600
    assert costs["transport"] == 300
    assert budget_costs(1, 2, ["天津"], [], 0, {})["accommodation"] == 0
    assert budget_costs(4, 2, ["天津", "上海"], [], 0, {"天津": 100})["accommodation"] == 700


def test_stay_windows_and_daily_cities_share_allocation():
    from app.services.planning_math import day_cities, stay_windows
    assert stay_windows("2026-12-01", 4, ["天津", "上海"]) == [
        ("天津", "2026-12-01", "2026-12-02"), ("上海", "2026-12-02", "2026-12-04")]
    assert day_cities(4, ["天津", "上海"]) == ["天津", "上海", "上海", "上海"]
    assert stay_windows("2026-12-01", 1, ["天津"]) == []
    assert day_cities(1, ["天津"]) == ["天津"]


@pytest.mark.asyncio
async def test_day_trip_skips_accommodation_stage(monkeypatch):
    from app.capabilities.hotel_multi_segment import hotel_flow
    state = {"current_step": "accommodation_planning", "user_requirement": {"travel_days": 1}}
    class Agent:
        async def aupdate_state(self, config, updates):
            state.update(updates)
    async def get_state(_):
        return Agent(), {}, state
    monkeypatch.setattr(hotel_flow, "get_agent_and_state", get_state)
    assert await hotel_flow.advance_zero_stay("conversation")
    assert state["current_step"] == "itinerary_generation"
    assert state["stay_prices"] == {} and state["selected_hotels"] == []


def test_password_limit_counts_utf8_bytes():
    from app.schemas.user import UserRegister
    from app.utils.security import hash_password, verify_password
    for password in ("x" * 73, "中" * 25):
        with pytest.raises(ValidationError):
            UserRegister(username="test", email="test@example.com", password=password)
        assert not verify_password(password, hash_password("valid-password"))
    assert UserRegister(username="test", email="test@example.com", password="中" * 24)


@pytest.mark.asyncio
async def test_submission_snapshot_is_task_local_and_survives_cache_eviction():
    from app.capabilities.base.payload_cache import PayloadCache, submission_payload
    cache = PayloadCache("train", capacity=1)
    cache.store("latest", "conversation")
    async def read(snapshot):
        with submission_payload("train", "conversation", snapshot):
            await asyncio.sleep(0)
            return cache.get("conversation")
    assert await asyncio.gather(read("card-one"), read("card-two")) == ["card-one", "card-two"]
    assert cache.get("conversation") == "latest"


def test_train_all_empty_candidates_accept_empty_selection(monkeypatch):
    from app.capabilities.train_multi_segment import train_agent_tool as tools
    from app.capabilities.train_multi_segment.train_skill_schema import TrainSubmitPayload
    monkeypatch.setattr(tools, "get_cached_payload", lambda _: SimpleNamespace(
        segments=[SimpleNamespace(segment_id="s1", train_list=[])]))
    assert tools.validate_train_submission(TrainSubmitPayload(segments=[]), "conversation").ok
    monkeypatch.setattr(tools, "get_cached_payload", lambda _: SimpleNamespace(
        segments=[SimpleNamespace(segment_id="s1", train_list=[object()])]))
    assert not tools.validate_train_submission(TrainSubmitPayload(segments=[]), "conversation").ok
