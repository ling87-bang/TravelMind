"""
LLM 工厂
项目里所有需要 ChatModel 的地方都从这里拿。
- get_qwen_llm(): 主对话模型（千问）
- get_deepseek_llm(): 辅助模型（后续章节：行程压缩、JSON 提炼）
"""
from langchain_openai import ChatOpenAI
from app.config import settings


def get_qwen_llm(streaming: bool = True, *, temperature: float | None = None) -> ChatOpenAI:
    """
    获取千问模型（兼容 OpenAI 接口协议）

    参数：
    - streaming: 是否启用流式输出。对话 Agent 建议开；离线 JSON 提炼建议关。

    返回：
    - 已配置好 base_url / api_key / temperature 的 ChatOpenAI 实例
    """
    return ChatOpenAI(
        model=settings.qwen_model_name,
        base_url=settings.qwen_base_url,
        api_key=settings.dashscope_api_key,
        temperature=settings.qwen_temperature if temperature is None else temperature,
        max_tokens=settings.qwen_max_tokens,
        streaming=streaming,
        timeout=settings.qwen_timeout,
        max_retries=settings.qwen_max_retries,
    )


def get_rag_llm() -> ChatOpenAI:
    """检索增强不是关键路径：短超时、不重试，失败由检索管道降级。"""
    return ChatOpenAI(
        model=settings.rag_model_name.strip() or settings.qwen_model_name,
        base_url=settings.qwen_base_url,
        api_key=settings.dashscope_api_key,
        temperature=0,
        max_tokens=1200,
        streaming=False,
        timeout=settings.rag_model_timeout,
        max_retries=0,
    )


def get_deepseek_llm() -> ChatOpenAI:
    """
    获取 DeepSeek 模型（用于后续章节的行程压缩 / 离线 JSON 提炼）
    温度设 0.3，追求稳定输出而非创造性。
    """
    # 辅助模型未单独配置时复用主模型，单个模型账号也能完成清单与摘要。
    if not settings.deepseek_api_key.strip():
        return get_qwen_llm(streaming=False)
    return ChatOpenAI(
        model=settings.deepseek_model_name,
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        temperature=0.3,
        streaming=False,
    )
