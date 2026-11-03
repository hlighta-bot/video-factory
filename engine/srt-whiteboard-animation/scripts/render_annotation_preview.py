import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int):
    font_paths = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("/mnt/c/Windows/Fonts/msyh.ttc"),
    ]
    for font_path in font_paths:
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size)
    return ImageFont.load_default()


def _clamp_label_box(x: int, y: int, right: int, bottom: int, label: str) -> tuple[int, int, int, int] | None:
    box_x0 = x + 52
    box_y0 = y + 8
    box_x1 = min(right - 8, x + 52 + len(label) * 19)
    box_y1 = min(bottom - 8, y + 46)
    if box_x1 <= box_x0 or box_y1 <= box_y0:
        return None
    return box_x0, box_y0, box_x1, box_y1


def main(image_path: str, annotation_path: str, output_path: str) -> None:
    image = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _font(28)
    small_font = _font(18)
    colors = [(38, 103, 255, 225), (255, 105, 92, 225), (41, 167, 102, 225), (181, 100, 255, 225)]

    data = json.loads(Path(annotation_path).read_text(encoding="utf-8"))
    for index, element in enumerate(data["elements"], start=1):
        region = element["region"]
        x, y = region["x"], region["y"]
        right, bottom = x + region["width"], y + region["height"]
        color = colors[(index - 1) % len(colors)]
        fill = (*color[:3], 24)
        draw.rounded_rectangle((x, y, right, bottom), radius=12, outline=color, width=4, fill=fill)
        draw.ellipse((x + 8, y + 8, x + 44, y + 44), fill=color)
        draw.text((x + 19, y + 8), str(index), anchor="ma", font=small_font, fill="white")
        label = f"{index}. {element['label']}  {element['reveal']['direction']}"
        label_box = _clamp_label_box(x, y, right, bottom, label)
        if label_box is not None:
            draw.rounded_rectangle(label_box, radius=6, fill=(255, 255, 255, 225))
        label_x = label_box[0] + 8 if label_box is not None else x + 52
        label_y = label_box[1] + 4 if label_box is not None else y + 12
        draw.text((label_x, label_y), label, font=small_font, fill=color)
        start = tuple(element["handPath"]["start"])
        end = tuple(element["handPath"]["end"])
        draw.line((start, end), fill=color, width=4)
        draw.polygon((end, (end[0] - 13, end[1] - 7), (end[0] - 13, end[1] + 7)), fill=color)

    result = Image.alpha_composite(image, overlay).convert("RGB")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path, quality=95)


if __name__ == "__main__":
    main(*sys.argv[1:4])
