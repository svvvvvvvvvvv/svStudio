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
                       hue_shift=float(plan.point_color.get('shift', 0.0)),
                       # ★ 亮度：一直没接（10-29）。点颜色是**唯一只作用于肤色范围**的亮度旋钮，
                       #   背景不是肤色 ⇒ 不动背景 ⇒ 不会触发"背景亮→收白→压脸"那条链
                       lum_scale=float(plan.point_color.get('lum', 0.0)))


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
                          highlights_note=plan.notes.get('highlights'),
                          highlight_verify=plan.notes.get('highlight_verify'),
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
        lines.append('  ⚠ 曝光欠账 %d 张（现在是引擎的账，不是 LR 截断的）：' % len(ed))
        for i in ed[:3]:
            lines.append('    %s: %s' % (i['stem'], i['exposure_debt']))
    hl = [i for i in items if i.get('highlights')]
    if hl:
        vals = sorted(i['highlights'] for i in hl)
        lines.append('  收白：%d 张 ｜ 起点 %+.0f（对分第一步，不是算出来的幅度）'
                     % (len(hl), vals[len(vals) // 2]))
    debt = [i for i in items if i.get('highlight_debt')]
    if debt:
        lines.append('  ⚠ 收白欠账 %d 张（① 已爆 = 引擎丢的细节 / 到尽头仍不达标）：' % len(debt))
        for i in debt[:3]:
            lines.append('    %s: %s' % (i['stem'], i['highlight_debt'][:80]))
    for i in bad[:3]:
        lines.append('  ! %s 自检不过：%s' % (i['stem'], i['diag']))
    lines.append('  ★ 只写了 XMP 与 manifest，**没有驱动 LR**')
    return '\n'.join(lines)


# ---------------------------------------------------------------- 产品阶段（会驱动 LR）

def apply_and_export(pairs, out_dir: str, log=print) -> Dict[str, str]:
    """把计划**真的**在 LR 里跑一遍并导出 ③ 成片。

    与上面"只写 XMP"分开的原因：
      · 算值阶段不许依赖 LR（公式要能查、要能离线复算）；
      · 但**产品**必须有 ③ 才能验收（肤色 L 分布、背景是否被动、是否仍是人样）。
    这一层是可选的、要显式调用的，不是算值的一部分。

    pairs = [(stem, ①的TIF路径, LRPlan)]
    返回 {stem: 导出的 TIF 路径}
    """
    import os
    import shutil
    import time

    PHOTO = r'E:\ds\photo\_corpus\kurai\full'
    import sys
    if PHOTO not in sys.path:
        sys.path.insert(0, PHOTO)
    import lr_xmp
    import lrstate as LS

    os.makedirs(out_dir, exist_ok=True)
    out = {}
    for stem, src, plan in pairs:
        rec = point_color_record(plan)
        xmp = plan_to_xmp(plan)          # 已含曲线；点颜色单独写进临时副本
        tmp = os.path.join(LS.TMP, 'design_%s_%d.tif'
                           % (stem, int(time.time() * 1000) % 100000))
        lr_xmp.write_tif(src, tmp, xmp)
        r = LS.run('ADD ' + tmp, timeout=180)
        if 'ADD' not in r.upper() and 'EXIST' not in r.upper():
            raise RuntimeError('ADD 失败 %s: %s' % (stem, r[:120]))
        # ★★ 补光：主体蒙版 + `LocalExposure2012 = EV/4`
        #   两个坑（都真踩过）：
        #   ① `MASK ... LocalExposure2012=` 走 LrDevelopController.setValue，命令报 ok、
        #      蒙版也建了，但导出时**一个字都不落**（四档脸 L 全一样）⇒ 必须用 MASKPARAM；
        #   ② MASKPARAM 要**回读**：读回来的值不匹配就当这一步没做，不许静默通过。
        _ev = float(getattr(plan, 'fill_ev', 0.0) or 0.0)
        _sh = float(getattr(plan, 'local_shadows', 0.0) or 0.0)
        _hl = float(getattr(plan, 'local_highlights', 0.0) or 0.0)
        # ★ 2026-10-30：蒙版不再只由 fill_ev 决定 —— LocalShadows 是"主体∩暗部"的
        #   原生等效（实测：脸 +7.1 L/单位，画面 p99 只 +0.1），必须能单独用。
        if max(abs(_ev), abs(_sh), abs(_hl)) > 1e-6:
            rm = LS.run('MASK %s subject' % tmp, timeout=180)
            if 'groups=0->0' in rm:
                raise RuntimeError('%s 主体蒙版没建出来：%s' % (stem, rm[:120]))
            _v = _ev / 4.0
            rp = LS.run('MASKPARAM %s LocalExposure2012 %.6f' % (tmp, _v), timeout=180)
            _rb = rp.split('readback=')[-1].split()[0] if 'readback=' in rp else '0'
            try:
                _okv = abs(float(_rb) - _v) <= 1e-6
            except ValueError:
                _okv = False
            if not _okv:
                raise RuntimeError('%s 补光没落下去（回读 %s ≠ %.4f）：%s'
                                   % (stem, _rb, _v, rp[:120]))
            log('    补光 %+.2f 档 ⇒ 蒙版 %.4f（回读 %s）' % (_ev, _v, _rb))
            # ★ §6E：局部是一整套旋钮 —— 只用曝光会把白裙一起提上去。
            #   Shadows 提暗部（文档保证按亮度选择性）、Highlights 压住人的亮部。
            #   ⚠ 桥的 MASKSX 回读**只列了一部分键**：LocalExposure2012 能读回，
            #     LocalShadows2012 / LocalHighlights2012 回读是 nil。
            #     "报 ok 但读不回来"不许当成功（老账）⇒ 这里**跳过并记账**，
            #     等把这两个键加进桥的回读列表、在 LR 里重载插件之后再启用。
            for _key, _ui in (('LocalShadows2012', float(getattr(plan, 'local_shadows', 0.0) or 0.0)),
                              ('LocalSaturation', float(getattr(plan, 'local_saturation', 0.0) or 0.0)),
                              ('LocalToningHue', float(getattr(plan, 'local_toning_hue', 0.0) or 0.0)),
                              ('LocalToningSaturation', float(getattr(plan, 'local_toning_sat', 0.0) or 0.0)),
                              ('LocalHighlights2012', float(getattr(plan, 'local_highlights', 0.0) or 0.0))):
                if abs(_ui) < 1e-6:
                    continue
                _x = _ui / 100.0           # §6C：其余滑杆 = UI 值 / 100（XMP 刻度）
                rr = LS.run('MASKPARAM %s %s %.4f' % (tmp, _key, _x), timeout=180)
                _b = rr.split('readback=')[-1].split()[0] if 'readback=' in rr else 'nil'
                try:
                    _ok2 = abs(float(_b) - _x) <= 1e-6
                except ValueError:
                    _ok2 = False
                if not _ok2:
                    log('    ! %s 跳过（桥回读 %s，未验证）' % (_key, _b))
                    continue
                log('    %s %+.0f（XMP %.3f，回读 %s）'
                    % (_key.replace('Local', '').replace('2012', ''), _ui, _x, _b))
        got = LS.expo(tmp, 'design_%s' % stem)
        dst = os.path.join(out_dir, '%s_final.tif' % stem)
        shutil.copy2(got, dst)
        out[stem] = dst
        log('  ③ %s -> %s' % (stem, os.path.basename(dst)))
    return out
