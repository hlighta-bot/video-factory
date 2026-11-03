"""Phase 23 Scene Plan Inspector smoke test.

Purpose:
    Verify that Scene Plan inspection is deterministic, readable, and grounded in
    the existing Scene Plan loader instead of a second parser.

Core logic:
    1. Use the Phase 22 artifact when available.
    2. Verify basic inspection fields.
    3. Verify text formatting and deterministic report equality.
    4. Verify empty and invalid Scene Plan behavior through existing loader rules.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from scene.inspector import (
    format_scene_plan_report,
    inspect_scene_plan,
    inspect_scenes,
    save_scene_plan_report,
)
from scene.scene_plan import ScenePlanError, load_scene_plan, save_scene_plan
from scene.story_adapter import story_to_scenes
from story_schema import SUPPORTED_VERSION, validate_story


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHASE_ROOT = PROJECT_ROOT / "output" / "phase23"
PHASE22_SCENES = PROJECT_ROOT / "output" / "phase22" / "scenes.json"
TEXT_REPORT = PHASE_ROOT / "scene-plan-report.txt"
JSON_REPORT = PHASE_ROOT / "scene-plan-report.json"
SUMMARY_REPORT = PHASE_ROOT / "phase23-report.json"


def _fallback_scenes() -> list:
    story = validate_story(
        {
            "version": SUPPORTED_VERSION,
            "title": "Rocket Launch",
            "segments": [
                {"text": "The rocket launches into the sky."},
                {"text": "The rocket rises upward."},
                {"text": "The rocket reaches space."},
            ],
        }
    )
    return story_to_scenes(story)


def _assert_load_error(path: Path, expected: str) -> None:
    try:
        inspect_scene_plan(path)
    except ScenePlanError as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError(f"expected ScenePlanError containing {expected!r}")


def _assert_basic_inspection(report: dict) -> None:
    assert report["version"] == "1.0", report
    assert report["scene_count"] > 0, report
    first = report["scenes"][0]
    assert first["id"], report
    assert first["renderer"], report
    assert first["duration_ms"] > 0, report
    assert isinstance(first["visual_plan"], dict), report
    assert isinstance(first["subtitle_summary"]["count"], int), report


def main() -> int:
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)

    source_plan = PHASE22_SCENES
    if not source_plan.exists():
        source_plan = PHASE_ROOT / "scenes-fallback.json"
        save_scene_plan(_fallback_scenes(), source_plan)

    report_a = inspect_scene_plan(source_plan)
    report_b = inspect_scene_plan(source_plan)
    assert report_a == report_b, "inspection report is not deterministic"
    assert "timestamp" not in json.dumps(report_a, sort_keys=True), report_a
    assert "pid" not in json.dumps(report_a, sort_keys=True), report_a
    _assert_basic_inspection(report_a)

    text = format_scene_plan_report(report_a)
    assert isinstance(text, str) and text, text
    for needle in ("Scene Plan", "Scene count", "ID:", "Renderer:", "Duration:"):
        assert needle in text, text

    text_path, json_path = save_scene_plan_report(report_a, TEXT_REPORT, JSON_REPORT)
    assert text_path.exists() and text_path.read_text(encoding="utf-8") == text
    assert json_path is not None and json_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8")) == report_a

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        empty_path = tmp / "empty.json"
        empty_path.write_text(json.dumps({"version": "1.0", "scenes": []}) + "\n", encoding="utf-8")
        empty_report = inspect_scene_plan(empty_path)
        assert empty_report["scene_count"] == 0, empty_report
        assert "Scene count: 0" in format_scene_plan_report(empty_report)

        bad_json = tmp / "bad.json"
        bad_json.write_text("{ not valid", encoding="utf-8")
        _assert_load_error(bad_json, "JSON is invalid")

        bad_version = tmp / "bad-version.json"
        bad_version.write_text(json.dumps({"version": "2.0", "scenes": []}) + "\n", encoding="utf-8")
        _assert_load_error(bad_version, "unsupported scene plan version")

        bad_scenes = tmp / "bad-scenes.json"
        bad_scenes.write_text(json.dumps({"version": "1.0"}) + "\n", encoding="utf-8")
        _assert_load_error(bad_scenes, "scenes list")

    artifact_plan = load_scene_plan(source_plan)
    summary = {
        "phase": 23,
        "status": "pass",
        "scene_plan": str(source_plan.relative_to(PROJECT_ROOT)),
        "scene_count": report_a["scene_count"],
        "text_report": "output/phase23/scene-plan-report.txt",
        "json_report": "output/phase23/scene-plan-report.json" if json_path else None,
        "inspector": "pass",
        "deterministic": True,
        "warnings": len(report_a["warnings"]),
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    SUMMARY_REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"scene_plan={source_plan.relative_to(PROJECT_ROOT)}")
    print(f"scene_count={report_a['scene_count']}")
    print(f"text_report={TEXT_REPORT.relative_to(PROJECT_ROOT)}")
    print(f"json_report={json_path.relative_to(PROJECT_ROOT)}")
    print(f"report={SUMMARY_REPORT.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
