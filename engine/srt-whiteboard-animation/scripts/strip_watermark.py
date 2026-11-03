#!/usr/bin/env python3
"""去除生成图角落的 "AI生成" 类白色水印（派生副本，绝不改动源图）。

依赖：numpy + Pillow，全程离线。

算法：
1. 在右下角扫描区内按"比周边背景更亮"的判据提取水印 mask（含抗锯齿边缘），
   再做连通域过滤与形态学膨胀；
2. 用 mask 外环带的逐行/逐列中值构造平滑渐变背景（双线性混合），
   仅在 mask 内替换为背景层，避免出现平整色斑；
3. 叠加极轻噪声保留纸面质感；
4. 回读校验残留亮像素。

用法：python3 strip_watermark.py <源目录> <输出目录>
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
from PIL import Image

SCAN_X, SCAN_Y = 0.74, 0.84
BRIGHT_DELTA = 5          # 比背景亮多少算水印
GROW = 3


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    k = (x * x + y * y) <= r * r
    pad = np.pad(mask, r, mode="constant")
    out = np.zeros_like(mask)
    h, w = mask.shape
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            if k[dy, dx]:
                out |= pad[dy:dy + h, dx:dx + w]
    return out


def fill_gradient(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """用环带逐行/逐列中值重建平滑背景，仅替换 mask 内像素。"""
    h, w, _ = img.shape
    ys, xs = np.nonzero(mask)
    m = 8  # 环带宽
    y0, y1 = max(ys.min() - 1, 0), min(ys.max() + 1, h - 1)
    x0, x1 = max(xs.min() - 1, 0), min(xs.max() + 1, w - 1)

    top = img[max(y0 - m, 0):y0, x0:x1 + 1]
    bot = img[y1 + 1:min(y1 + 1 + m, h), x0:x1 + 1]
    left = img[y0:y1 + 1, max(x0 - m, 0):x0]
    right = img[y0:y1 + 1, x1 + 1:min(x1 + 1 + m, w)]

    top_col = np.median(top, axis=0) if top.size else None
    bot_col = np.median(bot, axis=0) if bot.size else None
    left_row = np.median(left, axis=1) if left.size else None
    right_row = np.median(right, axis=1) if right.size else None

    H, W = y1 - y0 + 1, x1 - x0 + 1
    vy = np.zeros((H, W, 3), dtype=np.float32)
    vx = np.zeros((H, W, 3), dtype=np.float32)

    if top_col is not None and bot_col is not None:
        ty = np.linspace(0, 1, H)[:, None, None].astype(np.float32)
        vy = top_col[None, :, :] * (1 - ty) + bot_col[None, :, :] * ty
    elif top_col is not None:
        vy = np.repeat(top_col[None, :, :], H, axis=0)
    elif bot_col is not None:
        vy = np.repeat(bot_col[None, :, :], H, axis=0)

    if left_row is not None and right_row is not None:
        tx = np.linspace(0, 1, W)[None, :, None].astype(np.float32)
        vx = left_row[:, None, :] * (1 - tx) + right_row[:, None, :] * tx
    elif left_row is not None:
        vx = np.repeat(left_row[:, None, :], W, axis=1)
    elif right_row is not None:
        vx = np.repeat(right_row[:, None, :], W, axis=1)

    # 按到四边距离加权：靠近上下用 vy，靠近左右用 vx
    yy = np.arange(H)[:, None].astype(np.float32)
    xx = np.arange(W)[None, :].astype(np.float32)
    dy = np.minimum(yy + 1, H - yy).astype(np.float32)
    dx = np.minimum(xx + 1, W - xx).astype(np.float32)
    wy = (1.0 / np.maximum(dy, 1e-3))
    wx = (1.0 / np.maximum(dx, 1e-3))
    wy = np.repeat(wy[:, :, None], 3, axis=2)
    wx = np.repeat(wx[:, :, None], 3, axis=2)
    has_y = (top_col is not None) or (bot_col is not None)
    has_x = (left_row is not None) or (right_row is not None)
    if has_y and has_x:
        bg = (vy * wy + vx * wx) / (wy + wx)
    elif has_y:
        bg = vy
    else:
        bg = vx

    out = img.astype(np.float32).copy()
    region = out[y0:y1 + 1, x0:x1 + 1]
    sub_mask = mask[y0:y1 + 1, x0:x1 + 1]
    rng = np.random.default_rng(20261018)
    noise = rng.normal(0, 0.7, size=region.shape)
    region[sub_mask] = bg[sub_mask] + noise[sub_mask]
    out[y0:y1 + 1, x0:x1 + 1] = np.clip(region, 0, 255)
    return out


def process(src_dir: str, dst_dir: str) -> list[dict]:
    os.makedirs(dst_dir, exist_ok=True)
    report = []
    for path in sorted(glob.glob(os.path.join(src_dir, "*.png"))):
        name = os.path.basename(path)
        pil = Image.open(path).convert("RGB")
        img = np.array(pil).astype(np.float32)
        h, w, _ = img.shape
        x0, y0 = int(w * SCAN_X), int(h * SCAN_Y)

        roi = img[y0:h, x0:w]
        gray = roi.mean(axis=2)
        bg_med = float(np.median(gray))
        bright = gray > (bg_med + BRIGHT_DELTA)
        # 强白像素必须存在，避免把浅色笔触误判
        strong = (roi > 236).all(axis=2)
        if strong.sum() < 40 or bright.sum() < 60:
            pil.save(os.path.join(dst_dir, name))
            report.append({"file": name, "watermark": False, "action": "copied"})
            print(f"{name}: 未检出白水印，直通")
            continue

        full = np.zeros((h, w), dtype=bool)
        full[y0:h, x0:w] = bright | strong
        mask = dilate(full, GROW)

        out = fill_gradient(img, mask)
        Image.fromarray(out.astype(np.uint8)).save(os.path.join(dst_dir, name))

        chk = np.array(Image.open(os.path.join(dst_dir, name)).convert("RGB")).astype(np.float32)
        residual = int((chk[y0:h, x0:w].mean(axis=2) > bg_med + BRIGHT_DELTA + 3).sum())
        ys, xs = np.nonzero(mask)
        report.append({
            "file": name,
            "watermark": True,
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)],
            "residual_bright_px": residual,
        })
        print(f"{name}: 已去水印 bbox={report[-1]['bbox']} 残留亮像素={residual}")

    json.dump(report, open(os.path.join(dst_dir, "watermark-removal.json"), "w"),
              indent=1, ensure_ascii=False)
    return report


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    process(sys.argv[1], sys.argv[2])
    print("输出目录:", sys.argv[2])
