"""Phase 24 Asset Library MVP.

Purpose:
    Provide a deterministic local manifest-backed asset index so Visual Spec
    asset IDs can be resolved to actual files through the existing Resolver.

Core logic:
    1. Load and validate manifest.json.
    2. Reject duplicate asset ids and unsafe relative paths.
    3. Resolve asset paths relative to the library root.
    4. Return stable lookup results with explicit missing-file state.

Input data:
    - Manifest JSON path or dict
    - AssetLibrary root directory

Output data:
    - AssetLibrary instance with deterministic lookup results

Non-goals:
    - No asset discovery, generation, search, or network access.
    - No Scene / Visual Spec schema changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_MANIFEST_VERSION = "1.0"
SUPPORTED_ASSET_TYPES: tuple[str, ...] = ("subject", "environment", "object", "character")


class AssetLibraryError(ValueError):
    """Raised when an asset manifest is invalid or unsafe."""


@dataclass(frozen=True)
class AssetEntry:
    """A single asset index entry."""

    id: str
    type: str
    path: Path
    relative_path: str
    exists: bool
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "path": self.relative_path,
            "absolute_path": str(self.path),
            "exists": self.exists,
            "metadata": dict(self.metadata),
        }


class AssetLibrary:
    """Deterministic lookup index over a manifest-backed local asset library."""

    def __init__(self, root: Path, manifest: dict[str, Any], entries: dict[str, AssetEntry]):
        self.root = root
        self.manifest = manifest
        self._entries = entries

    def get(self, asset_id: str, asset_type: str | None = None) -> AssetEntry | None:
        """Return an asset by exact id, optionally filtering by type."""
        entry = self._entries.get(str(asset_id))
        if entry is None:
            return None
        if asset_type is not None and entry.type != asset_type:
            return None
        return entry

    def list_entries(self) -> list[AssetEntry]:
        """Return entries in manifest order."""
        return [self._entries[item["id"]] for item in self.manifest["assets"]]

    def summary(self) -> dict[str, Any]:
        """Return deterministic library summary information."""
        by_type: dict[str, int] = {}
        for entry in self.list_entries():
            by_type[entry.type] = by_type.get(entry.type, 0) + 1
        return {
            "version": self.manifest.get("version"),
            "root": str(self.root),
            "asset_count": len(self._entries),
            "by_type": by_type,
            "missing": sum(1 for entry in self._entries.values() if not entry.exists),
        }


def load_asset_manifest(path: Path | str) -> dict[str, Any]:
    """Load and validate a manifest JSON file."""
    manifest_path = Path(path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssetLibraryError(f"asset manifest JSON is invalid: {exc.msg}") from exc
    return validate_asset_manifest(data, root=manifest_path.parent, manifest_path=manifest_path)


def load_asset_library(manifest_path: Path | str) -> AssetLibrary:
    """Load manifest and build an AssetLibrary index rooted next to the manifest."""
    manifest_path = Path(manifest_path)
    return load_asset_library_dict(
        validate_asset_manifest(json.loads(manifest_path.read_text(encoding="utf-8")), root=manifest_path.parent, manifest_path=manifest_path),
        root=manifest_path.parent,
    )


def load_asset_library_dict(manifest: dict[str, Any], root: Path | str) -> AssetLibrary:
    """Build an AssetLibrary from an already-loaded manifest dict."""
    validated = validate_asset_manifest(manifest, root=root)
    entries: dict[str, AssetEntry] = {}
    for item in validated["assets"]:
        entries[item["id"]] = _build_entry(Path(root), item)
    return AssetLibrary(root=Path(root), manifest=validated, entries=entries)


def validate_asset_manifest(data: Any, root: Path | str | None = None, manifest_path: Path | str | None = None) -> dict[str, Any]:
    """Validate manifest structure and return a normalized copy."""
    if not isinstance(data, dict):
        raise AssetLibraryError("asset manifest must be a JSON object")

    version = data.get("version")
    if not isinstance(version, str) or version.strip() != DEFAULT_MANIFEST_VERSION:
        raise AssetLibraryError(f"unsupported asset manifest version: {version!r}")

    raw_assets = data.get("assets")
    if not isinstance(raw_assets, list):
        raise AssetLibraryError("assets must be a list")

    library_root = Path(root) if root is not None else (Path(manifest_path).parent if manifest_path is not None else Path("."))
    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []

    for index, item in enumerate(raw_assets):
        if not isinstance(item, dict):
            raise AssetLibraryError(f"asset #{index} must be an object")

        asset_id = item.get("id")
        asset_type = item.get("type")
        relative_path = item.get("path")

        if not isinstance(asset_id, str) or not asset_id.strip():
            raise AssetLibraryError(f"asset #{index} missing id")
        if not isinstance(asset_type, str) or not asset_type.strip():
            raise AssetLibraryError(f"asset #{index} missing type")
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise AssetLibraryError(f"asset #{index} missing path")

        asset_id = asset_id.strip()
        asset_type = asset_type.strip()
        if asset_id in seen_ids:
            raise AssetLibraryError(f"duplicate asset id: {asset_id}")
        seen_ids.add(asset_id)

        if asset_type not in SUPPORTED_ASSET_TYPES:
            raise AssetLibraryError(f"unsupported asset type: {asset_type}")

        _validate_relative_path(asset_id, relative_path)
        metadata = item.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise AssetLibraryError(f"asset {asset_id} metadata must be an object")

        normalized.append(
            {
                "id": asset_id,
                "type": asset_type,
                "path": relative_path.strip(),
                "metadata": dict(metadata),
            }
        )

    return {"version": version, "assets": normalized}


def _build_entry(root: Path, item: dict[str, Any]) -> AssetEntry:
    relative = item["path"]
    full_path = (root / relative).resolve()
    return AssetEntry(
        id=item["id"],
        type=item["type"],
        path=full_path,
        relative_path=relative,
        exists=full_path.exists(),
        metadata=item.get("metadata", {}),
    )


def _validate_relative_path(asset_id: str, relative_path: str) -> None:
    cleaned = relative_path.strip().replace("\\", "/")
    if not cleaned:
        raise AssetLibraryError(f"asset {asset_id} path is empty")

    path = PurePosixPath(cleaned)
    if path.is_absolute():
        raise AssetLibraryError(f"asset {asset_id} path must be relative: {relative_path}")

    normalized_parts: list[str] = []
    for part in path.parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            raise AssetLibraryError(f"asset {asset_id} path traversal is not allowed: {relative_path}")
        normalized_parts.append(part)

    if not normalized_parts:
        raise AssetLibraryError(f"asset {asset_id} path resolves to empty location")
