# -*- coding: utf-8 -*-
"""测一件事：**白端（scan_white_level）对 p95/clip 的灵敏度**。

用一张图跑 3 档白端，回答"选项 c（用白端控 p95）行不行"。
不改任何生产代码。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, r'E:\svStudio\svFilm')
import svFilm  # noqa: E402
import svFilm.lrbase  # noqa: E402,F401
import svFilm.presets  # noqa: E402,F401
import svFilm.io  # noqa: E402,F401
from svFilm import io, presets  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

RAF = r'D:\照片库\效果测试919'
OUT = r'E:\ds\photo\_run_1030_card004\_probe_white'
STEM = 'DSCF1608'
PRESET = '卡38'


def lab_of(rgb):
    import cv2
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    L = lab[..., 0].astype(np.float32) * 100.0 / 255.0
    return L


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    raf = None
    for e in ('.RAF', '.raf'):
        p = os.path.join(RAF, STEM + e)
        if os.path.isfile(p):
            raf = p
            break
    if raf is None:
        print('找不到 RAF：%s' % STEM)
        return 2
    lin = io.load_raw(raf).lin
    print('源 %s ｜ %s' % (os.path.basename(raf), PRESET))
    print('%-8s %7s %7s %8s %7s %7s' % ('白端', 'p50', 'p95', 'clip%', 'p1', '脸L~'))
    base = presets.load_raw(PRESET)['simulation'].get('scan_white_level', 1.0)
    for wl in (base, base - 0.06, base - 0.12):
        d = presets.render(lin, PRESET, presets.C,
                           overrides={'simulation.scan_white_level': float(wl)})
        arr = np.asarray(d if d.dtype == np.uint8 else (np.clip(d, 0, 1) * 255).astype(np.uint8))
        if arr.ndim == 3 and arr.shape[2] >= 3:
            arr = arr[..., :3]
        L = lab_of(arr)
        p = lambda q: float(np.percentile(L, q))
        print('%-8.3f %7.1f %7.1f %8.2f %7.2f %7.1f'
              % (wl, p(50), p(95), 100 * float((L >= 99).mean()), p(1), p(90)))
        Image.fromarray(arr).save(os.path.join(OUT, 'wl_%.3f.jpg' % wl), quality=86)
    print('\n产物：%s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
