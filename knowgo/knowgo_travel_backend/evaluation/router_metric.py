"""
意图路由正确性指标，基于DeepEval自定义Metric
路由输出是强类型RouterResult，包含planning布尔值和skills列表，属于分类任务
直接用断言打分：对得1分，错得0分，不需要调用LLM做裁判
- planning是核心开关，必须和预期完全一致
- skills分3种校验逻辑：
  expect_empty_skills=True → skills必须是空
  expected_skills_any不为空 → 实际skills只要包含里面任意一个就算通过
  两个参数都不传 → 不校验skills
"""
import json
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase


class RouterCorrectnessMetric(BaseMetric):
    """意图路由正确性校验：planning精确匹配 + skills多模式校验"""
    def __init__(
        self,
        expected_planning: bool,
        expected_skills_any: list[str] | None = None,
        expect_empty_skills: bool = False,
    ):
        # 两个skills参数不能同时开启，逻辑冲突，直接抛异常，不静默忽略配置错误
        if expect_empty_skills and expected_skills_any:
            raise ValueError("expect_empty_skills=True 和 expected_skills_any 非空不能同时设置，二选一")

        # 预期的规划分流结果，这个是必校验项
        self.expected_planning = expected_planning
        # 期望匹配的技能列表，None/空代表不校验skills
        self.expected_skills_any = expected_skills_any or []
        # 标记：是否要求skills必须为空，用来校验闲聊场景
        self.expect_empty_skills = expect_empty_skills

        # 阈值固定1.0，必须在构造函数赋值，防止外部赋值时机不对导致is_successful异常
        self.threshold = 1.0
        self.score = 0.0
        self.reason = ""

    def measure(self, test_case: LLMTestCase) -> float:
        # actual_output是测试用例传过来的RouterResult序列化后的json字符串
        try:
            actual = json.loads(test_case.actual_output)
        except (json.JSONDecodeError, TypeError):
            self.score, self.reason = 0.0, "路由返回不是合法JSON，结构化输出失败"
            return self.score

        # 取出planning标记，转布尔
        actual_planning = bool(actual.get("is_itinerary_planning"))
        # 提取所有技能名称，组装成列表
        actual_skills = [s.get("skill_name") for s in (actual.get("skills") or [])]

        # 第一步校验：planning必须完全匹配，这是最容易出bug的地方
        if actual_planning != self.expected_planning:
            self.score = 0.0
            self.reason = f"planning期望 {self.expected_planning}，实际 {actual_planning}"
            return self.score

        # 第二步校验skills，分三种情况
        # 场景1：闲聊类，要求技能列表必须是空
        if self.expect_empty_skills:
            if actual_skills:
                self.score = 0.0
                self.reason = f"期望技能为空，实际拿到 {actual_skills}"
                return self.score
            self.score, self.reason = 1.0, "planning正确，技能列表为空"
            return self.score

        # 场景2：技能宽松匹配，只要命中任意一个目标技能就通过
        if self.expected_skills_any:
            hit = [s for s in actual_skills if s in self.expected_skills_any]
            if not hit:
                self.score = 0.0
                self.reason = (
                    f"skills需要命中 {self.expected_skills_any} 其中一个，实际 {actual_skills}"
                )
                return self.score
            self.score, self.reason = 1.0, f"planning正确，命中技能 {hit}"
            return self.score

        # 场景3：不校验skills，planning正确就直接满分
        self.score, self.reason = 1.0, f"planning正确（skills={actual_skills}）"
        return self.score

    async def a_measure(self, test_case: LLMTestCase) -> float:
        # 异步版本直接复用同步逻辑，不用单独写
        return self.measure(test_case)

    def is_successful(self) -> bool:
        # 判断指标是否达标：分数大于等于阈值
        return self.score >= self.threshold