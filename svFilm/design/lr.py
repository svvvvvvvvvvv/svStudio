# -*- coding: utf-8 -*-
"""LR 侧求解器：**七步，一次算一次验，不闭环**。

    ① 定"看哪里"（选择：脸 / 人 / 整幅）
    ② 曝光 Exposure      → 脸进合理区（T4）
    ③ 高光 Highlights    → 不爆白（T1）
    ④ 曲线              → 形状（每锚点 ≤3 L*，S3）
    ⑤ 全局饱和           → 只在整幅明显偏灰时
    ⑥ 点颜色            → 肤色（T5 + 意图）
    ⑦ 验收三门

设计出处：分工设计 第六部分 §31-§36。

关键约束（照抄设计，别自己加）：
  · 曝光**一次算到位**：1 EV ≈ 30 L*，直读不闭环
  · 曲线**不许动 p50**（曝光归曝光滑杆）
  · Highlights 与曲线肩部**只用 Highlights**
  · 全局饱和**不许用来补肤色**
  · 唯一允许多轮的是点颜色（斜率有饱和倾向）

本文件只写"算什么值"与"判什么"，**不直接驱动 LR**；
驱动由 `design/lrsink.py`（待写）把值写成 XMP 作业单。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 经验斜率（都要能被实测替换，写在这里是为了让数值只有一个出处）
EV_PER_L = 1.0 / 30.0        # 中间调：1 EV ≈ 30 L*
PC_SCALE_PER_C = 0.58        # 点颜色饱和 0.6 ⇒ 肤色 C ×1.35
PC_SHIFT_PER_DEG = 1.0 / 30.0  # 色相 ±1 = ±30°


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
    curve: Optional[List[tuple]] = None          # [(x, y)] 0..255
    point_color: Optional[List[str]] = None      # 19 数字记录（见 lr_xmp.point_color）
    notes: Dict[str, str] = field(default_factory=dict)

    def as_xmp_scalars(self) -> Dict[str, float]:
        return dict(Exposure2012=self.exposure_ev, Highlights2012=self.highlights,
                    Shadows2012=self.shadows, Whites2012=self.whites,
                    Blacks2012=self.blacks, Saturation=self.saturation,
                    Vibrance=self.vibrance)


# ---------------------------------------------------------------- ① 看哪里

def pick_lookat(rec_engine: dict) -> str:
    """有脸→face，没脸但有人→person，都没有→matrix。"""
    skin = rec_engine.get('skin')
    fw = rec_engine.get('face_w_native') or 0
    if skin and fw >= 40:
        return 'face'
    if (rec_engine.get('env_share') or 0) < 0.95:
        return 'person'
    return 'matrix'


# ---------------------------------------------------------------- ② 曝光

def solve_exposure(rec_engine: dict, target_L: float = 72.0,
                   L_lo: float = 55.0, L_hi: float = 85.0) -> float:
    """一次算到位：按脸的读数，把脸推到合理区中部。

    目标不是某个卡的值；`target_L` 只是"合理区里的一个落点"（默认 72，
    取 T4 区间中点偏上一点）。
    """
    lookat = pick_lookat(rec_engine)
    if lookat == 'face':
        cur = (rec_engine.get('skin') or {}).get('L')
    elif lookat == 'person':
        cur = None                      # 人档：用整幅 p50 近似（实现留待测量侧给量）
    else:
        cur = (rec_engine.get('img') or {}).get('p50')
    if cur is None:
        return 0.0
    tgt = min(max(target_L, L_lo), L_hi)
    return (tgt - cur) * EV_PER_L


# ---------------------------------------------------------------- ③ 收白

def solve_highlights(rec_after_ev: dict, clip_max: float = 2.5) -> float:
    """把爆白压回阈值内。一次算：按 clip 超出的比例给负值（斜率待实测替换）。"""
    clip = (rec_after_ev.get('img') or {}).get('clip_pct') or 0.0
    if clip <= clip_max:
        return 0.0
    over = clip / max(clip_max, 1e-6)
    # 经验：Highlights −100 约能压 6~10 个百分点的爆白；这里按比例线性给，够用即止
    return -min(100.0, (over - 1.0) * 40.0)


# ---------------------------------------------------------------- ④ 曲线

def solve_curve(rec_engine: dict, rec_after_ev: dict, anchor_max: float = 3.0
                ) -> Optional[List[tuple]]:
    """只搬形状、不动 p50，且每锚点位移 ≤ anchor_max（S3 预算）。

    返回 16 点曲线；若需要的位移超预算 ⇒ 返回 None 并说明"该回引擎"。
    """
    return None      # 骨架：曲线构造沿用 solve_batch.build_curve（带锚点预算钳位）


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


# ---------------------------------------------------------------- 主流程（骨架）

def solve_one(rec_engine: dict, rec_after_ev: Optional[dict] = None,
              rec_final: Optional[dict] = None, log=print) -> LRPlan:
    """单图的 LR 计划。缺的中间态先留空，等测量接上再填。"""
    plan = LRPlan()
    plan.notes['lookat'] = pick_lookat(rec_engine)
    plan.exposure_ev = solve_exposure(rec_engine)
    if rec_after_ev:
        plan.highlights = solve_highlights(rec_after_ev)
        plan.curve = solve_curve(rec_engine, rec_after_ev)
    if rec_final:
        pc = solve_point_color(rec_final)
        if pc:
            plan.notes['point_color'] = str(pc)
    return plan
