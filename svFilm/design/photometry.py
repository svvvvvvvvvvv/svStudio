# -*- coding: utf-8 -*-
"""标准色度学：**L\\* ↔ 线性光 ↔ 曝光 ↔ sRGB 编码**。全部是定义式，没有拟合系数。

为什么单独一层（2026-10-30）：LR 侧的亮度换算过去散在 `expose` / `solver` /
`formula` / `curve` 四处，各写一个 `0.231·(L+16)` 的**局部斜率**，再用它去乘
几步、十几步的位移。斜率只在小步长成立；一步 1 EV 在 L*=50 处实际抬 17.2 L*，
线性式给 15.25（少 11%），在 L*=80 处实际 12.7、线性式给 11.1（少 13%）。
本层给**精确闭式**，并作为唯一真源。

出处（都可查，不是经验）
------------------------
· CIELAB（CIE 15:2018 / ISO 11664-4）
      L* = 116·f(Y/Yn) − 16
      f(t) = t^(1/3)                        t > ε = 216/24389
      f(t) = (κ·t + 16)/116                 t ≤ ε,  κ = 24389/27
  ⇒ 立方根段的反解与"曝光"复合后是**精确闭式**：
      L'(EV) = 2^(EV/3)·(L* + 16) − 16
  这正是旧代码里已经用对的 `ev_to_reach` 用的那条；现在把它提成唯一实现。
· sRGB（IEC 61966-2-1）
      E' = 12.92·E                          E ≤ 0.0031308
      E' = 1.055·E^(1/2.4) − 0.055          E > 0.0031308
  显示域 8-bit 码 = round(255·E')。 **L\\* 与码值不是线性关系**：L*=50 对应码 119，
  不是 127.5。曲线锚点必须走这条映射，否则整条曲线在暗部/亮部都偏。
· 曝光（Adobe Lightroom 文档：Exposure 的单位是 f-stop）
      +1 EV = 线性光 ×2。这是精确关系，不是近似。
· 分位数与单调映射可交换（概率论基本性质）
      Q_p(g(X)) = g(Q_p(X))  对任意单调不减 g 成立
  ⇒ 提亮后的 p95 **就是** p95 的精确映射；不需要"每档 p95 抬 11.2 L*"这种
  传递系数（那是把"分位点会换像素"和"映射"混为一谈，且没有出处）。

口径
----
`L*` 以显示白为 Yn=1（与测量层一致）；`code` 是 sRGB 8-bit 显示码 0..255。
超过 100 的 L* 只在"算过白多少"时用（`clamp=False`），不参与出片预测。
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- 常数（定义式）

EPSILON = 216.0 / 24389.0          # CIELAB 的 ε（=0.008856451679…）
KAPPA = 24389.0 / 27.0             # CIELAB 的 κ（=903.296296…）
L_BREAK = 8.0                      # f 的线性段/立方根段分界（L*=8 正好是 ε 的像）
LN2 = math.log(2.0)
SLOPE_K = LN2 / 3.0                # 立方根段 dL*/dEV 的系数（=0.231049060…）


# ---------------------------------------------------------------- Lab ↔ Y

def l_to_y(L: float) -> float:
    """L* → 相对亮度 Y/Yn（线性光，0..1 对应 L* 0..100）。"""
    L = float(L)
    if L > L_BREAK:
        return ((L + 16.0) / 116.0) ** 3
    return max(0.0, L / KAPPA)


def y_to_l(Y: float) -> float:
    """相对亮度 → L*。Y ≤ 0 给 0；Y > 1 会得到 >100（只在"过白多少"时用）。"""
    Y = float(Y)
    if Y <= 0.0:
        return 0.0
    if Y > EPSILON:
        return 116.0 * (Y ** (1.0 / 3.0)) - 16.0
    return KAPPA * Y


# ---------------------------------------------------------------- 曝光

def l_after_ev(L: float, ev: float, clamp: bool = True) -> float:
    """线性光增益 2^ev 之后同一个像素的 L*（**精确**，不是斜率乘档数）。

    立方根段上等价于 `2^(EV/3)·(L+16) − 16`；线性段与两端截断也一并处理。
    `clamp=True`：按显示白截到 100（像素不可能比白更亮）；
    `clamp=False`：允许 >100，用来回答"越过了白平台多少"。
    """
    if L is None:
        return 0.0
    y = l_to_y(float(L)) * (2.0 ** float(ev))
    if clamp:
        y = min(1.0, y)
    return y_to_l(y)


def l_after_ev_closed(L: float, ev: float) -> float:
    """立方根段的闭式（给单测对拍；生产走 `l_after_ev` 以覆盖线性段）。"""
    return (2.0 ** (float(ev) / 3.0)) * (float(L) + 16.0) - 16.0


def ev_for_l(L0: float, L1: float) -> float:
    """从 L0 到 L1 要几档曝光（精确反解：Y 比取对数）。"""
    y0, y1 = l_to_y(float(L0)), l_to_y(float(L1))
    if y0 <= 0.0 or y1 <= 0.0:
        return 0.0
    return math.log(y1 / y0, 2.0)


def slope_l_per_ev(L: float) -> float:
    """**导数** dL*/dEV（只在无穷小步长成立）。

    立方根段：0.231049·(L*+16)；线性段：L*·ln2（两段在 L*=8 处连续）。
    用途：只给"小步长/局部灵敏度"和界值估计；跨档换算一律用 `l_after_ev`
    / `ev_for_l`，不许拿它乘档数。
    """
    L = float(L)
    if L > L_BREAK:
        return SLOPE_K * (L + 16.0)
    return max(0.0, L) * LN2


def secant_l_per_ev(L: float, ev: float) -> float:
    """有限步长的平均斜率 (L'−L)/EV；EV=0 时退化为导数。"""
    if abs(float(ev)) < 1e-12:
        return slope_l_per_ev(L)
    return (l_after_ev(L, ev) - float(L)) / float(ev)


# ---------------------------------------------------------------- sRGB 编码

def srgb_encode(linear: float) -> float:
    """线性光 → sRGB 编码（0..1，IEC 61966-2-1）。"""
    c = min(1.0, max(0.0, float(linear)))
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def srgb_decode(code: float) -> float:
    """sRGB 编码（0..1）→ 线性光。"""
    c = min(1.0, max(0.0, float(code)))
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def l_to_code(L: float) -> float:
    """L* → sRGB 8-bit 显示码（0..255，浮点；写曲线时再取整）。"""
    return 255.0 * srgb_encode(l_to_y(L))


def code_to_l(code: float) -> float:
    """sRGB 8-bit 显示码（0..255）→ L*。"""
    return y_to_l(srgb_decode(float(code) / 255.0))


# ---------------------------------------------------------------- 白端

def over_white_L(L: float, ev: float) -> float:
    """提 ev 档之后越过白平台多少 L*（未截断；0 = 没过）。"""
    return max(0.0, l_after_ev(float(L), float(ev), clamp=False) - 100.0)


def ev_to_white(L: float) -> float:
    """从 L* 提到白平台（L*=100）还要几档。已经过白给 0。"""
    if float(L) >= 100.0:
        return 0.0
    return max(0.0, ev_for_l(float(L), 100.0))


def describe() -> str:
    """一句话口径（报告里引用，防止又有人写 0.231 乘档数）。"""
    return ('L*↔EV 用 CIELAB 精确式 L\'=2^(EV/3)(L+16)−16；'
            'L*↔码用 sRGB 传输函数（L*=50 ⇒ 码 %.0f）' % round(l_to_code(50.0), 1))
