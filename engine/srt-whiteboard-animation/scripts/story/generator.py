"""Prompt -> validated Canonical Story boundary."""

from __future__ import annotations

import json
import re
from typing import Any

from story_schema import StoryValidationError, validate_story

from .base import StoryProvider

_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


class StoryGenerationError(ValueError):
    """Raised when a Story Provider returns invalid content."""


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    raw = raw_text.strip()
    if not raw:
        raise StoryGenerationError("provider returned empty output")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(raw)
        if not match:
            raise StoryGenerationError("provider output does not contain a JSON object") from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise StoryGenerationError(f"provider returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise StoryGenerationError("provider output JSON root must be an object")
    return parsed


def generate_story(topic: str, provider: StoryProvider, *, provider_name: str | None = None) -> dict[str, Any]:
    """Generate and validate a canonical Story JSON payload.

    Args:
        topic: Human-readable prompt text.
        provider: Provider responsible for producing raw JSON text.
        provider_name: Optional log identifier; defaults to provider.name.

    Returns:
        Validated canonical Story JSON dict.

    Raises:
        StoryGenerationError: Provider output is empty or not valid JSON.
        StoryValidationError: JSON does not match canonical Story schema.
    """
    if not topic.strip():
        raise StoryGenerationError("topic must be a non-empty string")

    raw = provider.generate(topic)
    payload = _extract_json_object(raw)
    return validate_story(payload)
