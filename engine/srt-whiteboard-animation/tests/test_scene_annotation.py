"""Phase 25 Annotation Policy smoke test.

Purpose:
    Verify deterministic annotation reuse and generation behavior without
    changing the existing renderer or asset bridge contract.

Input data:
    - A generated local illustration fixture
    - scene.visual.image and optional scene.visual.annotation

Output data:
    - output/phase25-test/* fixtures and report JSON
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from auto_annotation import generate_annotation
from renderers.router import create_renderer
from scene.annotation import AnnotationPolicyError, ensure_scene_annotation
from scene.asset_bridge import auto_annotate_bridge
from scene.asset_resolver import resolve_assets
from scene.model import Scene
from scene.visual_plan import build_visual_plan
from scene.whiteboard_bridge import bridge_whiteboard


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = PROJECT_ROOT / "output" / "phase25-test"
FIXTURE_ROOT = PROJECT_ROOT / "output" / "phase25-test" / "assets"
PHASE_ROOT = PROJECT_ROOT / "output" / "phase25"
REPORT_PATH = PROJECT_ROOT / "output" / "phase25" / "annotation-policy-report.json"
SOURCE_IMAGE = PROJECT_ROOT / "output" / "my-rocket" / "rocket.jpg"
E2E_IMAGE = FIXTURE_ROOT / "rocket.jpg"
E2E_ENVIRONMENT = FIXTURE_ROOT / "sky.jpg"
E2E_ANNOTATION = FIXTURE_ROOT / "rocket.annotation.json"
E2E_MP4 = PHASE_ROOT / "rocket-e2e.mp4"


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


def _ensure_image_fixture() -> Path:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    target = TEST_ROOT / "rocket.png"
    if target.exists():
        return target
    if SOURCE_IMAGE.exists():
        image = cv2.imread(str(SOURCE_IMAGE), cv2.IMREAD_COLOR)
        if image is not None:
            cv2.imwrite(str(target), image)
            return target
    image = np.full((512, 512, 3), 245, dtype=np.uint8)
    cv2.circle(image, (140, 180), 60, (40, 40, 40), 6)
    cv2.rectangle(image, (300, 100), (430, 360), (40, 40, 40), 6)
    cv2.imwrite(str(target), image)
    return target


def _ensure_e2e_fixture_assets() -> None:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    if not E2E_IMAGE.exists():
        if not SOURCE_IMAGE.exists():
            raise FileNotFoundError(f"Missing E2E source image: {SOURCE_IMAGE}")
        shutil.copyfile(SOURCE_IMAGE, E2E_IMAGE)
    if not E2E_ENVIRONMENT.exists():
        sky = np.full((256, 256, 3), (120, 170, 245), dtype=np.uint8)
        cv2.imwrite(str(E2E_ENVIRONMENT), sky)


def _semantic_scene(intent: str, visual_plan: dict, annotation: str | None = None) -> Scene:
    visual = {
        "kind": "story",
        "requirements": ["illustration"],
        "intent": intent,
        "visual_plan": visual_plan,
    }
    if annotation is not None:
        visual["annotation"] = annotation
    return Scene(
        id="scene-annotation-e2e",
        renderer="auto",
        visual=visual,
        narration="test",
        subtitles=[],
        duration_ms=9000,
    )


def _scene(image: Path, annotation: str | None = None) -> Scene:
    visual = {"image": str(image)}
    if annotation is not None:
        visual["annotation"] = annotation
    return Scene(
        id="scene-annotation-test",
        renderer="whiteboard",
        visual=visual,
        narration="test",
        subtitles=[],
        duration_ms=9300,
    )


def main() -> int:
    image = _ensure_image_fixture()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    first = generate_annotation(
        image,
        output_path=TEST_ROOT / "rocket.annotation.json",
        scene_id="rocket.annotation",
        scene_duration_ms=9300,
    )
    second = generate_annotation(
        image,
        output_path=TEST_ROOT / "rocket.annotation-2.json",
        scene_id="rocket.annotation",
        scene_duration_ms=9300,
    )
    assert first.output_path is not None and second.output_path is not None
    assert first.annotation == second.annotation

    preserved = ensure_scene_annotation(_scene(image, annotation=str(first.output_path)))
    assert preserved.status["mode"] == "preserved"
    assert preserved.scene.visual["annotation"] == str(first.output_path)

    regenerated = ensure_scene_annotation(_scene(image), output_path=TEST_ROOT / "rocket.annotation-force.json", force=True)
    assert regenerated.status["mode"] == "generated"
    assert regenerated.scene.visual["annotation"] == str(TEST_ROOT / "rocket.annotation-force.json")

    try:
        ensure_scene_annotation(_scene(TEST_ROOT / "missing.png"))
    except AnnotationPolicyError as exc:
        assert "image not found" in str(exc)
    else:
        raise AssertionError("expected AnnotationPolicyError")

    if SOURCE_IMAGE.exists():
        _ensure_e2e_fixture_assets()
        e2e_scene = _semantic_scene("rocket_launch", build_visual_plan("rocket_launch", "rocket launch"))
        e2e_resolution = resolve_assets(e2e_scene, asset_roots=[FIXTURE_ROOT])
        e2e_bridge = auto_annotate_bridge(e2e_scene, e2e_resolution)
        assert e2e_bridge.status["ready"] is True
        assert e2e_bridge.status["annotation_status"] in {"resolved", "preserved"}
        assert e2e_bridge.scene.visual["image"] == str(E2E_IMAGE)
        assert e2e_bridge.scene.visual["annotation"] == str(E2E_ANNOTATION)
        assert E2E_ANNOTATION.exists()
        whiteboard_bridge = bridge_whiteboard(e2e_bridge.scene)
        render_scene = Scene.from_dict(whiteboard_bridge["scene"])
        renderer = create_renderer("whiteboard", total_ms=render_scene.duration_ms or 9000)
        generated = renderer.render(render_scene, E2E_MP4)
        assert generated.exists() and generated.stat().st_size > 0
        info = _ffprobe(generated)
        assert info["codec_name"] == "h264"

    report = {
        "phase": 25,
        "status": "pass",
        "image": str(image.relative_to(PROJECT_ROOT)),
        "first_annotation": str(first.output_path.relative_to(PROJECT_ROOT)),
        "second_annotation": str(second.output_path.relative_to(PROJECT_ROOT)),
        "deterministic": True,
        "preserve_existing": True,
        "generate_missing": True,
        "missing_image_rejected": True,
        "bridge_auto_annotate": SOURCE_IMAGE.exists(),
        "whiteboard_render": SOURCE_IMAGE.exists(),
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"deterministic={report['deterministic']}")
    print(f"preserve_existing={report['preserve_existing']}")
    print(f"generate_missing={report['generate_missing']}")
    print(f"bridge_auto_annotate={report['bridge_auto_annotate']}")
    print(f"whiteboard_render={report['whiteboard_render']}")
    print(f"elapsed_sec={report['elapsed_sec']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
