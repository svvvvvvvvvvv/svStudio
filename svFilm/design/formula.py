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

# ---------------------------------------------------------------- ⑦ 的分配律

L_PER_FILL_P99 = 11.2   # 局部曝光 1 档 → 画面 p99（实测，与上面同源）


def allocate_fill(face_L: float, target_L: float, p99: Optional[float],
                  shadow_max_ui: float = 100.0) -> Dict[str, object]:
    """把"要提多少"拆成 Shadows 与 Exposure 两份 —— **按物理约束拆，不按偏好**。

    规则（2026-10-30 实测得出）：
      · `LocalShadows` **几乎不推 p99**（实测 +0.1 / 满档），所以**先吃满**，不受白端限制；
      · 剩余才用 `LocalExposure`，它 1 档把 p99 抬 11.2 格 ⇒ 上限 = (100 − p99) / 11.2；
      · 上限不够 ⇒ **记账"欠多少格"**，不硬提。

    实测依据（DSCF1307）：
      只 LocalExposure 0.1525 → 脸 +6.1、clip 5.55%
      只 LocalShadows  1.0    → 脸 +6.5、clip 0.40%   ← 干净
      两者叠加               → 脸 +15.6、clip 5.67%
    """
    from .calibration import L_PER_LOCAL_SHADOWS, L_PER_FILL as _LPF
    L_PER_FILL = _LPF
    need = float(target_L) - float(face_L)
    if need <= 0:
        return dict(shadows_ui=0.0, ev=0.0, deficit_L=0.0, why='已达标')
    sh_xmp = min(1.0, need / L_PER_LOCAL_SHADOWS)
    sh_ui = sh_xmp * (float(shadow_max_ui) / 1.0)
    rest = need - sh_xmp * L_PER_LOCAL_SHADOWS
    ev = 0.0
    capped = False
    if rest > 1e-9 and p99 is not None:
        ev_max = max(0.0, (100.0 - float(p99)) / L_PER_FILL_P99)
        ev = min(rest / L_PER_FILL, ev_max)
        capped = ev < rest / L_PER_FILL - 1e-9
    elif rest > 1e-9:
        ev = rest / L_PER_FILL
    got = sh_xmp * L_PER_LOCAL_SHADOWS + ev * L_PER_FILL
    return dict(shadows_ui=round(sh_ui, 1), ev=round(ev, 4),
                deficit_L=round(max(0.0, need - got), 2),
                why=('白端截断' if capped else ('阴影吃满+曝光补齐' if ev > 0 else '阴影足够')))

# ---------------------------------------------------------------- 轮廓光 / 颜色锚

def _share_above(q: Dict[str, float], x: float) -> float:
    """用五阶分位当**分段线性 CDF**，估算"大于 x 的占比"。

    为什么不用全直方图：测量层只存分位（存直方图会让 json 膨胀十倍），
    而分位足以估"提亮会吃掉多少亮边"这个量级——它本来就是粗判。
    """
    xs = [q.get('L_p10'), q.get('L_p25'), q.get('L_p50'), q.get('L_p75'), q.get('L_p90')]
    us = [0.90, 0.75, 0.50, 0.25, 0.10]          # 对应"大于它的占比"
    if any(v is None for v in xs):
        return 0.0
    if x <= xs[0]:
        return 0.90
    if x >= xs[-1]:
        return 0.0
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            t = (x - xs[i]) / max(xs[i + 1] - xs[i], 1e-6)
            return us[i] + (us[i + 1] - us[i]) * t
    return 0.0


def predicted_rim(rim: Dict[str, float], face_L: float, dL: float) -> Dict[str, float]:
    """预测"提亮之后轮廓光还剩多少"（局部提亮才会吃掉它）。

        亮边 = 环带里比 `脸 + 10` 更亮的像素占比
        局部提亮 dL 之后，门槛变成 `脸 + dL + 10`、环带不动 ⇒ 亮边变少

    ★ 全局提亮不吃亮边（环带与脸同步抬升，差值不变），所以只在局部分支调用。
    ★ 提亮前就为 0 的（如 1608，亮边 0.000）**不许归咎于提亮**——那是源片的事。
    """
    now = _share_above(rim, float(face_L) + 10.0)
    after = _share_above(rim, float(face_L) + float(dL) + 10.0)
    ratio = (after / now) if now > 1e-9 else None
    return dict(rim_now=round(now, 4), rim_after=round(after, 4),
                ratio=(round(ratio, 3) if ratio is not None else None),
                judged=(ratio is not None and ratio < 0.7))


def neutral_anchor(rec: Dict[str, object]) -> Dict[str, object]:
    """颜色锚：画面里有没有"可信的中性面"。

    理论：色彩恒常性——人眼按场景光源解释颜色，中性面是判断光源的依据。
    没有中性面 ⇒ **锚不可信**，白平衡只做保守修正（不许硬归零）。
    """
    nu = rec.get('neutral') or {}
    share = float(nu.get('share') or 0.0)
    ok = share >= 0.05 and nu.get('n')
    return dict(ok=bool(ok), share=round(share, 4),
                a=(round(float(nu['a']), 2) if ok and nu.get('a') is not None else None),
                b=(round(float(nu['b']), 2) if ok and nu.get('b') is not None else None),
                why=('有中性面（%.1f%%）' % (share * 100) if ok
                     else '中性面不足（%.1f%%）⇒ 颜色锚不可信，只做保守修正' % (share * 100)))
