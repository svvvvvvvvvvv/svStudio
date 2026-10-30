# -*- coding: utf-8 -*-
"""LR 旋钮的**实测传递系数**（一次对照得来，写死在这里，别处不许再编）。

标定记录
--------
日期   2026-10-30
方法   同一张 ①（`_run_1029_tex2/engine/DSCF1231_engine.tif`），只动一个旋钮，其余全不动；
       导出后量同一取样点（脸 C* / 人的中位 L）。
数据   `_run_1030_card004/_calib/calib.json`（sat0/sat30/sh0/sh40 四张）
       · Saturation  0 → 30   ：脸 C* 20.00 → 26.63  ⇒ **+0.221 C\\*/单位**
       · LocalShadows 0 → 0.40 ：人的中位 L 41.57 → 47.45 ⇒ **+0.147 L\\*/单位**（UI 刻度）

刻度（§6C）
----------
  · 全局 `Saturation` 是 UI 刻度（−100..100）
  · 局部 `LocalShadows2012` 写 XMP 时要 /100（0.40 = UI 40）

为什么可信
----------
两项都是一次对照、单变量、同一测量口径（§13）；L\\* 与 C\\* 是 CIELAB 量，不随缩图变。
样本 n=1 张图 ⇒ 只当**一阶线性近似**，斜率误差大概 ±10%；够用来定幅度，不够用来做微调收敛。
"""
from __future__ import annotations

# 每 1 个 UI 单位（−100..100）换多少
C_PER_SAT_UNIT = 0.221        # 全局 Saturation → 肤色 C*
L_PER_SHADOW_UNIT = 0.147     # 局部 Shadows（UI 单位）→ 人身上中位 L*

SAT_LIMIT = 30.0              # 幅度上限：一次最多 ±30（再大就不是"补一点"，是换风格）
SHADOW_LIMIT = 60.0


def sat_for_dc(dc: float) -> float:
    """要把肤色 C\\* 挪 `dc` 需要多少 Saturation 单位。"""
    if not dc:
        return 0.0
    v = float(dc) / C_PER_SAT_UNIT
    return max(-SAT_LIMIT, min(SAT_LIMIT, v))


def shadows_for_dl(dl: float) -> float:
    """要把人身上中位 L\\* 挪 `dl` 需要多少局部 Shadows 单位（UI）。"""
    if not dl:
        return 0.0
    v = float(dl) / L_PER_SHADOW_UNIT
    return max(0.0, min(SHADOW_LIMIT, v))


# ---- 局部 fill（LocalExposure2012）的传递：2026-10-30 标定（_calib_fill/calib_fill.json）
#   两张图、三档（0 / 1.0 / 1.5 档），同一测量口径：
#     DSCF1231  脸 52.94 → 72.94 → 81.57  ⇒ 19.1 L*/档
#     DSCF1665  脸 43.53 → 62.35 → 70.98  ⇒ 18.3 L*/档   （取均值 18.7）
#   p99 的响应取决于是否已经饱和：
#     DSCF1231  81.57 → 94.51 → 98.43 ⇒ 11.2 L*/档
#     DSCF1665  98.04 → 99.61 → 100.0 ⇒ 1.3 L*/档（已撞 100）
#   ⇒ 用"斜率 + 100 天花板"两层描述，比单一斜率准。
# ---- 黑端（Blacks2012）：2026-10-30 标定（_calib3，DSCF1608）
#     Blacks −10/−20/−30 → p1 4.1→2.5/1.7/0.9
#     增益 0.150 / 0.115 / 0.103 L*/点 ⇒ **递减**（饱和），线性只在 −10 附近成立。
#     用途：提亮之后把黑点锚回去；量 = Δ黑 / 0.15，且**不许超过 −20**（再往里就是死黑）
L_PER_BLACKS = 0.150
BLACKS_MAX = 20.0

# ---- 曲线暗部斜率：同上标定
#     曲线在 x=32 处下压 6/12/18 → p5 6.3→4.1/2.5/1.2
#     增益 0.36 / 0.31 / 0.28 L*/像素 ⇒ 约 0.30（x≈32 处）
#     用途：提亮压缩了暗部跨度 r 时，用这段斜率把 r 补回 1
L_PER_CURVE_DARK = 0.30

# ---- 颜色分级（阴影轮）：同上标定
#     SplitToningShadowHue=220、S=20/40/60 → 背景 b50 4.0→0.04/−3.30/−6.39
#     增益 −0.198 / −0.182 / −0.173 b*/点 ⇒ 约 **0.18 b*/点**
#     ★★ 同时**脸也被拉冷**：脸 b* 13.3→10.2/8.0/5.3（≈0.13 b*/点）
#     ⇒ 阴影轮不是"只动背景"：脸的暗侧落在阴影区，会被一起染色。
#       所以冷暖分离必须配"脸保护"（主体蒙版反向，或把中间调轮留给脸）。
B_PER_SHADOW_GRADE = 0.18
B_SHADOW_GRADE_LEAK_FACE = 0.13

L_PER_FILL = 18.7        # 局部 fill 1 档 → 脸 L*
L_PER_FILL_P99 = 11.2    # 局部 fill 1 档 → 人 p99（未饱和时）
P99_CEIL = 100.0
