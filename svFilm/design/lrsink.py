# -*- coding: utf-8 -*-
"""LR 执行端（sink）：把 `LRPlan` 写成 XMP 作业单，驱动 LR 出图。

为什么走 XMP 而不是逐键写（10-28 实测）：
  · 桥逐键写有三个治不好的病：键名靠猜（写错静默丢弃）、结构化键写不进、状态只活在会话里。
  · XMP 是一份文件：可 diff、可回放、键名就是规范名。
  · 三条硬约束（缺一条 LR 一个数都不读）：`<?xpacket?>` 包头 · `AlreadyApplied="False"` · `WhiteBalance="As Shot"`。
  · PointColors **必须配 `ColorVariance`**，且 `SrcHue ≤ 6`（我们夹 5.95）。

本模块只做两件事：
  1. `plan_to_xmp(plan, curve=None, point_colors=None)` → XMP 文本
  2. `render(plan, src_tif, out_dir)` → 借 LR 桥出图（ADD + EXPORTTIF）

现成实现在 `E:\\ds\\photo\\_corpus\\kurai\\full\\lr_xmp.py`（含 point_color 的 19 数字构造）；
这里作为设计层的门面，把那份实现当底层用，避免两套漂移。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional

from .lr import LRPlan

# 底层实现（测量/桥那一侧）
_PHOTO = r'E:\ds\photo\_corpus\kurai\full'
if _PHOTO not in sys.path:
    sys.path.insert(0, _PHOTO)

CRS_VERSION = '18.1'
PROCESS_VERSION = '15.4'


def plan_to_xmp(plan: LRPlan, curve_pts: Optional[List[tuple]] = None,
                point_colors: Optional[List[str]] = None) -> str:
    """LRPlan → XMP 文本。缺的键不写（不写就是不动）。"""
    from lr_xmp import build_xmp                     # 底层实现，单一真源
    scalars = {k: v for k, v in plan.as_xmp_scalars().items() if abs(v or 0) > 1e-9}
    return build_xmp(scalars=scalars, curve=curve_pts, point_colors=point_colors)


def write_tif(src: str, dst: str, xmp_text: str) -> str:
    from lr_xmp import write_tif as _wt
    return _wt(src, dst, xmp_text)


def render(plan: LRPlan, src_tif: str, out_dir: str, tag: str = '') -> str:
    """渲一张：写 XMP → ADD → EXPORTTIF（原生尺寸），返回导出的 TIF 路径。"""
    import lrstate as LS
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(LS.TMP, 'design_%s_%s_%d.tif'
                       % (os.path.basename(src_tif).split('.')[0], tag or 'p',
                          int(time.time() * 1000) % 100000))
    write_tif(src_tif, tmp, plan_to_xmp(plan))
    r = LS.run('ADD ' + tmp, timeout=120)
    if 'ADDED' not in r and 'EXISTS' not in r.upper():
        raise RuntimeError('ADD 失败: %s' % r[:120])
    tif = LS.expo(tmp, 'design_%s' % (tag or 'p'))
    dst = os.path.join(out_dir, '%s_%s.tif' % (os.path.basename(src_tif).split('.')[0], tag or 'p'))
    import shutil
    shutil.copy2(tif, dst)
    return dst


def diagnostic(xmp_text: str) -> Dict[str, object]:
    """自检：三件套齐不齐、PointColors 有没有配 ColorVariance。"""
    out = dict(xpacket='<?xpacket' in xmp_text,
               already_applied='AlreadyApplied="False"' in xmp_text,
               as_shot='WhiteBalance="As Shot"' in xmp_text,
               has_point_colors='<crs:PointColors>' in xmp_text,
               has_color_variance='<crs:ColorVariance>' in xmp_text)
    out['ok'] = (out['xpacket'] and out['already_applied'] and out['as_shot']
                 and (not out['has_point_colors'] or out['has_color_variance']))
    return out
