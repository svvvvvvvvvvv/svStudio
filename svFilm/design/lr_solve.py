# -*- coding: utf-8 -*-
"""LR-B 图级求解：**WB → 曝光/明暗 → 颜色/肤色 → 局部**。

顺序是纪律（`纪律.md` 第 4 条 / `LR设计.md` §3）：

    1 卡级 WB（整批一个；引擎 RAW 解码层）
    2 曝光/明暗：Basic 为主（Exposure/Contrast/Highlights/Shadows/Whites/Blacks），
      曲线补形状；先让整幅/背景成立
    3 颜色：**只用蒙版里的 Point Color 调肤色**（不做 HSL）；肤色不动整体背景
    4 局部：一张卡一个**肤色蒙版**，只补关系，不补"整身"
    5 细节不在这里（锐化/降噪/导出是物理层）

落点谁定
--------
**PE（引擎印相曝光）定底片落点**，LR 的 Exposure 不拿 p50/脸去拖整幅
（`引擎设计.md` §3：PE 不对先修 PE，不拿 LR 补）。所以本模块：

  · 曝光滑杆默认 **0**（① 的落点已经是 PE 定的；背景不动）；
  · 明暗只对"形状"：把卡/peer 的对比窗、肩/趾关系套到 ① 自己的 p50 上，
    逐图 p50 **不拿去做卡的目标**（那是上一版把背景压到 L 21 的根因）；
  · 如果卡/peer 的形状在 ① 的 p50 上放不进 0..100（例如卡是低调、这批是高调），
    **不硬修**：报"卡/批不匹配"，只做 WB/肤色/局部（设计 §8）。

物理事故
--------
人身上可见死白（≥1%）或整幅死白（≥2.5%）⇒ 高光已被削顶，**不可逆**：
明暗/曲线不动（再压只会把背景压暗），只做颜色/局部，记账（设计 §8）。

肤色
----
只用**蒙版里的 Point Color**（`POINTSWATCH`，subject 组 ∩ 肤色色票）；
`SatScale` 由 C/L 关系给量：

    r0 = C0/L0（①）    r* = C*/L*（卡/peer）    e = r*/r0 − 1

色相归卡级 WB（引擎解码层），LR 里不做 HSL。
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

# ---- 接口事实 / 物理底线（都不是创作目标）

# Basic 滑杆的规划上限（实测域 ±50；超出不外推）
SLIDER_LIMIT = float(R.SLIDER_DOMAIN)
# 白端 p99 的目标关系：p99 ≈ p95 + 0.25×(p95−p75)（746 张大师成片的中位）
P99_OVER_SHOULDER = 0.25
# 分色剂量起点：卡自己的色度 C*（Lab）当量（C*→UI 无公开刻度，③ 目检）
SPLIT_UI_PER_C = 3.0
SPLIT_UI_MAX = 30.0
# 分色只处理**近中性的暖/冷方向**；卡里高光/暗部如果是场景色（树叶绿、天空蓝），
# 它不是“色偏”，不能拿来做全局分色（否则整幅高光加绿）。
SPLIT_C_MAX = 1.5
SPLIT_HUE_WARM = (20.0, 70.0)
SPLIT_HUE_COOL = (200.0, 260.0)
# SDK 文档：Point Color 的 SatScale 是 -1..1 连续量（没有“0.5 步长”的官方说法）。
# 只按 C/L 关系给量，不叠加自测增益/量化。
# 物理死白门（R1：只拦事故）：人身上 ≥1% 或整幅 ≥2.5%
CLIP_PERSON_MAX = 1.0
CLIP_FRAME_MAX = 2.5


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
            'contrast', 'contrast_win', 'skew', 'warm_cool_b', 'clip')
    return {k: _num(p.get(k)) for k in keys}


def _p99_of(q95: Optional[float], q75: Optional[float]) -> Optional[float]:
    if q95 is None or q75 is None:
        return None
    return float(q95) + P99_OVER_SHOULDER * max(0.0, float(q95) - float(q75))


# ---------------------------------------------------------------- 目标（形状）

def tone_target(peer: Optional[dict], card: dict,
                ledger: Optional[List[str]] = None) -> Optional[dict]:
    """这一张的**形状**目标：对比（p95−p5）、skew、趾/肩。

    返回 dict(contrast, skew, toe, shoulder, source, peer_n)；
    位置（p50）不在这里 —— 由 `anchor_target` 用 ① 自己的 p50 锚定；
    肤色/暖冷由 `skin_target` 单独给（与明暗是否成立无关）。
    """
    led = ledger if ledger is not None else []
    cp = _card_points(card)
    pn = int((peer or {}).get('peer_n') or 0)
    shape = dict((peer or {}).get('shape') or {})
    rel = dict((peer or {}).get('rel') or {})
    out: Dict[str, object] = dict(source='card', peer_n=pn)
    # ---- 形状：peer 优先
    contrast = _num(rel.get('contrast'))
    p50p = _num(rel.get('p50'))
    p95p = _num(rel.get('p95')) or _num(shape.get('p95'))
    skew = None
    if None not in (p50p, p95p, contrast) and float(contrast) > 1e-6:
        skew = (float(p50p) - (float(p95p) - float(contrast))) / float(contrast)
    toe = _num(shape.get('toe'))
    shoulder = _num(shape.get('shoulder'))
    if pn >= 3 and None not in (contrast, skew, toe, shoulder):
        out.update(contrast=float(contrast), skew=float(skew),
                   toe=float(toe), shoulder=float(shoulder), source='peer')
    else:
        contrast = cp.get('contrast')
        toe = cp.get('toe')
        shoulder = cp.get('shoulder')
        skew = cp.get('skew')
        if skew is not None and skew > 1.5:        # 旧卡里存的是百分数
            skew = skew / 100.0
        # 没有对比点就由 toe+shoulder+contrast_win 推
        if contrast is None:
            cw = cp.get('contrast_win')
            if None not in (toe, shoulder, cw):
                contrast = float(toe) + float(shoulder) + float(cw)
        if pn and pn < 3:
            led.append('peer_n=%d<3 ⇒ 回退卡点值（不猜）' % pn)
        if None in (contrast, skew, toe, shoulder):
            led.append('形状目标不全（contrast/skew/toe/shoulder）⇒ 明暗不做')
            return None
        out.update(contrast=float(contrast), skew=float(skew),
                   toe=float(toe), shoulder=float(shoulder))
    if not (0.02 < float(out['skew']) < 0.98) or float(out['contrast']) <= 1.0:
        led.append('形状目标不合理（contrast %.1f / skew %.2f）⇒ 明暗不做'
                   % (float(out['contrast']), float(out['skew'])))
        return None
    return out


def anchor_target(shape: dict, p50: Optional[float],
                  ledger: Optional[List[str]] = None) -> Optional[dict]:
    """把形状套到 ① 自己的 p50 上 → 目标分位（**不移动整幅落点**）。

    卡/批不匹配（目标跑出 0..100）⇒ 返回 None，只记账，不硬修。
    """
    led = ledger if ledger is not None else []
    if p50 is None:
        led.append('① 没有 p50 ⇒ 明暗目标不做')
        return None
    contrast = float(shape['contrast'])
    skew = float(shape['skew'])
    toe = float(shape['toe'])
    shoulder = float(shape['shoulder'])
    q5 = float(p50) - skew * contrast
    q95 = q5 + contrast
    q25 = q5 + toe
    q75 = q95 - shoulder
    if q5 < 0.5 or q95 > 99.5:
        led.append('卡/批不匹配：以 p50=%.1f 套卡形状（contrast %.1f / skew %.2f）'
                   '会到 [%.1f, %.1f]，超 0..100 ⇒ 不硬修，只做肤色/局部'
                   % (float(p50), contrast, skew, q5, q95))
        return None
    if not (q5 < q25 < float(p50) < q75 < q95):
        led.append('目标分位不单调（%.1f/%.1f/%.1f/%.1f/%.1f）⇒ 明暗不做'
                   % (q5, q25, float(p50), q75, q95))
        return None
    return dict(q5=q5, q25=q25, q50=float(p50), q75=q75, q95=q95,
                q99=_p99_of(q95, q75))


def skin_target(peer: Optional[dict], card: dict) -> dict:
    """肤色/暖冷目标（与明暗是否成立无关）。

    peer_n≥3 用 peer 的肤色中位；否则回退卡点值。
    """
    cp = _card_points(card)
    skin = dict((peer or {}).get('skin') or {})
    rel = dict((peer or {}).get('rel') or {})
    return dict(
        skin=dict(
            L=_num(skin.get('L')) if skin.get('L') is not None else cp.get('face_L'),
            C=_num(skin.get('C')) if skin.get('C') is not None else cp.get('face_C'),
            h=_num(skin.get('h')) if skin.get('h') is not None else cp.get('face_h')),
        warm_cool_b=(_num(rel.get('warm_cool_b'))
                     if rel.get('warm_cool_b') is not None
                     else cp.get('warm_cool_b')))


# ---------------------------------------------------------------- WB（卡级）

def wb_of(card: dict, recs: Dict[str, dict],
          ledger: Optional[List[str]] = None) -> WhiteBalance:
    """WB 由卡级/预设输入决定；成片白点只做诊断，**不反解**。

    卡里可存目录级 cast 目标（a*/b*），它是 look 诊断，不是引擎 WB 目标；
    引擎的 temperature/tint 由卡/预设直接给（如卡 108 = 5400/0.98）。
    """
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
                        source='card-level',
                        note=note)


# ---------------------------------------------------------------- Basic + 曲线

def _slider(name: str, L: float, dL: float, ledger: List[str]) -> float:
    """要改 dL ⇒ 滑杆值（UI）；域外不外推（给边界 + 记账）。"""
    if abs(dL) < 1e-9:
        return 0.0
    Lc = max(R.L_DOMAIN[0], min(R.L_DOMAIN[1], float(L)))
    v = R.slider_for_delta(name, Lc, dL)
    note = ''
    if v is None:
        k = R.k_tone(name, Lc) or 0.0
        v = 0.0 if abs(k) < 1e-6 else max(-SLIDER_LIMIT,
                                          min(SLIDER_LIMIT, 100.0 * float(dL) / k))
        note = '（超出实测域/±%.0f ⇒ 给边界值，不外推）' % SLIDER_LIMIT
    act = (R.k_tone(name, Lc) or 0.0) * float(v) / 100.0
    ledger.append('%s %+.1f → 预测 ΔL %+.2f%s' % (name, v, act, note))
    return round(float(v), 1)


def _add(pred: Dict[str, float], name: str, ui: float) -> None:
    for k in list(pred):
        Lc = max(R.L_DOMAIN[0], min(R.L_DOMAIN[1], pred[k]))
        pred[k] += (R.k_tone(name, Lc) or 0.0) * float(ui) / 100.0


def _clipped(rec: dict) -> Optional[str]:
    """物理事故：人身上/整幅可见死白。返回原因或 None。"""
    per = (rec.get('person') or {}).get('clip_pct')
    frame = (rec.get('img') or {}).get('clip_pct')
    if per is not None and float(per) >= CLIP_PERSON_MAX:
        return '人身上死白 %.2f%% ≥ %.0f%%' % (float(per), CLIP_PERSON_MAX)
    if frame is not None and float(frame) >= CLIP_FRAME_MAX:
        return '整幅死白 %.2f%% ≥ %.0f%%' % (float(frame), CLIP_FRAME_MAX)
    return None


def basic_tone(rec: dict, target: Optional[dict], ledger: List[str]) -> dict:
    """曝光（0）+ Basic 六根；无法对齐形状/已削顶时**不动**。"""
    img = rec.get('img') or {}
    qs = {k: _num(img.get(k)) for k in ('p5', 'p25', 'p50', 'p75', 'p95', 'p99')}
    if any(qs.get(k) is None for k in ('p5', 'p25', 'p50', 'p75', 'p95')):
        ledger.append('① 缺分位 ⇒ 明暗不做')
        return dict(ev=0.0, ui={}, pred=dict(qs))
    pred = {k: float(v) for k, v in qs.items() if v is not None}
    ui = {k: 0.0 for k in ('contrast', 'highlights', 'shadows', 'whites', 'blacks')}
    if target is None:
        ledger.append('曝光 0（PE 定落点；形状目标不成立 ⇒ 明暗不动）')
        return dict(ev=0.0, ui=ui, pred=pred)
    clip = _clipped(rec)
    if clip:
        ledger.append('曝光 0；%s ⇒ 高光已削顶（物理不可逆），'
                      '明暗/曲线不动，只做颜色/局部' % clip)
        return dict(ev=0.0, ui=ui, pred=pred)
    ledger.append('曝光 0（PE 定落点；形状套在 ① 的 p50=%.1f 上）' % qs['p50'])

    # 对比：让 p75−p25 到目标窗（上下两端各动一半）
    cw_t = float(target['q75']) - float(target['q25'])
    cw_c = pred['p75'] - pred['p25']
    d_c = 0.5 * (cw_t - cw_c)
    k75 = R.k_tone('contrast', max(R.L_DOMAIN[0], min(R.L_DOMAIN[1], pred['p75']))) or 0.0
    k25 = R.k_tone('contrast', max(R.L_DOMAIN[0], min(R.L_DOMAIN[1], pred['p25']))) or 0.0
    den = k75 - k25
    ui['contrast'] = 0.0 if abs(den) < 1e-6 else round(
        max(-SLIDER_LIMIT, min(SLIDER_LIMIT, 200.0 * d_c / den)), 1)
    _add(pred, 'contrast', ui['contrast'])
    ledger.append('对比 %+.1f → 预测窗 %.1f L*（目标 %.1f）'
                  % (ui['contrast'], pred['p75'] - pred['p25'], cw_t))

    ui['highlights'] = _slider('highlights', pred['p95'],
                               float(target['q95']) - pred['p95'], ledger)
    _add(pred, 'highlights', ui['highlights'])
    ui['shadows'] = _slider('shadows', pred['p25'],
                            float(target['q25']) - pred['p25'], ledger)
    _add(pred, 'shadows', ui['shadows'])
    if target.get('q99') is not None and pred.get('p99') is not None:
        ui['whites'] = _slider('whites', pred['p99'],
                               float(target['q99']) - pred['p99'], ledger)
        _add(pred, 'whites', ui['whites'])
    ui['blacks'] = _slider('blacks', pred['p5'],
                           float(target['q5']) - pred['p5'], ledger)
    _add(pred, 'blacks', ui['blacks'])
    return dict(ev=0.0, ui=ui, pred=pred)


def curve_for(pred: Dict[str, float], target: Optional[dict]) -> Optional[List[Tuple[int, int]]]:
    """Basic 之后的形状残差 → 16 点 LUT（不动 p50）。"""
    if target is None or pred.get('p50') is None:
        return None
    q = dict(p5=pred.get('p5'), p25=pred.get('p25'), p50=pred.get('p50'),
             p75=pred.get('p75'), p95=pred.get('p95'))
    if any(v is None for v in q.values()):
        return None
    shape = dict(toe=float(target['q25']) - float(target['q5']),
                 contrast_win=float(target['q75']) - float(target['q25']),
                 shoulder=float(target['q95']) - float(target['q75']))
    return curve_from_quantiles(q, shape, p95_cap=float(target['q95']))


# ---------------------------------------------------------------- 颜色 / 肤色

def _skin_of(rec: dict) -> dict:
    sk = rec.get('skin') or {}
    return dict(L=_num(sk.get('L', rec.get('face_L'))),
                C=_num(sk.get('C', rec.get('face_C'))),
                h=_num(sk.get('h', rec.get('face_h'))),
                b=_num(sk.get('b')),
                rgb=rec.get('skin_rgb'))


def grading(card: dict, rec: dict, target: dict, ledger: List[str]) -> dict:
    """肤色：**只用蒙版里的 Point Color**（不做 HSL）+ 冷暖/分色（按卡方向）。

    肤色色相归 WB（卡级、引擎解码）；LR 里不再用 HSL 拧色相。
    Point Color 的 SatScale 是**相对饱和刻度**，用肤色 C/L 关系给量：

        r0 = C0 / L0（这张 ①）
        r* = C* / L*（卡/peer）
        e  = r*/r0 − 1      ⇒ SatScale = clamp(e, −1, 1)

    这条色票同时是**局部肤色蒙版**的载体（建在 subject 组里，收窄到肤色）。
    """
    out = dict(point_color=None, split={})
    sk = _skin_of(rec)
    t = (target or {}).get('skin') or {}
    C0, L0 = sk.get('C'), sk.get('L')
    tC, tL = _num(t.get('C')), _num(t.get('L'))
    rgb = sk.get('rgb')
    if rgb:
        hh, ll, ss = colorsys.rgb_to_hls(*(float(v) / 255.0 for v in rgb[:3]))
        sat_scale = 0.0
        raw = 0.0
        if (C0 is not None and L0 is not None and L0 > 1e-3
                and tC is not None and tL is not None and tL > 1e-3):
            r0 = float(C0) / float(L0)
            rt = float(tC) / float(tL)
            raw = rt / r0 - 1.0
            if abs(raw) >= 0.02:
                # SDK 文档：SatScale 是 -1..1 连续量；直接用 C/L 关系，不叠加增益/量化
                sat_scale = round(max(-1.0, min(1.0, raw)), 3)
            ledger.append('Point Color（肤色蒙版）C/L %.3f → 卡 %.3f（差 %+.1f%%）'
                          ' ⇒ SatScale %+.3f' % (r0, rt, raw * 100.0, sat_scale))
        else:
            ledger.append('缺 C/L 关系 ⇒ Point Color 只建蒙版、SatScale 0')
        out['point_color'] = PointColor(
            h=round(float(hh), 4), s=round(float(ss), 4), l=round(float(ll), 4),
            range=0.5, sat_scale=sat_scale, raw=round(float(raw), 3),
            rgb=[int(v) for v in rgb[:3]])
    else:
        ledger.append('缺 skin_rgb ⇒ 没有肤色蒙版/Point Color（记账）')
    # ---- 冷暖/分色：只处理卡里**近中性的暖/冷方向**；场景色不做全局分色
    tone = (card or {}).get('tone') or {}
    wc_t = _num((target or {}).get('warm_cool_b'))
    wc_c = _num(rec.get('warm_cool_b'))
    if wc_t is not None and wc_c is not None and abs(wc_t - wc_c) >= 0.5:
        hi = tone.get('highlight') or {}
        sh = tone.get('shadow') or {}

        def _node(node):
            """(hue, C*)；不是近中性/不在暖冷轴 ⇒ None（不做分色）。"""
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
            ledger.append('分色跳过：卡高光/暗部不是近中性暖冷（高光 C=%.2f h=%s，'
                          '暗部 C=%.2f h=%s）⇒ 场景色不当色偏'
                          % (math.hypot(*(hi.get('lab') or [0, 0, 0])[1:]) if hi.get('lab') else 0.0,
                             (hi.get('hsl_h') if hi.get('hsl_h') is not None else '—'),
                             math.hypot(*(sh.get('lab') or [0, 0, 0])[1:]) if sh.get('lab') else 0.0,
                             (sh.get('hsl_h') if sh.get('hsl_h') is not None else '—')))
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
        ledger.append('暖冷 %.2f ≈ 卡 %.2f ⇒ 不分色' % (wc_c, wc_t))
    return out


# ---------------------------------------------------------------- 局部（肤色蒙版）

def _apply_plan_to_L(plan: LRPlan, L: float) -> float:
    """把已写进 plan 的 Basic/曲线作用到一个 L*（预测用，不是测量）。"""
    v = l_after_ev(float(L), plan.exposure_ev)
    for name, ui in (('contrast', plan.contrast), ('highlights', plan.highlights),
                     ('shadows', plan.shadows), ('whites', plan.whites),
                     ('blacks', plan.blacks)):
        if abs(float(ui or 0.0)) > 1e-9:
            Lc = max(R.L_DOMAIN[0], min(R.L_DOMAIN[1], v))
            v += (R.k_tone(name, Lc) or 0.0) * float(ui) / 100.0
    return apply_lut(plan.curve, v)


def local_face(plan: LRPlan, rec: dict, target: dict, ledger: List[str]) -> None:
    """补脸：用已知路径 **subject 组 + 局部 Point Color 肤色票 + LocalExposure**。

    昨天已定：SDK 没有 facialSkin，people 建不出；subject + 肤色票是最近实现。
    这条路径的边界：Point Color 的 RangeAmount 较宽时，大剂量会带到白衫；
    所以局部只该做“关系”，+2 档这种属于曝光救援，不该靠局部硬拉。
    """
    sk = _skin_of(rec)
    L0 = sk.get('L')
    tL = _num((target or {}).get('skin', {}).get('L'))
    if L0 is None or tL is None:
        ledger.append('缺脸 L 或卡/peer 的 face_L ⇒ 局部不动（记账）')
        return
    pred = _apply_plan_to_L(plan, L0)
    ev = ev_for_l(pred, tL)
    if abs(ev) < 0.05:
        ledger.append('脸 L 预测 %.1f ≈ 目标 %.1f ⇒ 局部不动' % (pred, tL))
        return
    plan.local = Local(mask='skin', exposure_ev=round(float(ev), 3))
    ledger.append('局部（subject + 肤色票）补脸 %+.2f 档：预测 %.1f → 目标 %.1f'
                  % (ev, pred, tL))


# ---------------------------------------------------------------- 主流程

def plan_batch(recs: Dict[str, dict], card: dict,
               peer_targets: Optional[Dict[str, dict]] = None,
               log=print) -> Dict[str, LRPlan]:
    """逐图出 LRPlan。`recs` = ① 的测量；`peer_targets` = `design.peer.build`。"""
    led0: List[str] = []
    wb = wb_of(card, recs, led0)
    for line in led0:
        log('  ' + line)
    out: Dict[str, LRPlan] = {}
    for stem, rec in recs.items():
        ledger: List[str] = []
        pt = (peer_targets or {}).get(stem) or {}
        skt = skin_target(pt, card)
        shape = tone_target(pt, card, ledger)
        p50 = _num((rec.get('img') or {}).get('p50'))
        target = anchor_target(shape, p50, ledger) if shape else None
        if target and shape:
            target.update(skin=skt['skin'], warm_cool_b=skt['warm_cool_b'],
                          source=shape.get('source'), peer_n=shape.get('peer_n'))
        plan = LRPlan()
        plan.wb = wb
        tone = basic_tone(rec, target, ledger)
        plan.exposure_ev = float(tone['ev'])
        for k in ('contrast', 'highlights', 'shadows', 'whites', 'blacks'):
            setattr(plan, k, float((tone.get('ui') or {}).get(k) or 0.0))
        plan.curve = curve_for(tone['pred'], target) if target else None
        if plan.curve:
            ledger.append('曲线 %d 点（补 Basic 之后的形状残差；不动 p50）'
                          % len(plan.curve))
        # ---- 颜色/肤色（即使明暗不动也做）
        target_skin = dict(skt['skin'])
        target_wc = skt['warm_cool_b']
        g = grading(card, rec, dict(skin=target_skin, warm_cool_b=target_wc), ledger)
        if g.get('point_color') is not None:
            plan.point_color = g['point_color']
        sp = g.get('split') or {}
        plan.split_shadow_hue = float(sp.get('shadow_hue') or 0.0)
        plan.split_shadow_sat = float(sp.get('shadow_sat') or 0.0)
        plan.split_highlight_hue = float(sp.get('highlight_hue') or 0.0)
        plan.split_highlight_sat = float(sp.get('highlight_sat') or 0.0)
        plan.split_balance = float(sp.get('balance') or 0.0)
        # ---- 局部（脸）：必须在全局明暗成立之后；target=None 说明背景没成立，
        #      不能再靠局部补脸（否则 LR 里就只剩“蒙版+亮度”，背景没人管）。
        if target is not None:
            local_face(plan, rec, dict(skin=target_skin), ledger)
        else:
            ledger.append('全局明暗未成立（卡/批不匹配）⇒ 局部不补脸；'
                          '先解决背景/换一张形状匹配的卡')
        # ---- 记账：预测 vs 目标（只描述，不判）
        sk = _skin_of(rec)
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
        plan.card_dev = dict(
            p50=round(pred.get('p50', 0.0) - float(target['q50']), 2)
            if (target and pred.get('p50') is not None) else None,
            p95=round(pred.get('p95', 0.0) - float(target['q95']), 2)
            if (target and pred.get('p95') is not None) else None,
            face_L=round(face_pred - float(tsk['L']), 2)
            if (face_pred is not None and tsk.get('L') is not None) else None,
            face_C=round(c_pred - float(tsk['C']), 2)
            if (c_pred is not None and tsk.get('C') is not None) else None,
            warm_cool_b=round(float(_num(rec.get('warm_cool_b')) or 0.0)
                              - float(target_wc), 2)
            if (rec.get('warm_cool_b') is not None and target_wc is not None) else None)
        if target:
            plan.target = dict(source=target.get('source'), peer_n=target.get('peer_n'),
                               q5=round(target['q5'], 2), q25=round(target['q25'], 2),
                               q50=round(target['q50'], 2), q75=round(target['q75'], 2),
                               q95=round(target['q95'], 2),
                               skin={k: (round(v, 2) if isinstance(v, (int, float))
                                         else v) for k, v in tsk.items()})
        plan.notes = dict(pred=pred, face_pred=face_pred,
                          framing=pt.get('framing'), peer_ids=pt.get('peer_ids') or [],
                          peer_n=pt.get('peer_n') or 0)
        plan.ledger = ledger
        out[stem] = plan
    return out


# ---------------------------------------------------------------- 人读汇总

def _f(v, fmt='%.1f', dash='—'):
    return dash if v is None else fmt % float(v)


def summary(plans: Dict[str, LRPlan], card_name: str = '',
            pe: Optional[float] = None) -> str:
    """一图一行的账：WB / PE / 明暗 / 肤色 / 分色 / 局部 / 卡偏差。"""
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
                 % ('stem', '明暗（Basic / 曲线）', '肤色（点颜色 SatScale）',
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
                         ('face_C', '脸C')):
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
    ap.add_argument('--cards', default=r'E:\svStudio\svFilm\analysis\corpus\kurai\cards_v6.json')
    a = ap.parse_args(argv)
    d = json.load(open(a.cards, encoding='utf-8'))
    card = next((c for c in d['cards'] if c['name'] == a.card), None)
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
