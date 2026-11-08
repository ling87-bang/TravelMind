"""
意图路由评测入口
两种运行方式任选：
- uv run pytest evaluation/test_router.py -v        # 原生pytest执行
- deepeval test run evaluation/test_router.py      # DeepEval CLI，结果可以上传Confident平台

逻辑：遍历所有测试用例，调用真实的 chat_route 路由函数。
把返回的强类型RouterResult转成JSON字符串，交给自定义指标做固定规则校验。
"""
import asyncio
import json
import os

# Windows环境下异步客户端需要Selector事件循环，和项目app/run.py保持一致
if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from deepeval import assert_test
from deepeval.test_case import LLMTestCase
import pytest
from app.agents.routers.chat_router import chat_route
from evaluation.router_cases import CASES, RouterCase
from evaluation.router_metric import RouterCorrectnessMetric


@pytest.mark.parametrize("case", CASES, ids=[c.note for c in CASES])
def test_chat_router(case: RouterCase):
    """单条路由测试：调用真实路由，校验planning和skills结果"""
    # 执行路由函数，async方法用asyncio.run转为同步调用；history用于对话上下文接续
    result = asyncio.run(chat_route(case.msg, history=case.history or None))

    # 将路由返回对象序列化JSON，作为deepeval测试的实际输出
    actual_output = json.dumps({
        "is_itinerary_planning": result.is_itinerary_planning,
        "skills": [s.model_dump() for s in result.skills],
    }, ensure_ascii=False)

    test_case = LLMTestCase(
        input=case.msg,
        actual_output=actual_output,
    )

    # 初始化自定义校验指标，必须全部匹配才能通过
    metric = RouterCorrectnessMetric(
        expected_planning=case.planning,
        expected_skills_any=case.skills,
        expect_empty_skills=case.expect_empty_skills,
    )
    metric.threshold = 1.0
    assert_test(test_case, [metric])