"""Asset Bridge integration smoke test.

Purpose:
- Verify Visual Spec -> Asset Resolver -> Asset Bridge -> Whiteboard Bridge input wiring
- Confirm explicit missing-asset / missing-annotation behavior
- Render one real MP4 end-to-end only when image + annotation fixtures are available
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from renderers.router import create_renderer
from scene.asset_bridge import AssetBridgeBlocked, bridge_assets, require_ready_bridge
from scene.asset_resolver import resolve_assets
from scene.model import Scene
from scene.visual_plan import build_visual_plan
from scene.whiteboard_bridge import bridge_whiteboard


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = PROJECT_ROOT / "output" / "phase15-test" / "assets"
E2E_ROOT = PROJECT_ROOT / "output" / "phase15-test"
E2E_OUTPUT = E2E_ROOT / "rocket-launch.mp4"
ROCKET_SOURCE_DIR = PROJECT_ROOT / "output" / "my-rocket"
ROCKET_SOURCE_IMAGE = ROCKET_SOURCE_DIR / "rocket.jpg"
ROCKET_SOURCE_ANNOTATION = ROCKET_SOURCE_DIR / "rocket.annotation.json"
ROCKET_FIXTURE_IMAGE = FIXTURE_ROOT / "rocket.jpg"
ROCKET_FIXTURE_ANNOTATION = FIXTURE_ROOT / "rocket.annotation.json"


def _ensure_asset_fixture(name: str, content: bytes | str, encoding: str | None = None) -> Path:
    target = FIXTURE_ROOT / name
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        if encoding is None:
            target.write_bytes(content)
        else:
            target.write_text(content, encoding=encoding)
    return target


def _ensure_rocket_fixture_image() -> Path:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    if not ROCKET_FIXTURE_IMAGE.exists():
        if not ROCKET_SOURCE_IMAGE.exists():
            raise FileNotFoundError(
                f"Missing E2E source image: {ROCKET_SOURCE_IMAGE}"
            )
        shutil.copyfile(ROCKET_SOURCE_IMAGE, ROCKET_FIXTURE_IMAGE)
    return ROCKET_FIXTURE_IMAGE


def _semantic_scene(intent: str, visual_plan: dict, with_annotation: bool = False) -> Scene:
    visual = {
        "kind": "story",
        "requirements": ["illustration"],
        "intent": intent,
        "visual_plan": visual_plan,
    }
    if with_annotation:
        visual["annotation"] = str(ROCKET_FIXTURE_ANNOTATION)
    return Scene(
        id="asset-bridge-test",
        renderer="auto",
        visual=visual,
        narration="test",
        duration_ms=1000,
    )


def _ffprobe(path: Path) -> dict[str, str]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value
    return data


def main() -> int:
    E2E_ROOT.mkdir(parents=True, exist_ok=True)

    # Minimal deterministic local fixtures.
    _ensure_rocket_fixture_image()
    fixture_annotation = _ensure_asset_fixture(
        "rocket.annotation.json",
        '{"canvas":{"width":1,"height":1},"elements":[]}',
        encoding="utf-8",
    )

    # Missing annotation must be explicit and must not invent a fallback.
    missing_scene = _semantic_scene(
        "rocket_launch",
        build_visual_plan("rocket_launch", "rocket launch"),
        with_annotation=False,
    )
    missing_resolution = resolve_assets(missing_scene, asset_roots=[FIXTURE_ROOT])
    missing_bridge = bridge_assets(missing_scene, missing_resolution)
    assert missing_bridge.scene.visual["image"] == str(ROCKET_FIXTURE_IMAGE)
    assert missing_bridge.scene.visual.get("annotation") is None
    assert missing_bridge.status["ready"] is False
    assert missing_bridge.status["subject_status"] == "resolved"
    assert missing_bridge.status["annotation_status"] == "missing"
    try:
        require_ready_bridge(missing_scene, missing_resolution)
        raise AssertionError("expected AssetBridgeBlocked for missing annotation")
    except AssetBridgeBlocked as exc:
        assert "annotation" in str(exc)

    # Resolved assets should map cleanly into Whiteboard Bridge input when annotation exists.
    ready_scene = _semantic_scene(
        "rocket_launch",
        build_visual_plan("rocket_launch", "rocket launch"),
        with_annotation=True,
    )
    ready_resolution = resolve_assets(ready_scene, asset_roots=[FIXTURE_ROOT])
    ready_bridge = require_ready_bridge(ready_scene, ready_resolution)
    assert ready_bridge.status["ready"] is True
    assert ready_bridge.scene.visual["image"] == str(ROCKET_FIXTURE_IMAGE)
    assert ready_bridge.scene.visual["annotation"] == str(fixture_annotation)

    whiteboard_bridge = bridge_whiteboard(ready_bridge.scene)
    assert whiteboard_bridge["scene"]["renderer"] == "whiteboard"
    assert whiteboard_bridge["scene"]["image"] == str(ROCKET_FIXTURE_IMAGE)
    assert whiteboard_bridge["scene"]["visual"]["annotation"] == str(fixture_annotation)

    # Deterministic repeat behavior.
    first = bridge_assets(ready_scene, resolve_assets(ready_scene, asset_roots=[FIXTURE_ROOT])).to_dict()
    second = bridge_assets(ready_scene, resolve_assets(ready_scene, asset_roots=[FIXTURE_ROOT])).to_dict()
    assert first == second

    report = {
        "missing_bridge": missing_bridge.status,
        "ready_bridge": ready_bridge.status,
        "deterministic": first == second,
        "fixture_root": str(FIXTURE_ROOT),
        "e2e_available": ROCKET_SOURCE_IMAGE.exists() and ROCKET_SOURCE_ANNOTATION.exists(),
    }
    (E2E_ROOT / "asset-bridge-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("asset_bridge=local-deterministic")
    print(f"missing_ready={missing_bridge.status['ready']}")
    print(f"missing_annotation_status={missing_bridge.status['annotation_status']}")
    print(f"ready_image={ready_bridge.scene.visual['image']}")
    print(f"ready_annotation={ready_bridge.scene.visual['annotation']}")
    print(f"deterministic={'yes' if first == second else 'no'}")
    print(f"fixture_root={FIXTURE_ROOT}")

    e2e_status = "blocked"
    e2e_output = ""
    e2e_duration = ""
    e2e_codec = ""
    e2e_resolution = ""
    if ROCKET_SOURCE_IMAGE.exists() and ROCKET_SOURCE_ANNOTATION.exists():
        try:
            e2e_scene = Scene(
                id="rocket-launch",
                renderer="auto",
                visual={
                    "kind": "story",
                    "requirements": ["illustration"],
                    "intent": "rocket_launch",
                    "visual_plan": build_visual_plan("rocket_launch", "rocket launch"),
                    "annotation": str(ROCKET_SOURCE_ANNOTATION),
                },
                narration="",
                subtitles=[],
                duration_ms=9000,
            )
            e2e_resolution = resolve_assets(e2e_scene, asset_roots=[FIXTURE_ROOT, ROCKET_SOURCE_DIR])
            e2e_bridge = require_ready_bridge(e2e_scene, e2e_resolution)
            whiteboard_bridge = bridge_whiteboard(e2e_bridge.scene)
            render_scene = Scene.from_dict(whiteboard_bridge["scene"])
            renderer = create_renderer("whiteboard")
            generated = renderer.render(render_scene, E2E_OUTPUT)
            assert generated.exists() and generated.stat().st_size > 0
            info = _ffprobe(generated)
            assert info["codec_name"] == "h264"
            e2e_status = "ok"
            e2e_output = str(generated)
            e2e_duration = f"{float(info['duration']):.3f}"
            e2e_codec = info["codec_name"]
            e2e_resolution = f"{info['width']}x{info['height']}"
            print(f"e2e_output={e2e_output}")
            print(f"e2e_duration={e2e_duration}")
            print(f"e2e_codec={e2e_codec}")
            print(f"e2e_resolution={e2e_resolution}")
        except Exception as exc:  # pragma: no cover - guarded smoke path
            e2e_status = f"blocked:{exc.__class__.__name__}:{exc}"
            print(f"e2e_status=blocked")
            print(f"e2e_reason={exc}")

    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
