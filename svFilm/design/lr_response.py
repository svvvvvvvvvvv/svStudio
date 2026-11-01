# -*- coding: utf-8 -*-
"""LR Basic 滑杆的**实测一阶响应**（规划起点，不是"正确值"）。

为什么有这个文件
----------------
设计要求 **Basic 为主、曲线补**（`LR设计.md` §3.1），而 Basic 六根滑杆
没有公开传递函数（Adobe 只写"按亮度区域自适应"）。没有模型就只能给 0，
那就退回了"只靠曲线"的旧路。这里用 2026-10-31 的 86 臂矩阵实测
（`analysis/corpus/kurai/lr_matrix_1030.py`，n=5 张 × ±50）拟合的 k(L)：

    ΔL*(L, v) = k_s(L) · v/100,     k_s(L) = a_s + b_s·L

| 滑杆 | a | b | 同数据 MAE | 留一源 CV MAE |
|---|---:|---:|---:|---:|
| Contrast   | −12.487 | 0.23459 | 1.21 | 1.29 |
| Highlights | −1.488  | 0.13442 | 0.96 | 1.11 |
| Shadows    | 11.324  | −0.12694 | 0.89 | 1.02 |
| Whites     | −0.719  | 0.12800 | 0.74 | 0.79 |
| Blacks     | 13.368  | −0.13703 | 1.17 | 1.19 |

边界（必须一起读）
------------------
· 源图是**大师成片**（已带胶片曲线），不是 ① 平基底片 ⇒ **绝对值不能搬**；
  这里只用它给"动多少"的**起点**，③ 出片后必须目检验证（设计允许的用法）。
· 只测了单变量、零基线、±50；滑杆交互与 >±50 未测。
· 实测域：L* 15~90、|滑杆| ≤ 50。超出**不外推**：给边界值并记账。
· k(L) 是看了数据才选的形式（线性），残差 P90 ≈ 2~3 L*。

出处与推导在 `analysis/corpus/kurai/lr_matrix_1030.py` / `analyze_matrix.py`；
本文件只是把**生产需要的五个系数 + 域**搬过来，不 import analysis。
"""
from __future__ import annotations

from typing import Optional

# ---- 实测系数（analysis/corpus/kurai/lr_matrix_1030.py 的 fit_coeffs.json）
TONES = {
    'contrast': (-12.4867, 0.23459),
    'highlights': (-1.4883, 0.13442),
    'shadows': (11.3240, -0.12694),
    'whites': (-0.7189, 0.12800),
    'blacks': (13.3678, -0.13703),
}

# 实测域（超出不外推；给边界值并记账）
L_DOMAIN = (15.0, 90.0)
SLIDER_DOMAIN = 50.0


def k_tone(slider: str, L: float) -> Optional[float]:
    """k_s(L)：每 100 点滑杆值对应的 L* 位移。"""
    c = TONES.get(str(slider))
    if not c:
        return None
    return float(c[0]) + float(c[1]) * float(L)


def delta(slider: str, L: float, value: float) -> Optional[float]:
    """一阶预测：ΔL = k(L)·value/100。"""
    k = k_tone(slider, L)
    return None if k is None else k * float(value) / 100.0


def slider_for_delta(slider: str, L: float, dL: float,
                     limit: Optional[float] = None) -> Optional[float]:
    """反解滑杆值（UI）。域外返回 None（调用方给边界值 + 记账，不外推）。"""
    if not (L_DOMAIN[0] <= float(L) <= L_DOMAIN[1]):
        return None
    k = k_tone(slider, L)
    if k is None or abs(k) < 0.05:
        return None
    v = 100.0 * float(dL) / float(k)
    lim = SLIDER_DOMAIN if limit is None else min(SLIDER_DOMAIN, float(limit))
    if abs(v) > lim + 1e-9:
        return None
    return round(v, 1)


def describe() -> str:
    return ('Basic 一阶响应：Contrast/Highlights/Shadows/Whites/Blacks 的 k(L) '
            '（实测 n=5、±50、L 15~90；域外不外推）')
