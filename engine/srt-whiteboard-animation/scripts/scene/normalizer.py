"""Scene Normalizer，兼容旧格式和新格式。"""

from .model import Scene


def normalize_scene(data: dict) -> Scene:
    """将旧格式或新格式 Scene 字典统一转换为 Scene 对象。

    旧格式示例：
        {"sceneId": "...", "image": "...", "srt": "...",
         "subtitles": [...], "subtitleCount": 3}

    新格式示例：
        {"id": "...", "renderer": "whiteboard", "duration_ms": 9300,
         "narration": "...", "subtitles": [...],
         "visual": {"image": "...", "annotation": "..."}}

    Args:
        data: 旧格式或新格式的 Scene 字典。

    Returns:
        统一的 Scene 对象。
    """
    # 检测格式：旧格式用 "sceneId"，新格式用 "id"
    if "sceneId" in data:
        # 旧格式 → 使用 from_dict 自动映射
        return Scene.from_dict(data)

    # 新格式 → 直接使用 from_dict
    return Scene.from_dict(data)
