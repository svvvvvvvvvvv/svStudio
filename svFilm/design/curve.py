# -*- coding: utf-8 -*-
"""曲线的基础件：16 点网格 + sRGB 码 ↔ L*。

曲线的语义在 `curve_design.py`（Basic 为主、曲线补形状）。
这里只放两件**定义式**：
  · 横轴是 0..255 **sRGB 显示码**，不是 L* ×2.55（L*=50 的码是 119）；
  · 16 点采样必须**包含锚点码位**，否则高光锚点会在插值里被抹掉。
"""
from __future__ import annotations

from typing import List

from .photometry import code_to_l, l_to_code


def lstar_to_code(L: float) -> int:
    """L* → sRGB 8-bit 码（精确：L*→Y→sRGB 传输函数）。"""
    return max(0, min(255, int(round(l_to_code(L)))))


def code_to_lstar(c: int) -> float:
    """sRGB 8-bit 码 → L*（与 `lstar_to_code` 互逆）。"""
    return max(0.0, min(100.0, code_to_l(c)))


def _pchip(xs, ys, xq):
    """单调三次插值（Fritsch-Carlson），无过冲。不依赖 scipy。"""
    n = len(xs)
    if n < 2:
        return [float(ys[0])] * len(xq)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] if h[i] else 0.0 for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    out = []
    for x in xq:
        if x <= xs[0]:
            out.append(float(ys[0]))
            continue
        if x >= xs[-1]:
            out.append(float(ys[-1]))
            continue
        i = 0
        while i < n - 2 and x > xs[i + 1]:
            i += 1
        t_ = (x - xs[i]) / h[i] if h[i] else 0.0
        t2, t3 = t_ * t_, t_ * t_ * t_
        out.append((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t_) * h[i] * m[i]
                   + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h[i] * m[i + 1])
    return out


def grid16(anchor_codes) -> List[float]:
    """16 点采样网格：**锚点优先占位**，剩余名额用均匀网格里离已有点最远的补。"""
    grid = [0.0, 255.0] + [float(x) for x in anchor_codes if 0.0 < float(x) < 255.0]
    cand = [i * 255.0 / 15.0 for i in range(1, 15)]
    while len(grid) < 16 and cand:
        best = max(cand, key=lambda c: min(abs(c - g) for g in grid))
        grid.append(best)
        cand.remove(best)
    grid = sorted(set(round(x, 6) for x in grid))[:16]
    if grid[0] != 0.0:
        grid = [0.0] + grid
    if grid[-1] != 255.0:
        grid = grid + [255.0]
    return grid
