#!/usr/bin/env python3
"""Canonical Story JSON schema and deterministic validator.

Purpose:
    Define the formal Story contract used by story_to_srt.

Canonical schema:
    {
      "version": "1.0",
      "title": "Rocket Launch",
      "segments": [{"text": "The rocket launches into the sky."}]
    }

Responsibilities:
    - Normalize accepted legacy inputs into canonical Story JSON.
    - Validate canonical Story JSON deterministically.
    - Reject unknown canonical fields with clear StoryValidationError messages.

Non-goals:
    - No LLM, network access, scene planning, visuals, assets, or duration policy.
"""
from __future__ import annotations

import json
from typing import Any

SUPPORTED_VERSION = "1.0"
CANONICAL_TOP_LEVEL_FIELDS = frozenset({"version", "title", "segments"})
CANONICAL_SEGMENT_FIELDS = frozenset({"text"})

LEGACY_TITLE_KEYS = ("title", "name", "headline", "subject")
LEGACY_TEXT_KEYS = ("text", "narration", "subtitle", "caption", "content", "line", "description")
LEGACY_NARRATION_KEYS = ("story", "script", "lines", "subtitles", "cues", "narrations")


class StoryValidationError(ValueError):
    """Raised when Story data violates the canonical schema."""


def validate_story(data: Any) -> dict[str, Any]:
    """Validate canonical Story JSON and return a detached copy.

    This function expects canonical shape only. Use normalize_story() for
    legacy Phase 17 inputs.
    """
    if not isinstance(data, dict):
        raise StoryValidationError("story must be a JSON object")

    unknown = sorted(set(data.keys()) - CANONICAL_TOP_LEVEL_FIELDS)
    if unknown:
        raise StoryValidationError("unknown field(s): " + ", ".join(f"story.{key}" for key in unknown))

    version = data.get("version")
    if version is None:
        raise StoryValidationError("story.version: required")
    if not isinstance(version, str):
        raise StoryValidationError("story.version: expected string")
    if version != SUPPORTED_VERSION:
        raise StoryValidationError(f"unsupported schema version: {version}")

    title = data.get("title")
    if title is None:
        raise StoryValidationError("story.title: required")
    if not isinstance(title, str):
        raise StoryValidationError("story.title: expected string")
    if not title.strip():
        raise StoryValidationError("story.title: expected non-empty string")

    segments = data.get("segments")
    if segments is None:
        raise StoryValidationError("story.segments: required")
    if not isinstance(segments, list):
        raise StoryValidationError("story.segments: expected array")
    if not segments:
        raise StoryValidationError("story.segments: expected at least 1 segment")

    validated_segments: list[dict[str, str]] = []
    for index, segment in enumerate(segments):
        location = f"story.segments[{index}]"
        if not isinstance(segment, dict):
            raise StoryValidationError(f"{location}: expected object")

        unknown_segment = sorted(set(segment.keys()) - CANONICAL_SEGMENT_FIELDS)
        if unknown_segment:
            raise StoryValidationError(
                "unknown field(s): " + ", ".join(f"{location}.{key}" for key in unknown_segment)
            )

        text = segment.get("text")
        if "text" not in segment:
            raise StoryValidationError(f"{location}.text: required")
        if not isinstance(text, str):
            raise StoryValidationError(f"{location}.text: expected string")
        if not text.strip():
            raise StoryValidationError(f"{location}.text: expected non-empty string")

        validated_segments.append({"text": text.strip()})

    return {
        "version": version,
        "title": title.strip(),
        "segments": validated_segments,
    }


def normalize_story(data: Any) -> dict[str, Any]:
    """Normalize legacy or canonical Story input into canonical Story JSON."""
    if isinstance(data, str):
        title = "Untitled Story"
        segments = [{"text": line.strip()} for line in data.splitlines() if line.strip()]
    elif isinstance(data, list):
        title = "Untitled Story"
        segments = [_normalize_segments_from_list(data)]
    elif isinstance(data, dict):
        title = _first_non_empty_string(data, LEGACY_TITLE_KEYS) or "Untitled Story"
        if "version" in data or "segments" in data:
            return validate_story(data)
        if "narration" in data:
            segments = _normalize_segments_from_value(data["narration"])
        else:
            segments = []
            for key in LEGACY_NARRATION_KEYS:
                if key in data:
                    segments = _normalize_segments_from_value(data[key])
                    break
    else:
        raise StoryValidationError("story must be a JSON object/list/string")

    return validate_story({"version": SUPPORTED_VERSION, "title": title, "segments": segments})


def _normalize_segments_from_value(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        return [{"text": line.strip()} for line in value.splitlines() if line.strip()]
    if isinstance(value, list):
        return _normalize_segments_from_list(value)
    if isinstance(value, dict):
        return [{"text": _string_or_first(value, LEGACY_TEXT_KEYS)}]
    raise StoryValidationError("narration/segments value must be a string or array")


def _normalize_segments_from_list(items: list[Any]) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = _string_or_first(item, LEGACY_TEXT_KEYS)
        else:
            raise StoryValidationError(f"narration/segments[{index}] must be a string or object")
        if text:
            segments.append({"text": text})
    return segments


def _string_or_first(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_non_empty_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    return _string_or_first(payload, keys)


def story_texts(story: Any) -> list[str]:
    """Return ordered segment texts from canonical Story input."""
    if isinstance(story, str):
        try:
            story = json.loads(story)
        except json.JSONDecodeError as exc:
            raise StoryValidationError("story must be a JSON object") from exc
    return [segment["text"] for segment in validate_story(story)["segments"]]
