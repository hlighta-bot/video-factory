"""Deterministic Local Asset Resolver MVP.

Purpose:
    Resolve Visual Spec semantics into local project asset files without
    any search, ranking, generation, or network access.

Core logic:
    1. Normalize the input scene if needed.
    2. Convert the scene into a Visual Spec via the existing whiteboard adapter.
    3. Map the spec's subject and environment values to asset filenames.
    4. Return deterministic resolved / missing results.

Input data:
    - Scene object or Scene-like dict

Output data:
    - dict[str, Any] with resolved assets and missing asset states

Notes:
    - Only local filesystem assets are considered.
    - Only exact, deterministic filename matches are supported.
    - Missing assets are reported explicitly and never silently replaced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .asset_library import AssetLibrary
from .model import Scene
from .normalizer import normalize_scene
from .visual_spec import VisualSpec, adapt_whiteboard


DEFAULT_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".svg")
DEFAULT_ASSET_ROOTS: tuple[str, ...] = ("assets", "output/my-rocket")


@dataclass(frozen=True)
class AssetResolution:
    """Deterministic asset resolution result."""

    renderer: str
    assets: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "renderer": self.renderer,
            "assets": {key: dict(value) for key, value in self.assets.items()},
            "missing": list(self.missing),
        }


def _slug(value: str) -> str:
    """Normalize an asset name into a deterministic filename stem."""
    return (
        str(value)
        .strip()
        .lower()
        .replace("_", "-")
        .replace(" ", "-")
    )


def _first_existing_asset(name: str, asset_roots: list[Path]) -> Path | None:
    raw = str(name).strip().lower()
    if not raw:
        return None

    if raw.endswith(".annotation"):
        base = _slug(raw[: -len(".annotation")])
        for root in asset_roots:
            for ext in (".annotation", ".annotation.json"):
                candidate = root / f"{base}{ext}"
                if candidate.exists():
                    return candidate
        return None

    stem = _slug(raw)
    if not stem:
        return None

    for root in asset_roots:
        for ext in DEFAULT_EXTENSIONS:
            candidate = root / f"{stem}{ext}"
            if candidate.exists():
                return candidate

    return None


def _find_element_value(elements: list[dict[str, Any]], role: str) -> str | None:
    for element in elements:
        if element.get("role") == role:
            return str(element.get("type", "") or "").strip()
    return None


def _find_environment_value(elements: list[dict[str, Any]]) -> str | None:
    for element in elements:
        if element.get("type") == "environment":
            return str(element.get("value", "") or "").strip()
    return None


def _candidate_from_library(requested: str, library: AssetLibrary | None, role: str) -> dict[str, Any] | None:
    if library is None:
        return None

    for candidate_id in (_slug(requested), requested.strip()):
        entry = library.get(candidate_id, asset_type=role)
        if entry is not None:
            status = "resolved" if entry.exists else "missing"
            return {
                "status": status,
                "requested": requested,
                "path": str(entry.path) if entry.exists else None,
                "exists": entry.exists,
                "role": role,
                "library_id": entry.id,
                "library_path": entry.relative_path,
            }
    return None


def resolve_assets(
    scene: Scene | dict,
    asset_roots: list[str | Path] | None = None,
    asset_library: AssetLibrary | None = None,
) -> AssetResolution:
    """Resolve local assets from a Visual Spec deterministically."""
    normalized = normalize_scene(scene) if isinstance(scene, dict) else scene
    spec: VisualSpec = adapt_whiteboard(normalized)
    roots = [Path(root) for root in (asset_roots or DEFAULT_ASSET_ROOTS)]

    subject_value = _find_element_value(spec.elements, "subject") or ""
    requests: list[tuple[str, str]] = [
        ("subject", subject_value),
        ("environment", _find_environment_value(spec.elements) or ""),
    ]

    assets: dict[str, dict[str, Any]] = {}
    missing: list[str] = []

    for role, requested in requests:
        if not requested:
            continue

        library_candidate = _candidate_from_library(requested, asset_library, role)
        if library_candidate is not None:
            assets[role] = library_candidate
            if library_candidate["status"] != "resolved":
                missing.append(requested)
            continue

        candidate = _first_existing_asset(requested, roots)
        if candidate is None:
            assets[role] = {
                "status": "missing",
                "requested": requested,
                "path": None,
                "exists": False,
                "role": role,
            }
            missing.append(requested)
        else:
            assets[role] = {
                "status": "resolved",
                "requested": requested,
                "path": str(candidate),
                "exists": True,
                "role": role,
            }

    if normalized.visual.get("annotation"):
        annotation_path = Path(normalized.visual["annotation"])
        assets["annotation"] = {
            "status": "preserved",
            "requested": str(normalized.visual["annotation"]),
            "path": str(annotation_path),
            "exists": annotation_path.exists(),
            "role": "annotation",
        }
    else:
        assets["annotation"] = {
            "status": "missing",
            "requested": "",
            "path": None,
            "exists": False,
            "role": "annotation",
        }

    return AssetResolution(
        renderer=spec.renderer,
        assets=assets,
        missing=missing,
    )
