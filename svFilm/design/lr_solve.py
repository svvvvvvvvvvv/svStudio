# -*- coding: utf-8 -*-
"""LR 侧求解：只用**卡的点值**做目标（不再用带/带中点）。

    from design.lr_solve import points_of, plan_batch
    card = <cards_v5.json 里的一张>
    plans = plan_batch(recs_1, card)      # recs_1 = ① 的测量 {stem: rec}

三件事：
  1. `points_of(card)` —— 从卡取点值（p50 / face_L / face_C / face_h / shape …）
  2. `plan_batch` —— 逐图：曲线（点值形状）→ placement（点值 p50 / face）→ 白端一档
  3. `summary` —— 人读的一行一图

★ 没有点值就不动、记账；**不退回带中点/带边**。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional
import colorsys

from .curve_design import apply_lut, curve_from_quantiles
from .invariants import T1
from .photometry import ev_for_l, l_after_ev
from .solver import Plan, local_exposure_for

# ★ 2026-10-31：**所有阈值改从卡的点值来**（SV：求解必须照着匹配卡）。
#   原来的 P95_MAX=93 / CLIP_MAX=0.5 是自造常数，已删；现在用 `points_of(card)`
#   的点值，不再从带的上下沿/中点取。
# ★ 2026-10-30 第二次收：`PHYS_P95=96` / `PHYS_CLIP=1.0` 也不是物理 ——
#   它们是上一版自己写的"保守上界"。真正的物理线只有两条：
#     · 白平台 L*=100（像素不可能更亮）；
#     · 爆白门用 T1（`invariants.T1['clip_max']`，一处真源）。
PHYS_P95 = 100.0
PHYS_CLIP = T1['clip_max']
# 一次收白最多压几档：政策（超过这个量级就不是单张图的问题，先查底片/场景）
MAX_WHITE_PULL_EV = 1.5
# ★ 2026-10-31：**不要用绝对斜率/连续反解**（C/UI 随图变）。肤色饱和只给一档；
#   没有公开传递函数的旋钮一律方向 + 一档 + ③ 验。
ONE_STEP_UI = 10.0
C_BAND_TOL = 1.0
H_BAND_TOL = 2.0           # 色相死区（Lab 度）
L_BAND_TOL = 1.5           # 明度死区（L*）
HUE_STEP_UI = 5.0          # 肤色色相半档（一档 10 UI ≈ 15~20°，太大）
# ★ 2026-11-01：Point Color 的动态饱和上限。关系差超过它，不再让 LR 硬补，
#   余量归引擎/卡。0.2 = 两档（一档 0.1 = 10 UI）。
POINT_COLOR_CAP = 0.2
POINT_COLOR_RANGE = 0.5


def points_of(card: dict) -> Dict[str, Optional[float]]:
    """卡 → **点值**（求解唯一允许的目标形态）。

    2026-10-31：不再从 `bands` 取中点/带边；没有 `points` 就返回 None，
    该步不动并记账（不许偷偷退回带中点）。
    """
    p = (card or {}).get('points') or {}
    keys = ('p50', 'p95', 'p5', 'contrast', 'clip', 'sky',
            'face_L', 'face_C', 'face_h',
            'toe', 'shoulder', 'contrast_win', 'skew',
            'face_over_bg', 'person_over_bg', 'warm_cool_b', 'light_ratio')
    return {k: (float(p[k]) if isinstance(p.get(k), (int, float)) else None)
            for k in keys}


# 分离色调的色相轴（UI 约定，不是标定值）：暖端 45°、冷端 210°。
SPLIT_WARM_HUE = 45.0
SPLIT_COOL_HUE = 210.0


def grading_of(card: dict, rec: dict) -> Dict[str, float]:
    """⑧ 彩色：**能算的算，不能算的只给方向 + 一档**（§47 / §6D）。

    2026-10-31：
      · 目标只用**卡的点值**（不是带中点/带边）；没有点值就不动、只记账。
      · 肤色饱和只降不升；色相半档；冷暖按卡的分色方向 + 一档。
      · 没有公开传递函数的旋钮 ⇒ 一档 10 UI，③ 验；不再用 1.03%/UI 连续反解。
    """
    pts = points_of(card)
    out = dict(saturation=0.0, vibrance=0.0,
               local_saturation=0.0, local_vibrance=0.0,
               local_toning_hue=0.0, local_toning_sat=0.0,
               point_color_h=0.0, point_color_s=0.0, point_color_l=0.0,
               point_color_range=0.0, point_color_sat_scale=0.0,
               point_color_sat_raw=0.0,
               split_highlight_hue=0.0, split_highlight_sat=0.0,
               split_shadow_hue=0.0, split_shadow_sat=0.0, split_balance=0.0)
    notes = []
    one = ONE_STEP_UI

    # ★ 2026-11-01：肤色走「主体蒙版 + Point Color」。
    #   不用绝对 C 靶；用 C/L 关系：
    #     r0 = C0/L0（这张 ① 的关系）
    #     r* = 卡 face_C/face_L（卡的关系，不是 C 点值本身）
    #     e = r*/r0 - 1；SatScale 是相对饱和刻度，e 就是动态量。
    #   上限 ±0.2；超过就报「LR 局部不够，余量归引擎/卡」，不硬补。
    cv = (rec.get('skin') or {}).get('C', rec.get('face_C'))
    lv = (rec.get('skin') or {}).get('L', rec.get('face_L'))
    tC = pts.get('face_C')
    tL = pts.get('face_L')
    rgbv = rec.get('skin_rgb')
    if (cv is not None and lv is not None and float(lv) > 1e-3
            and isinstance(tC, (int, float)) and isinstance(tL, (int, float))
            and float(tL) > 1e-3 and rgbv and len(rgbv) == 3):
        r0 = float(cv) / float(lv)
        rt = float(tC) / float(tL)
        e = rt / r0 - 1.0
        s = max(-POINT_COLOR_CAP, min(POINT_COLOR_CAP, e))
        try:
            h, l, sat = colorsys.rgb_to_hls(*(float(v) / 255.0 for v in rgbv))
            out['point_color_h'] = round(h * 6.0, 4)
            out['point_color_s'] = round(sat, 4)
            out['point_color_l'] = round(l, 4)
            out['point_color_range'] = POINT_COLOR_RANGE
            out['point_color_sat_raw'] = round(e, 3)
            out['point_color_sat_scale'] = round(s, 3)
            notes.append('肤色 C/L %.3f → 卡 %.3f（差 %+.1f%%）⇒ Point Color SatScale %+.2f'
                         % (r0, rt, e * 100.0, s))
            if abs(e) > POINT_COLOR_CAP + 1e-9:
                notes.append('关系差超过 ±%.1f ⇒ 只给边界值，余量归引擎/卡' % POINT_COLOR_CAP)
        except (TypeError, ValueError):
            notes.append('skin_rgb 无法转 HSL ⇒ Point Color 不动')
    else:
        notes.append('缺 C/L 关系或 skin_rgb ⇒ Point Color 不动（记账）')

    # 分色：卡的暗部/高光色偏（`design.card_tone` 从它自己的成片测出）
    tone = (card or {}).get('tone') or {}
    sh = ((tone.get('shadow') or {}) or {}).get('hsl_h')
    hi = ((tone.get('highlight') or {}) or {}).get('hsl_h')
    if sh is not None or hi is not None:
        if sh is not None:
            out['split_shadow_hue'] = round(float(sh), 1)
            out['split_shadow_sat'] = one
        if hi is not None:
            out['split_highlight_hue'] = round(float(hi), 1)
            out['split_highlight_sat'] = one
        notes.append('卡的分色：暗部 %s° / 高光 %s°（各 %g 点；来自卡的成片实测）'
                     % ('—' if sh is None else '%.0f' % sh,
                        '—' if hi is None else '%.0f' % hi, one))
    else:
        # 没测过这张卡 ⇒ 按卡的点值给方向 + 一档
        wc = pts.get('warm_cool_b')
        cur = rec.get('warm_cool_b')
        if wc is not None and cur is not None:
            d = float(wc) - float(cur)
            if abs(d) > 0.05:
                warm = d > 0
                out['split_highlight_hue'] = SPLIT_WARM_HUE if warm else SPLIT_COOL_HUE
                out['split_shadow_hue'] = SPLIT_COOL_HUE if warm else SPLIT_WARM_HUE
                out['split_highlight_sat'] = one
                out['split_shadow_sat'] = one
                notes.append('暖冷 %.2f → 卡的点值 %.2f ⇒ 分离色调 %g 点（方向 + 一档）'
                             % (float(cur), float(wc), one))
            else:
                notes.append('暖冷 %.2f ≈ 卡的点值 %.2f ⇒ 不动' % (float(cur), float(wc)))
    out['note'] = '；'.join(notes)
    return out


def _meas_of(rec: dict) -> Dict[str, object]:
    """① 的测录 → plan 需要的形状（v7 扁平 / 旧嵌套都吃）。

    ★ 2026-10-30：同时吃**旧测量**（`skin/bg/person` 嵌套）与 **v7 测量**
    （`face_L / person_L / bg_L_p90` 扁平；`m_*_v7.json`）。v7 没有 `shape`，
    就从它自己的 `img` 分位算（toe=p25−p5 等），口径与旧版一致。
    """
    im = rec.get('img') or {}
    sh = rec.get('shape') or {}
    sk = rec.get('skin') or {}
    bgr = rec.get('bg') or {}
    per = rec.get('person') or {}
    face_L = sk.get('L', rec.get('face_L'))
    face_C = sk.get('C', rec.get('face_C'))
    face_h = sk.get('h', rec.get('face_h'))
    person_L = per.get('L', rec.get('person_L'))
    # 白端预算优先用**人**的 p99；v7 叫 person_p99/face_p99，旧版叫 person.clip_p95
    p99 = (per.get('clip_p95') or rec.get('person_p99')
           or rec.get('face_p99') or rec.get('p99'))
    bg_hi = bgr.get('L_p90', rec.get('bg_L_p90'))
    bg_med = bgr.get('L', rec.get('bg_L'))
    if not sh and im:
        p5, p25, p50, p75, p95 = (im.get('p5'), im.get('p25'), im.get('p50'),
                                  im.get('p75'), im.get('p95'))
        if None not in (p5, p25, p50, p75, p95):
            span = float(p95) - float(p5)
            sh = dict(toe=float(p25) - float(p5),
                      shoulder=float(p95) - float(p75),
                      contrast_win=float(p75) - float(p25),
                      skew=((float(p50) - float(p5)) / span) if span > 1e-6 else None)
    return dict(bg_hi=bg_hi, bg_med=bg_med, person_L=person_L, p99=p99,
                p50=im.get('p50'), p95=im.get('p95'), p5=im.get('p5'),
                face_L=face_L, face_C=face_C, face_h=face_h,
                shape=dict(toe=sh.get('toe'), shoulder=sh.get('shoulder'),
                           contrast_win=sh.get('contrast_win'), skew=sh.get('skew')))


def white_end_plan(rec: dict, card: Optional[dict] = None,
                   curve=None, ev: float = 0.0) -> Dict[str, object]:
    """③ 白端：判据只看**人身上的爆白**（人像优先级最高）。

    2026-10-30 定：整幅/天空的 clip 是**线索**，不是门。
    实测 919 那批：1231 整幅 clip 5.29% 但人 0.00%（全是天空）；
    1526 整幅 1.58% 而人 **9.53%**（白裙爆了）—— 该压的正是后者。

    Whites 半可算（无公开传递函数）⇒ 只给**方向与档级**，出片后一次验（§52.1）。
    """
    im = rec.get('img') or {}
    per = rec.get('person') or {}
    bgr = rec.get('bg') or {}
    p95 = im.get('p95')
    p_clip = per.get('clip_pct')
    b_clip = bgr.get('clip_pct')
    # ★ 门 = 卡的点值（肩部），不是带边、不是我的常数；没有点值才退回白平台。
    pts = points_of(card)
    p95_gate = float(pts['p95']) if pts.get('p95') is not None else PHYS_P95
    clip_gate = PHYS_CLIP
    # ★ ③ 的判据是**曝光 + 曲线之后**的残差（§6B.2）：
    #   上一版只看曲线、不看已经给的全局曝光 ⇒ 曝光已经压下去了还报"要收白"。
    from .curve_design import apply_lut
    p95_ev = l_after_ev(float(p95), float(ev)) if p95 is not None else None
    p95_res = apply_lut(curve, float(p95_ev)) if (curve and p95_ev is not None) else p95_ev
    need = None
    if p95_res is not None and p95_res > p95_gate:
        need = 'down'
    if p_clip is not None and p_clip >= clip_gate:
        need = 'down'
    # ★ 2026-10-31 减法：Whites 没有公开传递函数，**不做模型反解**。
    #   方向 + 一档（−10 UI），③ 验了再说；残余欠账写进 note。
    whites_ui = 0.0
    note = ''
    if need == 'down' and p95_res is not None:
        whites_ui = -ONE_STEP_UI
        note = ('方向 + 一档 %.0f UI（残差 %.1f L*）；没有公开传递函数，不反解，③ 验'
                % (whites_ui, float(p95_res) - float(p95_gate)))
    return dict(need=need, amount=round(whites_ui, 1), whites_ui=round(whites_ui, 1),
                amount_basis='one_step', amount_note=note,
                p95=p95, p95_after_ev=p95_ev, p95_after_curve=p95_res,
                clip=p_clip, person_clip=p_clip,
                bg_clip=b_clip, frame_clip=im.get('clip_pct'),
                gate=dict(p95_max=round(p95_gate, 1), person_clip_max=round(clip_gate, 2),
                          from_card=pts.get('p95') is not None))


def plan_batch(recs: Dict[str, dict], card: dict,
               batch: Optional[dict] = None) -> Dict[str, dict]:
    """逐图出计划：曝光（整幅）· 曲线（形状）· 局部（人）· 白端（③）。

    `batch` = `batch_grade.build(recs, card)`：A 段给**全批同一条曲线**；
    颜色在 B 段逐图（`grading_of`），不用批级颜色。
    目标只用**卡的点值**（`points_of`）；没有点值就不动、记账，不退回带中点。
    """
    pts = points_of(card)
    _shape = {k: pts[k] for k in ('toe', 'shoulder', 'contrast_win', 'skew')
              if pts.get(k) is not None}
    _cap = float(pts['p95']) if pts.get('p95') is not None else PHYS_P95
    face_rel = pts.get('face_over_bg')
    out: Dict[str, dict] = {}

    def _curve_for(rec):
        q = rec.get('img') or {}
        return curve_from_quantiles(q, _shape, p95_cap=_cap)

    for stem, rec in recs.items():
        meas = _meas_of(rec)
        # A 段：给了 batch 就用全批那一条曲线；逐图曲线只作诊断兜底。
        curve = (batch.get('curve') if (batch and batch.get('curve'))
                 else _curve_for(rec))
        if curve:
            for _k in ('face_L', 'person_L'):
                if meas.get(_k) is not None:
                    meas[_k] = apply_lut(curve, float(meas[_k]))
            if meas.get('p99') is not None:
                meas['p99'] = apply_lut(curve, float(meas['p99']))

        # ★ 每张都算：把 p50 放到介质的目标密度（精确闭式），再用主体 p99 封顶。
        from .placement import ev_to_place, ev_cap_by_p99, density_of_l, l_of_density
        _tgt = (card or {}).get('target') or {}
        _D50 = _tgt.get('p50_target_D')
        _ev_place = 0.0
        if _D50 is not None and meas.get('p50') is not None:
            _ev_place = ev_to_place(float(meas['p50']), float(_D50))
            _ev_place = ev_cap_by_p99(_ev_place, meas.get('p99'))
            for _k in ('face_L', 'person_L', 'p99', 'p50', 'p95'):
                if meas.get(_k) is not None:
                    meas[_k] = l_after_ev(float(meas[_k]), _ev_place)
        _ev_white = _ev_place
        # ★ 白端：落点之后仍超卡的点值（肩）⇒ 再压；只为**人**服务。
        _p95_res = meas.get('p99') or meas.get('p95')
        if _p95_res is not None and float(_p95_res) > _cap:
            _ev_white += ev_for_l(float(_p95_res), float(_cap))
            _ev_white = max(_ev_white, -MAX_WHITE_PULL_EV)
            for _k in ('face_L', 'person_L', 'p99'):
                if meas.get(_k) is not None:
                    meas[_k] = l_after_ev(float(meas[_k]), _ev_white - _ev_place)

        p = Plan()
        _face = meas.get('face_L')
        _bghi = (rec.get('bg') or {}).get('L_p90')
        if _bghi is not None and curve:
            _bghi = apply_lut(curve, float(_bghi))
        _p99 = meas.get('p99')
        _tf = (_tgt.get('face') or {}).get('L')
        _res = dict(target=None, ev=0.0, capped_by=None)
        if _face is not None and isinstance(_tf, (int, float)):
            _Lt = float(_tf)
            _raw = ev_to_place(float(_face), density_of_l(_Lt))
            _ev_face = ev_cap_by_p99(_raw, _p99)
            _res = dict(target=_Lt, ev=_ev_face,
                        capped_by=('p99' if _ev_face < _raw else None))
        elif _face is not None and face_rel is not None and _bghi:
            _Lt = float(face_rel) * float(_bghi)
            _raw = ev_to_place(float(_face), density_of_l(_Lt))
            _ev_face = ev_cap_by_p99(_raw, _p99)
            _res = dict(target=_Lt, ev=_ev_face,
                        capped_by=('p99' if _ev_face < _raw else None))

        p.exposure_ev = round(float(_ev_white), 3)
        p.local_ev = round(float(_res['ev']), 3)
        p.local_exposure = round(local_exposure_for(p.local_ev), 5)
        p.light_weight = 1.0
        p.local_highlights = 0.0
        if _res.get('target') is not None and _face is not None:
            p.pred_face = round(l_after_ev(float(_face), float(_res['ev'])), 2)
            p.ledger.append('face_L %.1f ｜ 点目标 %.1f ｜ EV %+.3f%s'
                            % (_face, _res['target'], _res['ev'],
                               '（白端截断）' if _res.get('capped_by') else ''))
        else:
            p.ledger.append('没有脸的点目标 ⇒ 局部不动（记账，不退回带中点）')
        if _p99 is None and p.local_ev > 0:
            p.ledger.append('没人 p99 读数 ⇒ 局部提亮不设白端上限（记账，③ 出片必须验人 clip）')
        meas['face_target'] = _res.get('target')

        out[stem] = dict(plan=dict(p.as_dict(),
                                  light_weight=round(float(getattr(p, 'light_weight', 1.0)), 2),
                                  fit_dist=getattr(p, 'fit_dist', None),
                                  pred_face=getattr(p, 'pred_face', None)),
                         white=white_end_plan(rec, card, curve, ev=float(_ev_white)),
                         meas=meas, ledger=[str(e) for e in p.ledger],
                         curve=curve,
                         grading=grading_of(card, rec))
    return out



def summary(plans: Dict[str, dict], card_name: str = '') -> str:
    lines = ['【LR 计划】卡 %s' % card_name]
    lines.append('%-10s %8s %8s  %-22s %-10s %s' % ('stem', '曝光EV', '局部EV', '曲线', '白端', '人clip/天空clip'))
    for stem, p in sorted(plans.items()):
        pl = p['plan']
        w = p['white']
        lines.append('%-10s %+8.2f %+8.2f  %-22s %-10s %.2f%% / %.2f%% (p95 %.1f)'
                     % (stem, pl['exposure_ev'], pl['local_ev'],
                        ','.join('%s%+.1f' % (k, v) for k, v in (p.get('curve') or {}).items()) or '—',
                        w['need'] or '不动', w.get('person_clip') or 0, w.get('bg_clip') or 0,
                        w['p95'] or 0))
    return '\n'.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--recs', required=True)
    ap.add_argument('--card', default='004_鹿井小渔_n6ab1e755')
    # 2026-10-30：匹配层已改读 v5（sky/person 齐），诊断入口跟同一份。
    ap.add_argument('--cards', default=r'E:\svStudio\svFilm\analysis\corpus\kurai\cards_v5.json')
    a = ap.parse_args(argv)
    d = json.load(open(a.cards, encoding='utf-8'))
    card = next((c for c in d['cards'] if c['name'] == a.card), None)
    if card is None:
        print('没有这张卡：%s' % a.card)
        return 2
    recs = json.load(open(a.recs, encoding='utf-8'))['per_image']
    if isinstance(recs, list):
        recs = {os.path.basename(r['file']).replace('_engine.tif', '').replace('.tif', ''): r
                for r in recs}
    print(summary(plan_batch(recs, card), a.card))
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())


def refine(plans: Dict[str, dict], meas3: Dict[str, dict], card: dict) -> Dict[str, dict]:
    """**一次验后修正**：用实测 ③ 的残差，重算第二轮的量（只走一档，不反解）。

    2026-10-31：目标只用卡的点值；局部饱和只降不升；`local_exposure` 用
    `local_exposure_for`（EV/4 的定义式）。
    """
    pts = points_of(card)
    cap = float(pts['p95']) if pts.get('p95') is not None else PHYS_P95
    target_C = pts.get('face_C')
    out: Dict[str, dict] = {}
    for stem, p in plans.items():
        m3 = meas3.get(stem) or {}
        sk = m3.get('skin') or {}
        per = m3.get('person') or {}
        cur = dict(p.get('plan') or {})
        g = dict(p.get('grading') or {})
        face = sk.get('L')
        C = sk.get('C')
        p99 = per.get('clip_p95')
        dL = 0.0
        fb = (p.get('meas') or {}).get('face_target')
        if face is not None and isinstance(fb, (int, float)):
            dL = float(fb) - float(face)
        add_fill = 0.0
        if abs(dL) > L_BAND_TOL:
            add_fill = ev_for_l(float(face or 50.0), float(face or 50.0) + float(dL))
            if p99 is not None and add_fill > 0.0:
                ev_cap = max(0.0, ev_for_l(float(p99), float(cap)))
                add_fill = min(add_fill, ev_cap)
        add_sat = 0.0
        if (C is not None and isinstance(target_C, (int, float))
                and float(C) > float(target_C) + C_BAND_TOL):
            add_sat = -ONE_STEP_UI
        cur['local_ev'] = round(float(cur.get('local_ev') or 0.0) + add_fill, 3)
        cur['local_exposure'] = round(local_exposure_for(cur['local_ev']), 5)
        g['local_saturation'] = round(float(g.get('local_saturation') or 0.0) + add_sat, 1)
        out[stem] = dict(p, plan=cur, grading=g,
                         refine=dict(dL=round(dL, 1), add_fill=round(add_fill, 2),
                                     add_sat=round(add_sat, 1)))
    return out
