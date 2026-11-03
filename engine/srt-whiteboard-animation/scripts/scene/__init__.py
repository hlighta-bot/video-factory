"""Scene Schema 包，提供统一场景数据模型。"""

from .annotation import (
    AnnotationPolicyError,
    AnnotationPolicyResult,
    ensure_scene_annotation,
)
from .asset_bridge import (
    AssetBridgeBlocked,
    AssetBridgeInput,
    auto_annotate_bridge,
    bridge_assets,
    require_ready_bridge,
)
from .asset_library import (
    AssetEntry,
    AssetLibrary,
    AssetLibraryError,
    load_asset_library,
    load_asset_library_dict,
    load_asset_manifest,
    validate_asset_manifest,
)
from .inspector import format_scene_plan_report, inspect_scene_plan, inspect_scenes, save_scene_plan_report
from .model import Scene
from .normalizer import normalize_scene
from .scene_plan import (
    load_scene_plan,
    load_scene_scenes,
    save_scene_plan,
    scene_plan_from_dict,
    scene_plan_to_dict,
    scene_plan_to_json,
)

__all__ = [
    "Scene",
    "normalize_scene",
    "AssetEntry",
    "AssetLibrary",
    "AssetLibraryError",
    "load_asset_library",
    "load_asset_library_dict",
    "load_asset_manifest",
    "validate_asset_manifest",
    "load_scene_plan",
    "load_scene_scenes",
    "save_scene_plan",
    "scene_plan_from_dict",
    "scene_plan_to_dict",
    "scene_plan_to_json",
    "format_scene_plan_report",
    "inspect_scene_plan",
    "inspect_scenes",
    "save_scene_plan_report",
    "AnnotationPolicyError",
    "AnnotationPolicyResult",
    "ensure_scene_annotation",
    "AssetBridgeBlocked",
    "AssetBridgeInput",
    "auto_annotate_bridge",
    "bridge_assets",
    "require_ready_bridge",
]
