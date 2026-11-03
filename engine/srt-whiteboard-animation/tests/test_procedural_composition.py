"""Phase 33 procedural visual composition smoke test.

Purpose:
    Verify deterministic primitive composition for semantic fallback scenes using
    only SVG-compatible primitives supported by the existing renderer.

Input data:
    - Four representative semantic fallback texts and one unknown-topic fallback.

Output data:
    - output/phase33/procedural-composition-report.json
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from scene.model import Scene
from scene.planner import plan_scene
from scene.procedural_composition import (
    BACKGROUND_FILL,
    FRAME_FILL,
    SAFE_BOX,
    _element_bounds,
    _fit_group,
    _point_pairs,
    compose_procedural_scene,
)
from scene.visual_spec import adapt_whiteboard


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = PROJECT_ROOT / "output" / "phase33" / "procedural-composition-report.json"
SUPPORTED_KINDS = {"rect", "circle", "text", "polygon", "ellipse"}
CASES = [
    ("介绍中国的万里长城", "wall", "mountains", "extends", {"rect": 5, "text": 3}),
    ("介绍日本的富士山", "mountain", "mountain", "show", {"rect": 4, "text": 3}),
    ("介绍中国的大熊猫", "panda", "forest", "show", {"circle": 1, "rect": 3, "text": 3}),
    ("介绍日本的新干线", "train", "railway", "show", {"rect": 4, "text": 3}),
    ("火箭发射升空", "rocket", "sky", "launches", {"rect": 4, "polygon": 2, "circle": 1, "text": 3}),
    ("孩子在草地上行走", "person", "outdoor", "walks", {"rect": 7, "circle": 1, "text": 3}),
    ("一棵大树在森林里生长", "tree", "forest", "grows", {"rect": 3, "circle": 3, "text": 3}),
    ("城里的房子有许多窗户", "building", "city", "show", {"rect": 10, "polygon": 1, "text": 3}),
    ("小兔子在森林里跳跃", "rabbit", "forest", "hops", {"rect": 4, "ellipse": 1, "circle": 2, "text": 3}),
    ("夜晚的月亮闪闪发光", "moon", "sky", "glows", {"rect": 2, "circle": 5, "text": 3}),
    ("云朵旅行到蓝天", "cloud", "sky", "travels", {"rect": 2, "circle": 4, "ellipse": 1, "text": 3}),
    ("小鸟飞过天空", "bird", "sky", "flies", {"rect": 2, "circle": 1, "ellipse": 1, "polygon": 3, "text": 3}),
    ("帆船航行在海面上", "boat", "ocean", "sails", {"rect": 3, "polygon": 4, "text": 3}),
]


def _kind_counts(elements: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for element in elements:
        kind = str(element.get("kind", element.get("type", "")))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _assert_svg_compatible(payload: dict) -> None:
    kinds = set(_kind_counts(payload["elements"]))
    assert kinds and kinds.issubset(SUPPORTED_KINDS), kinds


def _assert_labels(payload: dict, subject: str, environment: str, action: str) -> None:
    labels = {element.get("text") for element in payload["elements"] if element.get("kind") == "text"}
    assert {subject, environment, action}.issubset(labels), labels


# ---------------------------------------------------------------------------
# Phase 32 M3-D: 程序化构图修复的定向检查（不写文件、不渲染媒体）
# ---------------------------------------------------------------------------

SUBJECT_CASES = [
    ("介绍中国的万里长城", "wall"),
    ("介绍日本的富士山", "mountain"),
    ("介绍中国的大熊猫", "panda"),
    ("介绍日本的新干线", "train"),
    ("火箭发射升空", "rocket"),
    ("孩子在草地上行走", "person"),
    ("一棵大树在森林里生长", "tree"),
    ("城里的房子有许多窗户", "building"),
    ("小兔子在森林里跳跃", "rabbit"),
    ("夜晚的月亮闪闪发光", "moon"),
    ("云朵旅行到蓝天", "cloud"),
    ("小鸟飞过天空", "bird"),
    ("帆船航行在海面上", "boat"),
]

SUBJECT_TEXTS = {subject: text for text, subject in SUBJECT_CASES}


def _elements_for(subject: str) -> list[dict]:
    scene = plan_scene(SUBJECT_TEXTS[subject])
    return compose_procedural_scene(scene, adapt_whiteboard(scene)).to_dict()["elements"]


def _body_elements(elements: list[dict]) -> list[dict]:
    """跳过全屏背景与内部画框两个基础元素。"""
    return elements[2:]


def _bounds(element: dict) -> tuple[float, float, float, float]:
    bounds = _element_bounds(element)
    assert bounds is not None, element
    return bounds


def _shapes(elements: list[dict], kind: str, fill: str | None = None) -> list[dict]:
    return [
        element
        for element in elements
        if element.get("kind") == kind and (fill is None or element.get("fill") == fill)
    ]


def _color_rgb(color: str) -> tuple[int, int, int]:
    raw = color.lstrip("#")
    return tuple(int(raw[index:index + 2], 16) for index in (0, 2, 4))


def _color_distance(first: str, second: str) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(_color_rgb(first), _color_rgb(second))))


def _check_subject_semantics() -> None:
    for text, subject in SUBJECT_CASES:
        scene = plan_scene(text)
        payload = compose_procedural_scene(scene, adapt_whiteboard(scene)).to_dict()
        assert payload["template"] == "semantic_story_fallback", payload["template"]
        assert payload["slots"][0] == subject, (text, payload["slots"])
        kinds = _kind_counts(payload["elements"])
        assert set(kinds).issubset(SUPPORTED_KINDS), kinds
        assert kinds.get("text", 0) >= 3, kinds
        if subject != "unknown":
            assert _body_elements(payload["elements"]), subject


def _check_rocket_layout() -> None:
    elements = _elements_for("rocket")
    bodies = _shapes(elements, "rect", "#94a3b8")
    noses = _shapes(elements, "polygon", "#ef4444")
    flames = _shapes(elements, "polygon", "#f97316")
    windows = _shapes(elements, "circle", "#38bdf8")
    pads = _shapes(elements, "rect", "#475569")
    assert len(bodies) == 1 and len(noses) == 1 and len(flames) == 1, (bodies, noses, flames)
    assert len(windows) == 1 and len(pads) == 1, (windows, pads)

    body, nose, flame, window, pad = bodies[0], noses[0], flames[0], windows[0], pads[0]
    body_bottom = body["y"] + body["height"]
    center_x = body["x"] + body["width"] / 2

    nose_points = _point_pairs(nose["points"])
    nose_base = max(point_y for _, point_y in nose_points)
    nose_tip = min(point_y for _, point_y in nose_points)
    assert nose_tip < nose_base, nose
    assert abs(nose_base - body["y"]) <= 1, (nose_base, body["y"])
    assert abs(nose_points[-1][0] - center_x) <= 1, nose

    flame_points = _point_pairs(flame["points"])
    flame_top = min(point_y for _, point_y in flame_points)
    flame_bottom = max(point_y for _, point_y in flame_points)
    assert abs(flame_top - body_bottom) <= 1, (flame_top, body_bottom)
    assert flame_bottom > body_bottom, flame
    flame_xs = [point_x for point_x, _ in flame_points]
    assert body["x"] <= min(flame_xs) and max(flame_xs) <= body["x"] + body["width"], flame

    assert body["x"] <= window["cx"] <= body["x"] + body["width"], window
    assert body["y"] <= window["cy"] <= body_bottom, window

    assert pad["y"] >= flame_bottom, (pad["y"], flame_bottom)
    assert pad["y"] >= body_bottom, (pad["y"], body_bottom)


def _check_person_layout() -> None:
    elements = _elements_for("person")
    heads = _shapes(elements, "circle")
    torsos = _shapes(elements, "rect", "#3b82f6")
    legs = sorted(_shapes(elements, "rect", "#1e3a5f"), key=lambda element: element["x"])
    arms = sorted(_shapes(elements, "rect", "#fde68a"), key=lambda element: element["x"])
    assert len(heads) == 1 and len(torsos) == 1, (heads, torsos)
    assert len(legs) == 2 and len(arms) == 2, (legs, arms)

    head, torso = heads[0], torsos[0]
    assert head["cy"] + head["r"] <= torso["y"] + 2, (head, torso)
    assert torso["x"] - 2 <= head["cx"] <= torso["x"] + torso["width"] + 2, (head, torso)

    torso_bottom = torso["y"] + torso["height"]
    for leg in legs:
        assert abs(leg["y"] - torso_bottom) <= 2, (leg, torso_bottom)
        assert torso["x"] - 2 <= leg["x"], (leg, torso)
        assert leg["x"] + leg["width"] <= torso["x"] + torso["width"] + 2, (leg, torso)

    assert arms[0]["x"] + arms[0]["width"] <= torso["x"] + 2, (arms[0], torso)
    assert arms[1]["x"] >= torso["x"] + torso["width"] - 2, (arms[1], torso)
    for arm in arms:
        assert arm["y"] >= torso["y"] - 2, (arm, torso)
        assert arm["y"] + arm["height"] <= torso_bottom + 2, (arm, torso)


def _check_tree_layout() -> None:
    elements = _elements_for("tree")
    trunks = _shapes(elements, "rect", "#78350f")
    canopies = [element for element in _shapes(elements, "circle") if element["fill"] in ("#15803d", "#166534")]
    assert len(trunks) == 1, trunks
    assert len(canopies) == 3, canopies

    trunk = trunks[0]
    trunk_top = trunk["y"]
    for canopy in canopies:
        assert canopy["cy"] < trunk_top, (canopy, trunk_top)
        assert canopy["cy"] + canopy["r"] > trunk_top, (canopy, trunk_top)
    assert trunk["y"] + trunk["height"] >= SAFE_BOX[3] - 2, trunk
    centers = sorted(canopy["cx"] for canopy in canopies)
    assert centers[0] < trunk["x"] + trunk["width"] / 2 < centers[-1], centers


def _check_building_layout() -> None:
    elements = _elements_for("building")
    walls = _shapes(elements, "rect", "#94a3b8")
    roofs = _shapes(elements, "polygon", "#7f1d1d")
    windows = _shapes(elements, "rect", "#93c5fd")
    doors = [element for element in _shapes(elements, "rect", "#7f1d1d") if element["height"] > element["width"]]
    assert len(walls) == 1 and len(roofs) == 1, (walls, roofs)
    assert len(windows) == 6, windows
    assert len(doors) == 1, doors

    wall, roof, door = walls[0], roofs[0], doors[0]
    roof_points = _point_pairs(roof["points"])
    roof_base = max(point_y for _, point_y in roof_points)
    assert abs(roof_base - wall["y"]) <= 1, (roof_base, wall["y"])
    base_xs = [point_x for point_x, point_y in roof_points if abs(point_y - roof_base) <= 0.01]
    assert len(base_xs) == 2, roof_points
    assert min(base_xs) <= wall["x"], (min(base_xs), wall["x"])
    assert max(base_xs) >= wall["x"] + wall["width"], (max(base_xs), wall["x"] + wall["width"])
    assert min(point_y for _, point_y in roof_points) < roof_base, roof

    wall_right = wall["x"] + wall["width"]
    wall_bottom = wall["y"] + wall["height"]
    for window in windows:
        assert wall["x"] < window["x"] < wall_right, (window, wall)
        assert window["x"] + window["width"] < wall_right, (window, wall)
        assert wall["y"] < window["y"] < wall_bottom, (window, wall)
        assert window["y"] + window["height"] < wall_bottom, (window, wall)

    for index, first in enumerate(windows):
        for second in windows[index + 1:]:
            overlap_x = min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"])
            overlap_y = min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"])
            assert overlap_x <= 0 or overlap_y <= 0, (first, second)

    assert wall["x"] < door["x"] < wall_right, (door, wall)
    assert door["x"] + door["width"] < wall_right, (door, wall)
    assert door["y"] + door["height"] <= wall_bottom + 1, (door, wall)


def _check_rabbit_layout() -> None:
    elements = _elements_for("rabbit")
    bodies = _shapes(elements, "ellipse")
    circles = _shapes(elements, "circle")
    heads = [element for element in circles if element["r"] >= 20]
    tails = [element for element in circles if element["r"] < 20]
    ears = sorted([element for element in _shapes(elements, "rect") if element["height"] > element["width"]], key=lambda element: element["x"])
    assert len(bodies) == 1 and len(heads) == 1 and len(tails) == 1, (bodies, heads, tails)
    assert len(ears) == 2, ears

    body, head, tail = bodies[0], heads[0], tails[0]
    assert head["cy"] < body["cy"], (head, body)
    assert head["cy"] + head["r"] >= body["cy"] - body["ry"] - 2, (head, body)

    head_left = head["cx"] - head["r"]
    head_right = head["cx"] + head["r"]
    head_top = head["cy"] - head["r"]
    for ear in ears:
        assert ear["y"] < head_top, (ear, head)
        assert ear["y"] + ear["height"] >= head_top + 4, (ear, head)
        assert head_left <= ear["x"], (ear, head)
        assert ear["x"] + ear["width"] <= head_right, (ear, head)
    assert ears[1]["x"] - (ears[0]["x"] + ears[0]["width"]) > 0, ears

    body_left = body["cx"] - body["rx"]
    assert tail["cx"] < body_left, (tail, body_left)
    assert tail["cx"] + tail["r"] >= body_left - 4, (tail, body_left)


def _check_moon_layout() -> None:
    elements = _elements_for("moon")
    circles = _shapes(elements, "circle")
    assert len(circles) == 5, circles
    moon = max(circles, key=lambda element: element["r"])
    rest = [element for element in circles if element is not moon]
    stars = [element for element in rest if element["cx"] > moon["cx"] + moon["r"]]
    craters = [element for element in rest if element not in stars]
    assert len(stars) == 1, (stars, rest)
    assert len(craters) == 3, craters

    for crater in craters:
        distance = math.hypot(crater["cx"] - moon["cx"], crater["cy"] - moon["cy"])
        assert distance + crater["r"] <= moon["r"], (crater, moon)

    star = stars[0]
    gap = math.hypot(star["cx"] - moon["cx"], star["cy"] - moon["cy"]) - moon["r"] - star["r"]
    assert gap > 0, (star, moon)
    assert gap <= 140, (gap, star, moon)


def _check_frame_bounds() -> None:
    safe_x0, safe_y0, safe_x1, safe_y1 = SAFE_BOX
    for _text, subject in SUBJECT_CASES:
        for element in _body_elements(_elements_for(subject)):
            if element.get("kind") == "text":
                continue
            element_x0, element_y0, element_x1, element_y1 = _bounds(element)
            assert element_x0 >= safe_x0 - 0.01 and element_y0 >= safe_y0 - 0.01, (subject, element)
            assert element_x1 <= safe_x1 + 0.01 and element_y1 <= safe_y1 + 0.01, (subject, element)


def _check_subject_contrast() -> None:
    pairs = [
        ("rocket", "rect", "#94a3b8", FRAME_FILL),
        ("building", "rect", "#94a3b8", FRAME_FILL),
        ("rabbit", "circle", "#cbd5e1", BACKGROUND_FILL),
        ("person", "rect", "#3b82f6", FRAME_FILL),
        ("tree", "circle", "#15803d", FRAME_FILL),
        ("moon", "circle", "#fef3c7", FRAME_FILL),
    ]
    for subject, kind, fill, against in pairs:
        matches = _shapes(_elements_for(subject), kind, fill)
        assert matches, (subject, kind, fill)
        assert fill != against, (subject, fill, against)
        assert _color_distance(fill, against) >= 40, (subject, fill, against, _color_distance(fill, against))


def _check_determinism() -> None:
    for text, subject in SUBJECT_CASES:
        first = _elements_for(subject)
        scene = plan_scene(text)
        second = compose_procedural_scene(scene, adapt_whiteboard(scene)).to_dict()["elements"]
        assert first == second, subject


def _check_invalid_geometry() -> None:
    rejected = [
        {"kind": "rect", "x": float("nan"), "y": 0.0, "width": 10.0, "height": 10.0, "fill": "#000000"},
        {"kind": "rect", "x": 0.0, "y": 0.0, "width": 0.0, "height": 10.0, "fill": "#000000"},
        {"kind": "circle", "cx": 0.0, "cy": 0.0, "r": -5.0, "fill": "#000000"},
        {"kind": "polygon", "points": "0,0 1,1", "fill": "#000000"},
    ]
    for element in rejected:
        assert _fit_group([element]) == [], element

    huge = {"kind": "rect", "x": -5000.0, "y": -5000.0, "width": 20000.0, "height": 20000.0, "fill": "#000000"}
    fitted = _fit_group([huge])
    assert len(fitted) == 1, fitted
    huge_x0, huge_y0, huge_x1, huge_y1 = _bounds(fitted[0])
    assert all(math.isfinite(value) for value in (huge_x0, huge_y0, huge_x1, huge_y1))
    assert huge_x0 >= SAFE_BOX[0] - 0.01 and huge_y0 >= SAFE_BOX[1] - 0.01, fitted
    assert huge_x1 <= SAFE_BOX[2] + 0.01 and huge_y1 <= SAFE_BOX[3] + 0.01, fitted

    far = {"kind": "rect", "x": 9000.0, "y": 9000.0, "width": 40.0, "height": 40.0, "fill": "#000000"}
    moved = _fit_group([far])
    assert len(moved) == 1, moved
    far_x0, far_y0, far_x1, far_y1 = _bounds(moved[0])
    assert far_x1 <= SAFE_BOX[2] + 0.01 and far_y1 <= SAFE_BOX[3] + 0.01, moved
    assert far_x0 >= SAFE_BOX[0] - 0.01 and far_y0 >= SAFE_BOX[1] - 0.01, moved


def _check_text_baseline_consistency() -> None:
    """SVG 用文本基线定位，PNG 必须落到同一视觉位置。"""
    from PIL import Image, ImageDraw

    from renderers.svg import _draw_text_at_baseline, _font

    size = 64
    baseline = 160
    element = {"kind": "text", "text": "Ag", "font-size": size, "fill": "#0f172a"}

    image = Image.new("RGB", (240, 240), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    _draw_text_at_baseline(draw, element, 20, baseline, (255, 255, 255))
    box = image.getbbox()
    assert box is not None, "text is not drawn"
    ascent_pixels = baseline - box[1]
    assert ascent_pixels > 0, (box, baseline)

    font = _font(size)
    expected_ascent = None
    if font is not None:
        try:
            expected_ascent = -font.getbbox("Ag", anchor="ls")[1]
        except Exception:
            try:
                expected_ascent = font.getmetrics()[0]
            except Exception:
                expected_ascent = None
    if expected_ascent:
        assert abs(ascent_pixels - expected_ascent) <= 4, (ascent_pixels, expected_ascent)
    else:
        assert 0.6 * size <= ascent_pixels <= 1.05 * size, (box, baseline, ascent_pixels)
    assert box[3] <= baseline + 0.1 * size, (box, baseline)

    overlay = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
    _draw_text_at_baseline(ImageDraw.Draw(overlay), element, 20, baseline, (255, 255, 255), 128)
    alpha_box = overlay.getbbox()
    assert alpha_box is not None, "alpha text is not drawn"
    assert abs(alpha_box[1] - box[1]) <= 2, (alpha_box, box)
    assert overlay.getpixel((120, baseline - 5))[3] in (0, 128), overlay.getpixel((120, baseline - 5))


M3D_CHECKS = [
    ("subject_semantics", _check_subject_semantics),
    ("rocket_layout", _check_rocket_layout),
    ("person_layout", _check_person_layout),
    ("tree_layout", _check_tree_layout),
    ("building_layout", _check_building_layout),
    ("rabbit_layout", _check_rabbit_layout),
    ("moon_layout", _check_moon_layout),
    ("frame_bounds", _check_frame_bounds),
    ("subject_contrast", _check_subject_contrast),
    ("determinism", _check_determinism),
    ("invalid_geometry", _check_invalid_geometry),
    ("text_baseline_consistency", _check_text_baseline_consistency),
]


def run_m3d_checks() -> dict:
    passed = 0
    failures = []
    for name, check in M3D_CHECKS:
        try:
            check()
        except AssertionError as error:
            failures.append(f"{name}: {error}")
        else:
            passed += 1
    return {"passed": passed, "failed": len(failures), "total": len(M3D_CHECKS), "failures": failures}


def main() -> int:
    results = []

    for text, expected_subject, expected_environment, expected_action, expected_counts in CASES:
        scene = plan_scene(text)
        spec = adapt_whiteboard(scene)
        composition = compose_procedural_scene(scene, spec).to_dict()
        first = compose_procedural_scene(scene, spec).to_dict()

        assert composition["template"] == "semantic_story_fallback", composition
        assert composition["slots"][:3] == [expected_subject, expected_environment, expected_action], composition["slots"]
        _assert_svg_compatible(composition)
        _assert_labels(composition, expected_subject, expected_environment, expected_action)
        assert all(kind in SUPPORTED_KINDS for kind in _kind_counts(composition["elements"])), _kind_counts(composition["elements"])
        assert _kind_counts(composition["elements"]) == expected_counts, _kind_counts(composition["elements"])
        assert first == composition

        results.append({
            "text": text,
            "subject": expected_subject,
            "environment": expected_environment,
            "action": expected_action,
            "kind_counts": _kind_counts(composition["elements"]),
            "element_count": len(composition["elements"]),
            "deterministic": True,
        })

    unknown_scene = Scene(
        id="unknown-composition",
        renderer="auto",
        visual={
            "kind": "story",
            "requirements": ["illustration"],
            "intent": "story_scene",
            "visual_plan": {"subject": "unknown", "action": "show", "environment": "landscape"},
        },
        narration="unknown topic",
        subtitles=[],
        duration_ms=4200,
    )
    unknown = compose_procedural_scene(unknown_scene, adapt_whiteboard(unknown_scene)).to_dict()
    _assert_svg_compatible(unknown)
    assert unknown["slots"] == ["unknown", "landscape", "show", "story_scene"], unknown["slots"]
    assert unknown["duration_ms"] == 4200
    assert _kind_counts(unknown["elements"])["rect"] == 2

    m3d = run_m3d_checks()
    print(f"m3d_checks={m3d['passed']}/{m3d['total']} passed, failed={m3d['failed']}")
    assert m3d["failed"] == 0, m3d["failures"]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({
        "phase": 33,
        "status": "pass",
        "primitive_vocabulary": sorted(SUPPORTED_KINDS),
        "cases": results,
        "unknown_topic_kind_counts": _kind_counts(unknown["elements"]),
        "m3d_checks": m3d,
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"cases={len(results)}")
    print(f"primitive_vocabulary={','.join(sorted(SUPPORTED_KINDS))}")
    print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
