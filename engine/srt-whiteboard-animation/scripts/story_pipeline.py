#!/usr/bin/env python3
"""One-command local Story → SRT → Phase 16 video pipeline entrypoint.

Purpose:
    Load local Story JSON, normalize/validate it into Canonical Story JSON,
    generate deterministic SRT, then call the existing Phase 16
    video_pipeline.main() entrypoint unchanged.

Input:
    Canonical Story JSON plus optional legacy Phase 17 Story shapes,
    optional local image path / asset roots.

Output:
    Generated SRT under the output directory and final MP4 produced by
    Phase 16 pipeline.

CLI:
    python scripts/story_pipeline.py <STORY_JSON> --output OUTPUT_DIR [--image PATH]
        [--renderer NAME] [--tts NAME] [--asset-root PATH] [--render-only]
        [--srt-output STORY.srt]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from story_schema import StoryValidationError, validate_story
from story_to_srt import DEFAULT_PAUSE_MS, DEFAULT_CHARS_PER_SECOND, write_srt_from_story
from video_pipeline import (
    _assert_outputs_do_not_overwrite_inputs,
    _assert_targets_do_not_exist,
    main as run_video_pipeline,
)


def _load_story(story_path: Path) -> dict:
    if not story_path.exists():
        raise FileNotFoundError(story_path)
    return validate_story(json.loads(story_path.read_text(encoding="utf-8-sig")))


def main_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Story → SRT → MP4")
    parser.add_argument("story", help="Local Story JSON path")
    parser.add_argument("--output", required=True, help="Output directory for generated SRT and final MP4")
    parser.add_argument("--image", help="Optional image path for legacy/manual image mode")
    parser.add_argument("--renderer", default="auto", help="Renderer override or auto")
    parser.add_argument(
        "--tts",
        choices=("audio8", "auto", "edge", "espeak"),
        default="audio8",
        help="TTS provider: audio8 (default), auto (Audio8 then eSpeak), edge, or espeak",
    )
    parser.add_argument(
        "--image-provider",
        choices=("none", "sensenova"),
        default="none",
        help="Scene image generation provider",
    )
    parser.add_argument("--asset-root", action="append", default=[], help="Additional asset root; repeatable")
    parser.add_argument("--env-py", help="Python interpreter path for helper scripts")
    parser.add_argument("--render-only", action="store_true", help="Render MP4 only; skip TTS/audio merge")
    parser.add_argument("--resume", action="store_true", help="Resume an interrupted SenseNova image run using the existing Story/SRT and valid images")
    parser.add_argument("--srt-output", help="Generated SRT filename or full path")
    parser.add_argument("--chars-per-second", type=float, default=DEFAULT_CHARS_PER_SECOND)
    parser.add_argument("--pause-ms", type=int, default=DEFAULT_PAUSE_MS)
    args = parser.parse_args(argv)

    if args.image and args.image_provider != "none":
        raise RuntimeError("--image and --image-provider cannot be used together")
    if args.resume and args.image_provider != "sensenova":
        raise RuntimeError("--resume currently requires --image-provider sensenova")

    story_path = Path(args.story).resolve()
    output_dir = Path(args.output).resolve()
    srt_path = Path(args.srt_output).resolve() if args.srt_output else output_dir / f"{story_path.stem}.srt"

    _load_story(story_path)

    if srt_path.resolve() == story_path:
        raise RuntimeError(
            f"拒绝执行：SRT 输出路径与 Story 输入相同 -> {srt_path}"
        )

    # Preflight every downstream artifact before writing the generated SRT.
    # video_pipeline performs the same checks before rendering, but doing it
    # here avoids leaving a new/overwritten SRT behind when a run is refused.
    try:
        _assert_outputs_do_not_overwrite_inputs(
            output_dir, srt_path=srt_path, image_path=args.image
        )
        _assert_targets_do_not_exist(
            output_dir,
            image_path=args.image,
            render_only=args.render_only,
            image_provider_name=args.image_provider,
            resume=args.resume,
        )
    except Exception as exc:
        print(f"[err] output preflight failed: {exc}")
        return 1
    if srt_path.exists() and not args.resume:
        raise RuntimeError(
            f"拒绝覆盖：SRT 输出文件已存在 -> {srt_path}；"
            "请指定新的 --srt-output 路径"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    if args.resume and srt_path.is_file():
        print(f"[resume] reuse SRT: {srt_path}")
    else:
        write_srt_from_story(
            story_path,
            srt_path,
            chars_per_second=args.chars_per_second,
            pause_ms=args.pause_ms,
        )
        print(f"[ok] story -> srt: {srt_path}")

    try:
        run_video_pipeline(
            str(srt_path),
            str(output_dir),
            renderer_name=args.renderer,
            tts_name=args.tts,
            render_only=args.render_only,
            env_py=args.env_py,
            asset_roots=args.asset_root,
            image_path=args.image,
            image_provider_name=args.image_provider,
            resume=args.resume,
        )
    except Exception as exc:
        print(f"[err] video pipeline failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
