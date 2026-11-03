"""Phase 26 LLM Story Generator MVP smoke test.

Purpose:
- Verify deterministic mock provider behavior.
- Verify provider/router and env-based provider construction pattern.
- Verify generator boundary uses strict validate_story().
- Verify invalid JSON / invalid canonical story fail explicitly.
- Verify generated story adapts to Scene[] and can flow into downstream tests.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import story
from story.generator import generate_story, StoryGenerationError
from story.router import create_story_provider
from story_schema import StoryValidationError, validate_story
from scene.story_adapter import story_to_scenes


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = PROJECT_ROOT / "output" / "phase26-test"
REPORT = PROJECT_ROOT / "output" / "phase26" / "story-generator-report.json"


def _provider_for_topic(topic: str):
    return story.create_story_provider("mock")


def _generate(topic: str):
    return generate_story(topic, story.create_story_provider("mock"))


def main() -> int:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)

    story_path = TEST_ROOT / "rocket-story.json"
    story_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "title": "Rocket Launch",
                "segments": [{"text": "火箭已经准备就绪。"}, {"text": "火箭升空。"}],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    story_from_file = json.loads(story_path.read_text(encoding="utf-8"))
    validate_story(story_from_file)

    generated = _generate("Rocket Launch")
    generated_again = _generate("Rocket Launch")
    assert generated == generated_again, "mock provider should be deterministic"
    assert generated["version"] == "1.0"
    assert generated["title"] == "Rocket Launch."
    assert len(generated["segments"]) == 4
    assert all("Rocket Launch." in segment["text"] or segment["text"] for segment in generated["segments"])

    adapter_scenes = story_to_scenes(generated)
    assert len(adapter_scenes) >= 1

    provider = _provider_for_topic("Rocket Launch")
    assert provider.name == "mock"
    assert story.create_story_provider("openai") is not None

    os.environ["OPENAI_API_KEY"] = "test-key"
    os.environ["OPENAI_BASE_URL"] = "https://example.invalid/v1"
    os.environ["OPENAI_MODEL"] = "test-model"
    openai_provider = story.create_story_provider("openai")
    assert openai_provider.name == "openai"

    class _BadJsonProvider(story.StoryProvider):
        name = "bad-json"

        def generate(self, topic: str) -> str:
            return "not json"

    try:
        generate_story("topic", _BadJsonProvider(), provider_name="bad-json")
    except StoryGenerationError as exc:
        assert "does not contain a JSON object" in str(exc)
    else:
        raise AssertionError("expected StoryGenerationError")

    class _BadSchemaProvider(story.StoryProvider):
        name = "bad-schema"

        def generate(self, topic: str) -> str:
            return json.dumps({"version": "1.0", "title": "Bad", "segments": []})

    try:
        generate_story("topic", _BadSchemaProvider(), provider_name="bad-schema")
    except StoryValidationError as exc:
        assert "expected at least 1 segment" in str(exc)
    else:
        raise AssertionError("expected StoryValidationError")

    report = {
        "phase": 26,
        "status": "pass",
        "story": str(story_path.relative_to(PROJECT_ROOT)),
        "generated_story_segments": len(generated["segments"]),
        "scene_count": len(adapter_scenes),
        "providers": ["mock", "openai"],
        "deterministic": generated == generated_again,
        "invalid_json_rejected": True,
        "invalid_schema_rejected": True,
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"story={story_path.relative_to(PROJECT_ROOT)}")
    print(f"generated_segments={len(generated['segments'])}")
    print(f"scene_count={len(adapter_scenes)}")
    print(f"report={REPORT.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={round(time.time() - SCRIPT_START, 3):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
