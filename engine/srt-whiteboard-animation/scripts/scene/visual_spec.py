"""Deterministic Visual Spec and Whiteboard Adapter MVP.

Purpose:
    Convert a renderer-agnostic Visual Plan into a renderer-specific
    Visual Spec without touching renderer implementation details.

Core logic:
    1. Normalize Scene-like input into Scene.
    2. Read visual.intent and visual.visual_plan.
    3. Map supported Whiteboard intents to stable element lists.
    4. Fall back to story_scene semantics for unknown intents.

Input data:
    - Scene object or Scene-like dict

Output data:
    - VisualSpec with renderer name and semantic elements

Notes:
    - No renderer-specific coordinates, code, or pixel geometry is emitted.
    - Visual Plan remains renderer-independent and selector-neutral.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import Scene
from .normalizer import normalize_scene


@dataclass(frozen=True)
class VisualSpec:
    """Renderer-specific semantic visual specification."""

    renderer: str
    elements: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "renderer": self.renderer,
            "elements": [dict(element) for element in self.elements],
        }


DEFAULT_WHITEBOARD_ELEMENTS: list[dict[str, str]] = [
    {"type": "scene", "role": "subject"},
    {"type": "environment", "value": "story"},
]


def _elements_from_plan(visual_plan: dict[str, Any]) -> list[dict[str, Any]]:
    semantic = visual_plan.get("semantic") or {}
    plan_subject = str(visual_plan.get("subject", "scene") or "scene")
    semantic_subject = str(semantic.get("subject", plan_subject) or plan_subject)
    subject = semantic_subject if plan_subject == "scene" else plan_subject
    plan_environment = str(visual_plan.get("environment", "story") or "story")
    semantic_environment = str(semantic.get("environment", plan_environment) or plan_environment)
    environment = semantic_environment if plan_environment == "story" else plan_environment
    motion = visual_plan.get("motion")
    plan_action = str(visual_plan.get("action", "show") or "show")
    semantic_action = semantic.get("action")
    action = semantic_action if plan_action == "show" else plan_action
    action = action or plan_action
    objects = semantic.get("objects") or []

    elements: list[dict[str, Any]] = [
        {"type": subject, "role": "subject"},
        {"type": "environment", "value": environment},
    ]

    if motion:
        elements.append({"type": "motion", "direction": str(motion)})
    if action:
        elements.append({"type": "action", "value": str(action)})

    for item in objects:
        elements.append({"type": str(item), "role": "object"})

    focus = visual_plan.get("focus")
    if focus:
        elements.append({"type": "focus", "value": str(focus)})

    formula = visual_plan.get("formula")
    if formula:
        elements.append({"type": "formula", "value": str(formula)})

    return elements


def adapt_whiteboard(scene: Scene | dict) -> VisualSpec:
    """Adapt a Scene to a Whiteboard Visual Spec."""
    normalized = normalize_scene(scene) if isinstance(scene, dict) else scene
    visual = normalized.visual or {}
    visual_plan = visual.get("visual_plan") or {}
    intent = str(visual.get("intent", "story_scene") or "story_scene")

    elements = _elements_from_plan(visual_plan)

    if intent == "story_scene" and not visual_plan:
        elements = DEFAULT_WHITEBOARD_ELEMENTS

    return VisualSpec(renderer="whiteboard", elements=elements)
