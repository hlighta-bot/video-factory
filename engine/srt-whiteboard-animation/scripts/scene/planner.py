"""Deterministic scene planner.

Purpose:
    Convert raw script text or SRT text into Scene objects without LLM/agent logic.

Core logic:
    1. Use keyword-based classification to map text to kind/requirements.
    2. Preserve SRT timings when available; estimate duration for plain text.
    3. Optionally group multiple SRT cues into fewer scenes.
    4. Emit only Selector-oriented visual fields: kind, requirements, and intent.
    5. Keep Phase 9 behavior intact for existing callers.

Input data:
    - direct text string, or
    - SRT text string, or
    - parsed SRT cues.

Output data:
    - Scene instance for direct text
    - list[Scene] for SRT planning
    - grouped list[Scene] when grouping is enabled

Design notes:
    - Grouping uses simple deterministic greedy rules.
    - Visual intent is a lightweight WHAT layer and does not affect selector priority.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

from scene.model import Scene
from scene.visual_plan import build_visual_plan
from scene.visual_semantic import extract_visual_semantic

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from parse_srt import parse_srt  # noqa: E402


DEFAULT_RENDERER = "auto"
DEFAULT_MIN_DURATION_MS = 1500
DEFAULT_CHARS_PER_SECOND = 5.0
DEFAULT_GROUP_TARGET_MS = 8000
DEFAULT_GROUP_MIN_MS = 4000
DEFAULT_GROUP_MAX_MS = 12000


def _classify_kind(text: str) -> tuple[str, list[str]]:
    """Classify text deterministically into a visual kind and requirements."""
    lowered = text.lower()

    math_patterns = [
        r"\bformula\b", r"\bequation\b", r"\bmath\b", r"\bgraph\b", r"\bgeometry\b", r"\bphysics\b",
        r"∑", r"∫", r"π", r"²", r"³", r"=",
        r"公式", r"方程", r"函数", r"几何", r"物理", r"数学", r"坐标",
    ]
    infographic_patterns = [
        r"\binfographic\b", r"\bchart\b", r"\bdata\b", r"\bdiagram\b", r"\bmetric\b", r"\btrend\b",
        r"\btable\b",
        r"信息图", r"图表", r"数据", r"统计", r"流程", r"对比", r"人口", r"下降",
    ]
    story_patterns = [
        r"\bstory\b", r"\billustration\b", r"\bscene\b", r"\bcharacter\b", r"\bhistorical\b",
        r"\bjourney\b", r"\badventure\b", r"\bexplains\b", r"\babout\b", r"\bonce upon\b",
        r"故事", r"场景", r"历史", r"人物", r"角色", r"说明",
    ]

    if any(re.search(pattern, lowered) for pattern in math_patterns):
        return "math", ["formula", "graph"]

    if any(re.search(pattern, lowered) for pattern in infographic_patterns):
        return "infographic", ["data", "text"]

    if any(re.search(pattern, lowered) for pattern in story_patterns):
        return "story", ["illustration"]

    return "story", ["illustration"]


def classify_text(text: str) -> tuple[str, list[str]]:
    """Classify text deterministically into a visual kind and requirements."""
    return _classify_kind(text)


def infer_visual_intent(text: str) -> str:
    """Map text to a deterministic visual intent label."""
    lowered = text.lower()

    # Rocket-family intents keep priority because they are the most specific visual story.
    if "rocket" in lowered or "火箭" in text:
        if any(token in lowered for token in ("点火", "发射", "起飞", "launch", "ignition")):
            return "rocket_launch"
        if any(token in lowered for token in ("太空", "大气层", "宇宙", "space", "atmosphere")):
            return "rocket_space"
        if any(token in lowered for token in ("飞行", "飞向天空", "上升", "冲向天空", "flying", "rise")):
            return "rocket_flying"
        return "rocket_scene"

    # Math intents.
    if any(token in lowered for token in ("quadratic", "二次函数", "二次", "抛物线", "y=x²")) or "y = x²" in lowered:
        return "quadratic_function"
    if any(token in lowered for token in ("geometry", "几何", "三角形", "角度")):
        return "geometry"
    if any(token in lowered for token in ("physics", "物理", "速度", "加速度", "力")):
        return "physics"
    if any(token in lowered for token in ("formula", "equation", "函数", "公式", "方程")):
        return "formula_explanation"

    # Infographic intents.
    if any(token in lowered for token in ("population", "人口")):
        return "population_change"
    if any(token in lowered for token in ("ratio", "比例", "占比", "share")):
        return "ratio_comparison"
    if any(token in lowered for token in ("growth", "下降", "增长", "trend", "变化")):
        return "data_change"
    if any(token in lowered for token in ("chart", "data", "diagram", "infographic", "数据", "图表")):
        return "data_overview"

    # Math graph remains a fallback for explicit function/coordinate wording.
    if any(token in lowered for token in ("graph", "坐标", "曲线")):
        return "function_graph"

    return "story_scene"


def estimate_duration_ms(text: str) -> int:
    """Estimate display duration for direct text without SRT timing."""
    chars = len(text.strip())
    if chars <= 0:
        return DEFAULT_MIN_DURATION_MS
    return max(DEFAULT_MIN_DURATION_MS, int(chars / DEFAULT_CHARS_PER_SECOND * 1000) + 1)


def _make_scene(
    scene_id: str,
    text: str,
    renderer: str,
    subtitles: list[dict],
    duration_ms: int,
    kind: str,
    requirements: list[str],
    intent: str,
) -> Scene:
    return Scene(
        id=scene_id,
        renderer=renderer,
        visual={
            "kind": kind,
            "requirements": requirements,
            "intent": intent,
            "visual_plan": build_visual_plan(intent, text),
            "semantic": extract_visual_semantic(text).to_dict(),
        },
        narration=text,
        subtitles=subtitles,
        duration_ms=duration_ms,
    )


def _format_srt_timestamp(ms: int) -> str:
    hours = ms // 3600000
    minutes = (ms // 60000) % 60
    seconds = (ms // 1000) % 60
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _make_subtitle(cue: dict, scene_index: int) -> dict:
    start_ms = int(cue.get("startMs", 0))
    end_ms = int(cue.get("endMs", start_ms))
    return {
        "id": cue.get("id", cue.get("index", scene_index)),
        "start": _format_srt_timestamp(start_ms),
        "end": _format_srt_timestamp(end_ms),
        "text": str(cue.get("text", "")).strip(),
        "start_ms": start_ms,
        "end_ms": end_ms,
    }


def plan_scene(text: str, scene_id: str = "scene-01", renderer: str = DEFAULT_RENDERER) -> Scene:
    """Plan one scene from direct text."""
    cleaned = text.strip()
    kind, requirements = classify_text(cleaned)
    return _make_scene(
        scene_id=scene_id,
        text=cleaned,
        renderer=renderer,
        subtitles=[],
        duration_ms=estimate_duration_ms(cleaned),
        kind=kind,
        requirements=requirements,
        intent=infer_visual_intent(cleaned),
    )


def plan_script(text: str, renderer: str = DEFAULT_RENDERER) -> list[Scene]:
    """Plan scenes from SRT text or a plain script string.

    Behavior:
        - If text looks like SRT, reuse parse_srt and keep SRT timings.
        - Otherwise return a single scene for the full text.
    """
    raw = text.strip()
    if "-->" in raw:
        cues = parse_srt(raw)
        if cues:
            return plan_srt(cues, renderer=renderer)

    return [plan_scene(raw, scene_id="scene-01", renderer=renderer)]


def _group_cues(
    cues: list[dict],
    target_duration_ms: int,
    min_duration_ms: int,
    max_duration_ms: int,
) -> list[list[dict]]:
    """Greedy sequential grouping over cue boundaries only."""
    groups: list[list[dict]] = []
    bucket: list[dict] = []

    for cue in cues:
        if bucket:
            bucket_start = int(bucket[0].get("startMs", 0))
            bucket_end = int(bucket[-1].get("endMs", bucket_start))
            bucket_span = max(0, bucket_end - bucket_start)
            next_end = int(cue.get("endMs", bucket_start))
            next_span = max(0, next_end - bucket_start)

            if next_span > max_duration_ms:
                groups.append(bucket)
                bucket = [cue]
            elif bucket_span >= target_duration_ms and bucket_span >= min_duration_ms:
                groups.append(bucket)
                bucket = [cue]
            else:
                bucket.append(cue)
        else:
            bucket.append(cue)

    if bucket:
        groups.append(bucket)

    merged: list[list[dict]] = []
    for group in groups:
        if merged:
            prev = merged[-1]
            prev_start = int(prev[0].get("startMs", 0))
            group_start = int(group[0].get("startMs", 0))
            group_end = int(group[-1].get("endMs", group_start))
            group_span = max(0, group_end - group_start)
            combined_span = max(0, group_end - prev_start)

            if group_span < min_duration_ms and combined_span <= max_duration_ms:
                merged[-1] = prev + group
                continue
            if combined_span <= target_duration_ms:
                merged[-1] = prev + group
                continue

        if len(group) >= 4:
            group_start = int(group[0].get("startMs", 0))
            group_end = int(group[-1].get("endMs", group_start))
            group_span = max(0, group_end - group_start)
            if group_span > target_duration_ms and group_span <= max_duration_ms:
                split_at = len(group) // 2
                merged.append(group[:split_at])
                merged.append(group[split_at:])
                continue

        merged.append(group)

    return merged


def plan_srt(
    cues: Iterable[dict],
    renderer: str = DEFAULT_RENDERER,
    grouped: bool = False,
    target_duration_ms: int = DEFAULT_GROUP_TARGET_MS,
    min_duration_ms: int = DEFAULT_GROUP_MIN_MS,
    max_duration_ms: int = DEFAULT_GROUP_MAX_MS,
) -> list[Scene]:
    """Plan one scene per SRT cue, or grouped scenes when grouped=True."""
    cue_list = list(cues)
    if grouped:
        return plan_srt_grouped(
            cue_list,
            renderer=renderer,
            target_duration_ms=target_duration_ms,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
        )

    scenes: list[Scene] = []
    for cue in cue_list:
        text = str(cue.get("text", "")).strip()
        start_ms = int(cue.get("startMs", 0))
        end_ms = int(cue.get("endMs", start_ms))
        duration_ms = max(0, end_ms - start_ms)
        kind, requirements = classify_text(text)
        scene_id = f"scene-{cue.get('index', len(scenes) + 1):02d}"
        subtitle = {
            "id": cue.get("id", cue.get("index", len(scenes) + 1)),
            "start": _format_srt_timestamp(start_ms),
            "end": _format_srt_timestamp(end_ms),
            "text": text,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
        scenes.append(
            _make_scene(
                scene_id=scene_id,
                text=text,
                renderer=renderer,
                subtitles=[subtitle],
                duration_ms=duration_ms,
                kind=kind,
                requirements=requirements,
                intent=infer_visual_intent(text),
            )
        )
    return scenes


def plan_srt_grouped(
    cues: Iterable[dict],
    renderer: str = DEFAULT_RENDERER,
    target_duration_ms: int = DEFAULT_GROUP_TARGET_MS,
    min_duration_ms: int = DEFAULT_GROUP_MIN_MS,
    max_duration_ms: int = DEFAULT_GROUP_MAX_MS,
) -> list[Scene]:
    """Plan grouped scenes from SRT cues while preserving cue boundaries and timings."""
    cue_list = list(cues)
    groups = _group_cues(cue_list, target_duration_ms, min_duration_ms, max_duration_ms)
    scenes: list[Scene] = []

    for scene_index, group in enumerate(groups, start=1):
        narration = "".join(str(cue.get("text", "")).strip() for cue in group)
        start_ms = int(group[0].get("startMs", 0))
        end_ms = int(group[-1].get("endMs", start_ms))
        duration_ms = max(0, end_ms - start_ms)
        kind, requirements = classify_text(narration)
        subtitles = [_make_subtitle(cue, len(scenes) + 1) for cue in group]
        scenes.append(
            _make_scene(
                scene_id=f"scene-{scene_index:02d}",
                text=narration,
                renderer=renderer,
                subtitles=subtitles,
                duration_ms=duration_ms,
                kind=kind,
                requirements=requirements,
                intent=infer_visual_intent(narration),
            )
        )

    return scenes


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Plan scenes from text or SRT")
    parser.add_argument("input", help="Raw script text, SRT file path, or SRT content")
    parser.add_argument("output", nargs="?", help="Optional output JSON file path")
    parser.add_argument("--scene-id", default="scene-01", help="Scene ID for direct text mode")
    parser.add_argument("--renderer", default=DEFAULT_RENDERER, help="Explicit renderer override")
    parser.add_argument("--group", action="store_true", help="Group SRT cues into scenes")
    parser.add_argument("--target-duration", type=int, default=DEFAULT_GROUP_TARGET_MS, help="Grouping target duration in ms")
    parser.add_argument("--min-duration", type=int, default=DEFAULT_GROUP_MIN_MS, help="Grouping minimum duration in ms")
    parser.add_argument("--max-duration", type=int, default=DEFAULT_GROUP_MAX_MS, help="Grouping maximum duration in ms")
    args = parser.parse_args(argv)

    path = Path(args.input)
    raw = path.read_text(encoding="utf-8-sig") if path.exists() else args.input

    if "-->" in raw:
        cues = parse_srt(raw)
        scenes = plan_srt(
            cues,
            renderer=args.renderer,
            grouped=args.group,
            target_duration_ms=args.target_duration,
            min_duration_ms=args.min_duration,
            max_duration_ms=args.max_duration,
        )
    else:
        scenes = [plan_scene(raw, scene_id=args.scene_id, renderer=args.renderer)]

    payload = [scene.to_dict() for scene in scenes]
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_text, encoding="utf-8")

    print(json_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
