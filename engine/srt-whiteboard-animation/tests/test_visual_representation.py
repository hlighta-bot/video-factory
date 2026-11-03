"""Visual Representation Resolver smoke tests.

Purpose:
    Verify explicit image, local asset, procedural fallback, and determinism
    behavior without touching renderer internals.
"""

from __future__ import annotations

import time
from pathlib import Path

from scene.asset_resolver import DEFAULT_ASSET_ROOTS
from scene.model import Scene
from scene.visual_representation import resolve_visual_representation


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = PROJECT_ROOT / "output" / "phase32-test" / "assets"
ASSETS_ROOT = PROJECT_ROOT / "assets"
LOCAL_ASSET_FILE = FIXTURE_ROOT / "rocket.png"
EXPLICIT_IMAGE_FILE = ASSETS_ROOT / "drawing-hand.png"


def _ensure_local_asset() -> Path:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    if not LOCAL_ASSET_FILE.exists():
        LOCAL_ASSET_FILE.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 12)
    return LOCAL_ASSET_FILE


def _scene(intent: str, visual_plan: dict, duration_ms: int = 9000) -> Scene:
    return Scene(
        id="representation-test",
        renderer="auto",
        visual={
            "kind": "story",
            "requirements": ["illustration"],
            "intent": intent,
            "visual_plan": visual_plan,
        },
        narration="test narration",
        subtitles=[],
        duration_ms=duration_ms,
    )


def main() -> int:
    _ensure_local_asset()

    rocket_scene = _scene(
        "rocket_launch",
        {
            "subject": "rocket",
            "action": "launch",
            "environment": "sky",
            "motion": "upward",
        },
    )

    local_result = resolve_visual_representation(rocket_scene, asset_roots=[FIXTURE_ROOT])
    assert local_result.kind == "local_asset"
    assert local_result.renderer == "whiteboard"
    assert str(Path(local_result.payload["image"])) == str(LOCAL_ASSET_FILE)
    assert local_result.payload["subject"] == "rocket"

    explicit_result = resolve_visual_representation(rocket_scene, asset_roots=[FIXTURE_ROOT], explicit_image=EXPLICIT_IMAGE_FILE)
    assert explicit_result.kind == "explicit_image"
    assert explicit_result.renderer == "whiteboard"
    assert str(Path(explicit_result.payload["image"])) == str(EXPLICIT_IMAGE_FILE)

    unknown_scene = _scene("story_scene", {"subject": "unknown", "action": "show", "environment": "landscape"})
    fallback_result = resolve_visual_representation(unknown_scene, asset_roots=[FIXTURE_ROOT])
    assert fallback_result.kind == "procedural_svg"
    assert fallback_result.renderer == "svg"
    assert fallback_result.payload["template"] == "semantic_story_fallback"
    assert fallback_result.status["fallback_used"] is True
    assert fallback_result.payload["elements"]

    no_environment_scene = _scene("story_scene", {"subject": "unknown", "action": "show"})
    no_environment_result = resolve_visual_representation(no_environment_scene, asset_roots=[FIXTURE_ROOT])
    assert no_environment_result.kind == "procedural_svg"
    assert no_environment_result.payload["slots"] == ["unknown", "story", "show", "story_scene"]

    first = fallback_result.to_dict()
    second = resolve_visual_representation(unknown_scene, asset_roots=[FIXTURE_ROOT]).to_dict()
    assert first == second

    default_roots = DEFAULT_ASSET_ROOTS
    assert isinstance(default_roots, tuple)

    print("representation_resolver=deterministic")
    print(f"local_kind={local_result.kind}")
    print(f"explicit_kind={explicit_result.kind}")
    print(f"fallback_kind={fallback_result.kind}")
    print(f"deterministic={'yes' if first == second else 'no'}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
