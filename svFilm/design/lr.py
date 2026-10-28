# -*- coding: utf-8 -*-
"""LR 侧求解器：**七步，一次算一次验，不闭环**。

    ① 定"看哪里"（选择：脸 / 人 / 整幅）
    ② 曝光 Exposure      → 脸进合理区（T4）
    ③ 高光 Highlights    → 不爆白（T1）
    ④ 曲线              → **只修不变量 + 落地意图**（不是去对别人的分位）
    ⑤ 全局饱和           → 只在整幅明显偏灰时
    ⑥ 点颜色            → 肤色（T5 + 意图）
    ⑦ 验收三门

设计出处：分工设计 第六部分 §31-§36 + 第三部分（没有"该到多少"）。

★ 曲线的语义（2026-10-29 改）：
  旧：拿"某一批成片的分位"当靶，造一条曲线把它对上。
  新：**没有靶**。曲线只做两件事——
      (a) 修不变量：爆白就压低端、堵黑就抬趾部
      (b) 落地意图：更亮/更暗 ⇒ 小幅整体位移
  且每锚点位移 ≤3 L*（S3）。**不做形状匹配。**

本文件只算值、只判定，**不直接驱动 LR**（驱动在 lrsink.py）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .curve import plan_curve
from .invariants import T1, T2
from .intent import Intent

# 经验斜率（都要能被实测替换；写在这里是为了让数值只有一个出处）
EV_PER_L = 1.0 / 30.0          # 中间调：1 EV ≈ 30 L*
PC_SCALE_PER_C = 0.58          # 点颜色饱和 0.6 ⇒ 肤色 C ×1.35
PC_SHIFT_PER_DEG = 1.0 / 30.0  # 色相 ±1 = ±30°
INTENT_L_PER_UNIT = 2.0        # 意图 ±1 ⇒ 整体位移 ±2 L*


@dataclass
class LRPlan:
    """一张图的 LR 参数（单位就是 LR 面板上的数）。"""
    exposure_ev: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    whites: float = 0.0
    blacks: float = 0.0
    saturation: float = 0.0
    vibrance: float = 0.0
    curve: Optional[List[Tuple[int, int]]] = None
    point_color: Optional[Dict[str, float]] = None
    notes: Dict[str, str] = field(default_factory=dict)

    def as_xmp_scalars(self) -> Dict[str, float]:
        return dict(Exposure2012=self.exposure_ev, Highlights2012=self.highlights,
                    Shadows2012=self.shadows, Whites2012=self.whites,
                    Blacks2012=self.blacks, Saturation=self.saturation,
                    Vibrance=self.vibrance)


# ---------------------------------------------------------------- ① 看哪里

def pick_lookat(rec_engine: dict) -> str:
    """有脸→face，没脸但有人→person，都没有→matrix（H2 降级链）。"""
    skin = rec_engine.get('skin')
    fw = rec_engine.get('face_w_native') or 0
    if skin and fw >= 40:
        return 'face'
    if (rec_engine.get('env_share') or 0) < 0.95:
        return 'person'
    return 'matrix'


# ---------------------------------------------------------------- ② 曝光

def solve_exposure(rec_engine: dict, target_L: float = 72.0,
                   L_lo: float = 55.0, L_hi: float = 85.0,
                   intent: Optional[Intent] = None) -> float:
    """一次算到位。`target_L` 只是合理区里的一个落点，不是某个靶。"""
    lookat = pick_lookat(rec_engine)
    if lookat == 'face':
        cur = (rec_engine.get('skin') or {}).get('L')
    elif lookat == 'person':
        cur = None
    else:
        cur = (rec_engine.get('img') or {}).get('p50')
    if cur is None:
        return 0.0
    tgt = min(max(target_L, L_lo), L_hi)
    if intent is not None:
        tgt += getattr(intent, 'brighter', 0.0) * INTENT_L_PER_UNIT
        tgt = min(max(tgt, L_lo), L_hi)          # 意图不许把脸推出合理区（S4）
    return (tgt - cur) * EV_PER_L


# ---------------------------------------------------------------- ③ 收白

def solve_highlights(rec_after_ev: dict, clip_max: Optional[float] = None) -> float:
    """把爆白压回阈值内。一次算：按超出比例给负值。"""
    cm = T1['clip_max'] if clip_max is None else clip_max
    clip = (rec_after_ev.get('img') or {}).get('clip_pct') or 0.0
    if clip <= cm:
        return 0.0
    over = clip / max(cm, 1e-6)
    return -min(100.0, (over - 1.0) * 40.0)


# ---------------------------------------------------------------- ④ 曲线

def solve_curve(rec_engine: dict, intent: Optional[Intent] = None,
                budget: float = 3.0) -> Tuple[Optional[List[Tuple[int, int]]], dict]:
    """**只修不变量 + 落地意图**，不对任何"别人的分位"造形。

    返回 (16 点曲线 | None, 说明)。None = 不需要曲线或预算内无解。
    """
    img = rec_engine.get('img') or {}
    q = {str(k): img.get('p%d' % k) for k in (5, 25, 50, 75, 95)}
    if not all(isinstance(v, (int, float)) for v in q.values()):
        return None, {'why': '缺分位'}
    src = dict(q)
    dst = dict(q)

    # (b) 意图：整体小幅位移
    b = getattr(intent, 'brighter', 0.0) if intent else 0.0
    if abs(b) > 1e-6:
        for k in dst:
            dst[k] = src[k] + b * INTENT_L_PER_UNIT

    # (a) 不变量：爆白压低端、堵黑抬趾部
    clip = img.get('clip_pct') or 0.0
    fixed, unmet = [], {}
    if clip > T1['clip_max']:
        want = clip - T1['clip_max']            # 想压多少（L*）
        d = min(budget, want)
        dst['95'] -= d
        dst['75'] -= d * 0.5
        fixed.append('压白端 %.2f' % d)
        if want > d + 1e-9:
            unmet['白端'] = round(want - d, 2)
    p1 = img.get('p1') or 0.0
    if p1 < T2['p1_min']:
        want = T2['p1_min'] - p1
        d = min(budget, want)
        dst['5'] += d
        dst['25'] += d * 0.5
        fixed.append('抬黑端 %.2f' % d)
        if want > d + 1e-9:
            unmet['黑端'] = round(want - d, 2)

    if src == dst:
        return None, {'why': '无需修正（不变量都过、意图为空）'}
    pts, info = plan_curve(src, dst, budget=budget)
    info['fixed'] = fixed
    if unmet:
        # 曲线上限 3 L* 修不了的量 ⇒ 这不是曲线的活（S3/S5：回引擎要余量）
        info['unmet'] = unmet
    return pts, info


# ---------------------------------------------------------------- ⑤ 饱和

def solve_saturation(rec_engine: dict, intent: Optional[Intent] = None) -> float:
    """只在"整幅明显偏灰"或意图要浓淡时动；幅度小，且**不许用来补肤色**。"""
    d = getattr(intent, 'denser', 0.0) if intent else 0.0
    return max(-15.0, min(15.0, d * 15.0))


# ---------------------------------------------------------------- ⑥ 点颜色

def solve_point_color(rec_before: dict, target_C: Optional[float] = None,
                      target_h: Optional[float] = None) -> Optional[Dict[str, float]]:
    """肤色剂量（批次一套）：先 C 后 h，夹 ±1。取样点逐图由调用方给。"""
    skin = rec_before.get('skin') or {}
    C, h = skin.get('C'), skin.get('h')
    if C is None or h is None:
        return None
    out = dict(scale=0.0, shift=0.0)
    if target_C:
        out['scale'] = max(-1.0, min(1.0, (target_C / C - 1.0) / PC_SCALE_PER_C))
    if target_h:
        dh = ((target_h - h + 180) % 360) - 180
        out['shift'] = max(-1.0, min(1.0, dh * PC_SHIFT_PER_DEG))
    return out


# ---------------------------------------------------------------- 主流程

def solve_one(rec_engine: dict, rec_after_ev: Optional[dict] = None,
              rec_final: Optional[dict] = None, intent: Optional[Intent] = None,
              log=print) -> LRPlan:
    """单图的 LR 计划。缺的中间态留空，等测量接上再填。"""
    plan = LRPlan()
    plan.notes['lookat'] = pick_lookat(rec_engine)
    plan.exposure_ev = round(solve_exposure(rec_engine, intent=intent), 3)

    base = rec_after_ev or rec_engine
    plan.highlights = round(solve_highlights(base), 1)
    pts, info = solve_curve(rec_engine, intent=intent)
    plan.curve = pts
    plan.notes['curve'] = ('无需' if pts is None else
                           '修正 %s ｜ 最大位移 %.2f' % (info.get('fixed') or '意图',
                                                       info.get('max_abs_delta') or 0))
    plan.saturation = round(solve_saturation(rec_engine, intent), 1)

    if rec_final is not None:
        pc = solve_point_color(rec_final)
        if pc and (abs(pc['scale']) > 1e-6 or abs(pc['shift']) > 1e-6):
            plan.point_color = pc
    return plan
