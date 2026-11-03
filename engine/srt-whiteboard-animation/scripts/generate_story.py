#!/usr/bin/env python3
"""CLI for LLM Story Generator MVP.

Usage:
    python scripts/generate_story.py --topic "Rocket Launch" --provider mock --output out/story.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from story.generator import generate_story
from story.router import create_story_provider


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate Canonical Story JSON from a topic prompt.")
    parser.add_argument("--topic", required=True, help="Topic or prompt text")
    parser.add_argument("--provider", default="mock", help="Story provider name")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args(argv)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    provider = create_story_provider(args.provider)
    story = generate_story(args.topic, provider, provider_name=args.provider)
    output_path.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"provider={args.provider}")
    print(f"segments={len(story['segments'])}")
    print(f"output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
