"""SVG Renderer，支持静态场景和最小元素动画。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

from .base import Renderer, SceneLike
from scene.normalizer import normalize_scene

XML_NS = "http://www.w3.org/2000/svg"
DEFAULT_FONT_PATH = Path("/usr/share/fonts/dejavu/DejaVuSans.ttf")
ANIMATION_FPS = 30
STATIC_DEFAULT_DURATION_MS = 3000


def _to_ms(value: Any, default: int = 0) -> int:
    """将动画时间字段安全转换为整数毫秒。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _hex_to_rgb(color: str | None) -> tuple[int, int, int]:
    """支持 '#rgb'、'#rrggbb' 和常见命名色。"""
    if not color or color.lower() == "none":
        return (0, 0, 0)

    named = {
        "black": "#000000",
        "white": "#ffffff",
        "red": "#ff0000",
        "green": "#008000",
        "blue": "#0000ff",
        "orange": "#ffa500",
    }
    color = named.get(color.lower(), color)
    if color.startswith("#"):
        raw = color[1:]
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        if len(raw) == 6:
            return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    return (0, 0, 0)


def _font(size: int):
    """加载 DejaVu 字体；失败时退回 Pillow 默认字体。"""
    if DEFAULT_FONT_PATH.exists():
        try:
            return ImageFont.truetype(str(DEFAULT_FONT_PATH), size)
        except Exception:
            pass
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _element_state(element: dict, time_ms: int):
    """计算元素在指定时间点的透明度与位置偏移。

    返回 None 表示元素当前不渲染。
    """
    animation = element.get("animation")
    start = _to_ms(element.get("start_ms"), 0)
    duration = _to_ms(element.get("duration_ms"), 0)

    if animation == "fade":
        if time_ms < start:
            return None
        if duration <= 0 or time_ms >= start + duration:
            return {"alpha": 1.0, "offset_x": 0.0, "offset_y": 0.0}
        return {"alpha": _clamp((time_ms - start) / duration), "offset_x": 0.0, "offset_y": 0.0}

    if animation == "appear":
        if time_ms < start:
            return None
        return {"alpha": 1.0, "offset_x": 0.0, "offset_y": 0.0}

    if animation == "move":
        if duration <= 0 or time_ms >= start + duration:
            return {
                "alpha": 1.0,
                "offset_x": float(element.get("to_x", element.get("x", 0))) - float(element.get("x", 0)),
                "offset_y": float(element.get("to_y", element.get("y", 0))) - float(element.get("y", 0)),
            }
        if time_ms < start:
            return {"alpha": 1.0, "offset_x": 0.0, "offset_y": 0.0}
        t = _clamp((time_ms - start) / duration)
        return {
            "alpha": 1.0,
            "offset_x": (float(element.get("to_x", element.get("x", 0))) - float(element.get("x", 0))) * t,
            "offset_y": (float(element.get("to_y", element.get("y", 0))) - float(element.get("y", 0))) * t,
        }

    return {"alpha": 1.0, "offset_x": 0.0, "offset_y": 0.0}


def _kind_of(element: dict) -> str:
    return element.get("kind", element.get("type", ""))


def _positioned_element(element: dict, state) -> dict:
    """返回带动画位置偏移的元素副本，便于 Pillow 绘制。"""
    data = dict(element)
    data["x"] = float(element.get("x", 0)) + state["offset_x"]
    data["y"] = float(element.get("y", 0)) + state["offset_y"]
    return data


def _rocket_svg(parent, element: dict) -> None:
    """写入一个内置简版火箭 SVG 组合。"""
    x = float(element.get("x", 0))
    y = float(element.get("y", 0))
    width = float(element.get("width", 80))
    height = float(element.get("height", 160))
    body = element.get("fill", "#e2e8f0")
    nose = element.get("nose_fill", "#ef4444")
    window = element.get("window_fill", "#38bdf8")
    flame = element.get("flame_fill", "#f97316")

    ET.SubElement(parent, "rect", {
        "x": str(x), "y": str(y), "width": str(width), "height": str(height),
        "rx": "24", "fill": body, "stroke": "#0f172a", "stroke-width": "4",
    })
    ET.SubElement(parent, "polygon", {
        "points": f"{x},{y} {x + width},{y} {x + width / 2},{y - 40}",
        "fill": nose, "stroke": "#0f172a", "stroke-width": "4",
    })
    ET.SubElement(parent, "circle", {
        "cx": str(x + width / 2), "cy": str(y + height * 0.36),
        "r": str(max(8, width * 0.16)), "fill": window,
        "stroke": "#0f172a", "stroke-width": "3",
    })
    ET.SubElement(parent, "polygon", {
        "points": f"{x + width * 0.25},{y + height} {x + width * 0.75},{y + height} {x + width / 2},{y + height + 45}",
        "fill": flame,
    })


def _draw_rocket(draw: ImageDraw.ImageDraw, element: dict) -> None:
    """用 Pillow 近似绘制内置简版火箭。"""
    x = float(element.get("x", 0))
    y = float(element.get("y", 0))
    width = float(element.get("width", 80))
    height = float(element.get("height", 160))
    body = _hex_to_rgb(element.get("fill", "#e2e8f0"))
    nose = _hex_to_rgb(element.get("nose_fill", "#ef4444"))
    window = _hex_to_rgb(element.get("window_fill", "#38bdf8"))
    flame = _hex_to_rgb(element.get("flame_fill", "#f97316"))

    draw.rectangle([x, y, x + width, y + height], fill=body, outline=(15, 23, 42), width=4)
    draw.polygon([(x, y), (x + width, y), (x + width / 2, y - 40)], fill=nose, outline=(15, 23, 42))
    draw.ellipse([x + width / 2 - width * 0.16, y + height * 0.36 - width * 0.16, x + width / 2 + width * 0.16, y + height * 0.36 + width * 0.16], fill=window, outline=(15, 23, 42))
    draw.polygon([(x + width * 0.25, y + height), (x + width * 0.75, y + height), (x + width / 2, y + height + 45)], fill=flame)


def write_svg_preview(scene, output_path: Path, time_ms: int = 0, width: int = 1280, height: int = 720) -> Path:
    """Write a single SVG preview frame to disk."""
    visual = scene.visual
    background = visual.get("background", "#ffffff")
    root = ET.Element("svg", {
        "xmlns": XML_NS, "width": str(width), "height": str(height),
        "viewBox": f"0 0 {width} {height}",
    })
    ET.SubElement(root, "rect", {"x": "0", "y": "0", "width": str(width), "height": str(height), "fill": background})

    for element in visual.get("elements", []):
        kind = _kind_of(element)
        state = _element_state(element, time_ms)
        if state is None:
            continue

        attrs = {key: str(value) for key, value in element.items() if key not in ("kind", "type", "text")}
        if state["alpha"] < 1.0:
            attrs["opacity"] = f"{state['alpha']:.3f}"

        if kind in ("rect", "rectangle"):
            target = _positioned_element(element, state)
            attrs.update({k: str(v) for k, v in target.items() if k in ("x", "y")})
            ET.SubElement(root, "rect", attrs)
        elif kind == "circle":
            ET.SubElement(root, "circle", attrs)
        elif kind == "ellipse":
            ET.SubElement(root, "ellipse", attrs)
        elif kind == "polygon":
            pts = str(element.get("points", "")).strip()
            # Shift polygon points by animation offset when present
            ox = state["offset_x"]
            oy = state["offset_y"]
            if ox or oy:
                pairs = pts.split()
                shifted: list[str] = []
                for pair in pairs:
                    coords = pair.split(",")
                    if len(coords) == 2:
                        shifted.append(f"{float(coords[0]) + ox:.2f},{float(coords[1]) + oy:.2f}")
                pts = " ".join(shifted)
            attrs["points"] = pts
            ET.SubElement(root, "polygon", attrs)
        elif kind == "line":
            target = _positioned_element(element, state)
            x1 = float(target.get("x", 0))
            y1 = float(target.get("y", 0))
            x2 = float(element.get("x2", element.get("x1", x1)))
            y2 = float(element.get("y2", element.get("y1", y1)))
            x2 += state["offset_x"]
            y2 += state["offset_y"]
            line_attrs = {
                "x1": str(x1), "y1": str(y1),
                "x2": str(x2), "y2": str(y2),
                "stroke": str(element.get("stroke", "#0f172a")),
                "stroke-width": str(element.get("stroke-width", 2)),
            }
            if state["alpha"] < 1.0:
                line_attrs["opacity"] = f"{state['alpha']:.3f}"
            ET.SubElement(root, "line", line_attrs)
        elif kind == "text":
            target = _positioned_element(element, state)
            attrs.update({"x": str(target["x"]), "y": str(target["y"])})
            text_el = ET.SubElement(root, "text", attrs)
            text_el.text = str(element.get("text", ""))
        elif kind == "rocket":
            group = ET.SubElement(root, "g", {"opacity": f"{state['alpha']:.3f}"})
            _rocket_svg(group, _positioned_element(element, state))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    output_path.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
    return output_path


def _parse_polygon_points(points_str: str, ox: float = 0.0, oy: float = 0.0) -> list[tuple[float, float]]:
    """Safely parse an SVG points string like 'x1,y1 x2,y2' into [(x,y),...].

    No eval()/exec() — only float() on individually split tokens.
    """
    result: list[tuple[float, float]] = []
    for pair in points_str.split():
        coords = pair.split(",")
        if len(coords) >= 2:
            try:
                px = float(coords[0]) + ox
                py = float(coords[1]) + oy
                result.append((px, py))
            except (ValueError, IndexError):
                continue
    return result


def _draw_polygon(draw, element: dict, ox: float, oy: float, alpha: int | None = None) -> None:
    """Draw a polygon element with safe points parsing."""
    pts = _parse_polygon_points(str(element.get("points", "")), ox, oy)
    if len(pts) < 3:
        return
    fill_val = _hex_to_rgb(element.get("fill", "#000000"))
    stroke_val = _hex_to_rgb(element.get("stroke")) if element.get("stroke") else None
    sw = max(0, int(float(element.get("stroke-width", 0))))
    if alpha is None:
        draw.polygon(pts, fill=fill_val, outline=stroke_val, width=sw)
    else:
        draw.polygon(pts, fill=(*fill_val, alpha), outline=(*stroke_val, alpha) if stroke_val else None, width=sw)


def _draw_ellipse(draw, element: dict, alpha: int | None = None) -> None:
    """Draw an ellipse element with cx, cy, rx, ry."""
    cx, cy = float(element.get("cx", 0)), float(element.get("cy", 0))
    rx, ry = float(element.get("rx", 0)), float(element.get("ry", 0))
    if rx <= 0 or ry <= 0:
        return
    box = [cx - rx, cy - ry, cx + rx, cy + ry]
    fill_val = _hex_to_rgb(element.get("fill", "#000000"))
    stroke_val = _hex_to_rgb(element.get("stroke")) if element.get("stroke") else None
    sw = max(0, int(float(element.get("stroke-width", 0))))
    if alpha is None:
        draw.ellipse(box, fill=fill_val, outline=stroke_val, width=sw)
    else:
        draw.ellipse(box, fill=(*fill_val, alpha), outline=(*stroke_val, alpha) if stroke_val else None, width=sw)


def _draw_line(draw, element: dict, ox: float, oy: float, alpha: int | None = None) -> None:
    """Draw a line element with safe point parsing."""
    x1, y1 = float(element.get("x1", element.get("x", 0))) + ox, float(element.get("y1", element.get("y", 0))) + oy
    x2, y2 = float(element.get("x2", element.get("x1", 0))) + ox, float(element.get("y2", element.get("y1", 0))) + oy
    stroke_val = _hex_to_rgb(element.get("stroke", "#0f172a"))
    sw = max(0, int(float(element.get("stroke-width", 2))))
    if alpha is None:
        draw.line([x1, y1, x2, y2], fill=stroke_val, width=sw)
    else:
        draw.line([x1, y1, x2, y2], fill=(*stroke_val, alpha), width=sw)


def _draw_text_at_baseline(draw, element: dict, x: float, y: float, fill, alpha: int | None = None) -> None:
    """按文本基线绘制文字，与 SVG 中 text y 的语义保持一致。"""
    text = str(element.get("text", ""))
    size = int(element.get("font-size", 36))
    font = _font(size)
    color = fill if alpha is None else (*fill, alpha)
    if font is None:
        draw.text((x, y), text, fill=color)
        return
    try:
        draw.text((x, y), text, fill=color, font=font, anchor="ls")
        return
    except (TypeError, ValueError):
        pass
    # 字体不支持 anchor 时退回 ascent 偏移，视觉位置与 SVG 基线保持一致。
    try:
        ascent, _ = font.getmetrics()
    except Exception:
        ascent = int(size * 0.8)
    draw.text((x, y - ascent), text, fill=color, font=font)


def _draw_frame(image: Image.Image, scene, width: int, height: int, time_ms: int) -> None:
    """用 Pillow 绘制指定时刻的 PNG 帧。"""
    visual = scene.visual
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width - 1, height - 1], fill=_hex_to_rgb(visual.get("background", "#ffffff")))

    for element in visual.get("elements", []):
        kind = _kind_of(element)
        state = _element_state(element, time_ms)
        if state is None:
            continue

        alpha = max(0, min(255, int(state["alpha"] * 255)))
        target = _positioned_element(element, state)
        x, y = float(target["x"]), float(target["y"])

        if alpha >= 255:
            if kind in ("rect", "rectangle"):
                draw.rectangle([x, y, x + float(target["width"]), y + float(target["height"])], fill=_hex_to_rgb(target.get("fill", "#000000")), outline=_hex_to_rgb(target.get("stroke")) if target.get("stroke") else None, width=max(0, int(float(target.get("stroke-width", 0)))))
            elif kind == "circle":
                cx, cy, r = float(target.get("cx", x)), float(target.get("cy", y)), float(target.get("r"))
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_hex_to_rgb(target.get("fill", "#000000")), outline=_hex_to_rgb(target.get("stroke")) if target.get("stroke") else None, width=max(0, int(float(target.get("stroke-width", 0)))))
            elif kind == "polygon":
                _draw_polygon(draw, target, 0, 0)
            elif kind == "ellipse":
                _draw_ellipse(draw, target)
            elif kind == "line":
                _draw_line(draw, target, 0, 0)
            elif kind == "text":
                _draw_text_at_baseline(draw, target, x, y, _hex_to_rgb(target.get("fill", "#000000")))
            elif kind == "rocket":
                _draw_rocket(draw, target)
        else:
            # Pillow 原生 draw 不支持半透明合成，这里用单独 RGBA overlay 做线性 alpha。
            overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            if kind in ("rect", "rectangle"):
                overlay_draw.rectangle([x, y, x + float(target["width"]), y + float(target["height"])], fill=(*_hex_to_rgb(target.get("fill", "#000000")), alpha), outline=(*_hex_to_rgb(target.get("stroke", "#000000")), alpha) if target.get("stroke") else None, width=max(0, int(float(target.get("stroke-width", 0)))))
            elif kind == "circle":
                cx, cy, r = float(target.get("cx", x)), float(target.get("cy", y)), float(target.get("r"))
                overlay_draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*_hex_to_rgb(target.get("fill", "#000000")), alpha), outline=(*_hex_to_rgb(target.get("stroke", "#000000")), alpha) if target.get("stroke") else None, width=max(0, int(float(target.get("stroke-width", 0)))))
            elif kind == "polygon":
                _draw_polygon(overlay_draw, target, 0, 0, alpha)
            elif kind == "ellipse":
                _draw_ellipse(overlay_draw, target, alpha)
            elif kind == "line":
                _draw_line(overlay_draw, target, 0, 0, alpha)
            elif kind == "text":
                _draw_text_at_baseline(overlay_draw, target, x, y, _hex_to_rgb(target.get("fill", "#000000")), alpha)
            elif kind == "rocket":
                _draw_rocket(overlay_draw, target)
                channel = overlay.split()[3]
                overlay.putalpha(channel.point(lambda value: int(value * alpha / 255)))
            image.alpha_composite(overlay)


def _render_animation_mp4(scene, output_path: Path, width: int, height: int, duration_ms: int) -> tuple[Path, Path]:
    """逐帧生成 SVG/PNG，并用 ffmpeg 合成 MP4。"""
    frames_dir = output_path.parent / f"{output_path.stem}.frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_count = max(1, round(duration_ms / 1000 * ANIMATION_FPS))

    for index in range(frame_count):
        time_ms = round(index * 1000 / ANIMATION_FPS)
        frame_svg = frames_dir / f"{index:04d}.svg"
        frame_png = frames_dir / f"{index:04d}.png"
        write_svg_preview(scene, frame_svg, time_ms, width, height)
        image = Image.new("RGBA", (width, height), (*_hex_to_rgb(scene.visual.get("background", "#ffffff")), 255))
        _draw_frame(image, scene, width, height, time_ms)
        image.convert("RGB").save(frame_png)

    svg_path = frames_dir / "latest.svg"
    write_svg_preview(scene, svg_path, min(duration_ms - 1, frame_count * 1000 // ANIMATION_FPS), width, height)

    _ffmpeg_encode_frames(frames_dir, output_path, ANIMATION_FPS, duration_ms)
    return svg_path, frames_dir


def _encode_static_mp4(scene, svg_path: Path, output_path: Path) -> None:
    """兼容 Phase 5：没有动画元素时仍输出静态 MP4。"""
    visual = scene.visual
    width = int(visual.get("width", 1280))
    height = int(visual.get("height", 720))
    duration_ms = max(1, int(scene.duration_ms or visual.get("duration_ms", STATIC_DEFAULT_DURATION_MS)))
    duration_sec = duration_ms / 1000.0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path = output_path.parent / f"{output_path.stem}.png"
    image = Image.new("RGBA", (width, height), (*_hex_to_rgb(visual.get("background", "#ffffff")), 255))
    _draw_frame(image, scene, width, height, 0)
    image.convert("RGB").save(frame_path)

    _ffmpeg_encode_static(frame_path, output_path, duration_sec, ANIMATION_FPS)
    frame_path.unlink(missing_ok=True)


def _ffmpeg_encode_static(frame_path: Path, output_path: Path, duration_sec: float, fps: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found; cannot encode SVG MP4")
    subprocess.run([
        ffmpeg, "-y", "-loop", "1", "-i", str(frame_path),
        "-t", f"{duration_sec:.3f}", "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output_path),
    ], check=True)


def _ffmpeg_encode_frames(frames_dir: Path, output_path: Path, fps: int, duration_ms: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found; cannot encode SVG MP4")
    duration_sec = duration_ms / 1000.0
    subprocess.run([
        ffmpeg, "-y", "-framerate", str(fps), "-i", str(frames_dir / "%04d.png"),
        "-t", f"{duration_sec:.3f}", "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output_path),
    ], check=True)


class SVGRenderer(Renderer):
    """最小 SVG / Procedural Renderer。"""

    def capabilities(self) -> frozenset[str]:
        return frozenset({"infographic", "text", "diagram", "shape", "data", "simple_animation"})

    def render(self, scene: SceneLike, output_path: Path) -> Path:
        scene = normalize_scene(scene) if isinstance(scene, dict) else scene
        output_path.parent.mkdir(parents=True, exist_ok=True)

        visual = scene.visual
        width = int(visual.get("width", 1280))
        height = int(visual.get("height", 720))
        duration_ms = max(1, int(scene.duration_ms or visual.get("duration_ms", STATIC_DEFAULT_DURATION_MS)))
        has_animation = any(element.get("animation") in ("appear", "fade", "move") for element in visual.get("elements", []))

        if has_animation:
            _render_animation_mp4(scene, output_path, width, height, duration_ms)
        else:
            svg_path = write_svg_preview(scene, output_path.parent / f"{output_path.stem}.svg", 0, width, height)
            _encode_static_mp4(scene, svg_path, output_path)

        return output_path
