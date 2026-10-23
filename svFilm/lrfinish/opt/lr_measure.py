# -*- coding: utf-8 -*-
r"""LR 收尾 · 测量层（纯函数，不碰 LR）。

同一把尺：svFilm.color.to_lab（D65），长边 1024，与 v3_measure 一致。
"""
from __future__ import annotations

import os
import sys

import numpy as np

import _boot  # noqa: E402
import paths
paths.ensure_import_path()
import svFilm.color as color            # noqa: E402

LONG = 1024


def load_rgb(path, long_edge=LONG):
    import cv2
    im = cv2.imdecode(np.frombuffer(open(path, 'rb').read(), np.uint8), cv2.IMREAD_UNCHANGED)
    if im is None:
        raise RuntimeError('decode fail: %s' % path)
    sc = 65535.0 if im.dtype == np.uint16 else (255.0 if im.dtype == np.uint8 else 1.0)
    if im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
    im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    im = np.asarray(im, np.float64) / sc
    h, w = im.shape[:2]
    if long_edge and max(h, w) > long_edge:
        r = long_edge / float(max(h, w))
        im = cv2.resize(im, (int(w * r), int(h * r)), interpolation=cv2.INTER_AREA)
    return np.clip(im, 0.0, 1.0)


def luma_code(rgb):
    u8 = np.clip(rgb * 255.0, 0, 255)
    return 0.2126 * u8[..., 0] + 0.7152 * u8[..., 1] + 0.0722 * u8[..., 2]


def neutral_mask(rgb, C_max=5.0, L_range=(50.0, 95.0)):
    """底片里的**中高亮**低彩度像素（白衬衫/灰墙/水泥）——护栏只管这些。
    暗部的中性像素本来就该被"降温"，不能算进去（10-22 踩过：2635 被误伤回退）。"""
    lab = color.to_lab(rgb)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.sqrt(a * a + b * b)
    return (C < C_max) & (L > L_range[0]) & (L < L_range[1])


def _med(x, min_n=500):
    x = np.asarray(x).ravel()
    return float(np.median(x)) if x.size >= min_n else float('nan')


def stats(path, nmask=None, ref=None):
    """量一张图。`ref` = 底片的统计（给护栏算增量用）。"""
    rgb = load_rgb(path)
    lab = color.to_lab(np.ascontiguousarray(rgb))
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.sqrt(a * a + b * b)
    sh, hi = L < 35, L >= 75
    code = luma_code(rgb)
    out = dict(
        p50=float(np.percentile(L, 50)),
        white=float(100 * (L > 95).mean()),
        black=float(100 * (L < 5).mean()),
        sh_a=_med(a[sh]), sh_b=_med(b[sh]), sh_C=_med(C[sh]),
        hi_a=_med(a[hi]), hi_b=_med(b[hi]), hi_C=_med(C[hi]),
    )
    # 码值分位（给曲线定锚点）
    for q in (0.5, 2.0, 50.0, 97.5, 99.5):
        key = 'code_p' + ('%g' % q).replace('.', '_')
        out[key] = float(np.percentile(code, q))
    if nmask is not None:
        m = nmask
        out['n_C'] = _med(C[m], 200)
        out['n_b'] = _med(b[m], 200)
        if ref is not None:
            out['n_dC'] = out['n_C'] - ref.get('n_C', out['n_C'])
            out['n_db'] = out['n_b'] - ref.get('n_b', out['n_b'])
    return out


def in_band(v, band):
    lo, hi = band
    return (v == v) and lo <= v <= hi


if __name__ == '__main__':
    import json
    for p in sys.argv[1:]:
        s = stats(p)
        print(os.path.basename(p), json.dumps({k: round(v, 2) for k, v in s.items()},
                                              ensure_ascii=False))
