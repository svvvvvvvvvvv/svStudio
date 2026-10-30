# -*- coding: utf-8 -*-
"""§6H 拟合式求解：不再"按规则给量"，而是**对着卡做最小二乘/网格搜索**。

思路（来自 GitHub 上的同类做法）
--------------------------------
· [autoshade `src/fit.rs`](https://github.com/skymanbp/autoshade/blob/v1.2.5/src/fit.rs)
  把 develop 参数**拟合**到参考图，而不是手推公式；
· [ComfyUI-OCIO `grade_nodes.py`](https://github.com/SlavaSexton/ComfyUI-OCIO/blob/main/grade_nodes.py)
  的 `_compute_match_grade(image, reference, match_strength)`：匹配 + **强度**（blend）；
· [frameandfocal](https://frameandfocal.com/post-processing/clever-technique-copying-color-grading-one-photo-another-343194)
  强调用 **ΔE 验证**而不是眼估。

我们缺的正是这三样：**匹配目标（卡）· 强度（blend）· 距离（ΔE/E）**。
本模块把 ③ 的四个量（曲线混合度、曝光、局部 Shadows、局部饱和）在卡的带附近做搜索，
目标函数 = 与卡的加权距离；然后取最小者。**纯 CPU，不跑 LR。**

距离（每项都归一到"卡的带宽度"）
   D = w1·|p50 − 卡p50带|   + w2·|p95 − 卡p95带|
     + w3·|脸L − 卡脸L带|    + w4·|脸C − 卡脸C带|
     + 罚项：人 p99 超卡肩、脸低于生理下界 55
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
    ff = _apply(face) + shadows * L_PER_SHADOW + eval_slope(face) * fill
    f99 = _apply(p99) + shadows * L_PER_SHADOW + eval_slope(p99) * fill
    fC = C + sat * C_PER_SAT
    return dict(p50=f50, p95=f95, face=ff, C=fC, p99=f99)


def distance(pred: Dict[str, float], bands: Dict[str, object]) -> float:
    b = bands or {}
    d = 0.0
    # ★ 人像优先：p95 只是**线索**（大师 27% 的成片都 >93），权重压低；脸是主目标
    # p95 不计入距离：它只是线索（大师 27% 成片 >93），计进去会把 EV 一路压到网格端点
    # d += ... p95 ...
    d += 0.5 * abs(pred['p50'] - _mid(b.get('p50'), 45.0)) / _half(b.get('p50'), 10.0)
    d += 2.0 * abs(pred['face'] - _mid(b.get('face_L'), 65.0)) / _half(b.get('face_L'), 6.0)
    d += 0.8 * abs(pred['C'] - _mid(b.get('face_C'), 24.0)) / _half(b.get('face_C'), 4.0)
    # 罚项（物理/生理）
    cap = float((b.get('p95') or [0, 93])[1])
    if pred['p99'] > cap:
        d += 2.0 * (pred['p99'] - cap) / 2.0
    if pred['face'] < 55.0:
        d += 1.0 * (55.0 - pred['face']) / 3.0
    return float(d)


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
                        d = distance(pred, bands)
                        if best is None or d < best['dist']:
                            best = dict(dist=round(d, 3), tone=tone, ev=ev, shadows=sh,
                                        sat=sat, fill=fl, curve=cur,
                                        pred={k: round(v, 1) for k, v in pred.items()})
    return best or {}
