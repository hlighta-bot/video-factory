"""Phase 16 unified pipeline smoke test.

Purpose:
- Verify the main chain: SRT → Scene Planner → Visual Spec → Asset Resolver → Asset Bridge → Renderer Selector → Renderer → MP4
- Ensure missing annotation fails explicitly through AssetBridgeBlocked
- Render a real E2E MP4 from output/my-rocket/rocket.srt
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from renderers.selector import select_renderer
from scene.asset_bridge import AssetBridgeBlocked, require_ready_bridge
from scene.asset_resolver import resolve_assets
from scene.planner import plan_srt_grouped
from scene.visual_spec import adapt_whiteboard
from scene.whiteboard_bridge import bridge_whiteboard
from parse_srt import parse_srt
from video_pipeline import main as run_pipeline


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_ROOT = PROJECT_ROOT / "output" / "phase16-test"
PIPELINE_SRT = PROJECT_ROOT / "output" / "my-rocket" / "rocket.srt"
PIPELINE_ASSET_ROOT = PROJECT_ROOT / "output" / "my-rocket"
PIPELINE_OUTPUT = PIPELINE_ROOT / "rocket-final.mp4"
PIPELINE_SCENE = PIPELINE_ROOT / "scene.json"
PIPELINE_REPORT = PIPELINE_ROOT / "pipeline-report.json"


def _ffprobe(path: Path) -> dict[str, str]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    data: dict[str, str] = {
        "codec_name": str(stream.get("codec_name", "")),
        "width": str(stream.get("width", "")),
        "height": str(stream.get("height", "")),
        "r_frame_rate": str(stream.get("r_frame_rate", "")),
        "duration": str((payload.get("format") or {}).get("duration", "")),
    }
    return data


def _planned_scene() -> object:
    cues = parse_srt(PIPELINE_SRT.read_text(encoding="utf-8"))
    scenes = plan_srt_grouped(cues, target_duration_ms=8000, min_duration_ms=4000, max_duration_ms=12000)
    assert scenes, "planner produced no scenes"
    return scenes[0]


def main() -> int:
    PIPELINE_ROOT.mkdir(parents=True, exist_ok=True)

    scene = _planned_scene()
    spec = adapt_whiteboard(scene)
    assert spec.renderer == "whiteboard"
    assert select_renderer(scene) == "whiteboard"

    # Missing annotation must remain explicit and cannot be silently synthesized.
    missing_scene = json.loads(json.dumps(scene.to_dict(), ensure_ascii=False))
    missing_resolution = resolve_assets(missing_scene, asset_roots=[PIPELINE_ASSET_ROOT])
    try:
        require_ready_bridge(missing_scene, missing_resolution)
        raise AssertionError("expected AssetBridgeBlocked for missing annotation")
    except AssetBridgeBlocked as exc:
        assert "annotation" in str(exc)

    # Attach the existing local annotation so the direct asset chain is explicit.
    scene.visual["annotation"] = str(PIPELINE_ASSET_ROOT / "rocket.annotation.json")
    scene.visual["image"] = str(PIPELINE_ASSET_ROOT / "rocket.jpg")

    # E2E uses the real pipeline entrypoint with the existing local image/annotation assets.
    run_pipeline(
        str(PIPELINE_SRT),
        str(PIPELINE_ROOT),
        renderer_name="auto",
        image_path=str(PIPELINE_ASSET_ROOT / "rocket.jpg"),
    )

    # Manual direct chain proves all required stages are wired without relying only on pipeline orchestration.
    direct_resolution = resolve_assets(scene, asset_roots=[PIPELINE_ASSET_ROOT])
    direct_bridge = require_ready_bridge(scene, direct_resolution)
    direct_bridge_output = bridge_whiteboard(direct_bridge.scene)
    assert select_renderer(direct_bridge.scene) == "whiteboard"
    assert direct_bridge_output["scene"]["renderer"] == "whiteboard"
    assert direct_bridge_output["scene"]["image"] == str(direct_bridge.scene.visual["image"])
    assert direct_bridge_output["scene"]["visual"]["annotation"] == str(direct_bridge.scene.visual["annotation"])

    assert PIPELINE_SCENE.exists()
    assert PIPELINE_OUTPUT.exists() and PIPELINE_OUTPUT.stat().st_size > 0

    info = _ffprobe(PIPELINE_OUTPUT)
    assert info["codec_name"] == "h264"
    assert info["width"] == "1080"
    assert info["height"] == "1080"
    duration = float(info["duration"])
    assert duration > 0

    report = {
        "srt": str(PIPELINE_SRT),
        "scene": str(PIPELINE_SCENE),
        "asset_root": str(PIPELINE_ASSET_ROOT),
        "resolved_image": str(direct_bridge.scene.visual["image"]),
        "resolved_annotation": str(direct_bridge.scene.visual["annotation"]),
        "output": str(PIPELINE_OUTPUT),
        "renderer": select_renderer(scene),
        "visual_spec": spec.to_dict(),
        "duration_sec": round(duration, 3),
        "codec": info["codec_name"],
        "resolution": f"{info['width']}x{info['height']}",
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    PIPELINE_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"pipeline_scene={PIPELINE_SCENE}")
    print(f"pipeline_output={PIPELINE_OUTPUT}")
    print(f"renderer={select_renderer(scene)}")
    print(f"duration={duration:.3f}")
    print(f"codec={info['codec_name']}")
    print(f"resolution={info['width']}x{info['height']}")
    print(f"report={PIPELINE_REPORT}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
