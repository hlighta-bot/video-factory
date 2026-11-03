"""统一 Scene Schema，支持多种 Renderer。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Scene:
    """统一 Scene 数据结构。

    字段说明：
        id: 场景唯一标识。
        renderer: 渲染器名称 (如 "whiteboard")。
        visual: 渲染器特定的视觉数据。
        narration: 场景旁白文本。
        subtitles: 字幕列表，每项含 id/start/end/text。
        duration_ms: 场景总时长（毫秒）。

    Whiteboard 示例 visual:
        {"image": "...", "annotation": "..."}

    未来 Manim 示例 visual:
        {"script": "...", "animation": "..."}
    """

    id: str
    renderer: str = "whiteboard"
    visual: dict[str, Any] = field(default_factory=dict)
    narration: str = ""
    subtitles: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def subtitle_count(self) -> int:
        """返回字幕数量，方便 planner 和测试直接校验。"""
        return len(self.subtitles)

    def to_dict(self) -> dict[str, Any]:
        """转换为旧格式 JSON 兼容的 dict。

        输出字段映射：
            id → sceneId
            duration_ms → 保留
            narration → 保留
            subtitles → 保留
            renderer → 保留
            visual → 保留
            image/annotation/srt/subtitleCount → 从 visual/subtitles 展开
        """
        # 展开 visual 中的 image 路径到顶层
        image = self.visual.get("image")
        srt = self.visual.get("srt")

        # 计算 duration_ms：若未显式设置，从字幕推算
        if self.duration_ms == 0 and self.subtitles:
            last = self.subtitles[-1].get("end", "00:00:00,000")
            try:
                parts = last.split(",")[0].split(":")
                self.duration_ms = (
                    int(parts[0]) * 3600000
                    + int(parts[1]) * 60000
                    + int(parts[2]) * 1000
                )
            except (ValueError, IndexError):
                pass

        data: dict[str, Any] = {
            "sceneId": self.id,
            "renderer": self.renderer,
            "duration_ms": self.duration_ms,
            "narration": self.narration,
            "subtitles": self.subtitles,
            "subtitleCount": len(self.subtitles),
        }

        if image:
            data["image"] = image
        if srt:
            data["srt"] = srt

        # 保留 visual 中除 image/srt 外的渲染器特定字段
        remaining = {
            k: v for k, v in self.visual.items()
            if k not in ("image", "srt")
        }
        if remaining:
            data["visual"] = remaining

        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scene":
        """从 dict 创建 Scene 实例。

        兼容旧格式（sceneId/image/srt/subtitles/subtitleCount）
        和新格式（id/renderer/duration_ms/narration/subtitles/visual）。
        """
        visual: dict[str, Any] = {}

        # 从旧格式提取 visual 字段
        if "image" in data:
            visual["image"] = data["image"]
        if "srt" in data:
            visual["srt"] = data["srt"]
        if "annotation" in data:
            visual["annotation"] = data["annotation"]

        # 从新格式提取 visual
        if "visual" in data and isinstance(data["visual"], dict):
            visual.update(data["visual"])

        return cls(
            id=data.get("id", data.get("sceneId", "")),
            renderer=data.get("renderer", "whiteboard"),
            visual=visual,
            narration=data.get("narration", ""),
            subtitles=data.get("subtitles", []),
            duration_ms=data.get("duration_ms", 0),
        )
