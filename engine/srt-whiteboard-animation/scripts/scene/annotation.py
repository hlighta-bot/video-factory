"""Phase 25 Annotation Policy MVP.

Purpose:
    Add deterministic reuse-vs-generation policy for scene annotations without
    changing the renderer, asset bridge, or resolver contracts.

Core logic:
    1. Prefer an existing valid scene.visual.annotation.
    2. If missing and an image exists, delegate to auto_annotation.generate_annotation.
    3. Fail explicitly when the image is missing.

Input data:
    - Scene object or dict with visual.image / visual.annotation
    - Optional output path / duration overrides

Output data:
    - Scene with visual.annotation populated when possible
    - AnnotationPolicyResult status dict describing preserve vs generate
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import Scene
from .normalizer import normalize_scene


class AnnotationPolicyError(ValueError):
    """Raised when annotation policy cannot produce a usable annotation."""


@dataclass(frozen=True)
class AnnotationPolicyResult:
    """Result of deterministic annotation policy evaluation."""

    scene: Scene
    status: dict[str, Any]
    output_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": self.scene.to_dict(),
            "status": dict(self.status),
            "output_path": str(self.output_path) if self.output_path is not None else None,
        }


def ensure_scene_annotation(
    scene: Scene | dict,
    *,
    output_path: str | Path | None = None,
    scene_duration_ms: int | None = None,
    force: bool = False,
) -> AnnotationPolicyResult:
    """Ensure a scene has a valid annotation path, reusing existing files by default."""
    normalized = normalize_scene(scene) if isinstance(scene, dict) else scene
    visual = dict(normalized.visual or {})
    image_path = visual.get("image")
    annotation_path = None if force else visual.get("annotation")

    status: dict[str, Any] = {
        "mode": "pending",
        "image_path": str(image_path) if image_path else None,
        "annotation_path": None,
        "generated": False,
    }

    if annotation_path:
        existing = Path(annotation_path)
        if not existing.exists():
            raise AnnotationPolicyError(f"annotation not found: {existing}")
        try:
            data = json.loads(existing.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AnnotationPolicyError(f"annotation invalid JSON: {existing}") from exc
        if not isinstance(data, dict) or "canvas" not in data or "elements" not in data:
            raise AnnotationPolicyError(f"annotation schema invalid: {existing}")

        updated = Scene(
            id=normalized.id,
            renderer=normalized.renderer,
            visual={**visual, "annotation": str(existing)},
            narration=normalized.narration,
            subtitles=list(normalized.subtitles),
            duration_ms=normalized.duration_ms,
        )
        status.update({"mode": "preserved", "annotation_path": str(existing)})
        return AnnotationPolicyResult(scene=updated, status=status, output_path=existing)

    if not image_path:
        raise AnnotationPolicyError("image path is required when annotation is missing")

    image_file = Path(image_path)
    if not image_file.exists():
        raise AnnotationPolicyError(f"image not found: {image_file}")

    from auto_annotation import generate_annotation

    target = Path(output_path) if output_path is not None else image_file.with_name(f"{image_file.stem}.annotation.json")
    resolved_duration = int(scene_duration_ms) if scene_duration_ms is not None else int(normalized.duration_ms or 9000)
    generated = generate_annotation(
        image_file,
        output_path=target,
        scene_id=normalized.id or target.stem,
        scene_duration_ms=resolved_duration,
    )
    if generated.output_path is None or not generated.output_path.exists():
        raise AnnotationPolicyError(f"annotation generation failed: {target}")

    updated = Scene(
        id=normalized.id,
        renderer=normalized.renderer,
        visual={**visual, "annotation": str(generated.output_path)},
        narration=normalized.narration,
        subtitles=list(normalized.subtitles),
        duration_ms=normalized.duration_ms,
    )
    status.update(
        {
            "mode": "generated",
            "annotation_path": str(generated.output_path),
            "generated": True,
        }
    )
    return AnnotationPolicyResult(scene=updated, status=status, output_path=generated.output_path)
