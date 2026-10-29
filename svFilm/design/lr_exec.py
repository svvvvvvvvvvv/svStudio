# -*- coding: utf-8 -*-
"""把 §56 步 6 的计划**执行**到 LR：LRPlan → XMP → 导出 ③。

分工（§56）：这一步不含任何"算值"——算值在 `lr_solve`，能离线复算；
这里只负责把算好的值搬进 LR 并出片。

映射（写死，可查）：
  ② 曝光      = 0（背景定曝光；本版不让全局为脸动）
  ③ 白端      = 明暗 `down` ⇒ Whites2012 = −8（**一档最小步**，半可算只给方向）
  ⑥ 曲线      = 本版**不动**（形状先记账；曲线 LUT 待接）
  ⑦ 局部      = LocalExposure2012 = local_ev / 4（实测公式，§61.1）
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .lr import LRPlan
from .lrsink import apply_and_export, write_jobs

WHITES_STEP = -8.0          # 一档最小步（LR 面板刻度）


def to_lrplans(plans: Dict[str, dict]) -> Dict[str, LRPlan]:
    """`lr_solve.plan_batch` 的输出 → {stem: LRPlan}。"""
    out: Dict[str, LRPlan] = {}
    for stem, p in plans.items():
        pl = p.get('plan') or {}
        w = p.get('white') or {}
        g = p.get('grading') or {}
        plan = LRPlan(
            exposure_ev=float(pl.get('exposure_ev') or 0.0),   # ② 白端（EV，文档单位）
            whites=0.0,   # ③ 不写白端：白端由 ② 的 EV 承担（§6B.2 最终口径）
            fill_ev=float(pl.get('local_ev') or 0.0),
            # local_highlights 暂不启用：桥对 LocalHighlights2012 的**回读是 nil**，
            #   '报 ok 但读不回来' = 不许信（老账）。改用'算好的上限'来防爆（见 lr_solve）。
            local_highlights=0.0,
            curve=p.get('curve'),
            saturation=float(g.get('saturation') or 0.0),
            vibrance=float(g.get('vibrance') or 0.0),
            split_highlight_hue=float(g.get('split_highlight_hue') or 0.0),
            split_highlight_sat=float(g.get('split_highlight_sat') or 0.0),
            split_shadow_hue=float(g.get('split_shadow_hue') or 0.0),
            split_shadow_sat=float(g.get('split_shadow_sat') or 0.0),
            split_balance=float(g.get('split_balance') or 0.0),
            notes=dict(
                curve=str(pl.get('curve') or {}),
                white_need=str(w.get('need')),
                person_clip=str(w.get('person_clip')),
                bg_clip=str(w.get('bg_clip')),
                p95=str(w.get('p95')),
            ),
        )
        out[stem] = plan
    return out


def run(plans: Dict[str, dict], engine_dir: str, out_dir: str,
        log=print) -> Dict[str, str]:
    """写 xmp → 导出 ③。返回 {stem: ③ 路径}。"""
    import os
    lrp = to_lrplans(plans)
    write_jobs(lrp, os.path.join(out_dir, 'xmp'),
               manifest_extra=dict(card='004', step='56-6'))
    pairs = []
    for stem in sorted(lrp):
        src = os.path.join(engine_dir, '%s_engine.tif' % stem)
        if os.path.isfile(src):
            pairs.append((stem, src, lrp[stem]))
        else:
            log('跳过（没有 ①）：%s' % stem)
    log('导出 %d 张（%s → %s）' % (len(pairs), engine_dir, os.path.join(out_dir, 'final')))
    return apply_and_export(pairs, os.path.join(out_dir, 'final'), log=log)
