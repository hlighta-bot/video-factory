"""Phase 34A composition inspector smoke tests.

Purpose:
    Validate deterministic read-only inspection, preview generation, and the
    CLI artifact contract without changing production rendering behavior.

Input data:
    - Four semantic cases, a Scene Plan artifact, and a single-scene artifact

Output data:
    - output/phase34a/composition-inspector-report.json
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from scene.composition_inspector import (
    inspect_composition_scene,
    inspect_composition_scene_plan,
    save_composition_report,
)
from inspect_composition import main as inspect_cli
from scene.model import Scene
from scene.planner import plan_scene
from scene.scene_plan import load_scene_plan, scene_plan_from_dict


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = PROJECT_ROOT / "output" / "phase34a" / "composition-inspector-report.json"
SCENE_JSON_PATH = PROJECT_ROOT / "output" / "phase34a" / "scene.json"
SCENE_PLAN_PATH = PROJECT_ROOT / "output" / "phase34a" / "scene-plan.json"
CASES = [
    ("介绍中国的万里长城", "wall", "mountains", "extends", ("mountain", "watchtower")),
    ("介绍日本的富士山", "mountain", "mountain", "show", ("peak",)),
    ("介绍中国的大熊猫", "panda", "forest", "show", ("bamboo",)),
    ("介绍日本的新干线", "train", "railway", "show", ("tracks",)),
]


def _snapshot(scene: Scene) -> dict:
    return scene.to_dict()


def main() -> int:
    results = []
    SCENE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)

    for text, subject, environment, action, objects in CASES:
        scene = plan_scene(text)
        before = _snapshot(scene)
        inspection = inspect_composition_scene(scene)
        after = _snapshot(scene)
        report = inspection.report
        summary = report["summary"]

        assert report["version"] == "1.0"
        assert summary["kind"] == "procedural_svg"
        assert summary["template"] == "semantic_story_fallback"
        assert summary["semantic"]["subject"] == subject
        assert summary["semantic"]["environment"] == environment
        assert summary["semantic"]["action"] == action
        assert set(summary["semantic"]["objects"]) == set(objects)
        assert summary["element_count"] >= 5
        assert set(summary["primitive_counts"]).issubset({"rect", "circle", "text"})
        assert report == inspect_composition_scene(scene).report
        assert before == after

        results.append({
            "text": text,
            "scene_id": report["scene_id"],
            "subject": subject,
            "environment": environment,
            "action": action,
            "objects": list(objects),
            "element_count": summary["element_count"],
            "primitive_counts": summary["primitive_counts"],
            "read_only": before == after,
        })

    wall_scene = plan_scene("介绍中国的万里长城")
    SCENE_JSON_PATH.write_text(
        json.dumps(wall_scene.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    SCENE_PLAN_PATH.write_text(
        json.dumps({
            "version": "1.0",
            "scenes": [wall_scene.to_dict()],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report_json_path = PROJECT_ROOT / "output" / "phase34a" / "composition-report.json"
    report_txt_path = PROJECT_ROOT / "output" / "phase34a" / "composition-report.txt"
    preview_svg_path = PROJECT_ROOT / "output" / "phase34a" / "composition-preview.svg"

    assert inspect_cli([
        str(SCENE_JSON_PATH.relative_to(PROJECT_ROOT)),
        "--out",
        str(report_json_path.relative_to(PROJECT_ROOT)),
        "--text",
        str(report_txt_path.relative_to(PROJECT_ROOT)),
        "--preview",
        str(preview_svg_path.relative_to(PROJECT_ROOT)),
    ]) == 0

    assert report_json_path.exists()
    assert report_txt_path.exists()
    assert preview_svg_path.exists()

    saved_report = json.loads(report_json_path.read_text(encoding="utf-8"))
    saved_text = report_txt_path.read_text(encoding="utf-8")
    saved_svg = preview_svg_path.read_text(encoding="utf-8")
    assert saved_report["summary"]["semantic"]["subject"] == "wall"
    assert saved_text.startswith("Composition Inspector")
    assert saved_svg.lstrip().startswith("<svg")

    plan_scenes = scene_plan_from_dict(load_scene_plan(SCENE_PLAN_PATH))
    plan_report = inspect_composition_scene_plan(SCENE_PLAN_PATH)
    assert plan_report == inspect_composition_scene(plan_scenes[0]).report
    assert plan_report["summary"]["semantic"]["subject"] == "wall"

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps({
            "phase": 34,
            "status": "pass",
            "cases": results,
            "plan_source": str(SCENE_PLAN_PATH),
            "scene_source": str(SCENE_JSON_PATH),
            "artifacts": {
                "report_json": str(report_json_path.relative_to(PROJECT_ROOT)),
                "report_txt": str(report_txt_path.relative_to(PROJECT_ROOT)),
                "preview_svg": str(preview_svg_path.relative_to(PROJECT_ROOT)),
            },
            "elapsed_sec": round(time.time() - SCRIPT_START, 3),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"cases={len(results)}")
    print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
