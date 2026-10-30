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

from .photometry import ev_for_l, ev_to_white, slope_l_per_ev

# ---------------------------------------------------------------- 物理/公式

def slope_L(L: float) -> float:
    """dL*/dEV 的导数（CIELAB 立方根律；**只用于小步长/灵敏度**）。

    2026-10-30：值改为 `ln2/3·(L+16)`（0.231049…），与 `photometry` 同一份；
    跨档换算必须用 `ev_for_l`，不许拿它乘档数。
    """
    return slope_l_per_ev(float(L))


# ---------------------------------------------------------------- ⑦ 局部

def ev_for_face(face_L: float, bg_hi: float, R: float,
                p99: Optional[float] = None) -> Dict[str, float]:
    """⑦ 局部补光：**连续函数**，不是网格。

        target = R × bg_hi                      ← R 是显式的卡带参数（见调用处）
        EV(L)  = ev_for_l(L, target)            ← CIELAB 精确反解，只提"低于目标"的部分
        上限   = ev_to_white(p99)               ← 白端物理上限（到 L*=100 还剩几档）

    ★ 2026-10-30 改了两处：
      ① 原来 `ev_raw = (target−L)/slope(L)` 是线性化，跨档会低估（1 档差 11%）⇒ 精确式；
      ② 原来 `cap=(100−p99)/11.2` 里的 11.2 是"局部 fill 1 档抬多少 p99"的标定常数
         （n=2），拿它当白端上限没有物理依据 ⇒ 换成 `ev_to_white(p99)`。
         注意：局部 fill 对 p99 的实际响应比全局 EV 弱（实测 1 档 +12.9，精确式给
         +18.4），所以精确上限是**保守**的 —— 宁少提，不拿脸的亮部冒险。

    返回 dict(ev, ev_raw, target, capped_by, ...)，全部可复算。
    """
    target = float(R) * float(bg_hi)
    ev_raw = max(0.0, ev_for_l(float(face_L), target))
    cap = None
    ev = ev_raw
    if p99 is not None:
        cap = ev_to_white(float(p99))
        if ev > cap:
            ev = cap
    return dict(ev=round(ev, 4), ev_raw=round(ev_raw, 4), target=round(target, 2),
                capped_by=('p99' if cap is not None and ev < ev_raw else None),
                cap=round(cap, 4) if cap is not None else None)


# ---------------------------------------------------------------- 风格参数

# R = 脸 / 背景亮部。**兜底值**，只在卡没有 `face_over_bg` 带时用。
#
# ★ 2026-10-30：生产路径不再用固定 0.85 —— `lr_solve.plan_batch` 从卡的
#   `face_over_bg` 带取：比值在带内 ⇒ R=当前比值（不动）；出带 ⇒ 动到最近带边。
#   这里保留 0.85 只为"卡缺这一维"时不至于没有目标；旧注释里的 [0.78,0.92]
#   是旧卡版本，cards_v5 已不是这个区间。
R_DEFAULT = 0.85

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
