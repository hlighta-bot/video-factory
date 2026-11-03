"""Deterministic procedural visual composition for SVG fallback.

Purpose:
    Convert a VisualSpec / semantic payload into a small vocabulary of reusable
    SVG primitives with stable composition rules.

Core logic:
    1. Interpret semantic subject/environment/action/objects from VisualSpec.
    2. Build every subject from a local layout (centre, size, part grid) instead
       of unrelated absolute slots, and derive object positions from the layout.
    3. Keep every subject/object inside the real inner canvas box with a margin.
    4. Emit generic SVG-compatible primitives only: rect, circle, ellipse,
       polygon, text.

Input data:
    - Scene-like object with visual.visual_plan semantic data and/or VisualSpec.

Output data:
    - Payload dict ready for VisualRepresentation.kind == "procedural_svg".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from .model import Scene
from .normalizer import normalize_scene
from .visual_spec import VisualSpec, adapt_whiteboard

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720

# 内部画框的真实边界，必须与 compose_procedural_scene 中绘制的画框 rect 保持一致。
CANVAS = (96, 132, 1088, 340)
FRAME_MARGIN = 16

BACKGROUND_FILL = "#f8fafc"
FRAME_FILL = "#e2e8f0"

CENTER_X = CANVAS[0] + CANVAS[2] // 2
GROUND_Y = CANVAS[1] + CANVAS[3] - FRAME_MARGIN
SAFE_BOX = (
    CANVAS[0] + FRAME_MARGIN,
    CANVAS[1] + FRAME_MARGIN,
    CANVAS[0] + CANVAS[2] - FRAME_MARGIN,
    CANVAS[1] + CANVAS[3] - FRAME_MARGIN,
)


# ---------------------------------------------------------------------------
# 基本图形构造
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | int:
    """把坐标规整为干净的数值，同时过滤 NaN / Infinity。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    rounded = round(number, 2)
    return int(rounded) if rounded.is_integer() else rounded


def _kind_of(element: dict[str, Any]) -> str:
    return str(element.get("kind", element.get("type", "")))


def _points_str(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{_num(x)},{_num(y)}" for x, y in points)


def _point_pairs(points: str) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for chunk in str(points or "").split():
        coords = chunk.split(",")
        if len(coords) < 2:
            continue
        try:
            pairs.append((float(coords[0]), float(coords[1])))
        except ValueError:
            continue
    return pairs


def _rect(x: Any, y: Any, width: Any, height: Any, fill: str, stroke: str | None = None, stroke_width: int = 2) -> dict[str, Any]:
    element: dict[str, Any] = {
        "kind": "rect",
        "x": _num(x),
        "y": _num(y),
        "width": _num(width),
        "height": _num(height),
        "fill": fill,
    }
    if stroke:
        element["stroke"] = stroke
        element["stroke-width"] = stroke_width
    return element


def _circle(cx: Any, cy: Any, r: Any, fill: str, stroke: str | None = None, stroke_width: int = 2) -> dict[str, Any]:
    element: dict[str, Any] = {"kind": "circle", "cx": _num(cx), "cy": _num(cy), "r": _num(r), "fill": fill}
    if stroke:
        element["stroke"] = stroke
        element["stroke-width"] = stroke_width
    return element


def _ellipse(cx: Any, cy: Any, rx: Any, ry: Any, fill: str, stroke: str | None = None, stroke_width: int = 2) -> dict[str, Any]:
    element: dict[str, Any] = {
        "kind": "ellipse",
        "cx": _num(cx),
        "cy": _num(cy),
        "rx": _num(rx),
        "ry": _num(ry),
        "fill": fill,
    }
    if stroke:
        element["stroke"] = stroke
        element["stroke-width"] = stroke_width
    return element


def _polygon(points: list[tuple[float, float]], fill: str, stroke: str | None = None, stroke_width: int = 2) -> dict[str, Any]:
    element: dict[str, Any] = {"kind": "polygon", "points": _points_str(points), "fill": fill}
    if stroke:
        element["stroke"] = stroke
        element["stroke-width"] = stroke_width
    return element


# ---------------------------------------------------------------------------
# 主体局部布局
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SubjectLayout:
    """主体的局部坐标体系：边界框 + 可选的部件度量。"""

    x0: float
    y0: float
    x1: float
    y1: float
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def _layout_wall() -> tuple[list[dict], _SubjectLayout]:
    width, height = 920, 36
    x = CENTER_X - width / 2
    y = GROUND_Y - height
    return [_rect(x, y, width, height, "#78350f", "#451a03", 2)], _SubjectLayout(x, y, x + width, y + height)


def _layout_mountain() -> tuple[list[dict], _SubjectLayout]:
    width, height = 820, 80
    x = CENTER_X - width / 2
    y = GROUND_Y - height
    return [_rect(x, y, width, height, "#cbd5e1", "#475569", 2)], _SubjectLayout(x, y, x + width, y + height)


def _layout_panda() -> tuple[list[dict], _SubjectLayout]:
    radius = 50
    cx, cy = CENTER_X, GROUND_Y - radius
    return [_circle(cx, cy, radius, "#0f172a", "#020617", 2)], _SubjectLayout(cx - radius, cy - radius, cx + radius, cy + radius)


def _layout_train() -> tuple[list[dict], _SubjectLayout]:
    width, height = 760, 40
    x = CENTER_X - width / 2
    y = GROUND_Y - 16 - height
    return [_rect(x, y, width, height, "#0f766e", "#134e4a", 2)], _SubjectLayout(x, y, x + width, y + height)


def _layout_scene() -> tuple[list[dict], _SubjectLayout]:
    width, height = 920, 36
    x = CENTER_X - width / 2
    y = GROUND_Y - height
    return [_rect(x, y, width, height, "#94a3b8", "#475569", 2)], _SubjectLayout(x, y, x + width, y + height)


def _layout_cloud() -> tuple[list[dict], _SubjectLayout]:
    """Soft cloud silhouette built from overlapping rounded primitives."""
    cx, cy = CENTER_X, CANVAS[1] + 188
    fill, stroke = "#f8fafc", "#475569"
    elements = [
        _ellipse(cx, cy + 24, 108, 38, fill, stroke, 2),
        _circle(cx - 58, cy + 12, 34, fill, stroke, 2),
        _circle(cx - 18, cy - 10, 45, fill, stroke, 2),
        _circle(cx + 34, cy - 5, 40, fill, stroke, 2),
        _circle(cx + 72, cy + 14, 29, fill, stroke, 2),
    ]
    return elements, _SubjectLayout(cx - 108, cy - 55, cx + 108, cy + 62)


def _layout_bird() -> tuple[list[dict], _SubjectLayout]:
    """A compact flying bird built from body, head, wing, tail, and beak shapes."""
    cx, cy = CENTER_X, CANVAS[1] + 200
    elements = [
        _ellipse(cx, cy, 70, 38, "#60a5fa", "#1e3a8a", 2),
        _polygon([(cx - 56, cy - 4), (cx - 96, cy - 24), (cx - 76, cy + 15)], "#2563eb", "#1e3a8a", 2),
        _polygon([(cx - 18, cy - 12), (cx + 22, cy - 72), (cx + 34, cy - 4)], "#93c5fd", "#1e3a8a", 2),
        _circle(cx + 58, cy - 22, 25, "#60a5fa", "#1e3a8a", 2),
        _polygon([(cx + 80, cy - 25), (cx + 116, cy - 14), (cx + 80, cy - 7)], "#f59e0b", "#92400e", 2),
    ]
    return elements, _SubjectLayout(cx - 96, cy - 72, cx + 116, cy + 38)


def _layout_boat() -> tuple[list[dict], _SubjectLayout]:
    """A small sailboat above two simple wave bands."""
    cx, cy = CENTER_X, CANVAS[1] + 194
    elements = [
        _polygon([(cx - 112, cy + 34), (cx + 112, cy + 34), (cx + 76, cy + 82), (cx - 78, cy + 82)], "#92400e", "#451a03", 2),
        _rect(cx - 4, cy - 110, 8, 145, "#78350f", "#451a03", 2),
        _polygon([(cx - 8, cy - 100), (cx - 8, cy + 18), (cx - 88, cy + 18)], "#f8fafc", "#334155", 2),
        _polygon([(cx + 8, cy - 78), (cx + 8, cy + 18), (cx + 74, cy + 18)], "#bfdbfe", "#334155", 2),
        _polygon([(cx - 228, cy + 106), (cx - 150, cy + 96), (cx - 70, cy + 106), (cx + 10, cy + 96), (cx + 90, cy + 106), (cx + 170, cy + 96), (cx + 228, cy + 106), (cx + 228, cy + 116), (cx - 228, cy + 116)], "#38bdf8", "#0369a1", 1),
    ]
    return elements, _SubjectLayout(cx - 228, cy - 110, cx + 228, cy + 116)


def _layout_rocket() -> tuple[list[dict], _SubjectLayout]:
    """机身、头锥、舷窗自下而上叠放；尾焰从机身尾端向下延伸，发射台在尾焰下方。"""
    body_w, body_h = 76, 142
    nose_h, flame_h = 46, 42
    pad_w, pad_h = 132, 10

    pad_y = GROUND_Y - pad_h - 2
    body_bottom = pad_y - 6 - flame_h
    body_top = body_bottom - body_h
    nose_tip = body_top - nose_h
    body_x = CENTER_X - body_w / 2
    window_r = 15
    window_cy = body_top + 54

    elements = [
        _rect(body_x, body_top, body_w, body_h, "#94a3b8", "#0f172a", 2),
        _polygon(
            [(body_x, body_top), (body_x + body_w, body_top), (CENTER_X, nose_tip)],
            "#ef4444",
            "#0f172a",
            2,
        ),
        _circle(CENTER_X, window_cy, window_r, "#38bdf8", "#0f172a", 2),
        _polygon(
            [(CENTER_X - 24, body_bottom), (CENTER_X + 24, body_bottom), (CENTER_X, body_bottom + flame_h)],
            "#f97316",
            "#c2410c",
            1,
        ),
    ]
    layout = _SubjectLayout(
        body_x,
        nose_tip,
        body_x + body_w,
        pad_y + pad_h,
        {
            "pad_x": CENTER_X - pad_w / 2,
            "pad_y": pad_y,
            "pad_w": pad_w,
            "pad_h": pad_h,
            "body_bottom": body_bottom,
        },
    )
    return elements, layout


def _layout_person() -> tuple[list[dict], _SubjectLayout]:
    """头在躯干上方，双腿接躯干底部，双臂由 objects 层接躯干两侧。"""
    head_r, neck = 24, 6
    torso_w, torso_h = 54, 70
    leg_w, leg_h, leg_gap = 16, 52, 8
    arm_w, arm_h = 14, 56

    torso_bottom = GROUND_Y - leg_h
    torso_top = torso_bottom - torso_h
    torso_x = CENTER_X - torso_w / 2
    head_cy = torso_top - neck - head_r

    elements = [
        _circle(CENTER_X, head_cy, head_r, "#fde68a", "#0f172a", 2),
        _rect(torso_x, torso_top, torso_w, torso_h, "#3b82f6", "#0f172a", 2),
        _rect(CENTER_X - leg_gap / 2 - leg_w, torso_bottom, leg_w, leg_h, "#1e3a5f", "#0f172a", 1),
        _rect(CENTER_X + leg_gap / 2, torso_bottom, leg_w, leg_h, "#1e3a5f", "#0f172a", 1),
    ]
    layout = _SubjectLayout(
        torso_x - arm_w,
        head_cy - head_r,
        torso_x + torso_w + arm_w,
        GROUND_Y,
        {
            "arm_left_x": torso_x - arm_w,
            "arm_right_x": torso_x + torso_w,
            "arm_y": torso_top + 8,
            "arm_w": arm_w,
            "arm_h": arm_h,
        },
    )
    return elements, layout


def _layout_tree() -> tuple[list[dict], _SubjectLayout]:
    """树干落地，三段树冠自上而下覆盖树干顶端。"""
    trunk_w, trunk_h = 26, 86
    canopy_r = 56
    side_r = 34

    trunk_x = CENTER_X - trunk_w / 2
    trunk_top = GROUND_Y - trunk_h
    canopy_cy = trunk_top - 22
    side_cy = trunk_top - 2

    elements = [
        _rect(trunk_x, trunk_top, trunk_w, trunk_h, "#78350f", "#451a03", 2),
        _circle(CENTER_X, canopy_cy, canopy_r, "#15803d", "#14532d", 2),
        _circle(CENTER_X - 42, side_cy, side_r, "#166534", "#14532d", 1),
        _circle(CENTER_X + 42, side_cy, side_r, "#166534", "#14532d", 1),
    ]
    layout = _SubjectLayout(CENTER_X - canopy_r, canopy_cy - canopy_r, CENTER_X + canopy_r, GROUND_Y)
    return elements, layout


def _layout_building() -> tuple[list[dict], _SubjectLayout]:
    """屋顶压在墙体上沿，门窗按墙体内的网格排布。"""
    wall_w, wall_h = 260, 190
    roof_h, roof_overhang = 54, 22
    win_w, win_h = 46, 32
    door_w, door_h = 56, 58

    wall_x = CENTER_X - wall_w / 2
    wall_top = GROUND_Y - wall_h

    elements: list[dict] = [
        _rect(wall_x, wall_top, wall_w, wall_h, "#94a3b8", "#0f172a", 2),
        _polygon(
            [
                (wall_x - roof_overhang, wall_top),
                (wall_x + wall_w + roof_overhang, wall_top),
                (CENTER_X, wall_top - roof_h),
            ],
            "#7f1d1d",
            "#450a0a",
            2,
        ),
    ]

    column_padding, column_gap = 30, 31
    for row in range(2):
        for column in range(3):
            win_x = wall_x + column_padding + column * (win_w + column_gap)
            win_y = wall_top + 34 + row * 52
            elements.append(_rect(win_x, win_y, win_w, win_h, "#93c5fd", "#0f172a", 1))

    elements.append(_rect(CENTER_X - door_w / 2, GROUND_Y - door_h, door_w, door_h, "#7f1d1d", "#450a0a", 1))

    layout = _SubjectLayout(wall_x - roof_overhang, wall_top - roof_h, wall_x + wall_w + roof_overhang, GROUND_Y)
    return elements, layout


def _layout_rabbit() -> tuple[list[dict], _SubjectLayout]:
    """头贴身体上方，双耳插入头顶，尾巴接在身体后侧。"""
    body_rx, body_ry = 42, 32
    head_r = 24
    ear_w, ear_h = 12, 34
    tail_r = 10

    body_cy = GROUND_Y - body_ry
    head_cy = body_cy - body_ry - head_r + 6
    head_top = head_cy - head_r
    ear_y = head_top - ear_h + 6

    elements = [
        _ellipse(CENTER_X, body_cy, body_rx, body_ry, "#cbd5e1", "#475569", 2),
        _circle(CENTER_X, head_cy, head_r, "#cbd5e1", "#475569", 2),
        _rect(CENTER_X - 20, ear_y, ear_w, ear_h, "#cbd5e1", "#475569", 1),
        _rect(CENTER_X + 8, ear_y, ear_w, ear_h, "#cbd5e1", "#475569", 1),
        _circle(CENTER_X - body_rx - 2, body_cy + 6, tail_r, "#cbd5e1", "#475569", 1),
    ]
    layout = _SubjectLayout(
        CENTER_X - body_rx - tail_r - 2,
        ear_y,
        CENTER_X + body_rx,
        GROUND_Y,
        {"body_x0": CENTER_X - body_rx, "body_cy": body_cy, "body_rx": body_rx, "head_cy": head_cy, "head_r": head_r, "tail_r": tail_r},
    )
    return elements, layout


def _layout_moon() -> tuple[list[dict], _SubjectLayout]:
    """月面圆 + 三处环形山；星星由 objects 层放在月亮外侧合理距离。"""
    radius = 62
    cx, cy = CENTER_X, 320
    star_r = 10
    star_x = cx + radius + 56
    star_y = cy - radius - 30

    elements = [
        _circle(cx, cy, radius, "#fef3c7", "#d97706", 2),
        _circle(cx - 32, cy - 24, 10, "#d97706", "#92400e", 1),
        _circle(cx + 24, cy + 16, 8, "#d97706", "#92400e", 1),
        _circle(cx - 12, cy + 32, 6, "#d97706", "#92400e", 1),
    ]
    layout = _SubjectLayout(
        cx - radius,
        cy - radius,
        cx + radius,
        cy + radius,
        {"star_x": star_x, "star_y": star_y, "star_r": star_r},
    )
    return elements, layout


_SUBJECT_LAYOUTS: dict[str, Callable[[], tuple[list[dict], _SubjectLayout]]] = {
    "wall": _layout_wall,
    "mountain": _layout_mountain,
    "panda": _layout_panda,
    "train": _layout_train,
    "scene": _layout_scene,
    "cloud": _layout_cloud,
    "bird": _layout_bird,
    "boat": _layout_boat,
    "rocket": _layout_rocket,
    "person": _layout_person,
    "tree": _layout_tree,
    "building": _layout_building,
    "rabbit": _layout_rabbit,
    "moon": _layout_moon,
}

# 主体自身已包含的结构部件，避免同一主体出现重复树干/第二屋顶/第三只耳朵等。
_SUBJECT_OWNED_OBJECTS: dict[str, set[str]] = {
    "rocket": {"flame"},
    "person": {"legs"},
    "tree": {"leaves", "trunk"},
    "building": {"windows", "roof"},
    "rabbit": {"ears", "tail"},
}

# ---------------------------------------------------------------------------
# 附属对象：全部由主体边界与度量推导位置
# ---------------------------------------------------------------------------


def _place_mountain(layout: _SubjectLayout) -> list[dict]:
    height = 46
    return [_rect(layout.x0 + 12, layout.y0 - height - 4, 220, height, "#cbd5e1", "#475569", 2)]


def _place_watchtower(layout: _SubjectLayout) -> list[dict]:
    width, height = 36, 72
    return [_rect(layout.cx - 140, layout.y0 + 6 - height, width, height, "#7f1d1d", "#450a0a", 2)]


def _place_peak(layout: _SubjectLayout) -> list[dict]:
    width, height = 120, 28
    return [_rect(layout.cx - width / 2, layout.y0 + 6, width, height, "#f8fafc", "#475569", 1)]


def _place_snow(layout: _SubjectLayout) -> list[dict]:
    return _place_peak(layout)


def _place_bamboo(layout: _SubjectLayout) -> list[dict]:
    width, height = 16, 72
    return [_rect(layout.x1 + 36, GROUND_Y - height, width, height, "#15803d", "#14532d", 2)]


def _place_tracks(layout: _SubjectLayout) -> list[dict]:
    width, height = 260, 8
    return [_rect(layout.x0, min(layout.y1 + 4, GROUND_Y - height), width, height, "#475569", "#1e293b", 1)]


def _place_forest(layout: _SubjectLayout) -> list[dict]:
    width, height = 160, 40
    return [_rect(layout.x1 + 40, GROUND_Y - height, width, height, "#166534", "#14532d", 1)]


def _place_launch_pad(layout: _SubjectLayout) -> list[dict]:
    metrics = layout.metrics
    if "pad_x" not in metrics:
        return []
    return [_rect(metrics["pad_x"], metrics["pad_y"], metrics["pad_w"], metrics["pad_h"], "#475569", "#1e293b", 2)]


def _place_arms(layout: _SubjectLayout) -> list[dict]:
    metrics = layout.metrics
    if "arm_left_x" not in metrics:
        return []
    return [
        _rect(metrics["arm_left_x"], metrics["arm_y"], metrics["arm_w"], metrics["arm_h"], "#fde68a", "#0f172a", 1),
        _rect(metrics["arm_right_x"], metrics["arm_y"], metrics["arm_w"], metrics["arm_h"], "#fde68a", "#0f172a", 1),
    ]


def _place_stars(layout: _SubjectLayout) -> list[dict]:
    metrics = layout.metrics
    if "star_x" not in metrics:
        return []
    return [_circle(metrics["star_x"], metrics["star_y"], metrics["star_r"], "#fef3c7", "#d97706", 1)]


_OBJECT_PLACERS: dict[str, Callable[[_SubjectLayout], list[dict]]] = {
    "mountain": _place_mountain,
    "watchtower": _place_watchtower,
    "peak": _place_peak,
    "snow": _place_snow,
    "bamboo": _place_bamboo,
    "tracks": _place_tracks,
    "forest": _place_forest,
    "launch_pad": _place_launch_pad,
    "arms": _place_arms,
    "stars": _place_stars,
}


def _default_layout() -> _SubjectLayout:
    width, height = 320, 120
    x = CENTER_X - width / 2
    y = GROUND_Y - height
    return _SubjectLayout(x, y, x + width, y + height)


# ---------------------------------------------------------------------------
# 画框约束
# ---------------------------------------------------------------------------


def _stroke_pad(element: dict[str, Any]) -> float:
    try:
        return max(0.0, float(element.get("stroke-width", 0) or 0)) / 2.0
    except (TypeError, ValueError):
        return 0.0


def _element_bounds(element: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """返回元素的完整几何边界（含描边），无法解析或尺寸非法时返回 None。"""
    kind = _kind_of(element)
    pad = _stroke_pad(element)
    try:
        if kind in ("rect", "rectangle"):
            x = float(element.get("x", 0))
            y = float(element.get("y", 0))
            width = float(element.get("width", 0))
            height = float(element.get("height", 0))
            if width < 0:
                x, width = x + width, -width
            if height < 0:
                y, height = y + height, -height
            if width <= 0 or height <= 0:
                return None
            return (x - pad, y - pad, x + width + pad, y + height + pad)
        if kind == "circle":
            cx = float(element.get("cx", 0))
            cy = float(element.get("cy", 0))
            r = float(element.get("r", 0))
            if r <= 0:
                return None
            return (cx - r - pad, cy - r - pad, cx + r + pad, cy + r + pad)
        if kind == "ellipse":
            cx = float(element.get("cx", 0))
            cy = float(element.get("cy", 0))
            rx = float(element.get("rx", 0))
            ry = float(element.get("ry", 0))
            if rx <= 0 or ry <= 0:
                return None
            return (cx - rx - pad, cy - ry - pad, cx + rx + pad, cy + ry + pad)
        if kind == "polygon":
            points = _point_pairs(str(element.get("points", "")))
            if len(points) < 3:
                return None
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        if kind == "line":
            x1 = float(element.get("x1", element.get("x", 0)))
            y1 = float(element.get("y1", element.get("y", 0)))
            x2 = float(element.get("x2", x1))
            y2 = float(element.get("y2", y1))
            return (min(x1, x2) - pad, min(y1, y2) - pad, max(x1, x2) + pad, max(y1, y2) + pad)
    except (TypeError, ValueError):
        return None
    return None


def _translate_element(element: dict[str, Any], dx: float, dy: float) -> dict[str, Any]:
    data = dict(element)
    kind = _kind_of(data)
    if kind in ("rect", "rectangle"):
        data["x"] = _num(float(data.get("x", 0)) + dx)
        data["y"] = _num(float(data.get("y", 0)) + dy)
    elif kind in ("circle", "ellipse"):
        data["cx"] = _num(float(data.get("cx", 0)) + dx)
        data["cy"] = _num(float(data.get("cy", 0)) + dy)
    elif kind == "polygon":
        points = [(px + dx, py + dy) for px, py in _point_pairs(str(data.get("points", "")))]
        data["points"] = _points_str(points)
    elif kind == "line":
        for key, delta, fallback_key in (("x1", dx, "x"), ("x2", dx, "x"), ("y1", dy, "y"), ("y2", dy, "y")):
            data[key] = _num(float(data.get(key, data.get(fallback_key, 0))) + delta)
    return data


def _scale_element(element: dict[str, Any], origin: tuple[float, float], factor: float) -> dict[str, Any] | None:
    if factor <= 0:
        return None
    ox, oy = origin
    data = dict(element)
    kind = _kind_of(data)
    if kind in ("rect", "rectangle"):
        x = ox + (float(data.get("x", 0)) - ox) * factor
        y = oy + (float(data.get("y", 0)) - oy) * factor
        data["x"] = _num(x)
        data["y"] = _num(y)
        data["width"] = _num(float(data.get("width", 0)) * factor)
        data["height"] = _num(float(data.get("height", 0)) * factor)
    elif kind == "circle":
        data["cx"] = _num(ox + (float(data.get("cx", 0)) - ox) * factor)
        data["cy"] = _num(oy + (float(data.get("cy", 0)) - oy) * factor)
        data["r"] = _num(float(data.get("r", 0)) * factor)
    elif kind == "ellipse":
        data["cx"] = _num(ox + (float(data.get("cx", 0)) - ox) * factor)
        data["cy"] = _num(oy + (float(data.get("cy", 0)) - oy) * factor)
        data["rx"] = _num(float(data.get("rx", 0)) * factor)
        data["ry"] = _num(float(data.get("ry", 0)) * factor)
    elif kind == "polygon":
        points = [
            (ox + (px - ox) * factor, oy + (py - oy) * factor)
            for px, py in _point_pairs(str(data.get("points", "")))
        ]
        data["points"] = _points_str(points)
    elif kind == "line":
        for x_key, y_key in (("x1", "y1"), ("x2", "y2")):
            data[x_key] = _num(ox + (float(data.get(x_key, 0)) - ox) * factor)
            data[y_key] = _num(oy + (float(data.get(y_key, 0)) - oy) * factor)
    else:
        return None
    return data


def _fit_group(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把一组相关图形作为整体约束在画框安全区内，保持部件之间的相对关系。"""
    parsed: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []
    for element in elements:
        bounds = _element_bounds(element)
        if bounds is None or not all(math.isfinite(value) for value in bounds):
            continue
        parsed.append((element, bounds))
    if not parsed:
        return []

    safe_x0, safe_y0, safe_x1, safe_y1 = SAFE_BOX
    x0 = min(bounds[0] for _, bounds in parsed)
    y0 = min(bounds[1] for _, bounds in parsed)
    x1 = max(bounds[2] for _, bounds in parsed)
    y1 = max(bounds[3] for _, bounds in parsed)
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return []

    factor = min(1.0, (safe_x1 - safe_x0) / width, (safe_y1 - safe_y0) / height)
    if factor < 1.0:
        origin = (x0 + width / 2, y0 + height / 2)
        scaled: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []
        for element, _ in parsed:
            candidate = _scale_element(element, origin, factor)
            if candidate is None:
                continue
            bounds = _element_bounds(candidate)
            if bounds is None or not all(math.isfinite(value) for value in bounds):
                continue
            scaled.append((candidate, bounds))
        if not scaled:
            return []
        parsed = scaled
        x0 = min(bounds[0] for _, bounds in parsed)
        y0 = min(bounds[1] for _, bounds in parsed)
        x1 = max(bounds[2] for _, bounds in parsed)
        y1 = max(bounds[3] for _, bounds in parsed)

    dx = 0.0
    if x0 < safe_x0:
        dx = safe_x0 - x0
    elif x1 > safe_x1:
        dx = safe_x1 - x1
    dy = 0.0
    if y0 < safe_y0:
        dy = safe_y0 - y0
    elif y1 > safe_y1:
        dy = safe_y1 - y1

    if dx == 0.0 and dy == 0.0:
        return [element for element, _ in parsed]
    return [_translate_element(element, dx, dy) for element, _ in parsed]


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProceduralComposition:
    """Composition result ready for VisualRepresentation payload."""

    template: str
    slots: list[str]
    semantic: dict[str, Any]
    elements: list[dict[str, Any]]
    background: str
    width: int
    height: int
    duration_ms: int
    source_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "slots": list(self.slots),
            "semantic": dict(self.semantic),
            "elements": [dict(element) for element in self.elements],
            "background": self.background,
            "width": self.width,
            "height": self.height,
            "duration_ms": self.duration_ms,
            "source_text": self.source_text,
        }


def compose_procedural_scene(
    scene: Scene | dict,
    spec: VisualSpec | None = None,
) -> ProceduralComposition:
    """Compose a deterministic SVG fallback from semantic data."""
    normalized = normalize_scene(scene) if isinstance(scene, dict) else scene
    visual = normalized.visual or {}
    visual_plan = visual.get("visual_plan") or {}
    semantic = visual_plan.get("semantic") or {}
    resolved_spec = spec or adapt_whiteboard(normalized)

    subject = _subject_from_payload(resolved_spec, semantic, visual_plan)
    environment = _environment_from_payload(resolved_spec, semantic, visual_plan)
    action = str(semantic.get("action") or visual_plan.get("action") or "show" or "show")
    objects = _objects_from_payload(resolved_spec, semantic)
    intent = str(visual.get("intent", "story_scene") or "story_scene")
    narration = str(normalized.narration or "")

    if environment == "sky" and subject in {"cloud", "bird"}:
        return _compose_sky_illustration(
            normalized, subject, environment, action, intent, objects, semantic, narration
        )

    elements: list[dict[str, Any]] = [
        {"kind": "rect", "x": 0, "y": 0, "width": DEFAULT_WIDTH, "height": DEFAULT_HEIGHT, "fill": BACKGROUND_FILL},
        {"kind": "rect", "x": CANVAS[0], "y": CANVAS[1], "width": CANVAS[2], "height": CANVAS[3], "fill": FRAME_FILL, "stroke": "#0f172a", "stroke-width": 4},
    ]

    subject_elements, layout = _subject_layout(subject)
    placed_elements = list(subject_elements)
    for object_name in objects[:3]:
        placed_elements.extend(_object_elements(object_name, subject, layout))
    elements.extend(_fit_group(placed_elements))

    elements.extend([
        {"kind": "text", "x": 144, "y": 206, "text": subject, "fill": "#0f172a", "font-size": 64},
        {"kind": "text", "x": 144, "y": 302, "text": environment, "fill": "#334155", "font-size": 32},
        {"kind": "text", "x": 144, "y": 372, "text": action, "fill": "#475569", "font-size": 28},
    ])

    return ProceduralComposition(
        template="semantic_story_fallback",
        slots=[subject, environment, action, intent],
        semantic=dict(semantic),
        elements=elements,
        background=BACKGROUND_FILL,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        duration_ms=int(normalized.duration_ms or 0),
        source_text=narration[:240],
    )


def _compose_sky_illustration(
    scene: Scene,
    subject: str,
    environment: str,
    action: str,
    intent: str,
    objects: list[str],
    semantic: dict[str, Any],
    narration: str,
) -> ProceduralComposition:
    """Build a full-frame editorial illustration for sky stories.

    This template intentionally avoids the old boxed debug-board treatment:
    atmospheric color layers, distant hills, sunlight and large subjects fill
    the frame, while subtitles remain the only on-screen text.
    """
    dusk = any(token in narration for token in ("夕阳", "暮色", "黄昏"))
    if dusk:
        sky_top, sky_bottom = "#667a9d", "#f3bd91"
        sun_fill, sun_halo = "#ee9f68", "#f8c994"
        far_hill, mid_hill, near_hill = "#9a8b9d", "#736b83", "#4c596f"
    else:
        sky_top, sky_bottom = "#79bfe1", "#f3dfbc"
        sun_fill, sun_halo = "#f6d18e", "#ffe6b0"
        far_hill, mid_hill, near_hill = "#9dbda9", "#6f9b83", "#547c6d"

    elements: list[dict[str, Any]] = [
        *_gradient_bands(sky_top, sky_bottom, 0, 500, 100),
        _circle(1050, 275 if dusk else 172, 92, sun_halo),
        _circle(1050, 275 if dusk else 172, 67, sun_fill),
        _polygon([(0, 505), (210, 365), (440, 505), (650, 385), (890, 520), (1100, 405), (1280, 495), (1280, 720), (0, 720)], far_hill),
        _polygon([(0, 570), (250, 440), (460, 585), (730, 435), (980, 590), (1180, 465), (1280, 520), (1280, 720), (0, 720)], mid_hill),
        _polygon([(0, 640), (260, 540), (520, 650), (790, 525), (1050, 645), (1280, 555), (1280, 720), (0, 720)], near_hill),
        # Delicate wind strokes and tiny wildflowers add texture without
        # competing with the story subjects or captions.
        _line(110, 430, 245, 405, "#e7f4f1", 3),
        _line(155, 454, 275, 433, "#e7f4f1", 2),
        _line(1010, 355, 1165, 375, "#e7f4f1", 3),
        _circle(166, 626, 5, "#f4d7a1"),
        _circle(196, 612, 4, "#f6e3b5"),
        _circle(1120, 634, 5, "#f4d7a1"),
    ]

    if dusk:
        elements.append(_line(748, 492, 1040, 462, "#493f4c", 14))
        elements.extend([
            _ellipse(780, 480, 19, 8, "#78906f"),
            _ellipse(1010, 466, 19, 8, "#78906f"),
        ])

    if subject == "cloud":
        cloud, cloud_layout = _layout_cloud()
        # Recenter and enlarge the cloud for a cinematic, uncluttered frame.
        cloud = [_scale_element(item, (CENTER_X, CANVAS[1] + 188), 1.55) for item in cloud]
        elements.extend(item for item in cloud if item is not None)
        if "bird" in objects:
            elements.extend(_place_bird_in_sky(824, 310 if not dusk else 450, scale=1.0))
    else:
        cloud, _ = _layout_cloud()
        for item in cloud:
            moved = _translate_element(item, -300, 10)
            scaled = _scale_element(moved, (CENTER_X - 300, CANVAS[1] + 198), 0.9)
            if scaled is not None:
                elements.append(scaled)
        if dusk:
            elements.extend(_place_bird_in_sky(835, 436, scale=0.78))
        else:
            bird, _ = _layout_bird()
            elements.extend(_scale_element(item, (CENTER_X, CANVAS[1] + 200), 1.35) for item in bird)

    return ProceduralComposition(
        template="sky_story_illustration",
        slots=[subject, environment, action, intent],
        semantic=dict(semantic),
        elements=elements,
        background=sky_top,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        duration_ms=int(scene.duration_ms or 0),
        source_text=narration[:240],
    )


def _gradient_bands(top: str, bottom: str, y0: int, y1: int, steps: int) -> list[dict[str, Any]]:
    """Approximate a soft sky gradient using subtle raster-friendly color bands."""
    def rgb(value: str) -> tuple[int, int, int]:
        raw = value.lstrip("#")
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))

    first, last = rgb(top), rgb(bottom)
    result = []
    for index in range(steps):
        ratio = index / max(1, steps - 1)
        color = "#" + "".join(
            f"{round(a + (b - a) * ratio):02x}"
            for a, b in zip(first, last)
        )
        y = y0 + round((y1 - y0) * index / steps)
        next_y = y0 + round((y1 - y0) * (index + 1) / steps)
        result.append(_rect(0, y, DEFAULT_WIDTH, next_y - y + 1, color))
    return result


def _line(x1: float, y1: float, x2: float, y2: float, stroke: str, width: float) -> dict[str, Any]:
    return {"kind": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "stroke": stroke, "stroke-width": width}


def _place_bird_in_sky(cx: float, cy: float, scale: float = 1.0) -> list[dict[str, Any]]:
    """Layered bluebird illustration with a wing, tail, eye and warm beak."""
    def p(x: float, y: float) -> tuple[float, float]:
        return (cx + x * scale, cy + y * scale)

    return [
        _ellipse(cx, cy + 12 * scale, 58 * scale, 34 * scale, "#456f93", "#315776", 2),
        _polygon([p(-25, 5), p(0, -42), p(34, -4), p(8, 25)], "#6e9db7", "#315776", 2),
        _polygon([p(-48, 8), p(-91, -8), p(-72, 23), p(-93, 39), p(-37, 36)], "#456f93", "#315776", 2),
        _circle(cx + 47 * scale, cy - 10 * scale, 30 * scale, "#5a87a5", "#315776", 2),
        _polygon([p(72, -8), p(112, 1), p(72, 8)], "#e4a85f", "#a96e38", 1),
        _circle(cx + 55 * scale, cy - 15 * scale, 4 * scale, "#172d3c"),
    ]


def _subject_from_payload(spec: VisualSpec, semantic: dict[str, Any], visual_plan: dict[str, Any]) -> str:
    for element in spec.elements:
        if element.get("role") == "subject":
            return str(element.get("type") or semantic.get("subject") or visual_plan.get("subject") or "scene") or "scene"
    return str(semantic.get("subject") or visual_plan.get("subject") or "scene") or "scene"


def _environment_from_payload(spec: VisualSpec, semantic: dict[str, Any], visual_plan: dict[str, Any]) -> str:
    for element in spec.elements:
        if element.get("type") == "environment":
            return str(element.get("value") or semantic.get("environment") or visual_plan.get("environment") or "story") or "story"
    return str(semantic.get("environment") or visual_plan.get("environment") or "story") or "story"


def _objects_from_payload(spec: VisualSpec, semantic: dict[str, Any]) -> list[str]:
    objects: list[str] = []
    for element in spec.elements:
        if element.get("role") == "object":
            value = str(element.get("type") or "").strip()
            if value and value not in objects:
                objects.append(value)
    if not objects:
        for item in semantic.get("objects") or []:
            value = str(item or "").strip()
            if value and value not in objects:
                objects.append(value)
    return objects


def _subject_layout(subject: str) -> tuple[list[dict], _SubjectLayout]:
    builder = _SUBJECT_LAYOUTS.get(subject)
    if builder is None:
        return [], _default_layout()
    return builder()


def _object_elements(name: str, subject: str, layout: _SubjectLayout) -> list[dict]:
    if name in _SUBJECT_OWNED_OBJECTS.get(subject, set()):
        return []
    placer = _OBJECT_PLACERS.get(name)
    if placer is None:
        # 未知对象：附在主体右侧地面，仍走画框约束。
        return [_rect(layout.x1 + 24, layout.y1 - 30, 90, 30, "#cbd5e1", "#475569", 1)]
    return [element for element in placer(layout) if element is not None]
