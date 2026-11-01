# -*- coding: utf-8 -*-
"""颜色数学（全部有公开出处，**不是经验系数**）。

· Lab → XYZ → xy → CCT：McCamy 1992 近似式
      n = (x − 0.3320) / (0.1858 − y)
      CCT = 449n³ + 3525n² + 6823.3n + 5520.33
· CIE 1960 UCS：u = 4x/(−2x+12y+3)，v = 6y/(−2x+12y+3)
· Duv：uv 到 Planck 轨迹的**法向距离**；正 = 轨迹上方（偏绿），负 = 下方（偏品红/紫）
· Lab → HSL：Lab → XYZ → 线性 sRGB → 伽马 → HSL（坐标变换，无拟合）

用途：颜色关系的换算（CCT/Duv），不是“把成片白点当场景光”。
"""
from __future__ import annotations

import math

import numpy as np

D65 = np.array([0.95047, 1.0, 1.08883])


def lab_to_xyz(lab) -> np.ndarray:
    L, a, b = [float(v) for v in lab]
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0

    def inv(t):
        return t ** 3 if t ** 3 > 0.008856 else (t - 16.0 / 116.0) / 7.787

    return np.array([inv(fx), inv(fy), inv(fz)]) * D65


def xyz_to_xy(xyz) -> tuple:
    X, Y, Z = [float(v) for v in xyz]
    s = X + Y + Z
    return (X / s, Y / s) if s > 1e-9 else (0.3127, 0.3290)


def cct_from_xy(x: float, y: float) -> float:
    """McCamy 1992（xy 必须落在黑体轨迹附近；Lab 中性区满足）。"""
    n = (x - 0.3320) / (0.1858 - y) if abs(0.1858 - y) > 1e-9 else 0.0
    return 449.0 * n ** 3 + 3525.0 * n ** 2 + 6823.3 * n + 5520.33


def cct_from_lab(lab) -> float:
    return cct_from_xy(*xyz_to_xy(lab_to_xyz(lab)))


def uv1960(x: float, y: float) -> tuple:
    d = -2.0 * x + 12.0 * y + 3.0
    return (4.0 * x / d, 6.0 * y / d)


def planckian_xy(T: float) -> tuple:
    """Planck 轨迹的 CIE 1931 xy（Kim et al. 2002 近似，1667–25000K）。"""
    T = max(1667.0, min(25000.0, float(T)))
    if T <= 4000.0:
        x = (-0.2661239e9 / T**3 - 0.2343589e6 / T**2
             + 0.8776956e3 / T + 0.179910)
    else:
        x = (-3.0258469e9 / T**3 + 2.1070379e6 / T**2
             + 0.2226347e3 / T + 0.240390)
    if T <= 2222.0:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif T <= 4000.0:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    return (x, y)


def duv_from_lab(lab) -> float:
    """CIE 1960 Duv：到 Planck 轨迹的**有符号法向距离**。

    正 = 轨迹上方（偏绿）；负 = 轨迹下方（偏品红/紫）。
    不是“相对 D65 的 v 差”——D65 自己的 Duv 约为 +0.003。
    """
    x, y = xyz_to_xy(lab_to_xyz(lab))
    u, v = uv1960(x, y)
    xp, yp = planckian_xy(cct_from_xy(x, y))
    up, vp = uv1960(xp, yp)
    d = math.hypot(u - up, v - vp)
    return float(math.copysign(d, v - vp))


def temp_shift_k(card_lab, ours_lab) -> float:
    """目标 CCT − 当前 CCT（只描述，不是 WB 设置）。"""
    return cct_from_lab(card_lab) - cct_from_lab(ours_lab)


def _srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)


_M = np.array([[3.2406, -1.5372, -0.4986],
               [-0.9689, 1.8758, 0.0415],
               [0.0557, -0.2040, 1.0570]])


def lab_to_srgb(lab) -> np.ndarray:
    return _srgb(_M @ lab_to_xyz(lab))


def lab_to_hsl(lab) -> tuple:
    """返回 (H 度, S 0..1, L 0..1)。用于把"脸偏橙 3°"换成 HSL 橙色 Hue 的位移。"""
    r, g, b = lab_to_srgb(lab)
    mx, mn = float(max(r, g, b)), float(min(r, g, b))
    L = (mx + mn) / 2.0
    if mx == mn:
        return (0.0, 0.0, L)
    d = mx - mn
    S = d / (2.0 - mx - mn) if L > 0.5 else d / (mx + mn)
    if mx == r:
        H = ((g - b) / d) % 6.0
    elif mx == g:
        H = (b - r) / d + 2.0
    else:
        H = (r - g) / d + 4.0
    return (H * 60.0, float(S), float(L))


def hue_shift_deg(card_lab, ours_lab) -> float:
    """卡的脸色相 − 我们的脸色相（HSL 度，−180..180）。"""
    hc = lab_to_hsl(card_lab)[0]
    ho = lab_to_hsl(ours_lab)[0]
    return ((hc - ho + 180.0) % 360.0) - 180.0
