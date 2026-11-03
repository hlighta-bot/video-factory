#!/usr/bin/env python3
"""Inspect a Scene Plan artifact for human review.

Purpose:
    Provide a deterministic CLI wrapper around scene.inspector so operators can
    review scenes.json before rendering.

Input:
    Scene Plan JSON path

Output:
    Human-readable text by default, JSON with --json

CLI:
    python scripts/inspect_scene_plan.py output/phase22/scenes.json
    python scripts/inspect_scene_plan.py output/phase22/scenes.json --json
"""

from __future__ import annotations

import argparse
import json

from scene.inspector import format_scene_plan_report, inspect_scene_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a Scene Plan JSON artifact")
    parser.add_argument("scene_plan", help="Path to scenes.json")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of text")
    args = parser.parse_args(argv)

    report = inspect_scene_plan(args.scene_plan)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_scene_plan_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
