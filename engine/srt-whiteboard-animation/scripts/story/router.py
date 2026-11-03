"""Story Provider 路由，根据名称创建对应 Provider。"""

from __future__ import annotations

from .base import StoryProvider


def create_story_provider(name: str, **kwargs) -> StoryProvider:
    """根据名称创建 Story Provider。

    Args:
        name: Provider 名称，目前支持 "mock"、"openai" 和 "gemini"。
        **kwargs: 传递给 Provider 构造函数的参数。

    Returns:
        StoryProvider 实例。

    Raises:
        ValueError: 不支持的 Provider 名称。
    """
    if name == "mock":
        from .mock_provider import MockStoryProvider

        return MockStoryProvider(**kwargs)
    if name == "openai":
        from .openai_provider import OpenAIChatProvider

        return OpenAIChatProvider(**kwargs)
    if name == "gemini":
        from .gemini_provider import GeminiStoryProvider

        return GeminiStoryProvider(**kwargs)

    raise ValueError(f"Unsupported story provider: {name}")
