"""Phase 27 deterministic semantic intent baseline test.

Purpose:
    Verify that the existing deterministic semantic intent classifier is stable,
    reuses the established intent taxonomy, and emits traceable Phase 27 artifacts.

Input data:
    - A fixed set of representative Chinese and English semantic probe texts

Output data:
    - output/phase27/semantic-report.json
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from scene.planner import infer_visual_intent
from scene.visual_plan import build_visual_plan
from story_schema import SUPPORTED_VERSION, validate_story


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = PROJECT_ROOT / "output" / "phase27"
REPORT_PATH = TEST_ROOT / "semantic-report.json"

CASES = [
    ("rocket_launch", "火箭点火，准备发射"),
    ("rocket_flying", "火箭正在天空中飞行"),
    ("rocket_space", "火箭进入太空"),
    ("quadratic_function", "y = x²"),
    ("population_change", "日本人口正在下降"),
    ("story_scene", "小猫在窗边睡觉"),
]


def _story_from_cases() -> dict:
    return validate_story(
        {
            "version": SUPPORTED_VERSION,
            "title": "Phase 27 Semantic Probe",
            "segments": [{"text": text} for _, text in CASES],
        }
    )


def main() -> int:
    cases = []
    for expected_intent, text in CASES:
        first = infer_visual_intent(text)
        second = infer_visual_intent(text)
        assert first == second, (text, first, second)
        assert first == expected_intent, (text, first, expected_intent)
        plan = build_visual_plan(first, text)
        assert plan["subject"], plan
        cases.append(
            {
                "intent": first,
                "visual_plan": plan,
                "text": text,
            }
        )

    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    report = {
        "cases": cases,
        "story": _story_from_cases(),
        "deterministic": True,
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"cases={len(cases)}")
    print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
