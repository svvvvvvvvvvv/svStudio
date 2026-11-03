# -*- coding: utf-8 -*-
"""合成一张「颜色标板」，给卷/纸做**颜色指纹**。

为什么自己画：作者那张 test 图是"人像+树叶"，没有中性灰阶、没有天空、没有地面、
没有高光/阴影像素 —— 而这几样正是判断"卷纸颜色性格"最敏感的地方（`卷纸测量.md` §3.4 允许自造）。

板子内容（全部线性 ProPhoto RGB，0..1）：
  12 级灰阶      0.005 ~ 0.90（中性轴 / 高光 / 阴影的色偏）
  肤色块         Lab(70, 12, 20)
  树叶绿         Lab(40, −20, 25)
  天空蓝         Lab(70, −2, −25)
  地面土黄       Lab(45, 8, 18)
  暖高光         Lab(95, 3, 8)
  冷阴影         Lab(15, −2, −6）

用法：`python analysis/medium/color_chart.py --out analysis/medium/out/chart.png`
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def lab_to_linear_prophoto(L, a, b):
    """Lab(D65) → 线性 sRGB → 线性 ProPhoto（板子按 ProPhoto 喂给引擎）。"""
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0

    def finv(t):
        return t ** 3 if t ** 3 > 0.008856 else (t - 16.0 / 116.0) / 7.787

    xyz = np.array([finv(fx), finv(fy), finv(fz)]) * common.WP
    lin_srgb = np.linalg.solve(common.M, xyz)
    # sRGB → XYZ(D65) → ProPhoto(D50) 近似：只做一次矩阵级转换（对比用，够）
    M_pp = np.array([[0.7976749, 0.1351917, 0.0313534],
                     [0.2880402, 0.7118741, 0.0000857],
                     [0.0000000, 0.0000000, 0.8252100]])
    brad = np.array([[0.9555766, -0.0230393, 0.0631636],
                     [-0.0282895, 1.0099416, 0.0210077],
                     [0.0122982, -0.0204830, 1.3299098]])
    xyz50 = brad @ (common.M @ lin_srgb)          # sRGB 线 → XYZ(D65) → D50
    return np.clip(np.linalg.solve(M_pp, xyz50), 0.0, 1.0)


PATCHES = {
    'skin': (70.0, 12.0, 20.0),
    'leaf': (40.0, -20.0, 25.0),
    'sky': (70.0, -2.0, -25.0),
    'ground': (45.0, 8.0, 18.0),
    'warm_hi': (95.0, 3.0, 8.0),
    'cool_shadow': (15.0, -2.0, -6.0),
}

RAMP = [0.005, 0.008, 0.012, 0.018, 0.028, 0.045, 0.07, 0.11, 0.18, 0.30, 0.50, 0.90]


def build(width=720, height=480):
    """返回 (图, 区域字典)。灰度条在上，彩色块在下。"""
    img = np.zeros((height, width, 3), np.float64)
    regions = {}
    # 12 级灰阶：上半区
    band = height // 2
    w = width // len(RAMP)
    for i, v in enumerate(RAMP):
        x0, x1 = i * w, (i + 1) * w if i < len(RAMP) - 1 else width
        img[0:band, x0:x1] = v
        regions['gray_%02d' % i] = (x0, 0, x1, band)
    # 6 个色块：下半区 3×2
    names = list(PATCHES)
    for j, name in enumerate(names):
        r, c = divmod(j, 3)
        x0 = c * (width // 3)
        x1 = width if c == 2 else (c + 1) * (width // 3)
        y0 = band + r * ((height - band) // 2)
        y1 = height if r == 1 else band + (r + 1) * ((height - band) // 2)
        img[y0:y1, x0:x1] = lab_to_linear_prophoto(*PATCHES[name])
        regions[name] = (x0, y0, x1, y1)
    return img, regions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT / 'chart.png'))
    args = ap.parse_args()
    img, regions = build()
    disp = np.clip(img, 0, 1) ** (1 / 2.2)          # 只给预览看
    common.save_png(disp, args.out)
    import json
    jp = os.path.splitext(args.out)[0] + '_regions.json'
    with open(jp, 'w', encoding='utf-8') as fh:
        json.dump(regions, fh, ensure_ascii=False, indent=1)
    print('写出', args.out, '和', jp, '| 区域', len(regions))


if __name__ == '__main__':
    main()
