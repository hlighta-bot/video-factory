"""TTS Provider 路由，根据名称创建对应 Provider。"""

from .base import TTSProvider
from .auto import AutoTTSProvider
from .audio8 import Audio8TTSProvider
from .edge import EdgeTTSProvider
from .espeak import EspeakTTSProvider


def create_tts_provider(name: str, **kwargs) -> TTSProvider:
    """根据名称创建 TTS Provider。

    Args:
        name: Provider 名称，支持 "audio8"、"auto"、"edge" 和 "espeak"。
        **kwargs: 传递给 Provider 构造函数的参数。

    Returns:
        TTSProvider 实例。

    Raises:
        ValueError: 不支持的 Provider 名称。
    """
    if name == "auto":
        return AutoTTSProvider(**kwargs)
    if name == "audio8":
        kwargs = dict(kwargs)
        kwargs["voice"] = kwargs.pop("audio8_voice", "default")
        return Audio8TTSProvider(**kwargs)
    if name == "edge":
        return EdgeTTSProvider(**kwargs)
    if name == "espeak":
        return EspeakTTSProvider(**kwargs)

    raise ValueError(f"Unsupported TTS provider: {name}")
