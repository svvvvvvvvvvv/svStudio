# -*- coding: utf-8 -*-
"""⚠⚠ 2026-10-30 废弃：网格搜索 + 罚项 = 「区间靶」，违反铁律 1。

现在生产路径不再调用本模块（`lr_solve.plan_batch` 已改成残差闭式，
见 `formula.ev_for_face`）。保留此文件只为留档与对照，**不要接回生产**。

废弃理由（设计沿革 §B9）：
  · `ev_grid / fill_grid` 是在离散网格里挑"离带最近"的——本质是把点靶换成带靶；
  · 罚项（只罚越界）虽比距离好，但仍需要"带"，而带内怎么选是任意的；
  · 真正的闭式：目标 = R × 背景亮部，幅度 = log2(Y比)，上限 = 白端物理量。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from .curve_design import apply_lut
from .solver import eval_slope

L_PER_SHADOW = 0.147
C_PER_SAT = 0.221


def _mid(band, dflt):
    if isinstance(band, (list, tuple)) and len(band) == 2:
        return 0.5 * (float(band[0]) + float(band[1]))
    return float(dflt)


def _half(band, dflt):
    if isinstance(band, (list, tuple)) and len(band) == 2:
        return max(0.5 * (float(band[1]) - float(band[0])), 1.0)
    return float(dflt)


def forward(meas: Dict[str, object], curve, ev: float, shadows: float,
            sat: float, fill: float = 0.0) -> Dict[str, float]:
    """前向模型：① → ③（可算）。**fill（局部曝光）是提脸的主力**，不能漏。"""
    p50 = float(meas.get('p50') or 45.0)
    p95 = float(meas.get('p95') or 90.0)
    face = float(meas.get('face_L') or 55.0)
    C = float(meas.get('face_C') or 20.0)
    p99 = float(meas.get('p99') or 90.0)

    def _apply(L):
        x = apply_lut(curve, L) if curve else L
        return x + eval_slope(x) * ev

    f50 = _apply(p50) + eval_slope(p50) * 0.0
    f95 = _apply(p95)
    from .calibration import L_PER_FILL
    ff = _apply(face) + shadows * L_PER_SHADOW + L_PER_FILL * fill
    # ★ 蒙版 fill 对 p99 的响应比中位弱得多（羽化/选区边缘）⇒ 取 0.3 倍斜率。
    #   这是**模型假设**，等标定（局部 fill 0 / 1.0 两点量 p99）出来后替换。
    from .calibration import L_PER_FILL_P99, P99_CEIL
    f99 = min(P99_CEIL, _apply(p99) + shadows * L_PER_SHADOW + L_PER_FILL_P99 * fill)
    fC = C + sat * C_PER_SAT
    return dict(p50=f50, p95=f95, face=ff, C=fC, p99=f99, p99_in=p99)


def _outside(v: float, band, half: float) -> float:
    """越界量（带内 = 0）。区间是**边界**，不是靶。"""
    if not isinstance(band, (list, tuple)) or len(band) != 2:
        return 0.0
    lo, hi = float(band[0]), float(band[1])
    if v < lo:
        return (lo - v) / max(half, 1e-6)
    if v > hi:
        return (v - hi) / max(half, 1e-6)
    return 0.0


def distance(pred: Dict[str, float], bands: Dict[str, object]) -> float:
    """**只罚越界**（纪律：区间是边界与线索，不是靶）。

    · 脸 L 落在卡的脸带内 ⇒ 0 分；越界多少罚多少
    · 脸 C 同理
    · 生理下沿 55 也是边界（低于才罚）
    · 人 p99：只罚"本方案额外加上去"的那部分（相对）
    · p50 / p95 **不进目标**（场景量，不是风格）
    """
    b = bands or {}
    d = 0.0
    d += 1.0 * _outside(pred['face'], b.get('face_L'), 6.0)
    d += 0.5 * _outside(pred['C'], b.get('face_C'), 4.0)
    if pred['face'] < 55.0:
        d += 1.0 * (55.0 - pred['face']) / 3.0
    cap = float((b.get('p95') or [0, 93])[1])
    base99 = float(pred.get('p99_in') or 0.0)
    cap_eff = max(cap, base99)
    if pred['p99'] > cap_eff:
        d += 1.0 * (pred['p99'] - cap_eff) / 2.0
    return float(d)


# 正则权重（"1 档曝光" = 0.33，其余按各自的物理量折算）
REG = dict(ev=0.33, fill=0.33, shadows=0.05, sat=0.044)


def penalize(ev: float, fill: float, shadows: float, sat: float) -> float:
    """动作惩罚：结果一样好时，优先选动作小的解（否则会走退化解）。"""
    return (REG['ev'] * abs(ev) + REG['fill'] * abs(fill)
            + REG['shadows'] * abs(shadows) + REG['sat'] * abs(sat))


def fit(meas: Dict[str, object], bands: Dict[str, object],
        curve_base=None, tone_grid: Tuple[float, ...] = (1.0, 0.75, 0.5, 0.25),
        ev_grid=(-0.6, -0.3, 0.0), sh_grid=(0, 20, 40, 60),
        sat_grid=(-10, 0, 10, 20),
        fill_grid=(0.0, 0.5, 1.0, 1.5)) -> Dict[str, object]:
    """在卡带附近搜索四个量，返回最优解与预测值。"""
    from .curve_design import curve_from_quantiles

    best = None
    q = dict(meas.get('quantiles') or {})
    for tone in tone_grid:
        # tone = "曲线往卡的方向走多少"（0 = 不修形状，1 = 完全按卡的形状残差修）
        cur = None
        if curve_base and q:
            base = curve_from_quantiles(q, bands.get('shape') or {},
                                        p95_cap=float((bands.get('p95') or [0, 93])[1]))
            if base:
                # tone = 曲线往"卡形状"走多少：0 = 恒等，1 = 完全用卡的形状残差
                cur = [(x, int(round(x + (y - x) * tone))) for x, y in base]
        for ev in ev_grid:
            for sh in sh_grid:
                for sat in sat_grid:
                    for fl in fill_grid:
                        pred = forward(meas, cur, ev, sh, sat, fl)
                        d = distance(pred, bands) + penalize(ev, fl, sh, sat)
                        if best is None or d < best['dist']:
                            best = dict(dist=round(d, 3), tone=tone, ev=ev, shadows=sh,
                                        sat=sat, fill=fl, curve=cur,
                                        pred={k: round(v, 1) for k, v in pred.items()})
    return best or {}
