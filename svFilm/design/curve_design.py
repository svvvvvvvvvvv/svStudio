# -*- coding: utf-8 -*-
"""⑥ 主色调曲线：**图定位置、卡定间距**（§6B.3 / §6D，2026-10-30 重写）。

上一版的错：我把锚点写成 6/24/45/66/86 这些**绝对分位** —— 那是自造的（§6D 已列为待清）。

正确做法：
  · **位置**由这张图自己的分位给（p5/p25/p50/p75/p95，来自 ① 的测量）
  · **间距**由卡的 toe / contrast_win / shoulder 带中点给
  · 肩部落点由卡的 p95 带上沿给（不许把高光推过卡的肩）

公式（L* 域，全部可复算；2026-10-30 与实现对齐）：

    toe_i = q25−q5   cw_i = q75−q25   sh_i = q95−q75      ← 这张图自己
    d_toe = toe_card−toe_i   d_cw = cw_card−cw_i   d_sh = sh_card−sh_i
    new_p25 = q25 + clamp(d_toe,               ±3)
    new_p75 = q75 + clamp(d_toe + d_cw,        ±3)
    new_p95 = min(q95 + clamp(d_toe+d_cw+d_sh, ±3), max(cap, new_p75+1))
    new_p50 = q50                                          ← 不动
    端点    = (0,0) / (100,100)                            ← 补满量程

插值：PCHIP（单调，不反折 ⇒ 不出 banding）；16 点采样**包含锚点码位**
（`curve.grid16`），否则 p95 的 cap 会被插值抹掉。

文献口径：Adobe Lightroom 参考手册说 Contrast「mainly affecting midtones」，
高光是肩部 rolloff；`ToneCurvePV2012` 的坐标是 **0..255 显示码**。
⚠ LR 自己对这 16 点的插值方式**未核**（我们写的是 PCHIP 采样；
autoshade 用分段线性）——两种在 16 点足够密时都只是近似，不改变"锚点在带内"的判据。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .curve import grid16
from .invariants import S3
from .photometry import code_to_l, l_to_code

# S3：曲线每锚点位移 ≤3 L*（一处真源在 invariants）。曲线只修"形状残差"，
# 不许替介质背锅；所以「卡的形状带」再大，也只能把锚点搬到预算边。
CURVE_BUDGET_L = float(S3['anchor_max'])


def _budget(delta: float, budget: float = CURVE_BUDGET_L) -> float:
    return max(-float(budget), min(float(budget), float(delta)))


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
    # `skew` 不参与：它要求移动 p50（把中间调往亮/暗推），而 A 段的硬边界是
    # "曲线保持 p50"（不动亮度）。卡的 skew 只进 ledger，不进 LUT。
    _ = _mid(shape.get('skew'))

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
    # ★ 2026-10-30：每锚点的**累计**位移受 S3 预算约束（≤3 L*）。
    #   旧代码把 d_toe/d_cw/d_sh 直接相加，低反差图会被卡的形状带一次抬 30+ L*
    #   （实测构造：p25 0→9、p75 20→59），既违反 S3，也会把中间调拉平。
    new25 = float(q25) + _budget(d_toe)
    new75 = float(q75) + _budget(d_toe + d_cw)
    # 旧式 `min(cand, max(cap, cand, new75+1))` 恒等于 cand（max 里含 cand）
    # ⇒ `p95_cap` 是死参数。现在：先算候选，再用 cap 夹；≥ new75+1 保单调。
    # ⚠ cap 是卡的**硬肩**，可能让 p95 的位移超过 S3（例如底片 p95=99、卡肩 91）。
    #   那属于"底片/卡不符"，由 ③ 的 S3 检查与曝光去处理，不在这里偷偷放宽。
    cand95 = float(q95) + _budget(d_toe + d_cw + d_sh)
    new95 = min(cand95, max(cap, new75 + 1.0))
    new50 = float(q50)

    xs_in = sorted([float(q5), float(q25), float(q50), float(q75), float(q95)])
    # 平坦/低反差图的相邻分位可能相等（p5=p25=0）；PCHIP 的 h=0 会除零。
    # 给重复的 x 一个极小间距（0.001 L*），保持严格递增；不改变可见结果。
    for i in range(1, len(xs_in)):
        if xs_in[i] <= xs_in[i - 1]:
            xs_in[i] = xs_in[i - 1] + 1e-3
    ys = [new5, new25, new50, new75, new95]
    # ★ 2026-10-30 修：旧代码还有一句 `ys = [max(y, x_original)]`，
    #   它把"任何分位都不许比原值低"当铁律 ⇒ 卡的肩 p95=96 永远压不到 99 以下，
    #   `p95_cap` 因此第二次变成死参数。曲线本来就是**形状修正**，肩该压就得压；
    #   只保留单调约束（下面）。
    prev = None
    mono = []
    for v in ys:
        prev = v if prev is None else max(prev, v)
        mono.append(prev)
    ys = mono

    # ★ 2026-10-30 修：5 个分位锚点**覆盖不到 0..100 的两端**。旧代码在
    #   x > p95 处直接把曲线钳在 p95 的 y 上 ⇒ 低反差的图（p95=30）会把
    #   30 L* 以上**整片压平**，高光全丢。补两个端点 (0,0)/(100,100)，
    #   形成完整的单调映射；p95 锚点仍按 cap 夹。
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

    # ★ 2026-10-30：16 点必须**包含锚点位置**（与 `curve.grid16` 同一套）。
    #   此前这里用均匀 16 点，p95 的 cap（code≈252）根本不进 LUT，
    #   “卡肩上沿”会在插值里被抹掉——和 10-29 在 curve.py 修过的是同一个坑。
    anchor_codes = [l_to_code(v) for v in xs if 0.0 < v < 100.0]
    grid = grid16(anchor_codes) if points == 16 else [255.0 * i / (points - 1) for i in range(points)]
    out: List[Tuple[int, int]] = []
    prev = -1
    for gx in grid:
        # 曲线 LUT 的横轴是**显示码**，不是 L*：L*↔码走 sRGB 传输函数。
        yl = _pchip(xs, ys, code_to_l(gx))
        y = int(round(l_to_code(yl)))
        y = max(prev, min(255, max(0, y)))
        out.append((int(round(gx)), y))
        prev = y
    return out


def apply_lut(curve, L: float) -> float:
    """把曲线作用到一个 L* 上（0..255 显示码 LUT，**经 sRGB 编码**做横轴）。

    曲线本身是显示码的单调 LUT；L* 只是我们描述亮度的尺子。所以进来先
    L*→码（sRGB），查完再码→L*。旧版用 `L*2.55` / `y/2.55` 当线性换算，
    在 L*=50 处把码差算了 8 格，曲线压暗的幅度会被系统性地高估。
    """
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
