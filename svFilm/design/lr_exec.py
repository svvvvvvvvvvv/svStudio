# -*- coding: utf-8 -*-
"""把 LRPlan **执行**到 LR：写 XMP 作业单 → 导出 ③。

分工（`LR设计.md` §8）：
  · 算值在 `lr_solve`（纯算，可离线复算）；
  · 落盘在这里和 `lrsink`（XMP + 桥）；
  · 这一步不含任何"算值"，也不判过/不过 —— 人目检才是验收。

跑批只出一次 ①、一次 ③；**不迭代**（`LR设计.md` §3 / `纪律.md`）。
"""
from __future__ import annotations

import os
from typing import Dict

from .lr_plan import LRPlan
from .lrsink import apply_and_export, write_jobs


def run(plans: Dict[str, LRPlan], engine_dir: str, out_dir: str,
        card: str = '', pe: float = None, log=print) -> Dict[str, str]:
    """写 xmp → 驱动 LR 导出 ③。返回 {stem: ③ 路径}。"""
    write_jobs(plans, os.path.join(out_dir, 'xmp'),
               manifest_extra=dict(card=card or '?', step='lr-b',
                                   print_exposure=pe))
    pairs = []
    for stem in sorted(plans):
        src = os.path.join(engine_dir, '%s_engine.tif' % stem)
        if os.path.isfile(src):
            pairs.append((stem, src, plans[stem]))
        else:
            log('跳过（没有 ①）：%s' % stem)
    log('导出 %d 张（%s → %s）' % (len(pairs), engine_dir,
                                 os.path.join(out_dir, 'final')))
    return apply_and_export(pairs, os.path.join(out_dir, 'final'), log=log)
