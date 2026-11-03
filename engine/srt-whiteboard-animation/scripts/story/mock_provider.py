"""Deterministic mock Story Provider."""

from __future__ import annotations

import json

from story_schema import SUPPORTED_VERSION

from .base import StoryProvider


def _normalize_sentence(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return ""
    return cleaned + "." if cleaned[-1] not in ".。！？!?；;" else cleaned


def _clean_segment(text: str) -> str:
    return _normalize_sentence(text)


def _make_title(topic: str) -> str:
    return _clean_segment(topic) or "Untitled Story"


class MockStoryProvider(StoryProvider):
    """Deterministic provider that maps a topic into canonical Story JSON."""

    @property
    def name(self) -> str:
        return "mock"

    def generate(self, topic: str) -> str:
        title = _make_title(topic)
        segment_topic = title.rstrip(".。！？!?；;").strip() or "Untitled Story"
        lower = segment_topic.lower()

        # Visualizability MVP: map known concrete topics to observable story beats.
        # Keep the Canonical Story schema unchanged; only text quality changes.
        if "火箭" in segment_topic or "rocket" in lower or "launch" in lower or "ignition" in lower:
            segments = [
                {"text": "火箭停在发射台上，发射架保持静止。"},
                {"text": "火箭开始点火，尾部喷出一团明亮的火焰。"},
                {"text": "火箭离开发射台，带着尾焰上升。"},
                {"text": "火箭继续飞行，穿过云层后飞向天空。"},
            ]
        elif ("云" in segment_topic or "cloud" in lower) and ("鸟" in segment_topic or "bird" in lower):
            segments = [
                {"text": "清晨，白云漂浮在蓝天上，一只小鸟从云边轻轻掠过。"},
                {"text": "小鸟迎着微风飞翔，阳光在它的翅膀上闪烁。"},
                {"text": "小鸟越过层层山影，身后的云朵被夕阳染成金色。"},
                {"text": "暮色降临，小鸟停在枝头，天空安静下来。"},
            ]
        else:
            segments = [
                {"text": f"关于{segment_topic}的故事，从一个安静的清晨开始。"},
                {"text": "微风吹过，周围的一切渐渐有了生气。"},
                {"text": "旅程继续向前，远处出现了新的风景。"},
                {"text": "太阳慢慢落下，这段旅程留下了温暖的回忆。"},
            ]

        story = {
            "version": SUPPORTED_VERSION,
            "title": title,
            "segments": segments,
        }
        return json.dumps(story, ensure_ascii=False, separators=(",", ":"))
