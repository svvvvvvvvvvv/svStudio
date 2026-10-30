# -*- coding: utf-8 -*-
"""成片域质感三量（**只做相对排序**，绝对值不可用）。

为什么只能相对：
  · 大师成片是 1080 长边 JPEG ⇒ 48µm 颗粒与 MTF50 都不可能被准确复原；
  · 但"哪张更颗粒 / 更锐 / 边缘更硬"的相对关系，在 1080 上仍然稳定。

三个量（都在 L* 域）：
  grain  平滑中亮区的高频残差（L* 单位）——颗粒 + 微反差
  ratio  高频能量 / 低频能量 —— 锐度倾向
  edge   边缘梯度中位 / 边缘对比中位 —— 边缘陡度

跑：
  & $py -X utf8 -m design.texture_stats --dir "E:\\ds\\大师成片\\鹿井" --out tex_鹿井.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict
from typing import Dict, List, Optional

import cv2
import numpy as np
from PIL import Image

LONG = 1080
EXT = ('*.jpg', '*.jpeg', '*.png', '*.tif', '*.tiff')


def _lab_L(path: str) -> np.ndarray:
    im = Image.open(path).convert('RGB')
    w, h = im.size
    k = LONG / max(w, h)
    if k < 1:
        im = im.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
    a = np.asarray(im).astype(np.float32) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    Y = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    return np.where(Y > 0.008856, 116.0 * np.cbrt(Y) - 16.0, 903.3 * Y)


def metrics(path: str) -> Optional[dict]:
    L = _lab_L(path)
    gx = cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3)
    g = cv2.magnitude(gx, gy)

    flat = (L > 30) & (L < 85) & (g < np.percentile(g, 60))
    if int(flat.sum()) < 20000:
        return None

    r1 = L - cv2.GaussianBlur(L, (0, 0), 1.0)
    r3 = L - cv2.GaussianBlur(L, (0, 0), 3.0)
    grain = float(np.std(r1[flat]))
    ratio = float(np.std(r1[flat])) / max(float(np.std(r3[flat])), 1e-6)

    em = g > np.percentile(g, 99.5)
    edge = (float(np.median(g[em]) / max(float(np.std(L[em])), 1e-6))
            if int(em.sum()) >= 500 else None)
    return dict(grain=round(grain, 3), ratio=round(ratio, 3),
                edge=(round(edge, 4) if edge is not None else None))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args(argv)

    files = sorted(set(f for e in EXT for f in glob.glob(os.path.join(a.dir, '**', e), recursive=True)))
    if a.limit:
        files = files[:a.limit]

    rows: List[dict] = []
    by_card: Dict[str, List[dict]] = defaultdict(list)
    for i, p in enumerate(files):
        try:
            m = metrics(p)
        except Exception:
            m = None
        if m is None:
            continue
        rel = os.path.relpath(p, a.dir).replace('\\', '/')
        m.update(file=rel, card=os.path.dirname(rel))
        rows.append(m)
        by_card[m['card']].append(m)
        if (i + 1) % 200 == 0:
            print('  %d/%d' % (i + 1, len(files)), flush=True)

    def med(xs):
        xs = [x for x in xs if x is not None]
        return float(np.median(xs)) if xs else None

    cards = []
    for card, ms in sorted(by_card.items()):
        if len(ms) < 5:
            continue
        g = med([m['grain'] for m in ms])
        r = med([m['ratio'] for m in ms])
        e = med([m['edge'] for m in ms])
        if g is None or r is None:
            continue
        cards.append(dict(card=card, n=len(ms),
                          grain=round(g, 3), ratio=round(r, 3),
                          edge=(round(e, 4) if e is not None else None)))

    out = dict(_meta=dict(dir=a.dir, n=len(files), ok=len(rows), long=LONG,
                          note='1080 上只做相对排序，绝对值不可用'),
               cards=cards, per_image=rows)
    json.dump(out, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print('ok %d / %d ｜ 卡 %d' % (len(rows), len(files), len(cards)))
    for key in ('grain', 'ratio', 'edge'):
        kv = [c[key] for c in cards if c.get(key) is not None]
        if not kv:
            continue
        p10, p50, p90 = np.percentile(kv, [10, 50, 90])
        iqr = np.percentile(kv, 75) - np.percentile(kv, 25)
        # 卡内离散（只有 5 张以上的卡参与）
        wi = []
        for ms in by_card.values():
            v = [m[key] for m in ms if m.get(key) is not None]
            if len(v) >= 5:
                wi.append(float(np.percentile(v, 75) - np.percentile(v, 25)))
        print('  %-6s 卡中位 P10/P50/P90 = %.3f / %.3f / %.3f ｜ 卡间 IQR %.3f ｜ 卡内 IQR 中位 %.4f'
              % (key, p10, p50, p90, iqr, float(np.median(wi)) if wi else float('nan')))
    print('写出 %s' % a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
