"""Phase 18 Canonical Story Schema / Validator smoke test.

Purpose:
- Verify canonical Story JSON validation.
- Verify strict unknown-field policy.
- Verify deterministic validation output.
- Verify legacy Phase 17 inputs normalize to canonical shape.
- Verify story_to_srt remains deterministic after canonical validation.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from parse_srt import parse_srt
from story_schema import (
    SUPPORTED_VERSION,
    StoryValidationError,
    normalize_story,
    story_texts,
    validate_story,
)
from story_to_srt import build_srt_from_story


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "story_canonical.json"
TEST_ROOT = PROJECT_ROOT / "output" / "phase18-test"
REPORT = TEST_ROOT / "story-schema-report.json"


def _canonical() -> dict:
    return {
        "version": SUPPORTED_VERSION,
        "title": "Rocket Launch",
        "segments": [
            {"text": "The rocket is ready on the launch pad."},
            {"text": "The countdown begins."},
            {"text": "The rocket launches into the sky."},
        ],
    }


def _assert_validation_error(data, expected: str) -> None:
    try:
        validate_story(data)
    except StoryValidationError as exc:
        assert expected in str(exc), exc
        return
    raise AssertionError(f"expected validation failure containing {expected!r} for {data!r}")


def _validations() -> list[tuple[object, str | None]]:
    return [
        (_canonical(), None),
        ({
            "version": SUPPORTED_VERSION,
            "title": "火箭发射",
            "segments": [{"text": "火箭已经准备好了。"}],
        }, None),
        ({"version": "1.0", "title": "Rocket", "segments": []}, "expected at least 1 segment"),
        ({"title": "Rocket", "segments": [{"text": "A"}]}, "story.version: required"),
        ({"version": 1.0, "title": "Rocket", "segments": [{"text": "A"}]}, "expected string"),
        ({"version": "2.0", "title": "Rocket", "segments": [{"text": "A"}]}, "unsupported schema version"),
        ({"version": SUPPORTED_VERSION, "segments": [{"text": "A"}]}, "story.title: required"),
        ({"version": SUPPORTED_VERSION, "title": "   ", "segments": [{"text": "A"}]}, "expected non-empty string"),
        ({"version": SUPPORTED_VERSION, "title": "Rocket", "segments": None}, "story.segments: required"),
        ({"version": SUPPORTED_VERSION, "title": "Rocket", "segments": "A"}, "story.segments: expected array"),
        ({"version": SUPPORTED_VERSION, "title": "Rocket", "segments": [{"bad": 1}]}, "unknown field(s): story.segments[0].bad"),
        ({"version": SUPPORTED_VERSION, "title": "Rocket", "segments": [{"text": None}]}, "expected string"),
        ({"version": SUPPORTED_VERSION, "title": "Rocket", "segments": [{"text": "  "}]}, "expected non-empty string"),
        ({"version": SUPPORTED_VERSION, "title": "Rocket", "segments": [], "foo": "bar"}, "unknown field(s)"),
        ({"version": SUPPORTED_VERSION, "title": "Rocket", "segments": [{"text": "A", "extra": 1}]}, "unknown field(s)"),
        (None, "story must be a JSON object"),
        ([], "story must be a JSON object"),
        ("hello", "story must be a JSON object"),
        (123, "story must be a JSON object"),
    ]


def main() -> int:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fixture = _canonical()
    FIXTURE_PATH.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    loaded = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    validated_a = validate_story(loaded)
    validated_b = validate_story(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
    assert validated_a == validated_b
    assert validated_a["version"] == SUPPORTED_VERSION
    assert validated_a["segments"] == fixture["segments"]
    assert [segment["text"] for segment in validated_a["segments"]] == [
        "The rocket is ready on the launch pad.",
        "The countdown begins.",
        "The rocket launches into the sky.",
    ]

    for data, expected in _validations():
        if expected is None:
            assert validate_story(data) == validate_story(data)
            continue
        _assert_validation_error(data, expected)

    legacy_cases = {
        "narration_list": {"title": "Legacy", "narration": ["A", "B"]},
        "story_list": {"story": ["A", "B"]},
        "script_list": {"script": [{"text": "A"}, {"narration": "B"}]},
        "lines_list": {"lines": ["A"]},
        "string": "A\n\nB",
    }
    for name, legacy in legacy_cases.items():
        normalized = normalize_story(legacy)
        assert normalized["version"] == SUPPORTED_VERSION
        assert normalized["title"]
        assert [segment["text"] for segment in normalized["segments"]] == ["A", "B"] if name != "lines_list" else ["A"]

    srt_a = build_srt_from_story(loaded)
    srt_b = build_srt_from_story(FIXTURE_PATH.read_text(encoding="utf-8-sig"))
    assert srt_a == srt_b
    cues = parse_srt(srt_a)
    assert len(cues) == 3
    assert cues[0]["text"] == "The rocket is ready on the launch pad."
    for index, cue in enumerate(cues[:-1]):
        assert cue["endMs"] < cues[index + 1]["startMs"]

    try:
        story_texts("hello")
        raise AssertionError("expected StoryValidationError for non-object canonical validator path")
    except StoryValidationError:
        pass

    # Canonical invalid Story must fail before any Phase 16 pipeline call.
    invalid_story = TEST_ROOT / "invalid-story.json"
    invalid_story.write_text(json.dumps({"version": "1.0", "title": "Invalid", "segments": []}), encoding="utf-8")
    try:
        build_srt_from_story(json.loads(invalid_story.read_text(encoding="utf-8")))
        raise AssertionError("expected invalid canonical story to fail before SRT generation")
    except ValueError as exc:
        assert "expected at least 1 segment" in str(exc), exc

    report = {
        "fixture": str(FIXTURE_PATH),
        "canonical_segments": len(validated_a["segments"]),
        "validation_cases": len(_validations()),
        "legacy_cases": len(legacy_cases),
        "srt_cues": len(cues),
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"fixture={FIXTURE_PATH}")
    print(f"canonical_segments={len(validated_a['segments'])}")
    print(f"validation_cases={len(_validations())}")
    print(f"legacy_cases={len(legacy_cases)}")
    print(f"srt_cues={len(cues)}")
    print(f"report={REPORT}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
