# -*- coding: utf-8 -*-
"""三态与回归检查：① 引擎片 → ② 过曲线 → （③ 只能靠 LR 出片）。

为什么要这一层（自审 P1-2）：三态没打通，`S1/S2/S3` 回归检查从未跑过。

**能算的**：① → ②（曲线就是一条逐通道 LUT，纯数学，不需要 LR）。
**不能算的**：② → ③（点颜色 `SatScale` 没有公开传递函数，只能在 LR 里出片后验收）。

所以这里给：
    apply_curve_image()  整幅过曲线（LUT）
    regression_curve()   ①→② 的回归检查：
                           · S3 曲线位移在预算内
                           · S2 背景 a/b 不该被曲线带跑（选择性）
                           · 记账：肤色色相/彩度被曲线推了多少（不修，只记）
    limit_note()         明确写出"②→③ 算不了"的原因

用法：
    from design.states import apply_curve_image, regression_curve
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .invariants import S2, S3


def apply_curve_image(disp, pts: Optional[List[Tuple[int, int]]]):
    """整幅过曲线：把 16 点 LUT 逐通道作用到显示域（0..1）。

    为什么这样就对：LR 的色调曲线本身就是一条单调 LUT（PV2012 的点曲线部分），
    在编码值上逐通道查表。这里做的正是这件事。
    """
    if not pts:
        return disp
    xs = np.array([p[0] for p in pts], float)
    ys = np.array([p[1] for p in pts], float)
    lut = np.interp(np.arange(256), xs, ys, left=ys[0], right=ys[-1])
    u = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    idx = np.clip(np.rint(u * 255.0).astype(np.int16), 0, 255)
    return np.clip(lut[idx] / 255.0, 0.0, 1.0)


def _lab(disp):
    """显示域 → (L, a, b)，与测量层同口径（浮点，不量化）。"""
    u = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    lin = np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    d = 6.0 / 29.0
    f = np.where(xyz > d ** 3, np.cbrt(xyz), xyz / (3 * d * d) + 4.0 / 29.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return L, a, b


@dataclass
class CurveRegression:
    ok: bool
    items: List[tuple]              # [(名字, 通过?, 值, 限, 说明)]
    accounting: Dict[str, float]    # 记账：曲线把肤色推了多少（不修）

    def report(self) -> str:
        lines = []
        for name, ok, val, lim, why in self.items:
            lines.append('  %s %-10s 值=%-9s 限=%-9s %s'
                         % ('✓' if ok else '✗', name, _fmt(val), _fmt(lim), why))
        if self.accounting:
            lines.append('  记账（不修，只记）：')
            for k, v in self.accounting.items():
                lines.append('    %-24s %+.2f' % (k, v))
        lines.append('  ⇒ %s' % ('通过' if self.ok else '有项不通过'))
        return '\n'.join(lines)


def _fmt(v):
    if isinstance(v, float):
        return '%.2f' % v
    return str(v)


def regression_curve(disp_engine, pts: Optional[List[Tuple[int, int]]],
                     curve_deltas: Optional[Dict[str, float]] = None,
                     budget: float = S3['anchor_max']) -> CurveRegression:
    """① → ② 的回归检查。

    disp_engine : ① 的显示域数组（0..1，H×W×3）
    pts         : 该图的曲线点集
    curve_deltas: plan_curve 给出的每个锚点位移（L*）
    """
    d2 = apply_curve_image(disp_engine, pts)
    L1, a1, b1 = _lab(disp_engine)
    L2, a2, b2 = _lab(d2)

    items = []
    # S3：曲线位移在预算内
    worst = max([abs(v) for v in (curve_deltas or {}).values()] or [0.0])
    items.append(('S3-预算', worst <= budget + 1e-6, round(worst, 2), budget,
                  '曲线每锚点位移'))

    # S2（曲线这一段的版本）：整幅 b* 不该被系统性推走
    db = float(np.median(b2) - np.median(b1))
    items.append(('S2-色偏', abs(db) <= S2['dab_max'], round(db, 2), S2['dab_max'],
                  '曲线带来的整幅 b* 位移'))

    # 中性区不该被曲线染色（拿低彩度像素看）
    C1 = np.hypot(a1, b1)
    m1 = C1 <= 10.0
    dn = float('nan')
    if int(m1.sum()) > 1000:
        dn = float(np.median(a2[m1]) - np.median(a1[m1]))
    items.append(('S2-中性', (not np.isfinite(dn)) or abs(dn) <= 1.0, round(dn, 2), 1.0,
                  '中性区 a* 位移'))

    ok = all(it[1] for it in items)

    # 记账：曲线对色相/彩度的推动（不修，交给点颜色或回引擎）
    acc = {}
    C2 = np.hypot(a2, b2)
    sel = (C1 > 12) & (L1 > 40) & (L1 < 90)      # "有颜色且在中亮度"的一撮，近似肤色域
    if int(sel.sum()) > 1000:
        h1 = np.degrees(np.arctan2(b1[sel], a1[sel])) % 360
        h2 = np.degrees(np.arctan2(b2[sel], a2[sel])) % 360
        dh = ((np.median(h2) - np.median(h1) + 180) % 360) - 180
        acc['色相被推(度)'] = round(float(dh), 2)
        acc['彩度比'] = round(float(np.median(C2[sel]) / max(np.median(C1[sel]), 1e-6)), 3)
    acc['L50 位移'] = round(float(np.median(L2) - np.median(L1)), 2)
    return CurveRegression(ok=ok, items=items, accounting=acc)


def limit_note() -> str:
    """把"哪一段算不了"写明白（免得以后又去算）。"""
    return ('② → ③ 这一段**不能在这里算**：点颜色的 SatScale/HueShift 没有公开传递函数，'
            '只能在 LR 出片后验收（S1 皮肤 L 分布不变 / S2 背景不动 / S4 仍是人样）。')
