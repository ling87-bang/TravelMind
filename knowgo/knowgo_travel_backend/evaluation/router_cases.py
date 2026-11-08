"""意图路由评测数据集

每条用例：(消息, 最近对话历史, 期望 planning, 期望命中的技能任一, 用例说明)
额外可选字段：expect_empty_skills=True 表示"技能必须为空"（仅闲聊用）；
        skills 给空列表只表示"不检查技能命中"，两者语义不同。
history 采用**线上真实格式**（"role: content"，role 为 user/assistant、半角冒号），
        与 chat_contract_service 拼接口径一致，避免评测输入失真。
用例为强类型 RouterCase（extra="forbid"）：字段写错在导入期即报错，杜绝断言静默失效。
来源：把开发期踩过的路由 bug 沉淀为回归用例（对照 app/agents/routers/chat_router.py）：
- 需求收集中的短回答（"杭州"/"2人"）曾被误判闲聊 → history 上下文续接规则（③ 组）
- 纯问候/闲聊误入规划通道 → planning 必须为 False、技能必须为空（① 组）
- 明确规划/查询请求 → planning=True 且预选对应技能（② 组）
"""
from pydantic import BaseModel, ConfigDict, Field

class RouterCase(BaseModel):
    """单条路由评测用例（强类型；字段拼错/类型不符在导入期即报错）"""

    model_config = ConfigDict(extra="forbid")

    msg: str
    history: list[str] = Field(default_factory=list)     # 线上格式 "role: content"
    planning: bool
    skills: list[str] = Field(default_factory=list)      # 期望命中的技能任一；空=不检查
    expect_empty_skills: bool = False                    # True=技能必须为空
    note: str

CASES: list[RouterCase] = [
    # ===== ① 闲聊/问候：planning 必须为 False，技能必须为空（expect_empty_skills）=====
    RouterCase(msg="你好呀", planning=False, expect_empty_skills=True, note="纯问候"),
    RouterCase(msg="今天天气不错啊", planning=False, expect_empty_skills=True, note="日常闲聊"),
    RouterCase(msg="你会做什么？", planning=False, expect_empty_skills=True, note="询问能力"),
    # ===== ②a 明确规划：planning=True 且预选规划技能 =====
    RouterCase(msg="帮我规划一次杭州3日游，2个大人", planning=True,
               skills=["itinerary_planning"], note="明确规划请求"),
    # ===== ② 单项查询：技能照常执行，但不进入规划流程（实测路由器语义：planning=False）=====
    RouterCase(msg="查一下明天杭州的天气", planning=False,
               skills=["weather_query"], note="天气单项查询"),
    RouterCase(msg="帮我查上海到杭州的高铁票", planning=False,
               skills=["train_query"], note="高铁单项查询"),
    RouterCase(msg="杭州有哪些酒店推荐", planning=False,
               skills=["hotel_query"], note="酒店单项查询"),
    RouterCase(msg="帮我查上海到北京的航班", planning=False,
               skills=["flight_query"], note="航班单项查询"),
    RouterCase(msg="杭州有什么好吃的推荐", planning=False,
               skills=["food_recommendation"], note="美食单项查询"),
    RouterCase(msg="从杭州东站怎么到西湖", planning=False,
               skills=["map_navigation"], note="导航单项查询"),
    RouterCase(msg="杭州有什么值得去的景点", planning=False,
               skills=["search_destination_guide"], note="攻略单项查询"),
    # ===== ③ 需求收集中的回答（历史 bug 高发区）：有 history 续接必须 planning=True =====
    RouterCase(msg="杭州", history=["assistant: 请问您想去哪里旅游？"],
               planning=True, note="答目的地（曾误判闲聊）"),
    RouterCase(msg="2个大人1个小孩", history=["assistant: 请问几个人出行？"],
               planning=True, note="答人数"),
    RouterCase(msg="预算3000左右", history=["assistant: 请问您的预算是多少？"],
               planning=True, note="答预算"),
    RouterCase(msg="高铁吧", history=["assistant: 您想坐什么交通？", "user: 高铁或自驾犹豫"],
               planning=True, note="选交通方式"),
    RouterCase(msg="特色美食", history=["assistant: 您对哪种餐饮类型感兴趣？"],
               planning=True, note="选餐饮类型"),
    RouterCase(msg="可以，就这样", history=["assistant: 已为您生成杭州3日游行程，是否确认？"],
               planning=True, note="确认行程"),
    RouterCase(msg="把第三天换成西湖", history=["assistant: 已为您生成杭州3日游行程"],
               planning=True, skills=["itinerary_planning"], note="修改行程"),
    # ===== ③负例：有 history 但仍是非规划（防"见 history 就判 planning"过拟合）=====
    RouterCase(msg="哈哈你真有意思", history=["assistant: 你好呀，我是您的旅行助手"],
               planning=False, expect_empty_skills=True, note="有历史仍闲聊"),
    RouterCase(msg="谢谢你", history=["assistant: 祝您旅途愉快！"],
               planning=False, expect_empty_skills=True, note="致谢非规划"),
    # ===== ④ 表单清单场景：一次性完整需求 → planning=True + 规划技能 =====
    RouterCase(msg="我想去苏州旅行，出发地福州，9月20日出发，3天2人，预算人均3000，坐高铁，喜欢文化探索",
               planning=True, skills=["itinerary_planning"], note="表单式完整需求"),
]
