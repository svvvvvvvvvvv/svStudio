# -*- coding: utf-8 -*-
"""11 个 target 配方的**颜色指纹**（作者引擎前向测量）。

两张输入，各管一件事：
  1. 作者 test 人像（`portrait_leaves_...tif`）→ 肤色 L/C/h、树叶 a/b、整幅 C（人/植物）
  2. 合成中性灰阶（12 级）→ **色偏曲线**：每一级的 a/b（阴影/中间/高光各自的偏色）

为什么不用彩色合成块：Lab→ProPhoto 的转换容易在色域边界失真；
中性灰阶在任何 RGB 空间都成立，色偏曲线是和卡里 `neutral_axis` 同口径的。

输出：out/color_sig.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

RAMP = [0.005, 0.008, 0.012, 0.018, 0.028, 0.045, 0.07, 0.11, 0.18, 0.30, 0.50, 0.90]


def gray_image(width=720, height=240):
    img = np.zeros((height, width, 3), np.float64)
    regions = {}
    w = width // len(RAMP)
    for i, v in enumerate(RAMP):
        x0, x1 = i * w, (width if i == len(RAMP) - 1 else (i + 1) * w)
        img[:, x0:x1] = v
        regions['g%02d' % i] = (x0, 0, x1, height)
    return img, regions


def measure_regions(disp, regions):
    lab = common.srgb_to_lab(disp)
    out = {}
    for name, (x0, y0, x1, y1) in regions.items():
        px = lab[y0:y1, x0:x1].reshape(-1, 3)
        L, a, b = np.median(px, 0)
        out[name] = dict(L=float(L), a=float(a), b=float(b),
                         C=float(np.hypot(a, b)),
                         h=float(np.degrees(np.arctan2(b, a)) % 360.0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--papers', choices=['target', 'all'], default='target')
    ap.add_argument('--out', default=str(common.OUT_DEFAULT / 'color_sig.json'))
    args = ap.parse_args()

    portrait = common.load_test_image()
    gray, gregions = gray_image()
    pairs = common.target_pairs() if args.papers == 'target' else \
        [(s, p) for s in common.stocks() for p in common.papers()]
    sig = {}
    for stock, paper in pairs:
        key = '%s + %s' % (stock, paper)
        try:
            d1 = common.render(stock, paper, image=portrait)
            d2 = common.render(stock, paper, image=gray)
        except Exception as e:                             # noqa: BLE001
            print('渲失败', key, e)
            continue
        m1 = common.metrics(d1)
        m2 = measure_regions(d2, gregions)
        sig[key] = dict(stock=stock, paper=paper, face=m1['face'], leaf=m1['leaf'],
                        whole_C=m1['whole_C'], contrast=m1['contrast'], gray=m2)
        # 灰阶三级：暗 / 中 / 亮
        g_lo, g_mid, g_hi = m2['g02'], m2['g06'], m2['g10']
        print('%-42s 肤色 C%5.1f h%5.1f ｜ 树叶 a%+5.1f ｜ 灰阶 暗 a%+4.1f b%+4.1f ｜ 中 a%+4.1f b%+4.1f ｜ 亮 a%+4.1f b%+4.1f'
              % (key, m1['face']['C'], m1['face']['h'], m1['leaf']['a'],
                 g_lo['a'], g_lo['b'], g_mid['a'], g_mid['b'], g_hi['a'], g_hi['b']))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(sig, fh, ensure_ascii=False, indent=1)
    print('\n写出', args.out, '｜配方', len(sig))


if __name__ == '__main__':
    main()
