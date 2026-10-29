# -*- coding: utf-8 -*-
"""§6F 预测合成器：把七步当成**一条可预测的链**，先把 ③ 预测出来再定各步的量。

为什么需要它（现在的毛病）：
  各步各算各的、最后靠"上限"兜底 ⇒ 1726 目标 55、实际出到 66.7（
  fill 1.46 EV 顶满 + Shadows 顶到 60，两步都在撞上限，谁也没对齐目标）。

口径（每个环节都用可算/已标定的传递）：
  L1 = LUT(曲线, L0)                 ← ⑥ 曲线（显示域 0..255 分段线性）
  L2 = L1 + slope(L1)·EV             ← ② 曝光（EV，CIELAB 反解）
  L3 = L2 + slope(L2)·fill_ev + 0.147·shadows_ui   ← ⑦ 局部（EV/4 与实测斜率）
  C3 = C0 + 0.221·sat_ui             ← ⑧ 饱和（实测斜率）

分配规则（人像优先）：
  1 先用 **Shadows**（只提人身上的暗部，不碰亮部）补到目标，最多 60
  2 还差的部分再用 **局部曝光** 补，最多 +1.5 档
  3 人的亮部（p99）预测不许过卡的 p95 上沿 ⇒ 超过就回退到上限
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple


def _slope(L: float) -> float:
    from .solver import eval_slope
    return eval_slope(float(L))


def compose(meas: Dict[str, object], curve, ev_white: float,
            target_face: Optional[float], cap_hi: float,
            l_per_shadow: float = 0.147,
            shadow_max: float = 60.0, fill_max: float = 1.5) -> Dict[str, float]:
    """返回 {exposure_ev, fill_ev, local_shadows, predicted_face, predicted_p99}。"""
    from .curve_design import apply_lut

    f0 = meas.get('face_L')
    p99 = meas.get('p99')
    if f0 is None:
        return dict(exposure_ev=ev_white, fill_ev=0.0, local_shadows=0.0,
                    predicted_face=None, predicted_p99=None)

    # ⑥ + ②
    f1 = apply_lut(curve, float(f0)) if curve else float(f0)
    f2 = f1 + _slope(f1) * float(ev_white)
    q1 = apply_lut(curve, float(p99)) if (curve and p99 is not None) else p99
    q2 = (float(q1) + _slope(float(q1)) * float(ev_white)) if q1 is not None else None

    if target_face is None:
        return dict(exposure_ev=ev_white, fill_ev=0.0, local_shadows=0.0,
                    predicted_face=f2, predicted_p99=q2)

    need = float(target_face) - f2
    shadows = 0.0
    fill = 0.0
    if need > 0:
        # ★ 在 p99 上限内**最大化**脸的提升（原来一撞上限就退回 0，等于放弃）
        #   分配顺序：Shadows 先（只提暗部），剩下用局部曝光。
        best = (0.0, 0.0, f2)
        for sh in range(0, int(shadow_max) + 1, 5):
            base = f2 + sh * l_per_shadow
            q_base = (float(q2) + sh * l_per_shadow) if q2 is not None else None
            room = fill_max
            if q_base is not None:
                room = min(fill_max, max(0.0, (cap_hi - q_base) / max(_slope(q_base), 1e-6)))
            fc = base + _slope(base) * room
            if fc > best[2]:
                best = (float(sh), float(room), fc)
        shadows, fill, pred = best
        # 目标够到就收手（不要为了"最大化"超过目标）
        if pred > target_face and shadows > 0:
            sh_need = (float(target_face) - f2) / l_per_shadow
            shadows = max(0.0, min(shadows, round(sh_need / 5.0) * 5.0))
            fill = 0.0
            pred = f2 + shadows * l_per_shadow

    pred_face = f2 + shadows * l_per_shadow + _slope(f2) * fill
    pred99 = (q2 if q2 is not None else None)
    if pred99 is not None:
        pred99 = float(pred99) + shadows * l_per_shadow + _slope(float(pred99)) * fill
    return dict(exposure_ev=round(float(ev_white), 3), fill_ev=round(float(fill), 3),
                local_shadows=round(float(shadows), 1),
                predicted_face=round(float(pred_face), 1),
                predicted_p99=(round(float(pred99), 1) if pred99 is not None else None))
