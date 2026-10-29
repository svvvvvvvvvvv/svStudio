# -*- coding: utf-8 -*-
"""⑥ 主色调曲线：**从卡的形状带反推一条曲线**（模仿大师的影调形状）。

文献口径（不是自己拍的）：
  · 胶片的影调是"中调近似线性 + 肩部渐压 + 趾部渐抬"；肩部压多少、从哪开始压，
    由**形状三项**（toe / shoulder / contrast_win）描述 —— 这正是我们把卡聚合出来的量。
  · 色调曲线的作用在**中间调为主**（Adobe Lightroom 参考手册："Contrast … mainly affecting midtones"），
    高光段是肩部：输入越接近 255，输出斜率越小（rolloff）。
  · 参考实现的口径（darktable filmic / IC Filmic Tone Curve）：
    pivot 固定在中灰（0.18 线性 ≈ 45% sRGB），对比围绕 pivot 展开，
    肩部用单调递减斜率，不许出现反折（否则出 banding）。

做法（可复算，不跑 LR）：
  1. 取卡的形状带中点：toe / shoulder / contrast_win（P25~P75 的中点）
  2. 把它们换算成**百分位上的位移**：我们量到的 toe/shoulder/contrast_win 就是
     p25−p5 / p95−p75 / p75−p25（§13.3），所以卡带直接给出"目标分位间距"
  3. 用单调三次插值（PCHIP）过 5 个锚点，得到 16 点 LUT：
        p5  → 目标 p5
        p25 → p5' + toe'
        p50 → p25' + 0.5·contrast_win'      （中调不动，除非卡要求）
        p75 → p25' + contrast_win'
        p95 → p75' + shoulder'
  4. 高光端额外压一手：若肩部带的上沿仍 > 93，把 p95 目标钳到 93（这正是 T1 的白端），
     并保证单调（y 不递减）

★ 为什么用曲线而不是 Whites：实测 Whites −8 对 p95 无影响（99.2→99.2）；
  曲线是**直接改输出值**的旋钮，压 1 个单位就换 1 个 L\*，可算、可验。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

P95_MAX = 93.0


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


def curve_from_shape(shape: Dict[str, Sequence[float]],
                     p95_cap: float = P95_MAX,
                     points: int = 16) -> Optional[List[Tuple[int, int]]]:
    """卡的形状带 → 16 点主色调曲线（0..255）。

    `shape` 里是 (lo, hi) 带：toe / shoulder / contrast_win。
    返回 None 表示"形状带不全，这一版不动曲线"。
    """
    def mid(k):
        v = shape.get(k)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return 0.5 * (float(v[0]) + float(v[1]))
        return None

    toe, sh, cw = mid('toe'), mid('shoulder'), mid('contrast_win')
    if None in (toe, sh, cw):
        return None
    # 目标分位（L* → 0..255 的 sRGB 编码值用 L* 近似，二者在本讨论里同序）
    p5, p25, p50, p75, p95 = 6.0, 24.0, 45.0, 66.0, 86.0      # 中性起点（不是靶，是曲线的参考刻度）
    t5 = p5
    t25 = t5 + max(toe, 1.0)
    t75 = t25 + max(cw, 2.0)
    t95 = min(t75 + max(sh, 1.0), p95_cap)
    t50 = 0.5 * (t25 + t75)
    xs = [p5, p25, p50, p75, p95]
    ys = [t5, t25, t50, t75, t95]
    out: List[Tuple[int, int]] = []
    prev = -1
    for i in range(points):
        x = 255.0 * i / (points - 1)
        # 把 0..255 的 x 映射到 L* 域再插值（同一序，线性近似）
        xl = 100.0 * x / 255.0
        yl = _pchip(xs, ys, xl)
        y = int(round(255.0 * yl / 100.0))
        y = max(prev, min(255, max(0, y)))            # 单调、不越界
        out.append((int(round(x)), int(y)))
        prev = y
    return out


def describe(curve: Optional[List[Tuple[int, int]]]) -> str:
    if not curve:
        return '曲线：不动'
    return '曲线：%d 点，p95 目标 %.0f' % (len(curve), 255.0 * curve[-2][1] / 255.0)


def apply_lut(curve, L: float) -> float:
    """把曲线作用到一个 L* 上（0..255 的 LUT，线性插值）。"""
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
