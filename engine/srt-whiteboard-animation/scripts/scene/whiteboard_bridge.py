"""Whiteboard Bridge MVP.

Purpose:
    Bridge semantic Visual Spec into the existing Whiteboard Renderer input
    contract without changing renderer behavior.

Core logic:
    1. Accept a Scene-like object.
    2. Reuse existing scene.visual assets (image / annotation).
    3. Normalize to a Scene and return the concrete render input.

Input data:
    - Scene object or Scene-like dict
    - optional scene.visual.image and scene.visual.annotation paths

Output data:
    - Scene with renderer and concrete visual asset paths

Notes:
    - No renderer algorithm duplication.
    - No annotation generation logic in this file.
    - If annotation is missing, the caller must provide it or run auto_annotation first.
"""

from __future__ import annotations

from pathlib import Path

from .model import Scene
from .normalizer import normalize_scene
from .visual_spec import VisualSpec, adapt_whiteboard


class WhiteboardBridgeInput(dict):
    """JSON-serializable bridge output wrapper."""

    pass


def bridge_whiteboard(scene: Scene | dict) -> WhiteboardBridgeInput:
    """Convert a semantic scene into Whiteboard Renderer input."""
    normalized = normalize_scene(scene) if isinstance(scene, dict) else scene

    visual = normalized.visual or {}
    image = visual.get("image")
    annotation = visual.get("annotation")

    if not image:
        raise ValueError("Whiteboard bridge requires visual.image")
    if not annotation:
        raise ValueError("Whiteboard bridge requires visual.annotation")

    render_scene = Scene(
        id=normalized.id,
        renderer="whiteboard",
        visual={
            "image": str(Path(image)),
            "annotation": str(Path(annotation)),
        },
        narration=normalized.narration,
        subtitles=normalized.subtitles,
        duration_ms=normalized.duration_ms,
    )

    return WhiteboardBridgeInput(
        scene=render_scene.to_dict(),
        spec=adapt_whiteboard(render_scene).to_dict(),
    )
