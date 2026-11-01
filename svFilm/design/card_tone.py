# -*- coding: utf-8 -*-
"""卡缺的那两块：**暗部/高光的冷暖特征**（目录 JSON 里 hue_bands / neutral_axis 是空的）。

为什么能量、也为什么要量：
  · 量的是"这张卡在暗部/高光的**色偏**"（一个颜色），不是"蓝色占多少像素"（内容）。
    同一套味道，拍海边和拍巷子，占比能差十倍，色偏不会。
  · 教程的第一句话就是"高光偏蓝、暗部偏青蓝"——这就是卡的暗部/高光色偏，
    卡里没有，只能从它自己的成片里取。

产物：写回每个**目录 JSON** 的 `tone` 字段：
    {"shadow": {"ab": [a,b], "lab": [L,a,b], "hsl_h": 204.1, "n": 16},
     "highlight": {...}, "n_images": 16}

用法：
    python -m design.card_tone            # 全部卡
    python -m design.card_tone --card 001_鹿井小渔_n66f7fa31
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np

_CORPUS = r'E:\svStudio\svFilm\analysis\corpus\kurai'

SHADOW = (5.0, 25.0)       # L* 区间：暗部
HIGHLIGHT = (85.0, 99.5)   # L* 区间：高光


def _to_lab(rgb: np.ndarray):
    """sRGB 0..1 → Lab（D65）。"""
    m = rgb <= 0.04045
    lin = np.where(m, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return L, a, b


def _hue_of(lab) -> float:
    from .color_math import lab_to_hsl
    return round(float(lab_to_hsl(list(lab))[0]), 1)


def _zone(L: np.ndarray, a: np.ndarray, b: np.ndarray, lo: float, hi: float):
    m = (L >= lo) & (L <= hi)
    if m.sum() < 200:
        return None
    return [float(np.median(L[m])), float(np.median(a[m])), float(np.median(b[m]))]


def build_card(card: dict) -> Optional[dict]:
    from PIL import Image
    from .matchcard import card_samples
    files = [p for p in card_samples(card.get('name') or '') if os.path.isfile(p)]
    if not files:
        return None
    sh, hi, overall, neutral = [], [], [], []
    for p in files:
        im = Image.open(p).convert('RGB')
        im.thumbnail((600, 600))
        rgb = np.asarray(im, np.float64) / 255.0
        L, a, b = _to_lab(rgb)
        zs = _zone(L, a, b, *SHADOW)
        zh = _zone(L, a, b, *HIGHLIGHT)
        if zs:
            sh.append(zs)
        if zh:
            hi.append(zh)
        # ★ 整幅彩度（背景 + 主体，不是只看脸）：C* = √(a²+b²) 的中位与 p75。
        #   为什么要它：`couplers.amount` 在引擎里是**整幅通道耦合的倍率**
        #   （couplers.py:126），只拿脸去推会漏掉背景那一半。
        C = np.hypot(a, b)
        overall.append([float(np.median(C)), float(np.percentile(C, 75)),
                        float(np.percentile(C, 90))])
        # ★ 中性轴（低彩度像素的 a*/b*）：这是"整幅偏什么色"，用来定放大机滤片位移。
        #   只取低彩度像素，避免把衣服/天空的颜色当白平衡。
        thr = float(np.percentile(C, 30))
        m = C <= max(thr, 1e-6)
        if int(m.sum()) >= 500:
            neutral.append([float(np.median(L[m])), float(np.median(a[m])),
                            float(np.median(b[m]))])

    def med(rows):
        if not rows:
            return None
        arr = np.array(rows)
        lab = [float(np.median(arr[:, i])) for i in range(3)]
        return dict(lab=[round(v, 2) for v in lab],
                    ab=[round(lab[1], 2), round(lab[2], 2)],
                    hsl_h=_hue_of(lab), n=len(rows))

    out = dict(shadow=med(sh), highlight=med(hi), n_images=len(files))
    if neutral:
        out['neutral'] = med(neutral)
    if overall:
        arr = np.array(overall)
        out['overall_C'] = dict(
            med=round(float(np.median(arr[:, 0])), 2),
            p75=round(float(np.median(arr[:, 1])), 2),
            p90=round(float(np.median(arr[:, 2])), 2),
            n=len(overall),
        )
    if not out['shadow'] and not out['highlight']:
        return None
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    want = ''
    if '--card' in argv:
        want = argv[argv.index('--card') + 1]
    from .matchcard import raw_cards
    from .medium import update_card
    cards = [c for c in raw_cards() if (not want or c['name'] == want)]
    n = 0
    for i, c in enumerate(cards, 1):
        t = build_card(c)
        if t:
            update_card(c['name'], tone=t)
            n += 1
            print('[%d/%d] %s  暗部 b=%s ｜ 高光 b=%s'
                  % (i, len(cards), c['name'],
                     (t.get('shadow') or {}).get('ab', ['—', '—'])[1],
                     (t.get('highlight') or {}).get('ab', ['—', '—'])[1]))
    print('写回 %d 张卡的目录 JSON' % n)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
