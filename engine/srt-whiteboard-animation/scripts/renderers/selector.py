"""Deterministic renderer selector for auto scenes."""

from typing import Any

from scene.normalizer import normalize_scene

from .base import SceneLike
from .capabilities import CAPABILITY_PRIORITY, DEFAULT_RENDERER, RendererCapability
from .manim import ManimRenderer
from .svg import SVGRenderer
from .whiteboard import WhiteboardRenderer


def get_renderer_capabilities() -> dict[str, RendererCapability]:
    """Build the first-version capability registry from renderer classes."""
    pairs = (
        ("whiteboard", WhiteboardRenderer()),
        ("svg", SVGRenderer()),
        ("manim", ManimRenderer()),
    )
    return {
        name: RendererCapability(name, renderer.capabilities())
        for name, renderer in pairs
    }


def _scene_visual(scene: SceneLike) -> dict[str, Any]:
    normalized = normalize_scene(scene) if isinstance(scene, dict) else scene
    return normalized.visual or {}


def _scene_request(scene: SceneLike) -> str:
    normalized = normalize_scene(scene) if isinstance(scene, dict) else scene
    return str(normalized.renderer or DEFAULT_RENDERER)


def _capabilities_from_visual(visual: dict[str, Any]) -> set[str]:
    capabilities = {str(visual.get("kind", ""))}
    raw_requirements = visual.get("requirements", [])
    if isinstance(raw_requirements, (list, tuple, set)):
        capabilities.update(str(item) for item in raw_requirements)
    elif raw_requirements:
        capabilities.add(str(raw_requirements))
    return {item for item in capabilities if item}


def select_renderer(scene: SceneLike) -> str:
    """Select a renderer name deterministically.

    Priority:
        explicit renderer -> math/manim -> infographic/svg -> story/whiteboard -> default
    """
    request = _scene_request(scene)
    if request != "auto":
        return request

    visual = _scene_visual(scene)
    requested = _capabilities_from_visual(visual)
    if not requested:
        return DEFAULT_RENDERER

    for capability_set, renderer_name in CAPABILITY_PRIORITY:
        if requested & capability_set:
            return renderer_name

    return DEFAULT_RENDERER
