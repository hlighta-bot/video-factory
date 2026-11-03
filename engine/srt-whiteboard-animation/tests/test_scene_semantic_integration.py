"""Phase 27 semantic-to-scene integration test.

Purpose:
    Verify that existing Planner behavior preserves deterministic semantic intent
    end-to-end through Scene creation, Visual Plan generation, and Visual Spec adaptation.

Input data:
    - Fixed semantic probe texts

Output data:
    - output/phase27/scenes.json
    - output/phase27/phase27-report.json
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from renderers.selector import select_renderer
from scene.planner import infer_visual_intent, plan_scene
from scene.visual_spec import adapt_whiteboard
from story_schema import SUPPORTED_VERSION, validate_story
from scene.story_adapter import story_to_scenes


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = PROJECT_ROOT / "output" / "phase27"
SCENES_PATH = TEST_ROOT / "scenes.json"
REPORT_PATH = TEST_ROOT / "phase27-report.json"

CASES = [
    ("火箭点火，准备发射", "rocket_launch", "whiteboard"),
    ("y = x²", "quadratic_function", "manim"),
    ("小猫在窗边睡觉", "story_scene", "whiteboard"),
]


def _scene_story() -> dict:
    return validate_story(
        {
            "version": SUPPORTED_VERSION,
            "title": "Phase 27 Semantic Integration",
            "segments": [{"text": text} for text, _, _ in CASES],
        }
    )


def main() -> int:
    scenes = []
    for text, expected_intent, expected_renderer in CASES:
        scene = plan_scene(text, scene_id=f"scene-{len(scenes) + 1:02d}")
        assert infer_visual_intent(text) == expected_intent, (text, scene.visual.get("intent"))
        assert scene.visual["intent"] == expected_intent, scene.to_dict()
        assert select_renderer(scene) == expected_renderer, scene.to_dict()
        assert scene.visual["visual_plan"]["subject"], scene.to_dict()

        spec = adapt_whiteboard(scene)
        assert spec.renderer == "whiteboard", spec.to_dict()
        assert spec.elements, spec.to_dict()
        scenes.append(
            {
                "scene": scene.to_dict(),
                "renderer": select_renderer(scene),
                "visual_spec": spec.to_dict(),
            }
        )

    adapter_scenes = story_to_scenes(_scene_story())
    assert len(adapter_scenes) == len(CASES), [scene.id for scene in adapter_scenes]
    assert [scene.visual["intent"] for scene in adapter_scenes] == [
        expected_intent for _, expected_intent, _ in CASES
    ], [scene.visual.get("intent") for scene in adapter_scenes]

    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    SCENES_PATH.write_text(json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "cases": len(CASES),
        "scenes": len(scenes),
        "adapter_scenes": len(adapter_scenes),
        "deterministic": True,
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"scenes={len(scenes)}")
    print(f"adapter_scenes={len(adapter_scenes)}")
    print(f"scenes_path={SCENES_PATH.relative_to(PROJECT_ROOT)}")
    print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
