# -*- coding: utf-8 -*-
"""LR 执行端（sink）：把 `LRPlan` 落成 **XMP 作业单**。**不驱动 LR**。

SV 定的规矩（2026-10-29）：不要用 LR 跑；数值与公式去查资料。
所以这里只做文件，不做桥：
    LRPlan  →  XMP 文本  →  写到 out_dir/<stem>.xmp  +  manifest.json
之后要不要导、什么时候导，是人决定的事。

为什么走 XMP（10-28 实测）：
  · 桥逐键写有三个治不好的病：键名靠猜（写错静默丢弃）、结构化键写不进、状态只活在会话里。
  · XMP 是一份文件：可 diff、可回放、键名就是规范名。
  · 三条硬约束缺一条 LR 一个数都不读：
        `<?xpacket?>` 包头 · `AlreadyApplied="False"` · `WhiteBalance="As Shot"`
  · `PointColors` **必须配 `ColorVariance`**，且 `SrcHue ≤ 5.95`（弧度制 0~6）。

用法：
    from design.lrsink import plan_to_xmp, write_jobs
    xmp = plan_to_xmp(plan)
    write_jobs({'DSCF1289': plan}, '%TEMP%/out')
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

from .lr import LRPlan

# 底层实现（已经验证过的那份 XMP 构造），单一真源
_PHOTO = r'E:\ds\photo\_corpus\kurai\full'
if _PHOTO not in sys.path:
    sys.path.insert(0, _PHOTO)

CRS_VERSION = '18.1'
PROCESS_VERSION = '15.4'


def point_color_record(plan: LRPlan) -> Optional[str]:
    """从剂量 + ②处取样色造 19 数字记录（复用已验证的构造函数）。

    为什么用 `point_color(rgb, ...)` 而不是 Lab→RGB：
      10-28 栽过——用 Lab 中心色反推出来的 HSL 色相是错的（0~360 与 0~6 弧度还会混）。
      这里直接喂**真实取样像素**（已过曲线 LUT），SrcHue 由它的 HSL 算。
    """
    if not plan.point_color or not plan.point_color_rgb:
        return None
    from lr_xmp import point_color
    return point_color(plan.point_color_rgb,
                       sat_scale=float(plan.point_color.get('scale', 0.0)),
                       hue_shift=float(plan.point_color.get('shift', 0.0)))


def plan_to_xmp(plan: LRPlan, point_colors: Optional[List[str]] = None) -> str:
    """LRPlan → XMP 文本。值为 0 的滑杆不写（不写 = 不动）。"""
    from lr_xmp import build_xmp
    scalars = {k: v for k, v in plan.as_xmp_scalars().items() if abs(v or 0) > 1e-9}
    if point_colors is None:
        rec = point_color_record(plan)
        point_colors = [rec] if rec else None
    return build_xmp(scalars=scalars, curve=plan.curve, point_colors=point_colors)


def diagnostic(xmp_text: str) -> Dict[str, object]:
    """自检：三件套齐不齐、点颜色有没有配 ColorVariance。"""
    d = dict(xpacket='<?xpacket' in xmp_text,
             already_applied='AlreadyApplied="False"' in xmp_text,
             as_shot='WhiteBalance="As Shot"' in xmp_text,
             has_point_colors='<crs:PointColors>' in xmp_text,
             has_color_variance='<crs:ColorVariance>' in xmp_text,
             tone_curve_custom='ToneCurveName2012="Custom"' in xmp_text)
    d['ok'] = (d['xpacket'] and d['already_applied'] and d['as_shot']
               and (not d['has_point_colors'] or d['has_color_variance']))
    return d


def write_jobs(plans: Dict[str, LRPlan], out_dir: str,
               point_colors: Optional[Dict[str, List[str]]] = None,
               manifest_extra: Optional[dict] = None) -> Dict[str, object]:
    """一批 LRPlan → 一批 .xmp + manifest.json。**不碰 LR**。

    plans = {stem: LRPlan}
    返回 manifest（也落在 out_dir/manifest.json）
    """
    os.makedirs(out_dir, exist_ok=True)
    items = []
    for stem, plan in plans.items():
        pcs = (point_colors or {}).get(stem)
        xmp = plan_to_xmp(plan, point_colors=pcs)
        diag = diagnostic(xmp)
        path = os.path.join(out_dir, '%s.xmp' % stem)
        with open(path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(xmp)
        items.append(dict(stem=stem, xmp=os.path.basename(path),
                          exposure_ev=plan.exposure_ev,
                          highlights=plan.highlights,
                          saturation=plan.saturation,
                          curve_points=(len(plan.curve) if plan.curve else 0),
                          lookat=plan.notes.get('lookat'),
                          curve_note=plan.notes.get('curve'),
                          highlight_debt=plan.notes.get('highlight_debt'),
                          exposure_debt=plan.notes.get('exposure_debt'),
                          point_color=plan.point_color,
                          point_color_rgb=plan.point_color_rgb,
                          diag=diag))
    manifest = dict(generated_utc=None, n=len(items), items=items,
                    caliber=dict(crs_version=CRS_VERSION,
                                 process_version=PROCESS_VERSION,
                                 note='Exposure 单位=档（Adobe 文档）；滑杆值 0 不写'))
    if manifest_extra:
        manifest.update(manifest_extra)
    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, default=float)
    return manifest


def summary(manifest: Dict[str, object]) -> str:
    items = manifest.get('items') or []
    bad = [i for i in items if not (i.get('diag') or {}).get('ok')]
    ev = [i['exposure_ev'] for i in items if i.get('exposure_ev') is not None]
    lines = ['XMP 作业单：%d 张 ｜ 自检不过 %d 张' % (len(items), len(bad))]
    if ev:
        lines.append('  曝光：中位 %+.2f 档 ｜ 范围 %+.2f ~ %+.2f'
                     % (sorted(ev)[len(ev) // 2], min(ev), max(ev)))
    with_curve = [i for i in items if i.get('curve_points')]
    lines.append('  带曲线：%d 张 ｜ 带点颜色：%d 张'
                 % (len(with_curve), sum(1 for i in items
                                         if (i.get('diag') or {}).get('has_point_colors'))))
    ed = [i for i in items if i.get('exposure_debt')]
    if ed:
        lines.append('  ⚠ 曝光被余量截断 %d 张：' % len(ed))
        for i in ed[:3]:
            lines.append('    %s: %s' % (i['stem'], i['exposure_debt']))
    debt = [i for i in items if i.get('highlight_debt')]
    if debt:
        lines.append('  ⚠ 引擎欠账 %d 张（高光余量不足，LR 只在政策值上兜一下）：' % len(debt))
        for i in debt[:3]:
            lines.append('    %s: %s' % (i['stem'], i['highlight_debt']))
    for i in bad[:3]:
        lines.append('  ! %s 自检不过：%s' % (i['stem'], i['diag']))
    lines.append('  ★ 只写了 XMP 与 manifest，**没有驱动 LR**')
    return '\n'.join(lines)
