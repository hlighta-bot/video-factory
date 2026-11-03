"""Real Gemini Story integration test.

Purpose:
- Skip cleanly when GEMINI_API_KEY is absent.
- Run a real Gemini call through generate_story().
- Validate canonical Story schema and downstream Scene planning.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from scene.story_adapter import story_to_scenes
from story.generator import generate_story
from story.router import create_story_provider
from story_schema import validate_story


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHASE_ROOT = PROJECT_ROOT / "output" / "phase29.2"
STORY_PATH = PHASE_ROOT / "generated-story.json"
REPORT_PATH = PHASE_ROOT / "real-gemini-integration-report.json"
TOPIC = "介绍中国的万里长城"


def _write_report(report: dict) -> None:
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        report = {
            "phase": 29.2,
            "status": "skipped",
            "reason": "GEMINI_API_KEY is not configured",
            "topic": TOPIC,
            "story_valid": False,
            "elapsed_sec": round(time.time() - SCRIPT_START, 3),
        }
        _write_report(report)
        print(f"status={report['status']} reason={report['reason']}")
        print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
        print(f"elapsed_sec={report['elapsed_sec']:.3f}")
        return 0

    try:
        provider = create_story_provider("gemini")
        story = generate_story(TOPIC, provider, provider_name="gemini")
    except Exception as exc:
        report = {
            "phase": 29.2,
            "status": "fail",
            "failure": "real_api_or_generation_error",
            "topic": TOPIC,
            "story_valid": False,
            "error": str(exc),
            "elapsed_sec": round(time.time() - SCRIPT_START, 3),
        }
        _write_report(report)
        print(f"status={report['status']}")
        print(f"error={exc}")
        print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
        print(f"elapsed_sec={report['elapsed_sec']:.3f}")
        return 1

    validate_story(story)
    scenes = story_to_scenes(story)
    STORY_PATH.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "phase": 29.2,
        "status": "pass",
        "provider": "gemini",
        "model": os.getenv("GEMINI_MODEL", ""),
        "topic": TOPIC,
        "story_valid": True,
        "title": story["title"],
        "segments": len(story["segments"]),
        "scene_count": len(scenes),
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    _write_report(report)
    print(f"status={report['status']}")
    print(f"story={STORY_PATH.relative_to(PROJECT_ROOT)}")
    print(f"title={story['title']}")
    print(f"segments={len(story['segments'])}")
    print(f"scene_count={len(scenes)}")
    print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={report['elapsed_sec']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
