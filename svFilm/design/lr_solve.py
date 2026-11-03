# -*- coding: utf-8 -*-
"""LR-B 图级求解：**WB → 曝光/明暗 → 形状 → 颜色/肤色 → 局部**。

设计（`LR设计.md`）：
  · LR 补四项残差：曝光/明暗、形状、肤色、颜色；
  · 目标顺序：peer（构图 + 连续光向量）→ hero（peer 不足）→ 卡；
  · 人/脸高光是硬底线；背景允许过曝，只记账；
  · Basic 四根默认 0（通用修图，不是卡对齐）；曲线只套肩趾比例；
  · Point Color 只动肤色：SatScale 按 C/L 关系给量；
  · 局部只走肤色窗，余量用 `face_p99`，只抬不压。

本模块只算，不写 XMP、不碰 LR。
"""
from __future__ import annotations

import colorsys
import math
import statistics as st
from typing import Dict, List, Optional, Tuple

from . import lr_response as R
from .curve_design import apply_lut, curve_from_quantiles
from .lr_plan import LRPlan, Local, PointColor, WhiteBalance
from .photometry import ev_for_l, l_after_ev

# ---- 物理底线 / 工程护栏（都不是风格目标）
PERSON_CLIP_MAX = 1.0          # 人身上可见死白 ≥1% ⇒ 明暗停住
PERSON_CEIL_L = 98.0           # 人/脸最亮处还留细节的物理上限
FACE_CEIL_L = 98.0             # 局部补脸的物理上限
WHITE_OVER_L = 97.5            # 白裙开始压的物理阈值
WHITE_MIN_SHARE = 0.005        # 白裙至少占人的 0.5% 才动
LOCAL_FACE_MAX_EV = 0.7        # 局部一次最多补多少（工程护栏，不是目标）
MAX_CURVE_SLOPE = 3.0          # 曲线局部斜率上限（code/code）；超过就不是补形状
POINT_SAT_CAP = 0.35           # Point Color SatScale 上限
POINT_HUE_CAP = 0.20           # Point Color HueShift 上限
POINT_LUM_CAP = 0.35           # Point Color LumScale 上限
SPLIT_UI_PER_C = 3.0
SPLIT_UI_MAX = 30.0
SPLIT_C_MAX = 1.5
SPLIT_HUE_WARM = (20.0, 70.0)
SPLIT_HUE_COOL = (200.0, 260.0)


# ---------------------------------------------------------------- 小工具

def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _med(xs) -> Optional[float]:
    v = sorted(float(x) for x in xs if isinstance(x, (int, float)))
    if not v:
        return None
    return float(st.median(v))


def _card_points(card: dict) -> Dict[str, Optional[float]]:
    p = (card or {}).get('points') or {}
    keys = ('p5', 'p25', 'p50', 'p75', 'p95',
            'face_L', 'face_C', 'face_h', 'toe', 'shoulder',
            'contrast', 'contrast_win', 'skew', 'warm_cool_b', 'clip',
            'face_over_bg')
    return {k: _num(p.get(k)) for k in keys}


def _valid_shape(sh: Optional[dict]) -> bool:
    if not sh:
        return False
    toe, shoulder = _num(sh.get('toe')), _num(sh.get('shoulder'))
    return toe is not None and shoulder is not None and 0.0 < toe < 1.0 and 0.0 < shoulder < 1.0


# ---------------------------------------------------------------- WB

def wb_of(card: dict, recs: Dict[str, dict],
          ledger: Optional[List[str]] = None) -> WhiteBalance:
    """WB 由卡级/预设输入决定；成片白点只做诊断。"""
    led = ledger if ledger is not None else []
    wbc = (card or {}).get('wb') or {}
    cast = wbc.get('cast') or {}
    a, b = _num(cast.get('a')), _num(cast.get('b'))
    if a is None or b is None:
        note = '卡级/预设输入；无 cast 目标（不反解白点）'
    else:
        note = ('卡级/预设输入；cast 目标 a*%+.1f b*%+.1f（仅诊断，不驱动 WB）'
                % (a, b))
    led.append('WB：' + note)
    return WhiteBalance(temperature=0.0, tint=0.0,
                        source='card-level', note=note)


# ---------------------------------------------------------------- 目标

def skin_target(target: Optional[dict], card: dict) -> dict:
    """肤色目标：**只用 peer/hero**；缺了就 None（不回退卡点值）。"""
    t = (target or {}).get('skin') or {}
    skin = {k: _num(t.get(k)) for k in ('L', 'C', 'h')}
    rel = (target or {}).get('rel') or {}
    wc = _num(rel.get('warm_cool_b'))
    if wc is None:
        wc = _num((target or {}).get('warm_cool_b'))
    ratio = _num(rel.get('face_over_bg'))
    if ratio is None:
        ratio = _num((target or {}).get('face_over_bg'))
    return dict(skin=skin, warm_cool_b=wc, face_over_bg=ratio)


def shape_target(card: dict, target: Optional[dict]) -> Optional[dict]:
    """peer/hero 的形状；没有就 None（不用卡中位兜底）。"""
    sh = (target or {}).get('shape') or {}
    if _valid_shape(sh):
        return dict(toe=float(sh['toe']), shoulder=float(sh['shoulder']),
                    contrast_win=_num(sh.get('contrast_win')) or 0.0,
                    source=str((target or {}).get('source') or 'peer'))
    return None


def curve_for(pred: Dict[str, float], shape: Optional[dict],
              ledger: Optional[List[str]] = None,
              q5_target: Optional[float] = None,
              meta: Optional[dict] = None) -> Optional[List[Tuple[int, int]]]:
    """形状残差 → 16 点 LUT；只套肩趾比例，不搬绝对分位。"""
    if not shape or pred.get('p50') is None:
        return None
    q = dict(p5=pred.get('p5'), p25=pred.get('p25'), p50=pred.get('p50'),
             p75=pred.get('p75'), p95=pred.get('p95'))
    if any(v is None for v in q.values()):
        return None
    toe = float(shape['toe'])
    shoulder = float(shape['shoulder'])
    q5, q50, q95 = float(q['p5']), float(q['p50']), float(q['p95'])
    q25 = q5 + toe * (q50 - q5)
    q75 = q95 - shoulder * (q95 - q50)
    shape_abs = dict(toe=q25 - q5, contrast_win=q75 - q25, shoulder=q95 - q75)
    # 比例口径（推荐路径）：压暗部后趾宽按**新跨度**重算，p50 锚点不动。
    # 宽度增量口径在分位很挤时会和 new5 打架（模拟 ③ 抓到中位被带跑 15 级）。
    fr = dict(toe=float(toe), shoulder=float(shoulder)) if q5_target is not None else None
    out = curve_from_quantiles(q, shape_abs, p95_cap=q95, q5_target=q5_target,
                               meta=meta, fracs=fr)
    if out:
        worst = max((float(y1) - float(y0)) / max(1.0, float(x1) - float(x0))
                    for (x0, y0), (x1, y1) in zip(out, out[1:]))
        if worst > MAX_CURVE_SLOPE:
            if ledger is not None:
                ledger.append('曲线局部斜率 %.2f > %.1f（code/code）⇒ 判为不硬修，'
                              '先查 PE/曝光/peer 形状' % (worst, MAX_CURVE_SLOPE))
            return None
    return out


# ---------------------------------------------------------------- 曝光 / 形状

def tone_solve(rec: dict, card: dict, target: Optional[dict],
               ledger: List[str]) -> dict:
    """分布 → 单调 tone map → 8 knot basis 解 sliders → 残差曲线。"""
    img = rec.get('img') or {}
    q = {k: _num(img.get(k)) for k in ('p5', 'p25', 'p50', 'p75', 'p95')}
    ui = {k: 0.0 for k in ('contrast', 'highlights', 'shadows', 'whites', 'blacks')}
    if any(q.get(k) is None for k in ('p5', 'p25', 'p50', 'p75', 'p95')):
        ledger.append('① 缺分位 ⇒ 明暗不做')
        return dict(ev=0.0, ui=ui, pred=dict(q), curve=None,
                    target=None, clipped=False)
    face_p99 = _num(rec.get('face_p99')) or _num((rec.get('skin') or {}).get('p99'))
    per = rec.get('person') or {}
    clip = _num(per.get('clip_pct'))
    if clip is None:
        clip = _num(rec.get('person_clip_pct')) or 0.0
    if ((face_p99 is not None and face_p99 >= 99.0)
            or (face_p99 is None and clip >= PERSON_CLIP_MAX)):
        ledger.append('脸/皮肤已削顶 ⇒ 明暗不做，只做颜色/局部')
        return dict(ev=0.0, ui=ui, pred=dict(q), curve=None,
                    target=None, clipped=True)

    sh = shape_target(card, target)
    ev = 0.0
    pred = {k: float(v) for k, v in q.items()}
    # ★ 交接：引擎报了 shadow_gap（渲出来的 p5 比 peer 高多少），曲线负责把暗部往 peer 压。
    pq = (target or {}).get('peer_q') or {}
    q5_t = _num(pq.get('p5')) or _num(pq.get(5))
    if q5_t is not None and pred.get('p5') is not None and float(pred['p5']) > q5_t + 1.0:
        ledger.append('交接：暗部比 peer 高 %.1f 级 ⇒ 曲线往 %.1f 压（上限 %.0f）'
                      % (float(pred['p5']) - q5_t, q5_t, 20.0))
    cmeta = {}
    curve = curve_for(pred, sh, ledger, q5_target=q5_t, meta=cmeta) if sh else None
    ledger.append('卡形状 tone：EV 0；曲线%s'
                  % (('%d 点' % len(curve)) if curve else '不动'))
    after = cmeta.get('new5') if curve else None
    return dict(ev=ev, ui=ui, pred=pred, curve=curve,
                target=sh, clipped=False,
                shadow_before=(None if pred.get('p5') is None else float(pred['p5'])),
                shadow_after=after,
                shadow_gap_before=(None if (pred.get('p5') is None or q5_t is None)
                                   else float(pred['p5']) - float(q5_t)),
                shadow_gap_after=(None if (after is None or q5_t is None)
                                  else float(after) - float(q5_t)))


# ---------------------------------------------------------------- 颜色 / 肤色

def _skin_of(rec: dict) -> dict:
    sk = rec.get('skin') or {}
    return dict(L=_num(sk.get('L', rec.get('face_L'))),
                C=_num(sk.get('C', rec.get('face_C'))),
                h=_num(sk.get('h', rec.get('face_h'))),
                b=_num(sk.get('b')),
                rgb=rec.get('skin_rgb'))


def grading(card: dict, rec: dict, target: dict,
            ledger: List[str], skin_post: Optional[dict] = None) -> dict:
    """肤色：Point Color 三轴只动肤色；目标来自卡/peer/hero 的 h/C/L。"""
    out = dict(point_color=None, split={})
    sk = _skin_of(rec)
    rgb = sk.get('rgb')
    if rgb:
        hh, ll, ss = colorsys.rgb_to_hls(*(float(v) / 255.0 for v in rgb[:3]))
        tsk = (target or {}).get('skin') or {}
        L0, C0, h0 = _num(sk.get('L')), _num(sk.get('C')), _num(sk.get('h'))
        if skin_post:
            L0 = _num(skin_post.get('L')) or L0
        Lt, Ct, ht = _num(tsk.get('L')), _num(tsk.get('C')), _num(tsk.get('h'))
        hue_shift, lum_scale, sat_scale, raw = 0.0, 0.0, 0.0, 0.0
        if h0 is not None and ht is not None:
            dh = ((float(ht) - float(h0) + 180.0) % 360.0) - 180.0
            hue_shift = max(-POINT_HUE_CAP, min(POINT_HUE_CAP, dh / 360.0))
        if L0 is not None and Lt is not None:
            lum_scale = max(-POINT_LUM_CAP, min(POINT_LUM_CAP, (float(Lt) - float(L0)) / 100.0))
        if L0 and C0 is not None and Lt and Ct is not None:
            r0 = float(C0) / float(L0)
            rt = float(Ct) / float(Lt)
            if r0 > 1e-9:
                raw = rt / r0 - 1.0
                sat_scale = max(-POINT_SAT_CAP, min(POINT_SAT_CAP, raw))
        if any(abs(v) > 1e-9 for v in (hue_shift, lum_scale, sat_scale)):
            ledger.append('Point Color：H %+.3f / L %+.3f / S %+.3f'
                          '（h %.1f→%.1f，L %.1f→%.1f，C/L %.3f→%.3f）'
                          % (hue_shift, lum_scale, sat_scale,
                             h0 if h0 is not None else float('nan'),
                             ht if ht is not None else float('nan'),
                             L0 if L0 is not None else float('nan'),
                             Lt if Lt is not None else float('nan'),
                             (float(C0) / float(L0)) if (L0 and C0 is not None) else float('nan'),
                             (float(Ct) / float(Lt)) if (Lt and Ct is not None) else float('nan')))
        else:
            ledger.append('Point Color：肤色 h/C/L 已在目标内 ⇒ 三轴 0，只做肤色窗')
        out['point_color'] = PointColor(
            h=round(float(hh), 4), s=round(float(ss), 4), l=round(float(ll), 4),
            range=0.5, hue_shift=round(float(hue_shift), 4),
            sat_scale=round(float(sat_scale), 4),
            lum_scale=round(float(lum_scale), 4),
            raw=round(float(raw), 4), rgb=[int(v) for v in rgb[:3]])
    else:
        ledger.append('缺 skin_rgb ⇒ 没有肤色蒙版/Point Color（记账）')

    # 分色：卡 tone 不再当靶（2026-11-02）；暂不做分色，等 peer 的 hue_bands 再接
    out['split'] = dict(shadow_hue=0.0, shadow_sat=0.0,
                        highlight_hue=0.0, highlight_sat=0.0, balance=0.0)
    ledger.append('分色不做：卡 tone 不当靶（等 peer 的 hue_bands 再接）')
    return out


# ---------------------------------------------------------------- 局部（肤色蒙版）

def _apply_plan_to_L(plan: LRPlan, L: float) -> float:
    """把已写进 plan 的曝光/曲线作用到一个 L*（预测用，不是测量）。"""
    v = l_after_ev(float(L), plan.exposure_ev)
    for name, ui in (('contrast', plan.contrast), ('highlights', plan.highlights),
                     ('shadows', plan.shadows), ('whites', plan.whites),
                     ('blacks', plan.blacks)):
        if abs(float(ui or 0.0)) > 1e-9:
            Lc = max(R.L_DOMAIN[0], min(R.L_DOMAIN[1], v))
            v += (R.k_tone(name, Lc) or 0.0) * float(ui) / 100.0
    return apply_lut(plan.curve, v)


def local_face(plan: LRPlan, rec: dict, target: dict, ledger: List[str]) -> None:
    """补脸：按脸/背景关系；余量用脸自己的 p99；只抬不压。"""
    sk = _skin_of(rec)
    L0 = sk.get('L')
    if not rec.get('skin_rgb'):
        ledger.append('没有 skin_rgb（没有肤色窗）⇒ 局部不补')
        return
    ratio = _num((target or {}).get('face_over_bg'))
    bg = _num(rec.get('bg_L'))
    if L0 is None or ratio is None or bg is None or bg <= 1e-6:
        ledger.append('缺脸/背景关系（face_over_bg）⇒ 局部不动（记账）')
        return
    tL = float(ratio) * float(bg)
    pred = _apply_plan_to_L(plan, L0)
    ev = ev_for_l(pred, tL)
    if ev < 0:
        ledger.append('脸/背景 %.2f 高于目标 %.2f（脸已比卡亮）⇒ 补脸只抬不压'
                      % (float(L0) / float(bg), float(ratio)))
        return
    face_p99 = _num(rec.get('face_p99'))
    if face_p99 is None:
        face_p99 = _num((rec.get('skin') or {}).get('p99'))
    if face_p99 is None:
        ledger.append('缺 face_p99（没有脸部余量）⇒ 局部不补')
        return
    room = ev_for_l(float(face_p99), FACE_CEIL_L)
    if room is None:
        ledger.append('脸 p99 余量算不出 ⇒ 局部不补')
        return
    room = max(0.0, min(float(room), LOCAL_FACE_MAX_EV))
    if room <= 0.02:
        ledger.append('脸 p99 %.1f 已到 %.0f ⇒ 局部不抬' % (face_p99, FACE_CEIL_L))
        return
    if ev > room:
        ledger.append('局部需要 %+.2f 档，但脸部余量只有 %.2f 档 ⇒ 只抬到余量'
                      % (ev, room))
        ev = float(room)
    if abs(ev) < 0.05:
        ledger.append('脸/背景 %.2f ≈ 目标 %.2f ⇒ 局部不动'
                      % (float(L0) / float(bg), float(ratio)))
        return
    plan.local = Local(mask='skin', exposure_ev=round(float(ev), 3))
    ledger.append('局部（subject + 肤色票）按脸/背景 %.2f → 目标 %.2f：%+.2f 档'
                  % (float(L0) / float(bg), float(ratio), ev))


# ---------------------------------------------------------------- 白裙局部

def local_white(plan: LRPlan, rec: dict, ledger: List[str]) -> bool:
    """白裙在人物蒙版里；过曝时压人物蒙版的 Highlights。"""
    w = (rec.get('parts') or {}).get('white') or {}
    p99 = _num(w.get('L_p99'))
    share = _num(w.get('share_person'))
    if p99 is None or share is None or share < WHITE_MIN_SHARE:
        return False
    after = _apply_plan_to_L(plan, float(p99))
    if plan.local.is_set() and plan.local.mask == 'skin':
        after = l_after_ev(after, plan.local.exposure_ev)
    if after <= WHITE_OVER_L:
        return False
    over = float(after) - WHITE_OVER_L
    ui = -min(40.0, max(10.0, over * 8.0))
    plan.white = Local(mask='person', highlights=round(float(ui), 1))
    ledger.append('白裙 p99 %.1f → 全局后 %.1f，超 %.1f：人物蒙版 Highlights %+.0f'
                  % (float(p99), after, over, ui))
    return True


# ---------------------------------------------------------------- 主流程

def plan_batch(recs: Dict[str, dict], card: dict,
               peer_targets: Optional[Dict[str, dict]] = None,
               hero_target: Optional[dict] = None,
               status_map: Optional[Dict[str, str]] = None,
               log=print) -> Dict[str, LRPlan]:
    """逐图出 LRPlan；目标 peer → hero；都没有就停（卡不当靶）。"""
    led0: List[str] = []
    wb = wb_of(card, recs, led0)
    for line in led0:
        log('  ' + line)
    out: Dict[str, LRPlan] = {}
    for stem, rec in recs.items():
        ledger: List[str] = []
        pt = (peer_targets or {}).get(stem) or {}
        peer_n = int(pt.get('peer_n') or 0)
        if peer_n >= 1 and _valid_shape(pt.get('shape')):
            # ★ 2026-11-02：peer 先算、最准；哪怕 1 张也用 peer（低置信已在
            #   peer_ratio 记账），不再等到 3 张才退 hero/卡。
            target, source = pt, 'peer'
        elif hero_target:
            target, source = hero_target, 'hero'
        else:
            raise RuntimeError('没有 peer/hero target：%s（卡中位不再当靶；先跑 peer 或给 hero）'
                               % stem)
        target = dict(target)
        target['source'] = source

        skt = skin_target(target, card)
        plan = LRPlan()
        plan.wb = wb
        tone = tone_solve(rec, card, target, ledger)
        plan.exposure_ev = float(tone.get('ev') or 0.0)
        for k in ('contrast', 'highlights', 'shadows', 'whites', 'blacks'):
            setattr(plan, k, float((tone.get('ui') or {}).get(k) or 0.0))
        plan.curve = tone.get('curve')
        plan.shadow_gap_before = tone.get('shadow_gap_before')
        plan.shadow_gap_after = tone.get('shadow_gap_after')
        plan.status = str((status_map or {}).get(stem)
                           or pt.get('status') or (target or {}).get('status') or '')
        plan.target_source = str(pt.get('target_from') or target.get('target_from') or source)
        plan.face_mid_target = pt.get('face_mid_target')
        if plan.curve:
            ledger.append('曲线 %d 点（只套肩趾比例）' % len(plan.curve))
        if plan.shadow_gap_before is not None:
            ledger.append('暗部：渲出比 peer 高 %.1f 级 → 曲线意图 %s'
                          % (plan.shadow_gap_before,
                             ('高 %.1f 级' % plan.shadow_gap_after)
                             if plan.shadow_gap_after is not None else '不动') + '（实测见 ③ 模拟）')

        target_skin = dict(skt['skin'])      # 设计：肤色目标 = peer 自己的 L/C/h

        local_face(plan, rec, dict(face_over_bg=skt.get('face_over_bg')), ledger)
        local_white(plan, rec, ledger)

        sk = _skin_of(rec)
        Lpost = (_apply_plan_to_L(plan, sk['L']) if sk.get('L') is not None else None)
        if Lpost is not None and plan.local.is_set() and plan.local.mask == 'skin':
            Lpost = l_after_ev(Lpost, plan.local.exposure_ev)
        g = grading(card, rec, dict(skin=target_skin,
                                    warm_cool_b=skt['warm_cool_b']), ledger,
                    skin_post=dict(L=Lpost))
        if g.get('point_color') is not None:
            plan.point_color = g['point_color']
        sp = g.get('split') or {}
        plan.split_shadow_hue = float(sp.get('shadow_hue') or 0.0)
        plan.split_shadow_sat = float(sp.get('shadow_sat') or 0.0)
        plan.split_highlight_hue = float(sp.get('highlight_hue') or 0.0)
        plan.split_highlight_sat = float(sp.get('highlight_sat') or 0.0)
        plan.split_balance = float(sp.get('balance') or 0.0)

        pred = {k: _apply_plan_to_L(plan, float((rec.get('img') or {}).get(k)))
                for k in ('p5', 'p25', 'p50', 'p75', 'p95')
                if _num((rec.get('img') or {}).get(k)) is not None}
        face_pred = (_apply_plan_to_L(plan, sk['L']) if sk.get('L') is not None else None)
        if face_pred is not None and plan.local.is_set():
            face_pred = l_after_ev(face_pred, plan.local.exposure_ev)
        c_pred = None
        if sk.get('C') is not None:
            c_pred = float(sk['C']) * (1.0 + float(plan.point_color.sat_scale))
        tsk = target_skin or {}
        cp = _card_points(card)
        plan.card_dev = dict(
            p50=round(pred.get('p50', 0.0) - float(cp['p50']), 2)
            if (cp.get('p50') is not None and pred.get('p50') is not None) else None,
            p95=round(pred.get('p95', 0.0) - float(cp['p95']), 2)
            if (cp.get('p95') is not None and pred.get('p95') is not None) else None,
            face_L=round(face_pred - float(tsk['L']), 2)
            if (face_pred is not None and tsk.get('L') is not None) else None,
            face_C=round(c_pred - float(tsk['C']), 2)
            if (c_pred is not None and tsk.get('C') is not None) else None,
            warm_cool_b=round(float(_num(rec.get('warm_cool_b')) or 0.0)
                              - float(skt['warm_cool_b']), 2)
            if (rec.get('warm_cool_b') is not None
                and skt.get('warm_cool_b') is not None) else None)
        plan.target = dict(source=source, peer_n=peer_n,
                           shape=tone.get('target') or {},
                           skin={k: (round(v, 2) if isinstance(v, (int, float)) else v)
                                 for k, v in tsk.items()},
                           # ★ 曝光目标本来只进 engine/ 的 target 表；顺手写进 plan，
                           #   目检页才能把「① 目标 vs ① 实测」摆在一起（2026-11-03）。
                           target_p50=pt.get('target_p50'),
                           face_mid_target=pt.get('face_mid_target'),
                           status=pt.get('status') or (target or {}).get('status'),
                           target_from=pt.get('target_from'),
                           target_rel=pt.get('target_rel'),
                           target_clamped=pt.get('target_clamped'))
        plan.notes = dict(pred=pred, face_pred=face_pred,
                          target_source=source,
                          framing=pt.get('framing'),
                          peer_ids=pt.get('peer_ids') or [],
                          peer_n=peer_n,
                          hero=bool(hero_target) if source == 'hero' else False)
        plan.ledger = ledger
        out[stem] = plan
    return out


# ---------------------------------------------------------------- 人读汇总

def _f(v, fmt='%.1f', dash='—'):
    return dash if v is None else fmt % float(v)


def summary(plans: Dict[str, LRPlan], card_name: str = '',
            pe: Optional[float] = None) -> str:
    """一图一行的账：WB / PE / 曝光/曲线 / 肤色 / 分色 / 局部 / 卡偏差。"""
    lines = ['【LR 计划】卡 %s' % card_name]
    if plans:
        wb = next(iter(plans.values())).wb
        if wb.is_set():
            lines.append('  WB：%.0fK / Tint G×%.3f ｜ %s'
                         % (wb.temperature, wb.tint, wb.note or ''))
        else:
            lines.append('  WB：' + (wb.note or '卡级/预设输入'))
        lines.append('  PE：%s' % (_f(pe, '%.3f') if pe is not None else '—（未标）'))
    lines.append('%-10s %-32s %-26s %-22s %-20s %s'
                 % ('stem', '曝光/曲线', '肤色（点颜色 SatScale）',
                    '分色', '局部（肤色蒙版）', '卡偏差'))
    for stem, p in sorted(plans.items()):
        tone = p.basic_text()
        if p.curve:
            tone += ' / %d点' % len(p.curve)
        split = ('暗 %.0f°/%.0f 高 %.0f°/%.0f'
                 % (p.split_shadow_hue, p.split_shadow_sat,
                    p.split_highlight_hue, p.split_highlight_sat)
                 if (abs(p.split_shadow_sat) > 1e-9
                     or abs(p.split_highlight_sat) > 1e-9) else '不动')
        dev = []
        for k, label in (('p50', 'p50'), ('p95', 'p95'), ('face_L', '脸L'),
                         ('face_C', '脸C'), ('warm_cool_b', '暖冷')):
            v = (p.card_dev or {}).get(k)
            if isinstance(v, (int, float)):
                dev.append('%s%+.1f' % (label, float(v)))
        lines.append('%-10s %-32s %-26s %-22s %-20s %s'
                     % (stem, tone[:32], p.color_text()[:26], split[:22],
                        p.local_text()[:20], ' '.join(dev) or '—'))
        for note in p.ledger:
            lines.append('    · ' + note)
    return '\n'.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """诊断入口：给一份 ① 测量 + 卡名，打印计划（不驱动 LR）。"""
    import argparse
    import json
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument('--recs', required=True)
    ap.add_argument('--card', required=True)
    a = ap.parse_args(argv)
    from .matchcard import raw_card
    card = raw_card(a.card)
    if card is None:
        print('没有这张卡：%s' % a.card)
        return 2
    doc = json.load(open(a.recs, encoding='utf-8'))
    rows = doc.get('per_image') if isinstance(doc, dict) else doc
    if isinstance(rows, list):
        recs = {os.path.basename(r['file']).replace('_engine.tif', '').replace('.tif', ''): r
                for r in rows}
    else:
        recs = rows
    plans = plan_batch(recs, card)
    print(summary(plans, card['name'], pe=(card or {}).get('pe')))
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
