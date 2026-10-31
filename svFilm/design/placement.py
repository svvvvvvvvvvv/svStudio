# -*- coding: utf-8 -*-
"""放置层：**每张都算**的落点求解。没有区间、没有容差、没有"越界才动"。

为什么单独一层（2026-10-31）
--------------------------
旧的 `lr_solve` 是反馈式：`if p99 > 卡的上沿: 压一档`、`if 脸/背景比值在带内: EV=0`。
那等于"不越界就不算"，19 张能全给出 EV=+0.00，脸暗着出片。
这里改成**闭式求解**：给定介质的目标密度，直接解出这张图需要的曝光。

公式（全部可查）
--------------
· 密度 ↔ 亮度：D = −log10(Y)，Y = L* 的 CIELAB 反函数（`photometry.l_to_y`）
· 曝光：+1 EV = 线性光 ×2（Lightroom 文档），且 L'(EV) = 2^(EV/3)·(L*+16) − 16
  ⇒ EV = 3·log2( (L_target + 16) / (L_actual + 16) )
  这是精确闭式，不是斜率近似。
· 上限：主体 p99 抬完不能超过 100（白端物理量），给出 EV 的硬上限。
"""
from __future__ import annotations

import math

from .photometry import l_to_y, l_after_ev, y_to_l

__all__ = ['density_of_l', 'l_of_density', 'ev_to_place', 'ev_cap_by_p99']


def density_of_l(L: float) -> float:
    """显示 L* → 密度 D = −log10(Y)。L*≤0 时给一个很大的数（受限于量程）。"""
    y = l_to_y(float(L))
    if y <= 1e-6:
        return 6.0
    return -math.log10(y)


def l_of_density(D: float) -> float:
    """密度 → 显示 L*（`density_of_l` 的逆）。D ≥ 6 时返回 0。"""
    y = 10.0 ** (-float(D))
    return y_to_l(y)


def ev_to_place(L_actual: float, D_target: float) -> float:
    """把 L_actual 放到目标密度需要的曝光（EV，f-stop）。

    精确闭式：EV = 3·log2((L_target+16)/(L_actual+16))。
    """
    L_target = l_of_density(D_target)
    a = float(L_actual) + 16.0
    b = float(L_target) + 16.0
    if a <= 0 or b <= 0:
        return 0.0
    return 3.0 * math.log2(b / a)


def ev_cap_by_p99(ev: float, p99, limit: float = 100.0) -> float:
    """抬亮时不能把主体 p99 顶过 `limit`（白端是物理量，不是容差）。"""
    if p99 is None:
        return ev
    a = float(p99) + 16.0
    b = float(limit) + 16.0
    if a <= 0:
        return ev
    ev_max = 3.0 * math.log2(b / a)
    return min(ev, ev_max)
