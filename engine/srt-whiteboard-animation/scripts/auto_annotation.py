import cv2
import json
import sys
from pathlib import Path


def parse_srt_duration(srt_path):
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

        value = (
            int(h) * 3600000
            + int(m) * 60000
            + int(s) * 1000
            + int(ms)
        )

        last_end = max(last_end, value)

    return last_end


def main(image_path, srt_path, output_path):

    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)

    if image is None:
        raise FileNotFoundError(image_path)

    height, width = image.shape

    # 黑色线稿
    _, binary = cv2.threshold(
        image,
        180,
        255,
        cv2.THRESH_BINARY_INV
    )

    # 连通区域
    num_labels, labels, stats, centroids = (
        cv2.connectedComponentsWithStats(
            binary,
            connectivity=8
        )
    )

    regions = []

    for i in range(1, num_labels):

        x, y, rw, rh, area = stats[i]

        if area < 100:
            continue

        regions.append({
            "x": int(x),
            "y": int(y),
            "width": int(rw),
            "height": int(rh),
            "area": int(area)
        })

    regions.sort(
        key=lambda r: r["area"],
        reverse=True
    )

    # 第一版最多使用 8 个主要区域
    regions = regions[:8]

    scene_duration = parse_srt_duration(srt_path)

    # 如果 SRT 解析不到时间，使用 9 秒
    if scene_duration <= 0:
        scene_duration = 9000

    # 稍微留一点结尾空间
    scene_duration += 300

    elements = []

    step = max(
        500,
        scene_duration // max(len(regions), 1)
    )

    for i, r in enumerate(regions):

        start = 300 + i * step

        if start >= scene_duration:
            start = max(0, scene_duration - 1000)

        reveal_duration = min(
            max(700, step - 300),
            scene_duration - start
        )

        cx = r["x"] + r["width"] // 2

        element = {
            "id": f"part-{i + 1}",
            "label": f"自动区域 {i + 1}",
            "sequence": i + 1,
            "narrativeRole": "场景内容",
            "subtitle": "",
            "type": "structure",
            "region": {
                "x": r["x"],
                "y": r["y"],
                "width": r["width"],
                "height": r["height"]
            },
            "reveal": {
                "direction": "top_to_bottom",
                "startMs": int(start),
                "durationMs": int(reveal_duration),
                "maskPaddingPx": 22,
                "protectedRegions": []
            },
            "handPath": {
                "start": [
                    int(cx),
                    int(r["y"])
                ],
                "end": [
                    int(cx),
                    int(r["y"] + r["height"])
                ],
                "easing": "easeInOut"
            }
        }

        elements.append(element)

    annotation = {
        "sceneId": Path(output_path).stem,
        "canvas": {
            "width": width,
            "height": height
        },
        "storyBasis": "自动生成场景",
        "sceneDurationMs": int(scene_duration),
        "elements": elements
    }

    Path(output_path).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    Path(output_path).write_text(
        json.dumps(
            annotation,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print(f"[ok] annotation: {output_path}")
    print(f"[ok] canvas: {width} x {height}")
    print(f"[ok] regions: {len(elements)}")
    print(f"[ok] duration: {scene_duration} ms")


if __name__ == "__main__":

    if len(sys.argv) != 4:
        print(
            "用法: auto_annotation.py "
            "<image> <srt> <output.json>"
        )
        sys.exit(1)

    main(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3]
    )
