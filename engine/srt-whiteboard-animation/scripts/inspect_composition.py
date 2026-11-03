#!/usr/bin/env python3
"""Inspect a scene's procedural composition and emit preview artifacts.

Usage:
    python scripts/inspect_composition.py scene.json --out output/phase34a/composition.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


from scene.composition_inspector import inspect_composition_scene, save_composition_report
from scene.model import Scene


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect procedural composition for a scene artifact")
    parser.add_argument("scene_json", help="Path to a single-scene JSON artifact")
    parser.add_argument("--out", required=True, help="Base output path for report JSON")
    parser.add_argument("--text", help="Optional path for human-readable report")
    parser.add_argument("--preview", help="Optional path for preview SVG")
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.scene_json).read_text(encoding="utf-8"))
    scene = Scene.from_dict(payload) if isinstance(payload, dict) else Scene.from_dict({"sceneId": payload[0]["sceneId"]})
    inspection = inspect_composition_scene(scene)

    report_json_path = Path(args.out)
    report_txt_path = Path(args.text) if args.text else report_json_path.with_suffix(".txt")
    preview_svg_path = Path(args.preview) if args.preview else report_json_path.with_suffix(".svg")
    save_composition_report(inspection, report_json_path, report_txt_path, preview_svg_path)
    print(report_json_path)
    print(report_txt_path)
    print(preview_svg_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
