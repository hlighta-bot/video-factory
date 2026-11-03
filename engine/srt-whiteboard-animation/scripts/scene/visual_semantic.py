"""Deterministic Visual Semantic layer.

Purpose:
    Extract compact semantic structure from narration text so downstream
    VisualPlan / VisualSpec / VisualRepresentation can avoid generic fallback.

Core logic:
    1. Use a small deterministic vocabulary for subjects and environments.
    2. Infer a small object list from concrete supporting nouns.
    3. Infer a conservative action from movement/building/existence cues.
    4. Preserve deterministic output across runs.

Input data:
    - narration or topic-like text

Output data:
    - VisualSemantic dataclass with JSON-serializable dict form
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VisualSemantic:
    """Renderer-agnostic semantic frame extracted from text."""

    subject: str
    environment: str
    action: str
    objects: list[str] = field(default_factory=list)
    source_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "environment": self.environment,
            "action": self.action,
            "objects": list(self.objects),
            "source_text": self.source_text,
        }


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(token.lower() in normalized for token in tokens)


def _pick_subject(text: str) -> str:
    if _contains_any(text, ("长城", "万里长城", "Great Wall", "wall")):
        return "wall"
    if _contains_any(text, ("富士山", "Mount Fuji", "Fuji")):
        return "mountain"
    if _contains_any(text, ("大熊猫", "熊猫", "panda")):
        return "panda"
    if _contains_any(text, ("新干线", "shinkansen", "bullet train")):
        return "train"
    if _contains_any(text, ("火箭", "rocket")):
        return "rocket"
    if _contains_any(text, ("云朵", "云层", "云", "cloud")):
        return "cloud"
    if _contains_any(text, ("小鸟", "鸟", "bird")):
        return "bird"
    if _contains_any(text, ("小船", "帆船", "船只", "船", "boat", "ship", "sailboat")):
        return "boat"
    if _contains_any(text, ("兔子", "rabbit")):
        return "rabbit"
    if _contains_any(text, ("月亮", "moon")):
        return "moon"
    if _contains_any(text, ("人", "孩子", "人物", "person")):
        return "person"
    if _contains_any(text, ("树", "tree")):
        return "tree"
    if _contains_any(text, ("房子", "建筑", "楼", "house", "building")):
        return "building"
    if _contains_any(text, ("山", "mountain")):
        return "mountain"
    return "scene"


def _pick_environment(text: str) -> str:
    if _contains_any(text, ("长城", "万里长城", "Great Wall", "wall")):
        return "mountains"
    if _contains_any(text, ("富士山", "Mount Fuji", "Fuji")):
        return "mountain"
    if _contains_any(text, ("大熊猫", "熊猫", "panda")):
        return "forest"
    if _contains_any(text, ("新干线", "shinkansen", "bullet train")):
        return "railway"
    if _contains_any(text, ("火箭", "rocket")):
        return "sky"
    if _contains_any(text, ("云朵", "云层", "云", "cloud", "sky")):
        return "sky"
    if _contains_any(text, ("鸟", "bird", "天空", "sky")):
        return "sky"
    if _contains_any(text, ("海洋", "海面", "大海", "海", "湖面", "湖", "水面", "ocean", "sea", "lake", "water")):
        return "ocean"
    if _contains_any(text, ("船", "boat", "ship", "sailboat")):
        return "ocean"
    if _contains_any(text, ("月亮", "moon")):
        return "sky"
    if _contains_any(text, ("兔子", "rabbit")):
        return "forest"
    if _contains_any(text, ("树", "tree")):
        return "forest"
    if _contains_any(text, ("房子", "建筑", "楼", "house", "building")):
        return "city"
    if _contains_any(text, ("山", "mountain")):
        return "mountains"
    if _contains_any(text, ("森林", "forest")):
        return "forest"
    if _contains_any(text, ("铁路", "铁路线", "railway")):
        return "railway"
    if _contains_any(text, ("人", "孩子", "人物", "person")):
        return "outdoor"
    return "scene"


def _pick_action(text: str, subject: str) -> str:
    if subject == "wall":
        return "extends"
    if _contains_any(text, ("延伸", "沿着", "extend", "extends", "stretch")):
        return "extends"
    if _contains_any(text, ("排列", "分布", "排列着", "array")):
        return "arranges"
    if _contains_any(text, ("旅行", "旅程", "travel", "journey")):
        return "travels"
    if subject == "panda" and _contains_any(text, ("吃", "啃", "bamboo", "竹")):
        return "eats"
    if subject == "train" and _contains_any(text, ("行驶", "运行", "travel", "move")):
        return "moves"
    if subject == "rocket" and _contains_any(text, ("发射", "升空", "launch")):
        return "launches"
    if subject == "bird" and _contains_any(text, ("飞", "飞行", "飞翔", "fly", "flies", "flying", "soar", "soars")):
        return "flies"
    if subject == "boat" and _contains_any(text, ("航行", "驶过", "漂行", "sail", "sails", "sailing")):
        return "sails"
    if subject == "person":
        if _contains_any(text, ("走", "行走", "walk")):
            return "walks"
        if _contains_any(text, ("跑", "跑步", "run")):
            return "runs"
    if subject == "rabbit":
        if _contains_any(text, ("跳", "蹦", "hop", "jump")):
            return "hops"
    if subject == "tree":
        if _contains_any(text, ("长", "生长", "grow")):
            return "grows"
    if subject == "moon":
        if _contains_any(text, ("发光", "闪耀", "glow", "shine")):
            return "glows"
    return "show"


def _pick_objects(text: str, subject: str) -> list[str]:
    objects: list[str] = []

    def _add(value: str) -> None:
        if value not in objects:
            objects.append(value)

    if subject == "wall":
        _add("mountain")
        _add("watchtower")
    elif subject == "mountain":
        _add("peak")
        if _contains_any(text, ("雪", "snow")):
            _add("snow")
    elif subject == "panda":
        _add("bamboo")
        if _contains_any(text, ("森林", "forest")):
            _add("forest")
    elif subject == "train":
        _add("tracks")
        if _contains_any(text, ("轨道", "rail", "track")):
            _add("tracks")
    elif subject == "rocket":
        _add("flame")
        _add("launch_pad")
    elif subject == "person":
        _add("legs")
        _add("arms")
    elif subject == "tree":
        _add("leaves")
        _add("trunk")
    elif subject == "building":
        _add("windows")
        _add("roof")
    elif subject == "rabbit":
        _add("ears")
        _add("tail")
    elif subject == "moon":
        _add("stars")

    # Preserve a second concrete subject when the narration describes it.
    # The cloud remains the visual anchor, while a bird is composed as a
    # companion object instead of disappearing from the generated scene.
    if subject == "cloud" and _contains_any(text, ("小鸟", "鸟", "bird")):
        _add("bird")

    return objects


def extract_visual_semantic(text: str) -> VisualSemantic:
    """Extract deterministic semantic structure from text."""
    source_text = str(text or "").strip()
    subject = _pick_subject(source_text)
    environment = _pick_environment(source_text)
    action = _pick_action(source_text, subject)
    objects = _pick_objects(source_text, subject)

    return VisualSemantic(
        subject=subject,
        environment=environment,
        action=action,
        objects=objects,
        source_text=source_text,
    )
