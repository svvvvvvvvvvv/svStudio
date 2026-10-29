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
