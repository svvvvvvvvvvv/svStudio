# -*- coding: utf-8 -*-
"""引擎侧：**按卡渲一次 ①**（不匹配、不选卡、不管落点）。

设计（`引擎设计.md` §2/§4.4/§5）：
  · 只渲一次：人选卡之后，用这个目录的介质 + 该目录的 PE 做基准渲 ①；
  · 不再有 ①′ 基线；匹配 A 用机内 JPG（`contentmatch`）；
  · 落点：**auto_exposure=subject，用脸皮肤 mask 测光**；PE 只作基准；
  · 生产默认走 A：**按交付尺寸渲**（长边 1080，与大师同域）。

本模块只剩三件事：渲、报余量、写 `_render.json`（供 --reuse 校验）。
选卡/匹配/交付不在这里。
"""
from __future__ import annotations

import copy
import json
import os
import time
from typing import Dict, Optional

# 余量只有在"上端没被砍"时才是一个可用的数（2026-10-29 实测）：
# `scan_white_level` 是上端硬天花，降它换来的余量是恒等式（砍多少报多少）。
CEILING_MIN_L = 99.5

# 交付域长边（`测量设计.md` §1.1 / `引擎设计.md` §4.4 A）
DELIVERY_LONG_SIDE = 1080

# 渲染并行：路数由**内存**算，不由核数算（2048 长边每路 2.3~3.1 GB）。
ENGINE_PER_JOB_GB = 3.1
ENGINE_MEM_BUDGET_GB = 24.0


# ---------------------------------------------------------------- 余量（产物，不是目标）

def headroom_clip(disp, ev: float = 1.0) -> float:
    """把已渲好的片子按线性域乘 2^ev，量"还会剪多少"。"""
    import numpy as _np
    u = _np.clip(_np.asarray(disp, _np.float64), 0.0, 1.0)
    lin = _np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)
    lin2 = _np.clip(lin * (2.0 ** ev), 0.0, 1.0)
    out = _np.where(lin2 <= 0.0031308, lin2 * 12.92,
                    1.055 * _np.power(lin2, 1.0 / 2.4) - 0.055)
    L2 = _lab_of_disp(out)
    return float((L2 > 99.0).mean() * 100.0)


def headroom_ev(disp, clip_max: float = 0.5, hi: float = 3.0) -> float:
    """这张底片还能抬几档（抬到 clip = clip_max 为止）。"""
    lo = 0.0
    if headroom_clip(disp, hi) <= clip_max:
        return float(hi)
    for _ in range(12):
        mid = 0.5 * (lo + hi)
        if headroom_clip(disp, mid) <= clip_max:
            lo = mid
        else:
            hi = mid
    return round(float(lo), 2)


def ceiling_L(disp) -> float:
    """这张底片的上端天花（全图最高 L*）。"""
    import numpy as _np
    return float(_np.max(_lab_of_disp(disp)))


def _code_to_L(code: float) -> float:
    """sRGB 显示码 → L*。"""
    c = max(0.0, min(1.0, float(code)))
    y = c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 116.0 * (y ** (1.0 / 3.0)) - 16.0


def headroom_report(disp, clip_max: float = 0.5, white_level: float = None,
                    max_px: int = 1_200_000) -> dict:
    """余量 + 可不可信，一起报。

    `scan_white_level` < 1 时图被切在上端那条线，报的余量只是"砍掉的高度"。
    """
    import math as _math
    import numpy as _np
    top = ceiling_L(disp)
    wl_known = white_level is not None
    wl = 1.0 if white_level is None else float(white_level)
    expect = _code_to_L(wl)
    cut = expect < CEILING_MIN_L

    arr = _np.asarray(disp)
    n = int(arr[..., 0].size) if arr.ndim >= 2 else int(arr.size)
    step = 1
    while n // (step * step) > max_px:
        step += 1
    sub = arr[::step, ::step] if arr.ndim >= 2 else arr[::step]
    ev = headroom_ev(sub, clip_max=clip_max)

    y = ((top + 16.0) / 116.0) ** 3 if top > -16.0 else 0.0
    taut = float(-_math.log2(y)) if y > 0 else None
    if cut:
        why = ('白端 scan_white_level=%.2f（期望天花 L*%.1f，实测 L*%.1f）⇒ '
               '天花被那条线限住，报的 %.2f 档最多就是砍掉的高度，不是可用余量'
               % (wl, expect, top, ev))
    elif not wl_known:
        why = '天花 L*%.1f；没传白端值，按 1.0 处理' % top
    else:
        why = '天花 L*%.1f，白端 1.0 ⇒ 余量可信' % top
    return dict(headroom_ev=ev, ceiling_L=round(top, 2), valid=bool(not cut),
                ceiling_expect_L=round(expect, 2), white_level=wl,
                tautology_ev=(round(taut, 3) if taut is not None else None), why=why)


# ---------------------------------------------------------------- 渲一次

def engine_jobs(n_images: int) -> int:
    """能开几路：内存预算是上限，图片数不够就少开。"""
    want = int(os.environ.get('SV_ENGINE_JOBS', '0') or 0)
    if want > 0:
        return max(1, min(want, n_images))
    by_mem = int(ENGINE_MEM_BUDGET_GB // ENGINE_PER_JOB_GB)
    return max(1, min(by_mem, os.cpu_count() or 1, n_images))


def _filters_of(medium: dict):
    sim = medium.get('simulation') or {}
    return float(sim.get('print_y_filter_shift') or 0.0), \
        float(sim.get('print_m_filter_shift') or 0.0)


def render_card(card, raf_paths: Dict[str, str], out_dir: str,
                long_side: int = DELIVERY_LONG_SIDE, log=print,
                targets: Dict[str, float] = None, table: dict = None) -> Dict[str, str]:
    """按 peer target 渲一批：**不读脸**，出 16-bit TIF。

    `targets`：{stem: target_p50}；`table`：色卡标定的曝光表。
    没有 target 时退回卡的 nominal pe（并记账），不建脸 mask。
    """
    from .card_render import medium_of
    from .texture import plan_texture, to_overrides

    os.makedirs(out_dir, exist_ok=True)
    medium0 = medium_of(card)
    media_label = '%s + %s' % (medium0['simulation']['film_stock'],
                              medium0['simulation']['print_paper'])
    try:
        white_level = float(medium0['simulation']['scan_white_level'])
    except Exception:                                      # noqa: BLE001
        white_level = None
    if white_level is not None and white_level < 1.0:
        log('  ! 卡的白端 scan_white_level=%.2f < 1.0：天花会被切在 L*%.1f，'
            '余量判为不可信' % (white_level, _code_to_L(white_level)))
    tex = plan_texture(_stock_of(card), _paper_of(card), long_side=long_side)
    ov = to_overrides(tex, long_side=long_side)
    # ★ 优先卡级 WB（一个目录一个值）；没有才用介质预设。不做逐批反解。
    wbc = (card.get('wb') if isinstance(card, dict)
           else getattr(card, 'wb', None)) or {}
    wbm = dict(medium0.get('wb') or {})
    if wbc.get('temperature') is not None and wbc.get('tint') is not None:
        temp, tint = wbc.get('temperature'), wbc.get('tint')
        why = 'card-wb'
    else:
        temp, tint = wbm.get('temperature'), wbm.get('tint')
        why = 'preset-wb'
    medium = medium0
    if temp is not None and tint is not None:
        wb = {'white_balance': 'custom',
              'temperature': float(temp), 'tint': float(tint)}
        wb_info = dict(calibrated=False, why=why,
                       y=_filters_of(medium0)[0], m=_filters_of(medium0)[1])
        log('  解码 WB：%s custom %.0fK / G×%.3f（整批共用，不逐帧）'
            % ('卡级' if why == 'card-wb' else '预设',
               float(temp), float(tint)))
    else:
        wb = {'white_balance': 'as_shot'}
        wb_info = dict(calibrated=False, why='no wb',
                       y=_filters_of(medium0)[0], m=_filters_of(medium0)[1])
        log('  解码 WB：没有卡/预设 wb ⇒ 相机 as-shot')
    log('  质感 overrides：%d 根（%s）' % (len(ov), '、'.join(sorted(
        k.split('.')[-1] for k in ov))))
    sim = dict(medium.get('simulation') or {})
    pe_nominal = float(sim.get('print_exposure') or 0.987)
    if targets and table is None:
        raise RuntimeError('缺少卡级曝光表（chart_calib.card_table_path）')
    jobs = engine_jobs(len(raf_paths))
    out: Dict[str, str] = {}
    skipped: list = []
    argv = [(stem, raf, medium, out_dir, ov, white_level, long_side, wb,
             (targets or {}).get(stem), table, pe_nominal)
            for stem, raf in raf_paths.items()]
    if jobs > 1:
        log('  并行渲：%d 路（长边 %d）' % (jobs, long_side))
        from multiprocessing import get_context
        with get_context('spawn').Pool(jobs) as pool:
            for stem, dst, hr, msgs in pool.imap_unordered(_render_one, argv):
                if dst is None:
                    skipped.append(stem)
                    for m in msgs:
                        log(m)
                    continue
                out[stem] = dst
                _write_headroom(out_dir, stem, hr)
                for m in msgs:
                    log(m)
    else:
        for a in argv:
            stem, dst, hr, msgs = _render_one(a)
            if dst is None:
                skipped.append(stem)
                for m in msgs:
                    log(m)
                continue
            out[stem] = dst
            _write_headroom(out_dir, stem, hr)
            for m in msgs:
                log(m)
    if skipped:
        raise RuntimeError('PE hit_bound，拒绝出片：%s（表够不到 target；换卡或重建表）'
                           % '、'.join(sorted(skipped)))
    _write_render_meta(out_dir, card, media_label, out, wb, wb_info, medium,
                       targets=targets, table=table)
    return out


def _stock_of(card) -> str:
    return card.stock if hasattr(card, 'stock') else str(card.get('stock') or '')


def _paper_of(card) -> str:
    return card.paper if hasattr(card, 'paper') else str(card.get('paper') or '')


def _render_one(args):
    """渲一张 + 量余量（**必须在模块级**：Windows 的 spawn 不能 pickle 闭包）。"""
    (stem, raf, medium, out_dir, ov, white_level, long_side, wb,
     target, table, pe_nominal) = args
    from svFilm import io, lrbase, presets
    from . import chart_calib
    medium = dict(medium)
    sim = dict(medium.get('simulation') or {})
    sim['auto_exposure'] = False
    sim['auto_exposure_method'] = sim.get('auto_exposure_method') or 'matrix'
    medium['simulation'] = sim
    media_label = '%s + %s' % (sim['film_stock'], sim['print_paper'])
    msgs = ['  渲 %s ← %s（介质 %s，长边 %d）'
            % (stem, os.path.basename(raf), media_label, long_side)]
    lin = io.load_raw(raf, max_side=long_side, wb=wb).lin
    if target is not None:
        sol = chart_calib.solve_pe(lin, float(target), table)
    else:
        sol = dict(pe=float(pe_nominal), p50_pred=None, delta_ev=0.0,
                   hit_bound=False, target=None, pe_nominal=float(pe_nominal),
                   chart_sha=(table or {}).get('chart_sha'))
    if sol.get('hit_bound'):
        msgs.append('  ! PE hit_bound（target %.1f；表边界 PE %.4f；预测 p50 %.1f）'
                    ' ⇒ 不渲这张，避免超出表的假曝光'
                    % (sol.get('target') or -1, sol['pe'],
                       sol.get('p50_pred') if sol.get('p50_pred') is not None else -1))
        return stem, None, {}, msgs
    disp = presets.render(lin, medium, overrides=ov,
                          print_exposure=float(sol['pe']))
    if sol.get('target') is not None:
        msgs.append('  peer target %.1f → PE %.4f（ΔEV %+.2f；预测 p50 %.1f%s）'
                    % (sol['target'], sol['pe'], sol['delta_ev'],
                       sol.get('p50_pred') if sol.get('p50_pred') is not None else -1,
                       '；hit_bound' if sol.get('hit_bound') else ''))
    else:
        msgs.append('  ! 没有 peer target ⇒ 用 nominal PE %.4f；记账' % sol['pe'])
    dst = os.path.join(out_dir, '%s_engine.tif' % stem)
    lrbase.save_tiff16(disp, dst)
    with open(os.path.join(out_dir, '%s_exposure.json' % stem), 'w',
              encoding='utf-8') as fh:
        json.dump(sol, fh, ensure_ascii=False, indent=1)
    hr = headroom_report(disp, white_level=white_level)
    msgs.append('  余量 %s：可抬 %.2f 档（产物，不是目标）'
                % ('可信' if hr['valid'] else '不可信', hr['headroom_ev']))
    return stem, dst, hr, msgs


def _write_headroom(out_dir, stem, hr):
    with open(os.path.join(out_dir, '%s_headroom.json' % stem), 'w',
              encoding='utf-8') as fh:
        json.dump(dict(stem=stem, headroom_ev=hr['headroom_ev'],
                       ceiling_L=hr['ceiling_L'], valid=hr['valid'],
                       ceiling_expect_L=hr['ceiling_expect_L'],
                       white_level=hr['white_level'],
                       tautology_ev=hr['tautology_ev'], why=hr['why'],
                       note='引擎按 peer target 解 PE；只报告余量，不改曝光；脸归 LR'),
                  fh, ensure_ascii=False, indent=1)


def _write_render_meta(out_dir: str, card, media_label: str, out: dict,
                       wb=None, wb_info=None, medium=None,
                       targets=None, table=None) -> str:
    """把"这批 ① 是用什么渲的"写下来（供 --reuse 校验）。"""
    from .card_render import medium_of
    m = medium or medium_of(card)
    sim = m.get('simulation') or {}
    info = dict(wb_info or {})
    ex = {}
    for stem in out:
        ep = os.path.join(out_dir, '%s_exposure.json' % stem)
        if os.path.isfile(ep):
            try:
                ex[stem] = json.load(open(ep, encoding='utf-8'))
            except Exception:                              # noqa: BLE001
                pass
    p = os.path.join(out_dir, '_render.json')
    d = dict(card=getattr(card, 'name', '') if hasattr(card, 'name')
             else str((card or {}).get('name') or ''),
             medium=media_label,
             print_exposure=float(sim.get('print_exposure') or 0.0),
             auto_exposure=False,
             auto_exposure_method='none',
             peer_targets={k: v.get('target') for k, v in ex.items()},
             pe_solved={k: v.get('pe') for k, v in ex.items()},
             delta_ev={k: v.get('delta_ev') for k, v in ex.items()},
             hit_bound={k: bool(v.get('hit_bound')) for k, v in ex.items()},
             chart_sha=(table or {}).get('chart_sha'),
             amount=float((m.get('couplers') or {}).get('amount') or 0.0),
             wb=(wb or {}).get('white_balance', 'as_shot'),
             wb_temperature=float((wb or {}).get('temperature') or 0.0),
             wb_tint=float((wb or {}).get('tint') or 1.0),
             wb_calibrated=bool(info.get('calibrated')),
             print_y_filter_shift=float(sim.get('print_y_filter_shift') or 0.0),
             print_m_filter_shift=float(sim.get('print_m_filter_shift') or 0.0),
             wb_target=info.get('target'),
             wb_base_white=info.get('base_white'),
             corpus=getattr(card, 'corpus', '') if hasattr(card, 'corpus')
             else str((card or {}).get('corpus') or ''),
             n=len(out), stems=sorted(out),
             ts=time.strftime('%Y-%m-%d %H:%M:%S'))
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return p


def _lab_of_disp(disp):
    """显示域 → L*（0..100），与测量层同口径（浮点，不量化）。"""
    import numpy as np
    u = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    lin = np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    d = 6.0 / 29.0
    f = np.where(xyz > d ** 3, np.cbrt(xyz), xyz / (3 * d * d) + 4.0 / 29.0)
    return 116.0 * f[..., 1] - 16.0
