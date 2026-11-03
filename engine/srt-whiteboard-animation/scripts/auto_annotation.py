#!/usr/bin/env python3
"""Auto Annotation generator.

Purpose:
    Generate a deterministic annotation JSON from a single illustration image.

Core logic:
    1. Reuse the existing grayscale threshold + connected components algorithm.
    2. Keep the renderer-compatible schema unchanged.
    3. Preserve the original CLI entry point via main(image_path, srt_path, output_path).

Input data:
    - image_path: local illustration file readable by OpenCV
    - output_path or scene_id: deterministic annotation identity target
    - scene_duration_ms: caller-provided scene duration or SRT-derived duration

Output data:
    - annotation JSON dict
    - annotation JSON file when persist=True
    - AnnotationResult including the generated path and write status

Important dependencies:
    - cv2
    - pathlib / json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2


def parse_srt_duration(srt_path: str | Path) -> int:
    """读取 SRT 最后一条字幕结束时间，返回毫秒。"""
    text = Path(srt_path).read_text(encoding="utf-8")
    last_end = 0

    for line in text.splitlines():
        line = line.strip()
        if "-->" not in line:
            continue

        end = line.split("-->")[1].strip().split()[0]
        h, m, rest = end.split(":")
        s, ms = rest.split(",")
        value = int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)
        last_end = max(last_end, value)

    return last_end


@dataclass(frozen=True)
class AnnotationResult:
    """Immutable result of annotation generation."""

    annotation: dict[str, Any]
    output_path: Path | None
    written: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path) if self.output_path is not None else None,
            "written": bool(self.written),
            "annotation": dict(self.annotation),
        }


def extract_regions(image_path: str | Path, *, min_area: int = 100, max_regions: int = 8) -> list[dict[str, int]]:
    """Extract deterministic ink regions from a local image."""
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(image_path)

    _, binary = cv2.threshold(image, 180, 255, cv2.THRESH_BINARY_INV)
    num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)

    regions: list[dict[str, int]] = []
    for index in range(1, num_labels):
        x, y, width, height, area = stats[index]
        if area < min_area:
            continue
        regions.append(
            {
                "x": int(x),
                "y": int(y),
                "width": int(width),
                "height": int(height),
                "area": int(area),
            }
        )

    regions.sort(key=lambda region: (region["area"], region["x"], region["y"]), reverse=True)
    return regions[:max_regions]


def build_annotation(
    image_path: str | Path,
    *,
    scene_id: str,
    scene_duration_ms: int,
    min_area: int = 100,
    max_regions: int = 8,
    story_basis: str = "自动生成场景",
) -> dict[str, Any]:
    """Build renderer-compatible annotation JSON from a single image."""
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(image_path)

    height, width = image.shape[:2]
    regions = extract_regions(image_path, min_area=min_area, max_regions=max_regions)
    duration = max(1000, int(scene_duration_ms))

    step = max(500, duration // max(len(regions), 1))
    elements: list[dict[str, Any]] = []

    for index, region in enumerate(regions, start=1):
        start_ms = 300 + (index - 1) * step
        if start_ms >= duration:
            start_ms = max(0, duration - 1000)

        reveal_duration = min(max(700, step - 300), max(1, duration - start_ms))
        center_x = region["x"] + region["width"] // 2

        elements.append(
            {
                "id": f"part-{index}",
                "label": f"自动区域 {index}",
                "sequence": index,
                "narrativeRole": "场景内容",
                "subtitle": "",
                "type": "structure",
                "region": {
                    "x": region["x"],
                    "y": region["y"],
                    "width": region["width"],
                    "height": region["height"],
                },
                "reveal": {
                    "direction": "top_to_bottom",
                    "startMs": int(start_ms),
                    "durationMs": int(reveal_duration),
                    "maskPaddingPx": 22,
                    "protectedRegions": [],
                },
                "handPath": {
                    "start": [int(center_x), int(region["y"])],
                    "end": [int(center_x), int(region["y"] + region["height"])],
                    "easing": "easeInOut",
                },
            }
        )

    return {
        "sceneId": scene_id,
        "canvas": {"width": int(width), "height": int(height)},
        "storyBasis": story_basis,
        "sceneDurationMs": int(duration),
        "elements": elements,
    }


def write_annotation(annotation: dict[str, Any], output_path: str | Path) -> Path:
    """Persist annotation JSON with stable UTF-8 formatting."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(annotation, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def generate_annotation(
    image_path: str | Path,
    *,
    output_path: str | Path | None = None,
    scene_id: str | None = None,
    scene_duration_ms: int = 9000,
    min_area: int = 100,
    max_regions: int = 8,
    story_basis: str = "自动生成场景",
    persist: bool = True,
) -> AnnotationResult:
    """Generate annotation JSON, optionally writing to disk."""
    image_file = Path(image_path)
    if not image_file.exists():
        raise FileNotFoundError(image_path)

    default_output = Path(output_path) if output_path is not None else image_file.with_suffix(".annotation.json")
    resolved_scene_id = scene_id or Path(default_output).stem

    annotation = build_annotation(
        image_file,
        scene_id=resolved_scene_id,
        scene_duration_ms=scene_duration_ms,
        min_area=min_area,
        max_regions=max_regions,
        story_basis=story_basis,
    )

    if not persist:
        return AnnotationResult(annotation=annotation, output_path=None, written=False)

    written_path = write_annotation(annotation, default_output)
    return AnnotationResult(annotation=annotation, output_path=written_path, written=True)


def main(image_path: str | Path, srt_path: str | Path, output_path: str | Path) -> AnnotationResult:
    """Compatibility entry point for the original SRT-driven CLI flow."""
    duration = parse_srt_duration(srt_path)
    if duration <= 0:
        duration = 9000
    return generate_annotation(
        image_path,
        output_path=output_path,
        scene_id=Path(output_path).stem,
        scene_duration_ms=duration,
    )


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="从单张线稿图生成确定性 annotation JSON")
    parser.add_argument(
        "paths",
        nargs="+",
        help="Legacy: IMAGE SRT OUTPUT; current CLI: IMAGE OUTPUT",
    )
    parser.add_argument("--scene-id", default=None)
    parser.add_argument("--duration-ms", type=int, default=9300)
    parser.add_argument("--min-area", type=int, default=100)
    parser.add_argument("--max-regions", type=int, default=8)
    parser.add_argument("--story-basis", default="自动生成场景")
    args = parser.parse_args(argv)

    if len(args.paths) == 3:
        image_path, srt_path, output_path = args.paths
        duration = parse_srt_duration(srt_path)
        scene_id = args.scene_id or Path(output_path).stem
        duration_ms = duration if duration > 0 else 9000
    elif len(args.paths) == 2:
        image_path, output_path = args.paths
        duration_ms = args.duration_ms
        scene_id = args.scene_id
    else:
        raise argparse.ArgumentTypeError("expected IMAGE OUTPUT or legacy IMAGE SRT OUTPUT")

    result = generate_annotation(
        image_path,
        output_path=output_path,
        scene_id=scene_id,
        scene_duration_ms=duration_ms,
        min_area=args.min_area,
        max_regions=args.max_regions,
        story_basis=args.story_basis,
    )
    print(f"[ok] annotation: {result.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
