# -*- coding: utf-8 -*-
"""公式层：能算的用公式算，不能算的显式写成风格参数（不许伪装成"求解"）。

2026-10-30 立。动机（SV 的原话）：
  「这些解法我感觉都很不行，应该是靠公式规律去推算的。你这只是把数值靶换成了区间靶，
    特别是这个 −30 属于打补丁的问题。」

本模块只装**闭式**的东西，出处逐条写在函数上：

① `ev_for_face`      局部补光的连续函数（替代网格搜索）
② `ev_budget_p99`    p99 预算给出的补光上限（替代常数 LocalHighlights=−30）
③ `slope_L`          CIELAB 立方根段的斜率 dL/dEV（可算，非拟合）
④ `quantile_shape`   （**未启用**）分位形状匹配 —— 需要卡的**分位函数**，
                     现在卡里只存了宽区间（p50 带 29~66），没有形状信息 ⇒ 见 README 欠账

设计原则（写死在这里，改要先改这段）：
  ・局部补光**本质是审美干预**（改的是场景光比），所以它不可能有"正确值"。
    这里把它写成一条**连续函数 + 一个显式风格参数 R**，摆在明面上；
    绝不用"往区间里搜"来假装它是求解出来的。
  ・任何常数都必须能被公式替代；替不掉的，必须写清"它是风格，不是计算"。
"""
from __future__ import annotations

from typing import Dict, Optional

# ---------------------------------------------------------------- 物理/公式

def slope_L(L: float) -> float:
    """CIELAB 亮度对曝光档的局部斜率 dL*/dEV（可算，不是拟合）。

    L* 的定义在 Y/Yn = (L+16)/116 的立方根段 ⇒ dY/Y = ln2·dEV，
    dL*/dEV = (1/3)·(116/… ) 的线性化结果，业界常用的写法就是 0.231·(L+16)。
    出处：本仓 `design/expose.py::eval_slope`（同一条），这里给出同值实现，
    免得公式层再依赖求解层。
    """
    return 0.231 * (float(L) + 16.0)


# 局部补光的实测传递（2026-10-30 标定，`_calib_fill/calib_fill.json`，两张图各三档）
L_PER_FILL = 18.7          # 局部 fill 1 档（LocalExposure2012=0.25）→ 脸 L*
L_PER_FILL_P99 = 11.2      # 同一档 → 人 p99（未饱和时；已撞 100 时退化，见标定文件）


# ---------------------------------------------------------------- ⑦ 局部

def ev_for_face(face_L: float, bg_hi: float, R: float,
                p99: Optional[float] = None) -> Dict[str, float]:
    """⑦ 局部补光：**连续函数**，不是网格。

        target = R × bg_hi                      ← R 是显式风格参数（见下方 R_DEFAULT）
        EV(L)  = max(0, (target − L) / slope(L))  ← 只提"低于目标"的那部分，天然收敛到 0
        上限   = (100 − p99) / L_PER_FILL_P99    ← 白端物理上限（不是自定 95）

    ★ 2026-10-30 修正：原来用 `p99_cap=95` 这个自定阈值，属"阈值无来源"（铁律 1）。
      L* 的白端是 100（物理），所以上限只该是 "到白端还剩多少"。

    返回 dict(ev, ev_raw, target, capped_by, ...)，全部可复算。

    为什么不是"打进一个区间"：区间是靶。这里是"把脸提到 R·bg_hi"，R 明写在调用处，
    想改风格就改 R 一个数 —— 它不假装是从画面里推出来的。
    """
    target = float(R) * float(bg_hi)
    ev_raw = max(0.0, (target - float(face_L)) / max(slope_L(face_L), 1e-6))
    cap = None
    ev = ev_raw
    if p99 is not None:
        cap = max(0.0, (100.0 - float(p99)) / L_PER_FILL_P99)
        if ev > cap:
            ev = cap
    return dict(ev=round(ev, 4), ev_raw=round(ev_raw, 4), target=round(target, 2),
                capped_by=('p99' if cap is not None and ev < ev_raw else None),
                cap=round(cap, 4) if cap is not None else None)


def local_highlights_for(ev: float, p99_before: float, p99_after_pred: float,
                         lh_per_unit: Optional[float] = None) -> float:
    """局部高光要压多少 —— **由溢出算**，不是常数 −30。

    fill 会把人的 p99 抬高 Δ = ev × L_PER_FILL_P99。
    如果 Δ 超出"卡 p95 上沿"给的空间，就要压回去：
        LH = −(Δ_overflow) / lh_per_unit
    但 `lh_per_unit`（LocalHighlights 1 个 UI 点能把 p99 压多少）**从未标定**，
    所以这里**不返回符号常数**：没标定就返回 0，并让调用方记账（不许静默给一个数）。
    """
    if lh_per_unit is None or lh_per_unit <= 0:
        return 0.0
    overflow = float(p99_after_pred) - float(p99_before)
    if overflow <= 0:
        return 0.0
    return -overflow / float(lh_per_unit)


# ---------------------------------------------------------------- 风格参数

# R = 脸 / 背景亮部。这是**风格**，不是算出来的。
# 出处：目录卡 004 的 face_over_bg 带 [0.78, 0.92] 的中点（0.85）。
# 语义：让脸来到背景亮部的 85% —— "快追上背景，但不追上"。
# 改一个数就整体变风格：0.55 ≈ 目录卡逆光片那种"脸明显暗于背景"。
R_DEFAULT = 0.85
