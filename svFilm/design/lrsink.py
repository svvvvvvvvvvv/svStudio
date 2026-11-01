# -*- coding: utf-8 -*-
"""LR 落盘：**LRPlan → XMP 作业单**（不驱动 LR）+ 产品阶段（驱动 LR 出 ③）。

两条路分开：
  · `write_jobs`  只写 XMP + manifest：可 diff、可回放、不碰 LR；
  · `apply_and_export` 把 XMP 塞进 TIF → ADD → 局部 → 导出 ③。

局部：LR SDK 没有 facialSkin 子类型 ⇒ 用 subject 建组 + **肤色点颜色**工具
把修正收窄到肤色（白衫低饱和，不在肤色窗里，不会被一起抬）；直接用 subject
整身提亮是设计禁止的，这里用肤色窗做交集合。`MASKPARAM` 必须回读校验。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Dict, Optional

from . import lr_xmp
from .calibration import LOCAL_EXPOSURE_PER_EV
from .lr_plan import LRPlan

CRS_VERSION = lr_xmp.CRS_VERSION
PROCESS_VERSION = lr_xmp.PROCESS_VERSION


def plan_to_xmp(plan: LRPlan) -> str:
    """LRPlan → XMP 文本。值为 0 的滑杆不写（不写 = 不动）。

    ★ 不写全局 `PointColors`：肤色点颜色是**局部肤色蒙版**里的色票
      （`POINTSWATCH` 建，isForMasking=true），写进 XMP 会变成全局色票，
      同一剂量落两次。
    """
    scalars = plan.as_xmp_scalars()
    return lr_xmp.build_xmp(scalars=scalars, curve=plan.curve, point_colors=None)


def diagnostic(xmp_text: str) -> Dict[str, object]:
    """自检：三件套 + 曲线 + WB 键在不在。"""
    d = dict(xpacket='<?xpacket' in xmp_text,
             already_applied='AlreadyApplied="False"' in xmp_text,
             has_settings='HasSettings="True"' in xmp_text,
             white_balance=('WhiteBalance="Custom"' if 'WhiteBalance="Custom"' in xmp_text
                            else ('WhiteBalance="As Shot"' if 'WhiteBalance="As Shot"' in xmp_text
                                  else 'missing')),
             has_temperature='crs:Temperature=' in xmp_text,
             tone_curve_custom='ToneCurveName2012="Custom"' in xmp_text,
             has_point_colors='<crs:PointColors>' in xmp_text,
             has_color_variance='<crs:ColorVariance>' in xmp_text)
    d['ok'] = bool(d['xpacket'] and d['already_applied'] and d['has_settings']
                   and d['white_balance'] != 'missing')
    return d


def write_jobs(plans: Dict[str, LRPlan], out_dir: str,
               manifest_extra: Optional[dict] = None) -> Dict[str, object]:
    """一批 LRPlan → 一批 .xmp + manifest.json。**不碰 LR**。"""
    os.makedirs(out_dir, exist_ok=True)
    # 清掉上一轮的 .xmp：留下的旧作业单会被误当成本轮（设计：一次算一份）
    for f in os.listdir(out_dir):
        if f.lower().endswith('.xmp'):
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass
    items = []
    for stem, plan in plans.items():
        xmp = plan_to_xmp(plan)
        diag = diagnostic(xmp)
        path = os.path.join(out_dir, '%s.xmp' % stem)
        with open(path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(xmp)
        items.append(dict(
            stem=stem, xmp=os.path.basename(path), diag=diag,
            wb=dict(decode='as_shot', card_white=plan.wb.card_white,
                    note=plan.wb.note),
            basic=dict(exposure_ev=plan.exposure_ev, contrast=plan.contrast,
                       highlights=plan.highlights, shadows=plan.shadows,
                       whites=plan.whites, blacks=plan.blacks),
            point_color=(dict(h=plan.point_color.h, s=plan.point_color.s,
                              l=plan.point_color.l, range=plan.point_color.range,
                              sat_scale=plan.point_color.sat_scale,
                              raw=plan.point_color.raw)
                         if plan.point_color.is_set() else None),
            split=dict(shadow_hue=plan.split_shadow_hue,
                       shadow_sat=plan.split_shadow_sat,
                       highlight_hue=plan.split_highlight_hue,
                       highlight_sat=plan.split_highlight_sat,
                       balance=plan.split_balance),
            local=dict(mask=plan.local.mask, exposure_ev=plan.local.exposure_ev,
                       shadows=plan.local.shadows, highlights=plan.local.highlights,
                       saturation=plan.local.saturation),
            curve_points=(len(plan.curve) if plan.curve else 0),
            target=plan.target, card_dev=plan.card_dev,
            ledger=list(plan.ledger)))
    manifest = dict(generated_utc=time.strftime('%Y-%m-%d %H:%M:%S'),
                    n=len(items), items=items,
                    caliber=dict(crs_version=CRS_VERSION,
                                 process_version=PROCESS_VERSION,
                                 note='滑杆值 0 不写；WB 卡级（引擎解码层，不在 XMP）；局部=肤色蒙版'))
    if manifest_extra:
        manifest.update(manifest_extra)
    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, default=float)
    return manifest


def summary(manifest: Dict[str, object]) -> str:
    items = manifest.get('items') or []
    bad = [i for i in items if not (i.get('diag') or {}).get('ok')]
    lines = ['XMP 作业单：%d 张 ｜ 自检不过 %d 张' % (len(items), len(bad))]
    wbs = {(i.get('wb') or {}).get('temperature') for i in items}
    wb0 = sorted(w for w in wbs if w)
    lines.append('  WB（卡级）：%s' % (
        '%.0fK（整批一个，引擎解码层）' % wb0[0] if len(wb0) == 1 else
        ('不统一：%s' % wb0 if wb0 else '不动（As Shot）')))
    if manifest.get('print_exposure') is not None:
        lines.append('  PE：%.3f' % float(manifest['print_exposure']))
    ev = [i['basic']['exposure_ev'] for i in items]
    if ev:
        lines.append('  曝光：中位 %+.2f ｜ 范围 %+.2f ~ %+.2f'
                     % (sorted(ev)[len(ev) // 2], min(ev), max(ev)))
    for key, label in (('contrast', '对比'), ('highlights', '高光'),
                       ('shadows', '阴影'), ('whites', '白'), ('blacks', '黑')):
        vals = [i['basic'][key] for i in items if abs(i['basic'].get(key) or 0) > 1e-9]
        if vals:
            lines.append('  %s：%d 张 ｜ 中位 %+.1f ｜ 范围 %+.1f ~ %+.1f'
                         % (label, len(vals), sorted(vals)[len(vals) // 2],
                            min(vals), max(vals)))
    pc = [i for i in items if i.get('point_color')]
    lines.append('  点颜色（肤色蒙版）：%d 张' % len(pc))
    if pc:
        ss = sorted(float((i.get('point_color') or {}).get('sat_scale') or 0.0)
                    for i in pc)
        lines.append('    SatScale 中位 %+.2f ｜ 范围 %+.2f ~ %+.2f'
                     % (ss[len(ss) // 2], ss[0], ss[-1]))
    sp = [i for i in items if abs(i['split']['shadow_sat']) > 1e-9
          or abs(i['split']['highlight_sat']) > 1e-9]
    if sp:
        lines.append('  分色：%d 张' % len(sp))
    loc = [i for i in items if abs((i.get('local') or {}).get('exposure_ev') or 0) > 1e-9]
    lines.append('  局部（肤色蒙版）：%d 张' % len(loc))
    cur = [i for i in items if i.get('curve_points')]
    lines.append('  曲线：%d 张' % len(cur))
    for i in bad[:3]:
        lines.append('  ! %s 自检不过：%s' % (i['stem'], i['diag']))
    lines.append('  ★ 只写了 XMP 与 manifest，**没有驱动 LR**')
    return '\n'.join(lines)


# ---------------------------------------------------------------- 产品阶段（会驱动 LR）

def apply_and_export(pairs, out_dir: str, log=print) -> Dict[str, str]:
    """LRPlan **真的**在 LR 里跑一遍并导出 ③。返回 {stem: ③ 路径}。

    pairs = [(stem, ①的TIF路径, LRPlan)]

    顺序（设计）：XMP（曝光/分色/曲线）→ ADD → subject 组 + 肤色色票
    → 局部曝光（MASKPARAM，回读）→ 导出 ③。WB 在引擎解码层，不在 XMP。
    """
    from . import lr_bridge as B

    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, '_work')
    os.makedirs(work, exist_ok=True)
    out: Dict[str, str] = {}
    for stem, src, plan in pairs:
        xmp = plan_to_xmp(plan)
        stamp = '%d' % (int(time.time() * 1000) % 1000000)
        tmp = os.path.join(work, 'design_%s_%s.tif' % (stem, stamp))
        lr_xmp.write_tif(src, tmp, xmp)
        B.add(tmp)
        time.sleep(2.0)          # 刚导入的 TIF 等 LR 建完预览/选中，再建蒙版

        # ---- 局部：**肤色蒙版** = LR 主体 AI 蒙版 ∩ 肤色点颜色工具。
        #   ★ SDK 只有 aiSelection 的 subject/people/sky/...，**没有 facialSkin
        #     subtype**（`LrDevelopController.createNewMask` 的 maskSubtype 列表里
        #     只有 subject/people/...；实测 people 建不出组，subject 可以）。
        #   所以用 subject 建组，再用 local point-color 色票（肤色色相）把修正
        #   收窄到肤色：白衫低饱和、不在肤色窗里，不会被一起抬（实测 +1EV：
        #   face L +3.9、人 clip 0.16%→0.74%）。这是设计意图（不改背景/白衫）
        #   在当前 SDK 下能做到的最近实现；SDK 若以后给 facialSkin，只换 mask 名。
        need_mask = (plan.point_color.is_set()
                     or plan.local.is_set())
        if need_mask:
            rc = B.run('CLEARMASKS %s' % tmp, timeout=180)
            log('    清残留蒙版：%s' % rc[:120])
            rm = B.run('MASK %s subject' % tmp, timeout=180)
            if 'groups=0->0' in rm or 'tiles=0->0' in rm:
                raise RuntimeError('%s 肤色蒙版没建出来：%s' % (stem, rm[:160]))
            time.sleep(1.0)
        point_ok = False
        if plan.point_color.is_set():
            pc = plan.point_color
            rp = B.point_watch(tmp, pc.h * 2.0 * 3.141592653589793,
                               pc.s, pc.l, pc.range, pc.sat_scale)
            if 'res=true' not in rp:
                # 一次重试：LR UI 刚建完组时偶尔还没选中
                time.sleep(1.0)
                rp = B.point_watch(tmp, pc.h * 2.0 * 3.141592653589793,
                                   pc.s, pc.l, pc.range, pc.sat_scale)
            if 'res=true' not in rp:
                log('    ! 肤色色票失败（H%.3f S%.3f L%.3f R%.3f Sat%+.3f）：%s'
                    % (pc.h, pc.s, pc.l, pc.range, pc.sat_scale, rp[:120]))
            else:
                point_ok = True
                log('    肤色色票（subject ∩ 肤色）H%.3f/S%.3f/L%.3f SatScale%+.2f'
                    % (pc.h, pc.s, pc.l, pc.sat_scale))
        # ---- 局部：只动肤色蒙版里的曝光（必须有肤色窗）
        if plan.local.is_set():
            if not point_ok:
                log('    ! 没有肤色窗（色票未落/无 skin_rgb）⇒ 跳过局部，避免 subject 整身抬')
            else:
                v = float(plan.local.exposure_ev) * LOCAL_EXPOSURE_PER_EV
                if abs(v) > 1e-9:
                    rb = B.mask_param(tmp, 'LocalExposure2012', v)
                    log('    局部曝光 %+.2f 档 ⇒ 蒙版 %.4f（回读 %s）'
                        % (plan.local.exposure_ev, v, rb))
            # ⚠ LocalHighlights2012 的桥回读/导出实测不可用（-1.0 会污染测量），
            #   不启用；靠肤色窗口本身压住白衫。
            for key, ui in (('LocalShadows2012', plan.local.shadows),):
                if abs(float(ui or 0.0)) < 1e-9:
                    continue
                try:
                    B.mask_param(tmp, key, float(ui) / 100.0)
                    log('    %s %+.0f（回读 ok）' % (key, float(ui)))
                except RuntimeError as ex:               # noqa: BLE001
                    log('    ! %s 跳过（桥回读不可用）：%s' % (key, str(ex)[:120]))
        got = B.export_tif(tmp, out_dir, tag=stem)
        dst = os.path.join(out_dir, '%s_final.tif' % stem)
        shutil.copy2(got, dst)
        out[stem] = dst
        log('  ③ %s -> %s' % (stem, os.path.basename(dst)))
    return out
