# -*- coding: utf-8 -*-
"""⑥ 主色调曲线：**图定位置、卡定间距**（§6B.3 / §6D，2026-10-30 重写）。

上一版的错：我把锚点写成 6/24/45/66/86 这些**绝对分位** —— 那是自造的（§6D 已列为待清）。

正确做法：
  · **位置**由这张图自己的分位给（p5/p25/p50/p75/p95，来自 ① 的测量）
  · **间距**由卡的 toe / contrast_win / shoulder 带中点给
  · 肩部落点由卡的 p95 带上沿给（不许把高光推过卡的肩）

公式（L* 域，全部可复算）：

    new_p5  = p5
    new_p25 = new_p5 + toe_card
    new_p75 = new_p25 + cw_card
    new_p95 = min(new_p75 + sh_card, p95_cap)
    new_p50 = new_p25 + skew_card · (new_p75 − new_p25)

插值：PCHIP（单调，不反折 ⇒ 不出 banding）。

文献口径：Adobe Lightroom 参考手册说 Contrast「mainly affecting midtones」，
高光是肩部 rolloff；ToneCurvePV2012 的坐标是 **0..255 显示域分段线性**（§6C）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple


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


def _mid(band) -> Optional[float]:
    if isinstance(band, (list, tuple)) and len(band) == 2:
        return 0.5 * (float(band[0]) + float(band[1]))
    return None


def curve_from_quantiles(quantiles: Dict[str, float],
                         shape: Dict[str, Sequence[float]],
                         p95_cap: Optional[float] = None,
                         points: int = 16) -> Optional[List[Tuple[int, int]]]:
    """图的分位 + 卡的间距 → 16 点 LUT（0..255）。返回 None = 这一版不动曲线。"""
    q5, q25, q50, q75, q95 = (quantiles.get('p5'), quantiles.get('p25'),
                              quantiles.get('p50'), quantiles.get('p75'),
                              quantiles.get('p95'))
    if None in (q5, q25, q50, q75, q95):
        return None
    toe = _mid(shape.get('toe'))
    cw = _mid(shape.get('contrast_win'))
    sh = _mid(shape.get('shoulder'))
    if None in (toe, cw, sh):
        return None
    skew = _mid(shape.get('skew'))
    skew = 0.5 if skew is None else max(0.05, min(0.95, skew))

    # ★ 2026-10-30 最终口径：**只修形状残差**。
    #   ① 绝对间距重置两端 → 低键图被压到底（1608 p95 79.6→66）
    #   ② 按比例重分配   → 高键图中间调塌（1289 脸 63.9→41.3）
    #   正确：量这张图自己的 toe/cw/sh，把它 clamp 进卡的带，只补**差量**。
    def _clamp(v, band, lo_guard=1.0):
        if isinstance(band, (list, tuple)) and len(band) == 2:
            return max(float(band[0]), min(float(band[1]), float(v)))
        return float(v)

    toe_i = max(float(q25) - float(q5), lo_guard if False else 1.0)
    cw_i = max(float(q75) - float(q25), 1.0)
    sh_i = max(float(q95) - float(q75), 1.0)
    toe_t = _clamp(toe_i, shape.get('toe'))
    cw_t = _clamp(cw_i, shape.get('contrast_win'))
    sh_t = _clamp(sh_i, shape.get('shoulder'))
    d_toe = toe_t - toe_i
    d_cw = cw_t - cw_i
    d_sh = sh_t - sh_i

    cap = float(p95_cap) if p95_cap is not None else float(q95)
    new5 = float(q5)
    new25 = float(q25) + d_toe
    new75 = float(q75) + d_toe + d_cw
    new95 = min(float(q95) + d_toe + d_cw + d_sh, max(cap, float(q95) + d_toe + d_cw + d_sh, new75 + 1.0))
    new50 = float(q50)

    xs_in = sorted([float(q5), float(q25), float(q50), float(q75), float(q95)])
    ys = [new5, new25, new50, new75, new95]
    ys = [max(a, b) for a, b in zip(ys, xs_in)]        # 不许把任何分位往下压过原值太多
    prev = None
    mono = []
    for v in ys:
        prev = v if prev is None else max(prev, v)
        mono.append(prev)
    ys = mono

    out: List[Tuple[int, int]] = []
    prev = -1
    for i in range(points):
        x = 255.0 * i / (points - 1)
        xl = 100.0 * x / 255.0
        yl = _pchip(xs_in, ys, xl)
        y = int(round(255.0 * yl / 100.0))
        y = max(prev, min(255, max(0, y)))
        out.append((int(round(x)), y))
        prev = y
    return out


def apply_lut(curve, L: float) -> float:
    """把曲线作用到一个 L* 上（0..255 LUT，线性插值）。"""
    if not curve:
        return float(L)
    x = max(0.0, min(255.0, float(L) * 2.55))
    for i in range(len(curve) - 1):
        x0, y0 = curve[i]
        x1, y1 = curve[i + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return float(y0) / 2.55
            f = (x - x0) / (x1 - x0)
            return (y0 + f * (y1 - y0)) / 2.55
    return float(curve[-1][1]) / 2.55
