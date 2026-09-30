from PIL import Image
import numpy as np
import cv2

path = "output/my-rocket/rocket.png"

# 读取
img = Image.open(path).convert("RGB")
a = np.array(img)

h, w = a.shape[:2]

print("=" * 60)
print("【1. 图片基本信息】")
print(f"尺寸       : {w} x {h}")
print(f"RGB范围    : {a.min()} ~ {a.max()}")

# 灰度
gray = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)

print()
print("【2. 灰度统计】")
print(f"灰度最小值 : {gray.min()}")
print(f"灰度最大值 : {gray.max()}")
print(f"平均灰度   : {gray.mean():.2f}")

# 不同黑色阈值
for threshold in [32, 64, 96, 128, 160, 200, 240]:
    mask = gray < threshold
    ratio = mask.mean() * 100
    print(f"灰度 < {threshold:3d} : {ratio:6.2f}%")

# 二值化
_, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

# 去除非常小的噪声
num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
    binary, connectivity=8
)

components = []

for i in range(1, num_labels):
    x, y, cw, ch, area = stats[i]

    if area >= 20:
        components.append({
            "area": int(area),
            "x": int(x),
            "y": int(y),
            "w": int(cw),
            "h": int(ch),
        })

components.sort(key=lambda x: x["area"], reverse=True)

print()
print("【3. 连通区域】")
print(f"全部区域数量     : {num_labels - 1}")
print(f"面积 >= 20 的区域: {len(components)}")

print()
print("最大的 20 个区域：")

for i, c in enumerate(components[:20], 1):
    print(
        f"{i:2d}. "
        f"面积={c['area']:7d}  "
        f"位置=({c['x']:4d},{c['y']:4d})  "
        f"尺寸={c['w']:4d}x{c['h']:4d}"
    )

# 边缘统计
edges = cv2.Canny(gray, 50, 150)

print()
print("【4. 线条/边缘】")
print(f"边缘像素比例 : {(edges > 0).mean() * 100:.2f}%")

# 画布四周黑色比例
border = 50

regions = {
    "上边": gray[:border, :],
    "下边": gray[-border:, :],
    "左边": gray[:, :border],
    "右边": gray[:, -border:],
}

print()
print("【5. 边缘区域黑色比例】")

for name, region in regions.items():
    print(
        f"{name}: "
        f"{(region < 180).mean() * 100:.2f}%"
    )

# 粗略判断
black_ratio = (gray < 180).mean() * 100
edge_ratio = (edges > 0).mean() * 100

print()
print("【6. 初步判断】")

if black_ratio < 2:
    print("线稿非常稀疏")
elif black_ratio < 10:
    print("线稿密度适中，比较适合白板动画")
elif black_ratio < 25:
    print("线稿较密，annotation 可能需要较多区域")
else:
    print("线稿非常密，可能需要先简化")

if edge_ratio < 1:
    print("边缘很少")
elif edge_ratio < 8:
    print("边缘数量适中")
else:
    print("边缘较多，可能存在较复杂细节")

small_components = sum(
    1 for c in components
    if c["area"] < 100
)

print(f"小型连通区域(<100像素): {small_components}")

if small_components > 100:
    print("提示：碎片较多，建议后续进行去噪")
else:
    print("提示：碎片数量暂时正常")

print()
print("=" * 60)
