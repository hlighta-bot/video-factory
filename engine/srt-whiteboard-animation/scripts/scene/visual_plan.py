"""Deterministic Visual Plan layer.

Purpose:
    Convert a visual intent and extracted VisualSemantic into a small,
    renderer-agnostic Visual Plan that describes WHAT should be shown.

Core logic:
    1. Map known intents to fixed subject/action/environment/motion/focus fields.
    2. Preserve the optional formula semantic for quadratic text when present.
    3. Use deterministic VisualSemantic as the runtime input layer.
    4. Fall back to a stable story plan for unknown intents.

Input data:
    - visual intent string
    - optional source text for lightweight semantic enrichment
    - optional precomputed VisualSemantic or serialized semantic dict

Output data:
    - dict[str, Any] visual plan

Notes:
    - The plan is intentionally shallow and JSON-serializable.
    - It is renderer-agnostic and must not contain drawing parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .visual_semantic import VisualSemantic, extract_visual_semantic


@dataclass(frozen=True)
class VisualPlan:
    """Renderer-agnostic WHAT-to-show plan."""

    subject: str
    action: str
    environment: str
    motion: str | None = None
    focus: str | None = None
    formula: str | None = None
    objects: list[str] = field(default_factory=list)


DEFAULT_PLAN = VisualPlan(
    subject="scene",
    action="show",
    environment="story",
    motion=None,
    focus="narrative",
)

PLANS_BY_INTENT: dict[str, VisualPlan] = {
    "rocket_launch": VisualPlan(
        subject="rocket",
        action="launch",
        environment="sky",
        motion="upward",
    ),
    "rocket_flying": VisualPlan(
        subject="rocket",
        action="fly",
        environment="sky",
        motion="forward",
    ),
    "rocket_space": VisualPlan(
        subject="rocket",
        action="travel",
        environment="space",
        motion="forward",
    ),
    "quadratic_function": VisualPlan(
        subject="parabola",
        action="show",
        environment="coordinate_plane",
        focus="quadratic_function",
    ),
    "geometry": VisualPlan(
        subject="geometric_shape",
        action="show",
        environment="coordinate_plane",
        focus="geometry",
    ),
    "physics": VisualPlan(
        subject="physical_system",
        action="explain",
        environment="diagram",
        focus="physics",
    ),
    "population_change": VisualPlan(
        subject="population",
        action="compare",
        environment="chart",
        focus="population_change",
    ),
    "data_change": VisualPlan(
        subject="data",
        action="compare",
        environment="chart",
        focus="change",
    ),
    "ratio_comparison": VisualPlan(
        subject="data",
        action="compare",
        environment="chart",
        focus="ratio",
    ),
    "story_scene": DEFAULT_PLAN,
}


def _quote_formula(text: str) -> str | None:
    """Extract a simple explicit formula marker when present."""
    lowered = text.lower()
    for marker in ("y=x²", "y = x²", "y=x^2", "y = x^2"):
        if marker in lowered:
            return "x²" if "²" in marker else "x^2"
    return None


def build_visual_plan(intent: str, text: str = "", semantic: VisualSemantic | dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a deterministic, renderer-agnostic visual plan."""
    plan = PLANS_BY_INTENT.get(intent, DEFAULT_PLAN)
    semantic_value = semantic if semantic is not None else extract_visual_semantic(text)
    if not isinstance(semantic_value, VisualSemantic):
        semantic_value = _semantic_from_dict(semantic_value)

    if intent == "quadratic_function":
        formula = _quote_formula(text)
        if formula is not None:
            return {
                "subject": plan.subject,
                "action": plan.action,
                "environment": plan.environment,
                "focus": plan.focus or "",
                "formula": formula,
                "semantic": semantic_value.to_dict(),
            }

    payload: dict[str, Any] = {
        "subject": plan.subject,
        "action": plan.action,
        "environment": plan.environment,
        "semantic": semantic_value.to_dict(),
    }
    if plan.motion:
        payload["motion"] = plan.motion
    if plan.focus:
        payload["focus"] = plan.focus
    return payload


def plan_visual(scene: Any) -> dict[str, Any]:
    """Attach a visual plan to an existing scene-like object if supported."""
    intent = getattr(scene, "visual", {}).get("intent", "story_scene")
    visual = getattr(scene, "visual", {}) or {}
    semantic = visual.get("semantic") if isinstance(visual, dict) else None
    return build_visual_plan(intent, getattr(scene, "narration", ""), semantic=semantic)


def _semantic_from_dict(data: Any) -> VisualSemantic:
    if not isinstance(data, dict):
        return VisualSemantic(subject="scene", environment="story", action="show")
    return VisualSemantic(
        subject=str(data.get("subject", "scene") or "scene"),
        environment=str(data.get("environment", "story") or "story"),
        action=str(data.get("action", "show") or "show"),
        objects=[str(item) for item in data.get("objects", []) or []],
        source_text=str(data.get("source_text", "") or ""),
    )
