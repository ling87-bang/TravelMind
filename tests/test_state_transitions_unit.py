"""不调用模型、数据库或 MCP 的状态转换单元测试。"""

from types import SimpleNamespace

from app.tools.state_transition import (
    record_requirement_tool,
    select_destination_tool,
    select_transport_tool,
)


def runtime():
    return SimpleNamespace(tool_call_id="test-call")


def test_requirement_advances_to_destination_step():
    command = record_requirement_tool.func(
        departure_city="北京",
        departure_date="2026-10-01",
        travel_days=4,
        budget_min=3000,
        budget_max=5000,
        travel_styles=["culture", "food"],
        runtime=runtime(),
    )

    assert command.update["current_step"] == "destination_recommendation"
    assert command.update["user_requirement"]["budget_level"] == "comfort"
    assert command.update["user_requirement"]["departure_city"] == "北京"


def test_destination_advances_to_transport_step():
    command = select_destination_tool.func(destination="西安", runtime=runtime())

    assert command.update["selected_destination"] == "西安"
    assert command.update["current_step"] == "transport_planning"


def test_invalid_transport_does_not_advance_state():
    command = select_transport_tool.func(transport_type="ship", runtime=runtime())

    assert "current_step" not in command.update
    assert "交通方式无效" in command.update["messages"][0].content


def test_valid_transport_advances_to_accommodation_step():
    command = select_transport_tool.func(transport_type="train", runtime=runtime())

    assert command.update["selected_transport"] == "train"
    assert command.update["current_step"] == "accommodation_planning"
