"""Phase 23 Scene Plan Inspector MVP.

Purpose:
    Inspect an existing Scene Plan artifact without changing Scene or renderer
    behavior. The inspector consumes Scene Plan JSON through the existing Scene
    Plan loader, then produces a deterministic human-readable or JSON report.

Core logic:
    1. Load the Scene Plan artifact with load_scene_plan().
    2. Convert the artifact to Scene objects with scene_plan_from_dict().
    3. Extract stable metadata from Scene fields and existing visual.plan data.
    4. Add lightweight deterministic warnings for obvious structural issues.

Input data:
    - Scene Plan JSON path
    - list[Scene] already loaded by the caller

Output data:
    - dict report
    - text report

Non-goals:
    - No rendering, no asset resolution, no validation replacement.
    - No runtime metadata such as timestamps, process IDs, or temp paths.
    - No Scene model extension.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scene.scene_plan import load_scene_plan, scene_plan_from_dict


PLAN_VISUAL_KEYS: tuple[str, ...] = (
    "subject",
    "action",
    "environment",
    "motion",
    "focus",
    "formula",
)


def inspect_scene_plan(path: Path | str) -> dict[str, Any]:
    """Inspect a Scene Plan artifact and return a deterministic report dict."""
    plan = load_scene_plan(path)
    scenes = scene_plan_from_dict(plan)
    return inspect_scenes(scenes, artifact=plan)


def inspect_scenes(scenes: list, artifact: dict[str, Any] | None = None) -> dict[str, Any]:
    """Inspect Scene objects and return a deterministic report dict."""
    warnings: list[dict[str, Any]] = []
    scene_reports: list[dict[str, Any]] = []

    for index, scene in enumerate(scenes, start=1):
        visual = scene.visual or {}
        visual_plan = visual.get("visual_plan") if isinstance(visual, dict) else None
        subtitles = list(scene.subtitles or [])

        if not scene.id:
            warnings.append({"level": "WARNING", "scene": f"Scene {index:02d}", "message": "id is empty"})
        if not scene.renderer:
            warnings.append({"level": "WARNING", "scene": f"Scene {index:02d}", "message": "renderer is empty"})
        if scene.duration_ms <= 0:
            warnings.append({"level": "WARNING", "scene": f"Scene {index:02d}", "message": "duration_ms must be > 0"})
        if not scene.narration:
            warnings.append({"level": "WARNING", "scene": f"Scene {index:02d}", "message": "narration is empty"})
        if not visual_plan:
            warnings.append({"level": "WARNING", "scene": f"Scene {index:02d}", "message": "visual_plan is missing"})

        subtitle_summary: dict[str, Any] = {"count": len(subtitles)}
        if subtitles:
            subtitle_summary["first"] = subtitles[0]
            subtitle_summary["last"] = subtitles[-1]

        scene_reports.append(
            {
                "index": index,
                "id": scene.id,
                "renderer": scene.renderer,
                "duration_ms": scene.duration_ms,
                "duration_sec": round(scene.duration_ms / 1000.0, 3),
                "narration": scene.narration,
                "intent": visual.get("intent") if isinstance(visual, dict) else None,
                "visual_plan": {key: visual_plan[key] for key in PLAN_VISUAL_KEYS if isinstance(visual_plan, dict) and key in visual_plan},
                "subtitle_summary": subtitle_summary,
            }
        )

    return {
        "version": artifact.get("version") if artifact is not None else None,
        "scene_count": len(scenes),
        "scenes": scene_reports,
        "warnings": warnings,
    }


def format_scene_plan_report(report: dict[str, Any]) -> str:
    """Format a Scene Plan inspection report as deterministic text."""
    lines: list[str] = [
        "Scene Plan",
        "==========",
        f"Version: {report.get('version', '')}",
        f"Scene count: {report.get('scene_count', 0)}",
        "",
    ]

    for scene in report.get("scenes", []):
        index = scene.get("index") or 0
        lines.extend(
            [
                f"Scene {int(index):02d}",
                "-" * 8,
                f"ID: {scene.get('id', '')}",
                f"Renderer: {scene.get('renderer', '')}",
                f"Duration: {scene.get('duration_ms', 0)} ms ({scene.get('duration_sec', 0):.3f}s)",
                f"Narration: {scene.get('narration', '')}",
                "",
            ]
        )

        intent = scene.get("intent")
        lines.extend(["Intent:", "  " + (intent if intent else "<missing>"), ""])

        visual_plan = scene.get("visual_plan") or {}
        lines.append("Visual Plan:")
        if visual_plan:
            for key in PLAN_VISUAL_KEYS:
                if key in visual_plan:
                    lines.append(f"  {key}: {visual_plan[key]}")
        else:
            lines.append("  <missing>")
        lines.append("")

        subtitle_summary = scene.get("subtitle_summary") or {"count": 0}
        lines.extend(
            [
                f"Subtitle count: {subtitle_summary.get('count', 0)}",
                "First subtitle:",
                _format_subtitle(subtitle_summary.get("first")),
                "Last subtitle:",
                _format_subtitle(subtitle_summary.get("last")),
                "",
            ]
        )

    if report.get("warnings"):
        lines.extend(["Warnings", "========", ""])
        for warning in report["warnings"]:
            lines.append(f"- [{warning.get('level', 'WARNING')}] {warning.get('scene', '')}: {warning.get('message', '')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def save_scene_plan_report(report: dict[str, Any], text_path: Path | str, json_path: Path | str | None = None) -> tuple[Path, Path | None]:
    """Save a Scene Plan inspection report as text and optional JSON."""
    text_path = Path(text_path)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(format_scene_plan_report(report), encoding="utf-8")

    if json_path is None:
        return text_path, None

    json_path = Path(json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return text_path, json_path


def _format_subtitle(subtitle: Any) -> str:
    if not isinstance(subtitle, dict):
        return "  <missing>"
    parts = [
        f"id={subtitle.get('id', '')}",
        f"start={subtitle.get('start', '')}",
        f"end={subtitle.get('end', '')}",
        f"text={subtitle.get('text', '')}",
    ]
    return "  " + " ".join(parts)
