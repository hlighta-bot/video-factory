#!/usr/bin/env python3
"""Deterministic canonical Story → SRT converter.

Purpose:
    Convert a validated Canonical Story JSON file into a valid SRT timeline
    without LLM, network calls, image generation, or asset lookup.

Canonical input format:
    {
      "version": "1.0",
      "title": "Rocket launch",
      "segments": [
        {"text": "The rocket ignites."},
        {"text": "It rises into space."}
      ]
    }

Legacy Phase 17 inputs are normalized before validation and SRT generation:
    narration[], story[], script[], lines[], and string.

Output format:
    Standard SRT text with HH:MM:SS,mmm timestamps. Cue order matches segment
    order. Cue durations are estimated deterministically from text length and
    clamped to MIN_CUE_MS/MAX_CUE_MS.

CLI:
    python scripts/story_to_srt.py <STORY_JSON> [--output STORY.srt]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from story_schema import StoryValidationError, normalize_story, story_texts

DEFAULT_CHARS_PER_SECOND = 5.0
DEFAULT_PAUSE_MS = 250
MIN_CUE_MS = 1500
MAX_CUE_MS = 8000


def _timestamp_ms(ms: int) -> str:
    hours = ms // 3_600_000
    minutes = (ms // 60_000) % 60
    seconds = (ms // 1_000) % 60
    millis = ms % 1_000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _cue_duration_ms(text: str, chars_per_second: float = DEFAULT_CHARS_PER_SECOND) -> int:
    chars = len(text)
    if chars <= 0:
        raise ValueError("Story cue text must not be empty")
    if chars_per_second <= 0:
        raise ValueError("chars_per_second must be positive")
    estimated = int(chars / chars_per_second * 1000)
    return max(MIN_CUE_MS, min(MAX_CUE_MS, estimated))


def build_srt_from_story(
    payload: Any,
    *,
    chars_per_second: float = DEFAULT_CHARS_PER_SECOND,
    pause_ms: int = DEFAULT_PAUSE_MS,
) -> str:
    """Build deterministic SRT text from a canonical Story input."""
    try:
        lines = story_texts(payload)
    except StoryValidationError as exc:
        raise ValueError(str(exc)) from exc
    if not lines:
        raise ValueError("Story contains no non-empty narration lines")
    if pause_ms < 0:
        raise ValueError("pause_ms must be non-negative")

    blocks: list[str] = []
    cursor = 0
    for index, line in enumerate(lines, start=1):
        duration = _cue_duration_ms(line, chars_per_second=chars_per_second)
        start_ms = cursor
        end_ms = cursor + duration
        cursor = end_ms + pause_ms
        blocks.append(
            f"{index}\n"
            f"{_timestamp_ms(start_ms)} --> {_timestamp_ms(end_ms)}\n"
            f"{line}"
        )
    return "\n\n".join(blocks) + "\n"


def write_srt_from_story(
    story_path: Path,
    output_path: Path,
    *,
    chars_per_second: float = DEFAULT_CHARS_PER_SECOND,
    pause_ms: int = DEFAULT_PAUSE_MS,
) -> str:
    """Read local Story JSON and write generated SRT text."""
    if not story_path.exists():
        raise FileNotFoundError(story_path)
    payload = normalize_story(json.loads(story_path.read_text(encoding="utf-8-sig")))
    srt_text = build_srt_from_story(
        payload,
        chars_per_second=chars_per_second,
        pause_ms=pause_ms,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(srt_text, encoding="utf-8")
    return srt_text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic Canonical Story JSON → SRT")
    parser.add_argument("story", help="Local Story JSON path")
    parser.add_argument("--output", help="Output SRT path; default: STORY.srt next to input")
    parser.add_argument("--chars-per-second", type=float, default=DEFAULT_CHARS_PER_SECOND)
    parser.add_argument("--pause-ms", type=int, default=DEFAULT_PAUSE_MS)
    args = parser.parse_args(argv)

    story_path = Path(args.story)
    output_path = Path(args.output) if args.output else story_path.with_suffix(".srt")

    try:
        write_srt_from_story(
            story_path,
            output_path,
            chars_per_second=args.chars_per_second,
            pause_ms=args.pause_ms,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[err] story_to_srt failed: {exc}", file=sys.stderr)
        return 1

    print(f"story={story_path}")
    print(f"srt={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
