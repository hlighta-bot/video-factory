"""Asset Bridge MVP.

Purpose:
    Wire deterministic local asset resolution into Whiteboard Bridge input
    without generating or fabricating missing assets.

Core logic:
    1. Normalize the incoming scene.
    2. Inject resolved local assets into scene.visual when present.
    3. Preserve existing visual.annotation when available.
    4. Explicitly mark missing asset inputs as blocked.

Input data:
    - Scene object or Scene-like dict
    - AssetResolution from scene.asset_resolver.resolve_assets

Output data:
    - Scene with resolved image/annotation inputs injected when available
    - dict[str, Any] status report with deterministic readiness details

Notes:
    - No renderer logic, no routing, no asset search beyond supplied resolution.
    - The bridge itself remains annotation-preserving; auto-annotation is exposed via a
      thin wrapper so missing scene annotations can be generated before readiness checks.
    - The bridge remains renderer-neutral except for the Whiteboard contract keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .annotation import ensure_scene_annotation
from .asset_resolver import AssetResolution
from .model import Scene
from .normalizer import normalize_scene


class AssetBridgeBlocked(ValueError):
    """Raised when a required asset input is missing from AssetResolution."""


@dataclass(frozen=True)
class AssetBridgeInput:
    """JSON-serializable bridge result for a scene with resolved assets."""

    scene: Scene
    status: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": self.scene.to_dict(),
            "status": dict(self.status),
        }


def _asset_entry(resolution: AssetResolution, role: str) -> dict[str, Any] | None:
    entry = resolution.assets.get(role)
    return dict(entry) if isinstance(entry, dict) else None


def auto_annotate_bridge(scene: Scene | dict, resolution: AssetResolution) -> AssetBridgeInput:
    """Bridge a scene after ensuring a deterministic annotation exists.

    This wrapper preserves the original bridge behavior for resolved assets, then
    applies annotation policy only when a resolved subject image is available.
    """
    base = bridge_assets(scene, resolution)
    if base.status["subject_status"] != "resolved" or not base.scene.visual.get("image"):
        return require_ready_bridge(base.scene, resolution)

    prepared = ensure_scene_annotation(base.scene, scene_duration_ms=None)
    updated_assets = dict(resolution.assets)
    if prepared.output_path is not None:
        updated_assets["annotation"] = {
            "status": "resolved",
            "requested": str(prepared.output_path),
            "path": str(prepared.output_path),
            "exists": True,
            "role": "annotation",
            "mode": prepared.status["mode"],
        }
    updated_resolution = AssetResolution(
        renderer=resolution.renderer,
        assets=updated_assets,
        missing=list(resolution.missing),
    )
    return require_ready_bridge(prepared.scene, updated_resolution)


def bridge_assets(scene: Scene | dict, resolution: AssetResolution) -> AssetBridgeInput:
    """Apply resolved assets to a scene for Whiteboard Bridge consumption."""
    normalized = normalize_scene(scene) if isinstance(scene, dict) else scene
    updated = Scene(
        id=normalized.id,
        renderer=normalized.renderer,
        visual=dict(normalized.visual or {}),
        narration=normalized.narration,
        subtitles=list(normalized.subtitles),
        duration_ms=normalized.duration_ms,
    )

    status: dict[str, Any] = {
        "renderer": resolution.renderer,
        "subject_status": "missing",
        "annotation_status": "missing",
        "image_path": None,
        "annotation_path": None,
        "missing": list(resolution.missing),
        "ready": False,
    }

    subject = _asset_entry(resolution, "subject")
    if subject is not None and subject.get("exists") and subject.get("path"):
        updated.visual["image"] = str(Path(subject["path"]))
        status["subject_status"] = "resolved"
        status["image_path"] = str(Path(subject["path"]))
    else:
        status["subject_status"] = "missing"

    annotation_path = updated.visual.get("annotation")
    if annotation_path:
        updated.visual["annotation"] = str(Path(annotation_path))
        status["annotation_status"] = "preserved"
        status["annotation_path"] = str(Path(annotation_path))
    else:
        status["annotation_status"] = "missing"

    status["ready"] = (
        status["subject_status"] == "resolved"
        and status["annotation_status"] == "preserved"
    )

    return AssetBridgeInput(scene=updated, status=status)


def require_ready_bridge(scene: Scene | dict, resolution: AssetResolution) -> AssetBridgeInput:
    """Bridge assets and fail fast when required Whiteboard inputs are missing."""
    result = bridge_assets(scene, resolution)
    if result.status["ready"]:
        return result

    annotation = _asset_entry(resolution, "annotation")
    if (
        annotation is not None
        and annotation.get("status") == "resolved"
        and annotation.get("exists")
        and annotation.get("path")
    ):
        result.scene.visual["annotation"] = str(Path(annotation["path"]))
        result.status["annotation_status"] = "resolved"
        result.status["annotation_path"] = str(Path(annotation["path"]))
        result.status["ready"] = result.status["subject_status"] == "resolved"
        if result.status["ready"]:
            return result

    blockers = []
    if result.status["subject_status"] != "resolved":
        blockers.append("subject")
    if result.status["annotation_status"] not in {"preserved", "resolved"}:
        blockers.append("annotation")

    raise AssetBridgeBlocked(
        "Asset bridge is not ready; missing required inputs: " + ", ".join(blockers)
    )
