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
EXPOSURE_STEP_MAX = 3.0        # 全局硬上限（只防失控；不再用 0.7 小步）
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
    """肤色目标：peer/hero 优先，缺了回退卡；只给关系。"""
    t = (target or {}).get('skin') or {}
    c = (card or {}).get('points') or {}
    skin = {}
    for k, ck in (('L', 'face_L'), ('C', 'face_C'), ('h', 'face_h')):
        v = _num(t.get(k))
        skin[k] = v if v is not None else _num(c.get(ck))
    rel = (target or {}).get('rel') or {}
    wc = _num(rel.get('warm_cool_b'))
    if wc is None:
        wc = _num((target or {}).get('warm_cool_b'))
    if wc is None:
        wc = _num(c.get('warm_cool_b'))
    ratio = _num(rel.get('face_over_bg'))
    if ratio is None:
        ratio = _num((target or {}).get('face_over_bg'))
    if ratio is None:
        ratio = _num(c.get('face_over_bg'))
    return dict(skin=skin, warm_cool_b=wc, face_over_bg=ratio)


def _card_shape(card: dict) -> Optional[dict]:
    p = _card_points(card)
    try:
        p5, p25, p50 = (float(p[k]) for k in ('p5', 'p25', 'p50'))
        p75, p95 = (float(p[k]) for k in ('p75', 'p95'))
        toe = (p25 - p5) / (p50 - p5)
        shoulder = (p95 - p75) / (p95 - p50)
    except Exception:                                      # noqa: BLE001
        return None
    if not (0.0 < toe < 1.0 and 0.0 < shoulder < 1.0):
        return None
    return dict(toe=toe, shoulder=shoulder,
                contrast_win=p75 - p25, source='card')


def shape_target(card: dict, target: Optional[dict]) -> Optional[dict]:
    """peer/hero 的形状优先；缺了用卡的肩趾比例。"""
    sh = (target or {}).get('shape') or {}
    if _valid_shape(sh):
        return dict(toe=float(sh['toe']), shoulder=float(sh['shoulder']),
                    contrast_win=_num(sh.get('contrast_win')) or 0.0,
                    source=str((target or {}).get('source') or 'peer'))
    return _card_shape(card)


def curve_for(pred: Dict[str, float], shape: Optional[dict],
              ledger: Optional[List[str]] = None) -> Optional[List[Tuple[int, int]]]:
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
    out = curve_from_quantiles(q, shape_abs, p95_cap=q95)
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

def exposure_for(rec: dict, target: Optional[dict],
                 ledger: List[str]) -> float:
    """补全局曝光：对齐 peer/hero 的**背景分布**；人/脸只做上限约束。

    不再拿脸 p99 / 单张中位当全局靶。背景 p10/p90/p95 三个分位各算一个 EV，
    取平均；人 clip / 脸 p99 只负责把正 EV 压住。
    """
    per = rec.get('person') or {}
    face_p99 = _num(rec.get('face_p99')) or _num((rec.get('skin') or {}).get('p99'))
    clip = _num(per.get('clip_pct'))
    if clip is None:
        clip = _num(rec.get('person_clip_pct')) or 0.0
    if face_p99 is None and clip >= PERSON_CLIP_MAX:
        ledger.append('无脸可测；人身上死白 %.2f%% ≥ %.0f%% ⇒ 全局曝光停住'
                      % (clip, PERSON_CLIP_MAX))
        return 0.0

    bg = rec.get('bg') or {}
    tb = (target or {}).get('bg') or {}
    pairs = []
    for k in ('L_p10', 'L_p90', 'L_p95'):
        cur, tgt = _num(bg.get(k)), _num(tb.get(k))
        if cur is not None and tgt is not None:
            pairs.append((k, float(cur), float(tgt)))
    if len(pairs) < 2:
        ledger.append('没有 peer/hero 的背景分布目标 ⇒ 全局曝光保持起点')
        return 0.0

    evs = [ev_for_l(cur, tgt) for _, cur, tgt in pairs]
    ev = sum(evs) / float(len(evs))
    if abs(ev) < 0.05:
        return 0.0
    ev = max(-EXPOSURE_STEP_MAX, min(EXPOSURE_STEP_MAX, ev))

    # 人/脸只做上限：正 EV 不能把人推到死白。
    p99 = face_p99 or _num(rec.get('person_p99')) or _num(per.get('p99'))
    if p99 is None:
        p99 = _num(rec.get('face_p99'))
    if ev > 0 and p99 is not None:
        room = ev_for_l(float(p99), PERSON_CEIL_L)
        if room is not None:
            ev = min(ev, max(0.0, float(room)))
    desc = '，'.join('%s %.1f→%.1f' % (k, cur, tgt) for k, cur, tgt in pairs)
    ledger.append('全局曝光 %+.2f 档：背景分布对齐 peer/hero（%s；人 p99 %s）'
                  % (ev, desc, ('%.1f' % p99) if p99 is not None else '—'))
    return round(float(ev), 3)


def _basic_shape(pred: dict, sh: dict) -> dict:
    """用 peer/hero 的肩趾比例给 Basic 一个粗形状；细分仍交给曲线/局部。"""
    p5, p25, p50 = float(pred['p5']), float(pred['p25']), float(pred['p50'])
    p75, p95 = float(pred['p75']), float(pred['p95'])
    toe = float(sh.get('toe') or 0.0)
    shoulder = float(sh.get('shoulder') or 0.0)
    tq25 = p5 + toe * (p50 - p5)
    tq75 = p95 - shoulder * (p95 - p50)
    cur_toe, cur_shoulder = p25 - p5, p95 - p75
    cur_win = p75 - p25
    t_toe, t_shoulder = tq25 - p5, p95 - tq75
    t_win = tq75 - tq25
    return dict(
        contrast=round(max(-25.0, min(25.0, (t_win - cur_win) * 1.0)), 1),
        highlights=round(max(-30.0, min(30.0, (t_shoulder - cur_shoulder) * 1.5)), 1),
        shadows=round(max(-25.0, min(25.0, (t_toe - cur_toe) * 1.0)), 1),
        whites=0.0, blacks=0.0,
    )


def tone_solve(rec: dict, card: dict, target: Optional[dict],
               ledger: List[str]) -> dict:
    """曝光 + 形状；Basic 按 peer 肩趾给粗形状，曲线再补细节。"""
    img = rec.get('img') or {}
    q = {k: _num(img.get(k)) for k in ('p5', 'p25', 'p50', 'p75', 'p95')}
    ui = {k: 0.0 for k in ('contrast', 'highlights', 'shadows', 'whites', 'blacks')}
    if any(q.get(k) is None for k in ('p5', 'p25', 'p50', 'p75', 'p95')):
        ledger.append('① 缺分位 ⇒ 明暗不做')
        return dict(ev=0.0, ui=ui, pred=dict(q), curve=None,
                    target=None, clipped=False)
    ev = exposure_for(rec, target, ledger)
    pred = {k: l_after_ev(float(v), ev) for k, v in q.items()}
    clipped = False
    face_p99 = _num(rec.get('face_p99')) or _num((rec.get('skin') or {}).get('p99'))
    per = rec.get('person') or {}
    clip = _num(per.get('clip_pct'))
    if clip is None:
        clip = _num(rec.get('person_clip_pct')) or 0.0
    if ((face_p99 is not None and face_p99 >= 99.0)
            or (face_p99 is None and clip >= PERSON_CLIP_MAX)):
        clipped = True
        ledger.append('脸/皮肤已削顶 ⇒ 曲线不做，只做颜色/局部')
        return dict(ev=0.0, ui=ui, pred=pred, curve=None,
                    target=None, clipped=True)
    sh = shape_target(card, target)
    if sh is None:
        ledger.append('没有可用的卡/peer/hero 形状 ⇒ 曲线不做')
        return dict(ev=ev, ui=ui, pred=pred, curve=None,
                    target=None, clipped=False)
    ui.update(_basic_shape(pred, sh))
    if any(abs(v) > 1e-9 for v in ui.values()):
        ledger.append('Basic 粗形状：对比%+.0f 高光%+.0f 阴影%+.0f（peer 肩趾）'
                      % (ui['contrast'], ui['highlights'], ui['shadows']))
    curve = curve_for(pred, sh, ledger)
    if curve:
        ledger.append('风格曲线：套 %s 肩趾比例（toe %.2f / shoulder %.2f）到本图锚点'
                      % (sh.get('source') or '卡', float(sh['toe']), float(sh['shoulder'])))
    else:
        ledger.append('形状目标 %s（toe %.2f / shoulder %.2f）未落地，按不硬修处理'
                      % (sh.get('source') or '卡', float(sh['toe']), float(sh['shoulder'])))
    return dict(ev=ev, ui=ui, pred=pred, curve=curve,
                target=sh, clipped=False)


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

    # 冷暖/分色：只处理卡里近中性的暖/冷方向；场景色不做全局分色
    tone = (card or {}).get('tone') or {}
    wc_t = _num((target or {}).get('warm_cool_b'))
    wc_c = _num(rec.get('warm_cool_b'))
    if wc_t is not None and wc_c is not None and abs(wc_t - wc_c) >= 0.5:
        hi = tone.get('highlight') or {}
        sh = tone.get('shadow') or {}

        def _node(node):
            lab = node.get('lab')
            if not lab:
                return None
            C = math.hypot(float(lab[1]), float(lab[2]))
            h = _num(node.get('hsl_h'))
            if h is None or C > SPLIT_C_MAX:
                return None
            if not (SPLIT_HUE_WARM[0] <= h <= SPLIT_HUE_WARM[1]
                    or SPLIT_HUE_COOL[0] <= h <= SPLIT_HUE_COOL[1]):
                return None
            return h, C

        hi_n, sh_n = _node(hi), _node(sh)
        if hi_n is None or sh_n is None:
            out['split'] = dict(shadow_hue=0.0, shadow_sat=0.0,
                                highlight_hue=0.0, highlight_sat=0.0, balance=0.0)
            ledger.append('分色跳过：卡高光/暗部不是近中性暖冷 ⇒ 场景色不当色偏')
        else:
            hi_h, hi_c = hi_n
            sh_h, sh_c = sh_n
            out['split'] = dict(
                shadow_hue=round(float(sh_h), 1),
                shadow_sat=round(min(SPLIT_UI_MAX, sh_c * SPLIT_UI_PER_C), 1),
                highlight_hue=round(float(hi_h), 1),
                highlight_sat=round(min(SPLIT_UI_MAX, hi_c * SPLIT_UI_PER_C), 1),
                balance=0.0)
            ledger.append('分色（卡，近中性暖冷）：暖冷 %.2f → %.2f ⇒ '
                          '暗 %.0f°/%.0f、高 %.0f°/%.0f'
                          '（剂量=卡色度×%g，③ 目检）'
                          % (wc_c, wc_t, sh_h, out['split']['shadow_sat'],
                             hi_h, out['split']['highlight_sat'], SPLIT_UI_PER_C))
    elif wc_t is not None and wc_c is not None:
        ledger.append('暖冷 %.2f ≈ 目标 %.2f ⇒ 不分色' % (wc_c, wc_t))
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

def _card_target(card: dict) -> dict:
    return dict(source='card', peer_n=0, shape=_card_shape(card) or {},
                skin={}, rel={}, exposure={})


def plan_batch(recs: Dict[str, dict], card: dict,
               peer_targets: Optional[Dict[str, dict]] = None,
               hero_target: Optional[dict] = None,
               log=print) -> Dict[str, LRPlan]:
    """逐图出 LRPlan；目标顺序 peer → hero → 卡。"""
    led0: List[str] = []
    wb = wb_of(card, recs, led0)
    for line in led0:
        log('  ' + line)
    out: Dict[str, LRPlan] = {}
    for stem, rec in recs.items():
        ledger: List[str] = []
        pt = (peer_targets or {}).get(stem) or {}
        peer_n = int(pt.get('peer_n') or 0)
        if peer_n >= 3 and _valid_shape(pt.get('shape')):
            target, source = pt, 'peer'
        elif hero_target:
            target, source = hero_target, 'hero'
        else:
            target, source = _card_target(card), 'card'
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
        if plan.curve:
            ledger.append('曲线 %d 点（只套肩趾比例）' % len(plan.curve))

        target_skin = dict(skt['skin'])

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
                                 for k, v in tsk.items()})
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
