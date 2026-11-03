"""Phase 31 Automatic Visual Asset Resolution smoke test.

Purpose:
    Verify deterministic no-`--image` asset resolution for the Story pipeline.

Core logic:
    1. Plan a Scene from SRT text.
    2. Resolve local assets from Visual Spec without relying on a manual image argument.
    3. Verify subject is required, environment may be missing, and annotation can be auto-generated.

Input data:
    - Rocket subject fixture and optional existing annotation fixture

Output data:
    - Generated annotation artifacts under output/phase31/assets

Notes:
    - This test is intentionally focused on resolver/bridge semantics only.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from parse_srt import parse_srt as parse_srt_cues
from scene.asset_bridge import AssetBridgeBlocked, auto_annotate_bridge, require_ready_bridge
from scene.asset_resolver import resolve_assets
from scene.planner import plan_srt_grouped
from scene.whiteboard_bridge import bridge_whiteboard


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHASE_ROOT = PROJECT_ROOT / "output" / "phase31"
FIXTURE_ROOT = PHASE_ROOT / "assets"
SOURCE_IMAGE = PROJECT_ROOT / "output" / "my-rocket" / "rocket.jpg"
SOURCE_ANNOTATION = PROJECT_ROOT / "output" / "my-rocket" / "rocket.annotation.json"
REPORT_PATH = PHASE_ROOT / "asset-auto-resolution-report.json"
SRT_TEXT = "1\n00:00:00,000 --> 00:00:05,000\nThe rocket ignites and launches into the sky.\n\n2\n00:00:05,000 --> 00:00:08,000\nThe rocket rises upward.\n"


def _ensure_fixture() -> Path:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    target = FIXTURE_ROOT / "rocket.jpg"
    if not target.exists():
        if not SOURCE_IMAGE.exists():
            raise FileNotFoundError(f"Missing source image: {SOURCE_IMAGE}")
        shutil.copyfile(SOURCE_IMAGE, target)
    return target


def _report(**kwargs) -> dict:
    base = {
        "phase": 31,
        "status": "pass",
        "fixture_root": str(FIXTURE_ROOT),
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    base.update(kwargs)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    return base


def main() -> int:
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    _ensure_fixture()

    cues = parse_srt_cues(SRT_TEXT)
    scenes = plan_srt_grouped(cues, target_duration_ms=8000, min_duration_ms=4000, max_duration_ms=12000)
    assert scenes, "planner produced no scenes"
    scene = scenes[0]
    scene.visual["srt"] = str(PHASE_ROOT / "auto.story.srt")

    missing_environment_resolution = resolve_assets(scene, asset_roots=[FIXTURE_ROOT])
    assert missing_environment_resolution.assets["subject"]["status"] == "resolved"
    assert missing_environment_resolution.assets["subject"]["path"] == str(FIXTURE_ROOT / "rocket.jpg")
    assert missing_environment_resolution.assets["environment"]["status"] == "missing"
    assert "sky" in missing_environment_resolution.missing

    try:
        require_ready_bridge(scene, missing_environment_resolution)
        raise AssertionError("expected AssetBridgeBlocked for missing annotation")
    except AssetBridgeBlocked as exc:
        assert "annotation" in str(exc)

    bridged = auto_annotate_bridge(scene, missing_environment_resolution)
    assert bridged.status["ready"] is True, bridged.to_dict()
    assert bridged.status["subject_status"] == "resolved"
    assert bridged.status["annotation_status"] in {"resolved", "preserved"}
    assert Path(bridged.scene.visual["annotation"]).exists()

    missing_subject_resolution = resolve_assets(
        {"id": scene.id, "renderer": "auto", "visual": {"intent": "rocket_launch", "visual_plan": {"subject": "missing_subject", "environment": "sky"}}},
        asset_roots=[FIXTURE_ROOT],
    )
    try:
        auto_annotate_bridge(scene, missing_subject_resolution)
        raise AssertionError("expected AssetBridgeBlocked for missing subject")
    except AssetBridgeBlocked as exc:
        assert "subject" in str(exc)

    whiteboard_bridge = bridge_whiteboard(bridged.scene)
    assert whiteboard_bridge["scene"]["renderer"] == "whiteboard"
    assert Path(whiteboard_bridge["scene"]["image"]).exists()
    assert Path(whiteboard_bridge["scene"]["visual"]["annotation"]).exists()

    first = resolve_assets(scene, asset_roots=[FIXTURE_ROOT]).to_dict()
    second = resolve_assets(scene, asset_roots=[FIXTURE_ROOT]).to_dict()
    assert first == second

    report = _report(
        resolved_subject=str(bridged.scene.visual["image"]),
        resolved_annotation=str(bridged.scene.visual["annotation"]),
        environment_status=missing_environment_resolution.assets["environment"]["status"],
        deterministic=first == second,
        whiteboard_ready=True,
    )
    print(f"status={report['status']}")
    print(f"resolved_subject={report['resolved_subject']}")
    print(f"resolved_annotation={report['resolved_annotation']}")
    print(f"environment_status={report['environment_status']}")
    print(f"deterministic={report['deterministic']}")
    print(f"elapsed_sec={report['elapsed_sec']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
