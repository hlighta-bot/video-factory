"""TTS Provider 路由，根据名称创建对应 Provider。"""

from .base import TTSProvider
from .edge import EdgeTTSProvider


def create_tts_provider(name: str, **kwargs) -> TTSProvider:
    """根据名称创建 TTS Provider。

    Args:
        name: Provider 名称，目前支持 "edge"。
        **kwargs: 传递给 Provider 构造函数的参数。

    Returns:
        TTSProvider 实例。

    Raises:
        ValueError: 不支持的 Provider 名称。
    """
    if name == "edge":
        return EdgeTTSProvider(**kwargs)

    raise ValueError(f"Unsupported TTS provider: {name}")
