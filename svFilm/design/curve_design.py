# -*- coding: utf-8 -*-
"""主色调曲线：**Basic 为主，曲线补形状**（`LR设计.md` §3.1）。

曲线只做一件事：把 Basic 之后还剩下的**形状残差**补上。
  · 位置（p50）归曝光，曲线不动它；
  · 间距（toe / contrast_win / shoulder）按卡/peer 的形状关系补；
  · 肩部落点用目标的 p95 夹住（不把高光推过肩）；
  · 端点 (0,0)/(100,100) 补满量程，PCHIP 单调（不反折、不出 banding）。

★ 不再有 "S3 曲线预算 ±3 L*" 这类自造上限（设计 §4：删固定剂量/任意 cap）。
  形状差多少就补多少；补不动是底片/卡不匹配的问题，记账，不在曲线里偷偷放宽。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .curve import grid16
from .photometry import code_to_l, l_to_code


def _pchip(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    """单调三次 Hermite（Fritsch–Carlson）：保证不反折。"""
    n = len(xs)
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
    i = max(j for j in range(n - 1) if xs[j] <= x)
    t = (x - xs[i]) / h[i]
    h00 = 2 * t ** 3 - 3 * t ** 2 + 1
    h10 = t ** 3 - 2 * t ** 2 + t
    h01 = -2 * t ** 3 + 3 * t ** 2
    h11 = t ** 3 - t ** 2
    return h00 * ys[i] + h10 * h[i] * m[i] + h01 * ys[i + 1] + h11 * h[i] * m[i + 1]


def _point(v) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return 0.5 * (float(v[0]) + float(v[1]))
    return None


def curve_from_quantiles(quantiles: Dict[str, float],
                         shape: Dict[str, Sequence[float]],
                         p95_cap: Optional[float] = None,
                         points: int = 16) -> Optional[List[Tuple[int, int]]]:
    """Basic 之后的分位 + 目标形状 → 16 点 LUT（0..255）。None = 曲线不动。"""
    q5, q25, q50, q75, q95 = (quantiles.get('p5'), quantiles.get('p25'),
                              quantiles.get('p50'), quantiles.get('p75'),
                              quantiles.get('p95'))
    if None in (q5, q25, q50, q75, q95):
        return None
    toe = _point(shape.get('toe'))
    cw = _point(shape.get('contrast_win'))
    sh = _point(shape.get('shoulder'))
    if None in (toe, cw, sh):
        return None

    toe_i = max(float(q25) - float(q5), 1.0)
    cw_i = max(float(q75) - float(q25), 1.0)
    sh_i = max(float(q95) - float(q75), 1.0)
    d_toe = toe - toe_i
    d_cw = cw - cw_i
    d_sh = sh - sh_i

    new5 = float(q5)
    new25 = float(q25) + d_toe
    new75 = float(q75) + d_toe + d_cw
    cap = float(p95_cap) if p95_cap is not None else float(q95)
    cand95 = float(q95) + d_toe + d_cw + d_sh
    new95 = min(cand95, max(cap, new75 + 1.0))
    new50 = float(q50)                     # 位置归曝光，曲线不动 p50

    xs_in = sorted([float(q5), float(q25), float(q50), float(q75), float(q95)])
    for i in range(1, len(xs_in)):
        if xs_in[i] <= xs_in[i - 1]:
            xs_in[i] = xs_in[i - 1] + 1e-3
    ys = [new5, new25, new50, new75, new95]
    prev = None
    mono = []
    for v in ys:
        prev = v if prev is None else max(prev, v)
        mono.append(prev)
    ys = mono

    pts = [(0.0, 0.0)]
    for x, y in zip(xs_in, ys):
        if x <= 1e-6 or x >= 100.0 - 1e-6:
            continue
        if x <= pts[-1][0] + 1e-6:
            continue
        pts.append((float(x), float(y)))
    pts.append((100.0, 100.0))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    anchor_codes = [l_to_code(v) for v in xs if 0.0 < v < 100.0]
    grid = grid16(anchor_codes) if points == 16 else \
        [255.0 * i / (points - 1) for i in range(points)]
    out: List[Tuple[int, int]] = []
    prev = -1
    for gx in grid:
        yl = _pchip(xs, ys, code_to_l(gx))
        y = int(round(l_to_code(yl)))
        y = max(prev, min(255, max(0, y)))
        out.append((int(round(gx)), y))
        prev = y
    return out


def apply_lut(curve, L: float) -> float:
    """把曲线作用到一个 L* 上（显示码 LUT，经 sRGB 编码做横轴）。"""
    if not curve:
        return float(L)
    x = max(0.0, min(255.0, l_to_code(float(L))))
    for i in range(len(curve) - 1):
        x0, y0 = curve[i]
        x1, y1 = curve[i + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return code_to_l(float(y0))
            f = (x - x0) / (x1 - x0)
            return code_to_l(float(y0) + f * float(y1 - y0))
    return code_to_l(float(curve[-1][1]))
