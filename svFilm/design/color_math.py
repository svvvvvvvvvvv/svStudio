# -*- coding: utf-8 -*-
"""颜色数学（全部有公开出处，**不是经验系数**）。

· Lab → XYZ → xy → CCT：McCamy 1992 近似式
      n = (x − 0.3320) / (0.1858 − y)
      CCT = 449n³ + 3525n² + 6823.3n + 5520.33
· CIE 1960 UCS：u = 4x/(−2x+12y+3)，v = 6y/(−2x+12y+3)；Δuv = uv 到黑体轨迹的垂直距离
· Lab → HSL：Lab → XYZ → 线性 sRGB → 伽马 → HSL（坐标变换，无拟合）

用途：LR 的「温度/色调/HSL 色相」不再靠标定，直接由这几个公式算。
注：Lightroom 温度滑杆对 RAW 是开尔文刻度（Adobe 公开量程 2000–50000K），
    所以 CCT 差多少 K 就拨多少 K；色调用 Δuv 的符号定方向。
"""
from __future__ import annotations

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


def duv_from_lab(lab) -> float:
    """相对 D65 白点的 Δuv（正 = 偏品红/紫，负 = 偏绿）。"""
    u, v = uv1960(*xyz_to_xy(lab_to_xyz(lab)))
    u0, v0 = uv1960(0.3127, 0.3290)
    return float(v - v0)


def temp_shift_k(card_lab, ours_lab) -> float:
    """目标 CCT − 当前 CCT（正 = 要更暖）。"""
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
