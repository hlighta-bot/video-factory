"""Renderer Provider 路由，根据名称创建对应 Renderer。"""

from .base import Renderer
from .whiteboard import WhiteboardRenderer


def create_renderer(name: str, **kwargs) -> Renderer:
    """根据名称创建 Renderer。

    Args:
        name: Renderer 名称，目前支持 "whiteboard"。
        **kwargs: 传递给 Renderer 构造函数的参数。

    Returns:
        Renderer 实例。

    Raises:
        ValueError: 不支持的 Renderer 名称。
    """
    if name == "whiteboard":
        return WhiteboardRenderer(**kwargs)

    raise ValueError(f"Unsupported renderer: {name}")
