"""Deterministic Visual Representation MVP.

Purpose:
    Add a minimal concrete representation layer between Visual Spec and
    renderer selection, so unknown topics can fall back to a generic
    procedural representation instead of failing when local assets are absent.

Core logic:
    1. Normalize the incoming scene.
    2. Build a deterministic Visual Spec with the existing adapter.
    3. Attempt local asset resolution first.
    4. If the subject asset is missing, emit a deterministic procedural fallback.
    5. Prefer explicit image paths above both branches.

Input data:
    - Scene object or Scene-like dict
    - optional asset roots
    - optional explicit image path

Output data:
    - VisualRepresentation dict with kind, renderer, payload, and status

Notes:
    - No semantic asset inference.
    - No topic-specific branching.
    - No fake scene.jpg generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .asset_resolver import resolve_assets
from .model import Scene
from .normalizer import normalize_scene
from .procedural_composition import compose_procedural_scene
from .visual_spec import adapt_whiteboard


@dataclass(frozen=True)
class VisualRepresentation:
    """Renderer-facing concrete visual representation."""

    kind: str
    renderer: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "renderer": self.renderer,
            "payload": dict(self.payload),
            "status": dict(self.status),
        }


def resolve_visual_representation(
    scene: Scene | dict,
    asset_roots: list[str | Path] | None = None,
    explicit_image: str | Path | None = None,
    renderer_preference: str | None = None,
) -> VisualRepresentation:
    """Resolve a concrete renderable representation for a scene.

    Priority:
        explicit image > local asset > procedural fallback
    """
    normalized = normalize_scene(scene) if isinstance(scene, dict) else scene
    visual = normalized.visual or {}
    spec = adapt_whiteboard(normalized)

    if explicit_image is not None:
        image_path = str(Path(explicit_image))
        return VisualRepresentation(
            kind="explicit_image",
            renderer=renderer_preference or (normalized.renderer if normalized.renderer and normalized.renderer != "auto" else "whiteboard"),
            payload={
                "image": image_path,
                "subject": _subject_from_elements(spec.elements),
                "environment": _environment_from_elements(spec.elements),
            },
            status={
                "source": "explicit_image",
                "asset_resolution": None,
                "fallback_used": False,
            },
        )

    resolution = resolve_assets(normalized, asset_roots=asset_roots or [])
    asset_payload = _local_asset_payload(normalized, resolution)

    if asset_payload is not None:
        return VisualRepresentation(
            kind="local_asset",
            renderer=renderer_preference or resolution.renderer,
            payload=asset_payload,
            status={
                "source": "local_asset",
                "asset_resolution": resolution.to_dict(),
                "fallback_used": False,
            },
        )

    fallback_payload = _procedural_payload(normalized, spec)
    return VisualRepresentation(
        kind="procedural_svg",
        renderer="svg",
        payload=fallback_payload,
        status={
            "source": "fallback",
            "asset_resolution": resolution.to_dict(),
            "fallback_used": True,
        },
    )


def _subject_from_elements(elements: list[dict[str, Any]]) -> str:
    for element in elements:
        if element.get("role") == "subject":
            return str(element.get("type", "") or "")
    return ""


def _environment_from_elements(elements: list[dict[str, Any]]) -> str:
    for element in elements:
        if element.get("type") == "environment":
            return str(element.get("value", "") or "")
    return ""


def _local_asset_payload(
    scene: Scene,
    resolution: Any,
) -> dict[str, Any] | None:
    subject = resolution.assets.get("subject")
    if not isinstance(subject, dict) or subject.get("status") != "resolved" or not subject.get("path"):
        return None

    payload: dict[str, Any] = {
        "image": str(subject["path"]),
        "subject": subject.get("requested", _subject_from_elements(adapt_whiteboard(scene).elements)),
    }

    annotation = resolution.assets.get("annotation")
    if isinstance(annotation, dict) and annotation.get("path"):
        payload["annotation"] = str(annotation["path"])

    environment = resolution.assets.get("environment")
    if isinstance(environment, dict) and environment.get("status") == "resolved" and environment.get("path"):
        payload["environment"] = str(environment["path"])

    return payload


def _procedural_payload(scene: Scene, spec: Any) -> dict[str, Any]:
    return compose_procedural_scene(scene, spec).to_dict()
