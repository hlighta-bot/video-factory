"""Phase 21 Scene Plan Artifact MVP.

Purpose:
    Persist Scene[] from the existing planner as a stable, auditable JSON artifact.

Core logic:
    1. Reuse Scene.to_dict() as the canonical serialization boundary.
    2. Convert any Path-like fields to strings at that boundary.
    3. Wrap scenes in a minimal versioned artifact format.

Input data:
    - list[Scene]

Output data:
    - Scene Plan dict
    - JSON file content
    - optional loaded dict

Non-goals:
    - No planner redesign
    - No runtime metadata embedding
    - No path normalization beyond string conversion for artifact safety
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scene.model import Scene

SCENE_PLAN_VERSION = "1.0"


class ScenePlanError(ValueError):
    """Raised when Scene Plan artifact content is invalid."""


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def scene_plan_to_dict(scenes: list[Scene]) -> dict[str, Any]:
    """Convert Scene[] into a stable, JSON-serializable Scene Plan dict."""
    if not isinstance(scenes, list) or not all(isinstance(scene, Scene) for scene in scenes):
        raise ScenePlanError("scenes must be a list[Scene]")
    return {
        "version": SCENE_PLAN_VERSION,
        "scenes": [_json_safe(scene.to_dict()) for scene in scenes],
    }


def scene_plan_to_json(scenes: list[Scene], *, indent: int = 2) -> str:
    """Serialize Scene[] into Scene Plan JSON text."""
    return json.dumps(scene_plan_to_dict(scenes), ensure_ascii=False, indent=indent, sort_keys=True) + "\n"


def save_scene_plan(scenes: list[Scene], output_path: Path | str, *, indent: int = 2) -> Path:
    """Write Scene[] as a JSON artifact file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scene_plan_to_json(scenes, indent=indent), encoding="utf-8")
    return path


def load_scene_plan(path: Path | str) -> dict[str, Any]:
    """Load a Scene Plan artifact from JSON."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenePlanError(f"scene plan JSON is invalid: {exc.msg}") from exc
    _validate_scene_plan(data)
    return data


def scene_plan_from_dict(data: dict[str, Any]) -> list[Scene]:
    """Convert a Scene Plan dict back into Scene objects."""
    _validate_scene_plan(data)
    scenes = data["scenes"]
    try:
        return [Scene.from_dict(scene) for scene in scenes]
    except Exception as exc:  # noqa: BLE001 - normalize artifact errors at the boundary
        raise ScenePlanError(f"invalid scene in scene plan: {exc}") from exc


def load_scene_scenes(path: Path | str) -> list[Scene]:
    """Load Scene Plan JSON and restore it as Scene objects."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenePlanError(f"scene plan JSON is invalid: {exc.msg}") from exc
    return scene_plan_from_dict(data)


def _validate_scene_plan(data: Any) -> None:
    if not isinstance(data, dict):
        raise ScenePlanError("scene plan artifact must be a JSON object")
    if data.get("version") != SCENE_PLAN_VERSION:
        raise ScenePlanError(f"unsupported scene plan version: {data.get('version')!r}")
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        raise ScenePlanError("scene plan artifact must include a scenes list")
