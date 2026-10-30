# -*- coding: utf-8 -*-
"""LR 滑杆的**实测一阶响应**（2026-10-31 矩阵，n=5 张 × ±50）。

为什么不再用 borrowed tone_model
--------------------------------
`tone_model.py` 的 8 结点基函数来自 autoshade；2026-10-31 的 86 臂矩阵实测
（`analysis/corpus/kurai/lr_matrix_1030.py`）显示它在这些图上的残差
MAE 2.61 / P90 6.42 / 最大 15.83 L\\* —— 尤其 Highlights 在 L≈60 处被模型
判成 0、实测却有 3~4 L\\* 的位移。**结论：borrowed 模型不能用于剂量**，
只保留作结构参考（`tone_model.py` 顶部已注明）。

本模块用同一批实测拟合**每根滑杆的 k(L)**（单位：L\\*/100 滑杆值）：

    ΔL(L, v) = k_s(L) · v/100,      k_s(L) = a_s + b_s·L

| 滑杆 | a | b | 同数据 MAE | 留一源 CV MAE | 最大 |
|---|---:|---:|---:|---:|---:|
| Contrast | −12.487 | 0.23459 | 1.21 | 1.29 | 4.05 |
| Highlights | −1.488 | 0.13442 | 0.96 | 1.11 | 3.37 |
| Shadows | 11.324 | −0.12694 | 0.89 | 1.02 | 2.89 |
| Whites | −0.719 | 0.12800 | 0.74 | 0.79 | 3.46 |
| Blacks | 13.368 | −0.13703 | 1.17 | 1.19 | 4.31 |

Saturation（n=10）：

    ΔC = C0 · (1.16905·S + 0.00242·S²) / 100      （S 为 UI；r(30)=1.24, r(60)=1.31）

局部（主体蒙版，n=2~4，只在本批这张脸上）：

    LocalExposure：ΔL = 21.12·EV − 2.548·EV²（EV = 文件值×4；低段≈精确式，高段压缩）
    LocalShadows ：L60：0.5/1.0 → +0.49/+0.96 L*；L46：+1.90/+4.80 L*
                   ⇒ k(L)=17.30−0.27·L（旧的 7.0 是"哪一张都不对"的均值）
    LocalSaturation：ΔC = C0·0.364·UI/100（≈全局相对模型的 36%）
    LocalToning  ：文件 0.45/0.5 → 脸 C +15.6、h −21°；**强染色，不用于微调**

边界（**一阶试点，不是确认性标定**）
------------------------------------
· 源图是**大师成片**（已带胶片曲线），不是 ① 平基底片；`k(L)` 条件于这张底片曲线，
  绝对值不能搬到 ①/RAW；"随 L 变"的形状比 borrowed 模型可信。
· 5 张图的 5 个分位高度相关 ⇒ 有效 n≈5；留一源 CV 只是 5 折，不是独立 held-out。
· 只测了单变量、零基线、±50；滑杆**交互**（Contrast+Whites、Exposure+Highlights）
  与 >±50 都未测 ⇒ `slider_for_delta` 加了 L15~90 / ±50 / S≤60 的门，超出只给方向。
· 线性 k(L) 是看了数据才选的形式；残差 P90 ≈ 2~3 L\\*，只作规划起点，③ 必须验。
· 测量只验证了**确定性**（同图 5 次导出逐位一致），没验证 mask/姿态/色彩管理的准确性。
"""
from __future__ import annotations

import math
from typing import Optional

# ---- 实测系数（来源：analysis/corpus/kurai/analyze_matrix.py 的 fit_coeffs.json）
TONES = {
    'contrast': (-12.4867, 0.23459),
    'highlights': (-1.4883, 0.13442),
    'shadows': (11.3240, -0.12694),
    'whites': (-0.7189, 0.12800),
    'blacks': (13.3678, -0.13703),
}
SAT_A = 1.16905
SAT_B = 0.00242
LOCAL_SAT_R = 0.3639          # 局部饱和 / 全局相对模型的比值（n=4，两张脸）
# 局部 Shadows：文件 1.0 → 脸 ΔL* 随**起点**强烈变化
#   L60.6 → +0.96；L46.4 → +4.80（2026-10-31 补测 s4）
#   k(L) = A + B·L（单位 L*/100）；旧的 7.0 是"哪一张都不对"的均值。
LOCAL_SHADOW_A = 17.30
LOCAL_SHADOW_B = -0.27
LOCAL_EXP_A = 21.1209         # ΔL = A·EV + B·EV²
LOCAL_EXP_B = -2.54834

# 实测域：矩阵只测了 L 15~90、滑杆 ±50、S 0~60。超出就**不外推**，
# 只给方向/一档步进（设计 §3.3 "前向模型 ±50% ⇒ 动到带边即止"）。
L_DOMAIN = (15.0, 90.0)
SLIDER_DOMAIN = 50.0
SAT_DOMAIN = 60.0


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
    """反解：要改 dL，需要多少滑杆值（UI，−100..100）。

    **只在实测域内返回**（L 15~90、|v|≤50）；超出返回 None，交调用方只给方向。
    """
    if not (L_DOMAIN[0] <= float(L) <= L_DOMAIN[1]):
        return None
    k = k_tone(slider, L)
    if k is None or abs(k) < 0.05:
        return None
    v = 100.0 * float(dL) / float(k)
    lim = min(SLIDER_DOMAIN, 100.0) if limit is None else float(limit)
    if abs(v) > lim + 1e-9:
        return None
    return round(v, 1)


def sat_dC(C0: float, S: float) -> float:
    """全局 Saturation → 肤色 ΔC*（S 为 UI）。"""
    s = float(S)
    return float(C0) * (SAT_A * s + SAT_B * s * s) / 100.0


def sat_for_dC(C0: float, dC: float) -> Optional[float]:
    """反解全局 Saturation（解二次；取符号一致、量程内的根）。"""
    c0 = float(C0)
    if c0 <= 1e-6:
        return None
    target = 100.0 * float(dC) / c0
    # 反解出的 S 若超过实测域（±60），不许外推
    A, B = SAT_B, SAT_A
    if abs(A) < 1e-12:
        s = target / B
        return max(-100.0, min(100.0, s))
    disc = B * B + 4.0 * A * target
    if disc < 0:
        return None
    r1 = (-B + math.sqrt(disc)) / (2.0 * A)
    r2 = (-B - math.sqrt(disc)) / (2.0 * A)
    cands = [r for r in (r1, r2) if -SAT_DOMAIN <= r <= SAT_DOMAIN]
    if not cands:
        return None
    # 取与目标同号、绝对值小的根（小步优先）
    cands.sort(key=lambda r: (abs(r) if (r == 0 or (r > 0) == (target > 0)) else 1e9,
                              abs(r)))
    return round(cands[0], 1)


def local_sat_for_dC(C0: float, dC: float) -> Optional[float]:
    """局部 Saturation（UI）→ 目标 ΔC；用实测比值 0.364。"""
    c0 = float(C0)
    if c0 <= 1e-6:
        return None
    ui = 100.0 * float(dC) / (c0 * LOCAL_SAT_R)
    if abs(ui) > 60.0:          # 矩阵只测了 0.3/0.6（UI 30/60）
        return None
    return round(ui, 1)


def local_shadow_k(L: float) -> float:
    """局部 Shadows 的 k(L)：文件 1.0（UI 100）→ 脸 ΔL*。"""
    return max(0.0, LOCAL_SHADOW_A + LOCAL_SHADOW_B * float(L))


def local_shadow_for_dL(L: float, dL: float) -> Optional[float]:
    """局部 Shadows 文件值（0..1）→ 目标 ΔL；按起点的 k(L) 反解。"""
    k = local_shadow_k(L)
    if k < 1e-6:
        return None
    f = float(dL) / k
    if f < 0 or f > 1.0:
        return None
    return round(f, 3)


def local_exposure_dL(ev: float) -> float:
    """局部 Exposure 的实测 ΔL（EV；文件值×4）。高段压缩。"""
    ev = max(0.0, float(ev))
    return LOCAL_EXP_A * ev + LOCAL_EXP_B * ev * ev


def local_ev_for_dL(dL: float) -> Optional[float]:
    """反解局部 Exposure 的 EV（解二次；无实数根返回 None）。"""
    target = float(dL)
    if target <= 0:
        return 0.0
    A, B = LOCAL_EXP_B, LOCAL_EXP_A
    disc = B * B + 4.0 * A * target       # A<0
    if disc < 0:
        return None
    r = (-B + math.sqrt(disc)) / (2.0 * A)
    r = abs(r)
    return round(r, 3)


def describe() -> str:
    return ('实测一阶响应（n=5, ±50）：Contrast/Highlights/Shadows/Whites/Blacks '
            '各自 k(L)；Saturation 相对二次；局部另计。')
