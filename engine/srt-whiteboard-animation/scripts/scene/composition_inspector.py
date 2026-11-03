"""Deterministic procedural composition inspector.

Purpose:
    Provide a read-only inspection layer for procedural fallback scenes so
    developers can review structured composition output and preview SVGs without
    touching the main rendering path.

Core logic:
    1. Normalize Scene input.
    2. Resolve VisualRepresentation without changing renderer behavior.
    3. Extract deterministic summary fields from procedural payload.
    4. Return a stable JSON report and SVG preview scene payload.

Input data:
    - Scene object or Scene-like dict

Output data:
    - dict report
    - dict preview scene payload

Non-goals:
    - No mutation of Scene, VisualPlan, VisualSpec, or renderer state.
    - No asset resolution changes.
    - No fallback logic changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import Scene
from .normalizer import normalize_scene
from .procedural_composition import compose_procedural_scene
from .visual_representation import resolve_visual_representation
from .visual_spec import adapt_whiteboard


REPORT_VERSION = "1.0"


@dataclass(frozen=True)
class CompositionInspection:
    """Immutable inspection result for one scene."""

    report: dict[str, Any]
    preview_scene: dict[str, Any]


def inspect_composition_scene(scene: Scene | dict) -> CompositionInspection:
    """Inspect a single scene and return a deterministic report."""
    normalized = normalize_scene(scene) if isinstance(scene, dict) else scene
    spec = adapt_whiteboard(normalized)
    composition = compose_procedural_scene(normalized, spec)
    representation = resolve_visual_representation(normalized, asset_roots=[])

    payload = representation.payload if representation.kind == "procedural_svg" else composition.to_dict()
    preview_scene = _preview_scene_from_payload(normalized, payload)
    report = _build_report(normalized, composition, representation)
    return CompositionInspection(report=report, preview_scene=preview_scene)


def inspect_composition_scene_plan(path: Any) -> dict[str, Any]:
    """Inspect the first scene in a Scene Plan JSON artifact."""
    from .scene_plan import load_scene_plan, scene_plan_from_dict

    plan = load_scene_plan(path)
    scenes = scene_plan_from_dict(plan)
    if not scenes:
        raise ValueError("scene plan contains no scenes")
    return inspect_composition_scene(scenes[0]).report


def format_composition_report(report: dict[str, Any]) -> str:
    """Format a composition inspection report as deterministic text."""
    summary = report.get("summary") or {}
    semantic = summary.get("semantic") or {}
    lines: list[str] = [
        "Composition Inspector",
        "======================",
        f"Scene id: {report.get('scene_id', '')}",
        f"Narration: {report.get('narration', '')}",
        f"Duration: {report.get('duration_ms', 0)} ms",
        f"Source: {report.get('source', '')}",
        "",
        "Summary",
        "-------",
        f"Kind: {summary.get('kind', '')}",
        f"Template: {summary.get('template', '')}",
        f"Background: {summary.get('background', '')}",
        f"Canvas: {summary.get('width', 0)}x{summary.get('height', 0)}",
        f"Element count: {summary.get('element_count', 0)}",
        "",
        "Semantic",
        "--------",
    ]
    for key in ("subject", "environment", "action", "objects"):
        value = semantic.get(key, "")
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        lines.append(f"{key}: {value}")
    lines.append("")

    lines.extend([
        "Primitive counts",
        "----------------",
    ])
    counts = summary.get("primitive_counts") or {}
    for kind in sorted(counts):
        lines.append(f"{kind}: {counts[kind]}")
    if not counts:
        lines.append("none")
    lines.append("")

    lines.extend([
        "Representative primitives",
        "-------------------------",
    ])
    for item in summary.get("representative_primitives") or []:
        lines.append(f"- {item.get('kind', '')} | {item.get('text', '') or item.get('fill', '')}")
    if not summary.get("representative_primitives"):
        lines.append("none")
    return "\n".join(lines).rstrip() + "\n"


def save_composition_report(
    inspection: CompositionInspection,
    report_json_path: Any,
    report_txt_path: Any,
    preview_svg_path: Any,
) -> tuple[Path, Path, Path]:
    """Save JSON, text, and SVG outputs for a composition inspection."""
    from renderers.svg import write_svg_preview

    json_path = Path(report_json_path)
    txt_path = Path(report_txt_path)
    svg_path = Path(preview_svg_path)

    for path in (json_path, txt_path, svg_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(
        json.dumps(inspection.report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    txt_path.write_text(format_composition_report(inspection.report), encoding="utf-8")

    summary = inspection.report.get("summary") or {}
    preview_scene = inspection.preview_scene
    if isinstance(preview_scene, dict):
        preview_scene = normalize_scene(preview_scene)
    write_svg_preview(
        preview_scene,
        svg_path,
        time_ms=0,
        width=int(summary.get("width", 1280)),
        height=int(summary.get("height", 720)),
    )
    return json_path, txt_path, svg_path


def _build_report(
    scene: Scene,
    composition: Any,
    representation: Any,
) -> dict[str, Any]:
    payload = composition.to_dict()
    elements = [dict(element) for element in payload.get("elements", [])]
    semantic = payload.get("semantic") or {}
    primitive_counts = _primitive_counts(elements)
    representative = _representative_primitives(elements)

    return {
        "version": REPORT_VERSION,
        "scene_id": scene.id,
        "narration": scene.narration,
        "duration_ms": scene.duration_ms,
        "source": "procedural_svg",
        "summary": {
            "kind": representation.kind,
            "template": payload.get("template"),
            "semantic": {
                "subject": semantic.get("subject", ""),
                "environment": semantic.get("environment", ""),
                "action": semantic.get("action", ""),
                "objects": list(semantic.get("objects") or []),
            },
            "background": payload.get("background"),
            "width": payload.get("width"),
            "height": payload.get("height"),
            "element_count": len(elements),
            "primitive_counts": primitive_counts,
            "representative_primitives": representative,
            "slots": list(payload.get("slots") or []),
        },
        "read_only": True,
    }


def _preview_scene_from_payload(scene: Scene, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "sceneId": scene.id,
        "renderer": "svg",
        "duration_ms": scene.duration_ms,
        "narration": scene.narration,
        "subtitles": list(scene.subtitles or []),
        "visual": {
            "background": payload.get("background", "#f8fafc"),
            "width": int(payload.get("width", 1280)),
            "height": int(payload.get("height", 720)),
            "elements": [dict(element) for element in payload.get("elements", [])],
        },
    }


def _primitive_counts(elements: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for element in elements:
        kind = str(element.get("kind", element.get("type", "")))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _representative_primitives(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for element in elements:
        kind = str(element.get("kind", element.get("type", "")))
        if kind in seen:
            continue
        seen.add(kind)
        item = {"kind": kind}
        if "text" in element:
            item["text"] = element["text"]
        elif "fill" in element:
            item["fill"] = element["fill"]
        selected.append(item)
        if len(selected) >= 3:
            break
    return selected
