"""Phase 28 visualizability smoke test for Story generation."""

from __future__ import annotations

import json
import time
from pathlib import Path

from scene.story_adapter import story_to_scenes
from story.generator import generate_story
from story.router import create_story_provider
from story_schema import validate_story


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = PROJECT_ROOT / "output" / "phase28" / "visualizability-report.json"

ROCKET_TOPIC = "火箭点火"
GENERIC_TOPIC = "云朵旅行"
EXPECTED_ROCKET_INTENTS = ["rocket_launch", "rocket_launch", "rocket_launch", "rocket_flying"]


def main() -> int:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    rocket_story = generate_story(ROCKET_TOPIC, create_story_provider("mock"), provider_name="mock")
    generic_story = generate_story(GENERIC_TOPIC, create_story_provider("mock"), provider_name="mock")
    rocket_again = generate_story(ROCKET_TOPIC, create_story_provider("mock"), provider_name="mock")

    validate_story(rocket_story)
    validate_story(generic_story)
    assert rocket_story == rocket_again, "mock story must stay deterministic"
    assert rocket_story["title"] == "火箭点火."
    assert generic_story["title"] == "云朵旅行."

    rocket_texts = [segment["text"] for segment in rocket_story["segments"]]
    assert len(rocket_texts) == 4
    assert rocket_texts == [
        "火箭停在发射台上，发射架保持静止。",
        "火箭开始点火，尾部喷出一团明亮的火焰。",
        "火箭离开发射台，带着尾焰上升。",
        "火箭继续飞行，穿过云层后飞向天空。",
    ]
    assert not any("火箭点火." in text for text in rocket_texts)
    assert not any("我们今天要聊的是" in text for text in rocket_texts)
    assert not any(text.startswith("最后，") for text in rocket_texts)

    generic_texts = [segment["text"] for segment in generic_story["segments"]]
    assert len(generic_texts) == 4
    assert all("云朵旅行." not in text for text in generic_texts)
    assert "中央" in generic_texts[0]
    assert "轨迹" in generic_texts[1]
    assert "细节" in generic_texts[2]
    assert "最终位置" in generic_texts[3]

    scenes = story_to_scenes(rocket_story)
    assert len(scenes) == 4
    intents = [scene.visual.get("intent") for scene in scenes]
    assert intents == EXPECTED_ROCKET_INTENTS
    assert set(intents).issubset({"rocket_launch", "rocket_flying"})
    assert not any(intent == "story_scene" for intent in intents)

    generic_scenes = story_to_scenes(generic_story)
    assert len(generic_scenes) == 4
    generic_intents = [scene.visual.get("intent") for scene in generic_scenes]
    assert all(intent for intent in generic_intents)

    report = {
        "phase": 28,
        "status": "pass",
        "rocket_story": rocket_story,
        "generic_story": generic_story,
        "rocket_intents": intents,
        "generic_intents": generic_intents,
        "scene_count": len(scenes),
        "generic_scene_count": len(generic_scenes),
        "deterministic": rocket_story == rocket_again,
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"rocket_segments={len(rocket_texts)}")
    print(f"generic_segments={len(generic_texts)}")
    print(f"rocket_intents={','.join(intents)}")
    print(f"generic_intents={','.join(generic_intents)}")
    print(f"scene_count={len(scenes)}")
    print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={round(time.time() - SCRIPT_START, 3):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
