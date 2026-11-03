"""Canonical Story → Scene Adapter MVP.

Purpose:
    Provide a minimal bridge from validated Canonical Story JSON into
    existing Scene Planner APIs without changing Story Schema or Planner logic.

Core logic:
    1. Validate Story with the existing Canonical Story validator.
    2. Convert canonical Story into a deterministic SRT timeline using
       story_to_srt.build_srt_from_story().
    3. Parse that timeline with parse_srt().
    4. Delegate scene construction, timing, classification, visual intent, and
       grouping rules to scene.planner.plan_srt_grouped().

Input data:
    - dict or JSON string canonical Story payload

Output data:
    - list[Scene]

Non-goals:
    - No Story Schema changes
    - No Renderer / Asset / Planner internals changes
    - No LLM, network, image generation, or new visual fields
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from scene.model import Scene
from scene.planner import plan_srt

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from parse_srt import parse_srt  # noqa: E402
from story_schema import StoryValidationError, normalize_story, validate_story  # noqa: E402
from story_to_srt import build_srt_from_story, DEFAULT_CHARS_PER_SECOND, DEFAULT_PAUSE_MS  # noqa: E402


class StoryAdapterError(ValueError):
    """Raised when canonical Story cannot be adapted into scenes."""


def _normalize_story_payload(story: Any, *, allow_legacy: bool) -> dict[str, Any]:
    if allow_legacy:
        return normalize_story(story)
    if isinstance(story, str):
        try:
            story = json.loads(story)
        except json.JSONDecodeError as exc:
            raise StoryAdapterError("story must be a valid JSON object") from exc
    return validate_story(story)


def story_to_scenes(
    story: Any,
    *,
    allow_legacy: bool = False,
    renderer: str = "auto",
    chars_per_second: float = DEFAULT_CHARS_PER_SECOND,
    pause_ms: int = DEFAULT_PAUSE_MS,
) -> list[Scene]:
    """Convert a canonical Story into Scene objects via the existing Planner."""
    try:
        validated_story = _normalize_story_payload(story, allow_legacy=allow_legacy)
    except StoryValidationError as exc:
        raise StoryAdapterError(str(exc)) from exc

    srt_text = build_srt_from_story(
        validated_story,
        chars_per_second=chars_per_second,
        pause_ms=pause_ms,
    )
    cues = parse_srt(srt_text)
    if not cues:
        raise StoryAdapterError("Story produced no planneable cues")
    return plan_srt(cues, renderer=renderer)
