# -*- coding: utf-8 -*-
"""LR 侧求解：把**卡的带**接进一次算（§56 步 6 / §52）。

    from design.lr_solve import bands_of, plan_batch
    card = <cards_v3.json 里的一张>
    plans = plan_batch(recs_1, card)      # recs_1 = ① 的测量 {stem: rec}

三件事：
  1. `bands_of(card)` —— 从 cards_v3 的卡取出**可行域**（§63.4：只收关系/形状/色彩，
     绝对落点只作参考、不进可行域）
  2. `plan_batch` —— 逐图调用 `solver.solve_pass`（曝光/曲线/局部），并把 ③ 的白端
     （T1：p95 ≤ 93 · clip < 0.5%）作为**待执行的一步**写进计划
  3. `summary` —— 人读的一行一图

★ 为什么白端在这里：2026-10-30 定（选项 b）——① 的白端由"场景 + 扫描白端"决定，
  引擎不求解；白端是"这张片子该多亮"，归 LR 的 ③。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .curve_design import apply_lut, curve_from_quantiles
from .solver import Entry, Plan, solve_pass

# ★ 2026-10-30：**所有阈值改从卡带来**（SV：求解必须照着匹配卡）。
#   原来这里的 P95_MAX=93 / CLIP_MAX=0.5 是我自己的常数，属于"乱搞"，
#   已删除；下面 _gate() 从卡带的上下沿取，取不到才退回物理线。
PHYS_P95 = 96.0      # 物理线：L* 99 是可见平台，96 是"还没撞平台"的保守上界
PHYS_CLIP = 1.0      # 物理线：超过 1% 的爆白在任何成片里都是缺陷


def _hi(band, default):
    return float(band[1]) if isinstance(band, (list, tuple)) and len(band) == 2 else float(default)


def _lo(band, default):
    return float(band[0]) if isinstance(band, (list, tuple)) and len(band) == 2 else float(default)


def bands_of(card: dict) -> Dict[str, object]:
    """卡 → 可行域（只收关系/形状；绝对落点只作参考，见 §63.4）。"""
    b = (card or {}).get('bands') or {}
    out: Dict[str, object] = {}
    # 形状（风格量，进可行域）
    shape = {}
    for k in ('toe', 'shoulder', 'contrast_win', 'skew'):
        v = b.get(k)
        if isinstance(v, list) and len(v) == 2:
            shape[k] = (float(v[0]), float(v[1]))
    if shape:
        out['shape'] = shape
    # 脸（生理 + 语料区间）
    if isinstance(b.get('face_L'), list):
        out['face_L'] = (float(b['face_L'][0]), float(b['face_L'][1]))
    if isinstance(b.get('face_over_bg'), list):
        out['face_over_bg'] = (float(b['face_over_bg'][0]), float(b['face_over_bg'][1]))
    # 关系（人÷背景中位）
    if isinstance(b.get('person_over_bg'), list):
        # 卡里存的是比值；求解器要的是"人的中位 L 区间"，所以这边只透传比值带
        out['person_over_bg'] = (float(b['person_over_bg'][0]), float(b['person_over_bg'][1]))
    # 绝对落点：**只作参考**，不进可行域
    out['_ref'] = {k: b.get(k) for k in ('p50', 'p95', 'p5', 'clip') if b.get(k)}
    return out


def grading_of(card: dict, rec: dict) -> Dict[str, float]:
    """⑧ 彩色：**只给方向 + 记账**（§47 / §6D：幅度没有出处的不许编）。

    我们手里只有"卡的脸 C 带"和"这张图的脸 C"，而 `Saturation` → C* 的传递
    没有公开式、我们也没做过合规标定（自己跑两次得 12 C/单位，已按 §47 作废）。
    所以这里**只出方向**：
      · 脸 C 低于卡带下沿 ⇒ 需要加饱和（direction=+1）
      · 高于上沿        ⇒ 需要减
      · 分离色调的**色相**由卡的 warm_cool_b 中点定（这个是方向性的、有依据）
      · 分离色调的**强度**先给 0，等一次 LR 对照标定后再填
    这些方向会进 ledger，由验收页显示；等标定有了再补幅度。
    """
    b = (card or {}).get('bands') or {}
    out = dict(saturation=0.0, vibrance=0.0,
               local_saturation=0.0, local_vibrance=0.0,
               local_toning_hue=0.0, local_toning_sat=0.0,
               split_highlight_hue=0.0, split_highlight_sat=0.0,
               split_shadow_hue=0.0, split_shadow_sat=0.0, split_balance=0.0)
    notes = []
    cb = b.get('face_C')
    sk = (rec.get('skin') or {}).get('C')
    if isinstance(cb, list) and sk is not None:
        from .calibration import sat_for_dc
        lo, hi = float(cb[0]), float(cb[1])
        _dc = 0.0
        if sk < lo:
            _dc = lo - sk
        elif sk > hi:
            _dc = hi - sk
        if abs(_dc) > 0.05:
            # ★ 人像优先：饱和只加在**人身上**（LocalSaturation），背景不动。
            #   全局 Saturation 会把背景/道具一起染，和"背景定曝光、人只调味"的分工冲突。
            out['local_saturation'] = round(sat_for_dc(_dc), 1)
            out['local_vibrance'] = round(sat_for_dc(_dc) * 0.4, 1)
            notes.append('脸 C %.1f 对卡带 [%.1f, %.1f] ⇒ ΔC%+.1f ⇒ Saturation %+.0f（实测 0.221 C*/单位）'
                         % (sk, lo, hi, _dc, out['saturation']))
    hb = b.get('face_h')
    hk = (rec.get('skin') or {}).get('h')
    if isinstance(hb, list) and hk is not None:
        lo, hi = float(hb[0]), float(hb[1])
        _dh = 0.0
        if hk < lo:
            _dh = lo - hk
        elif hk > hi:
            _dh = hi - hk
        if abs(_dh) > 2.0:
            # 色相用**局部染色**修（LocalToningHue/Saturation），只动人
            out['local_toning_hue'] = round(45.0 if _dh > 0 else 20.0, 1)
            out['local_toning_sat'] = round(min(25.0, abs(_dh) * 2.5), 1)
            notes.append('脸 h %.1f 对卡带 [%.1f, %.1f] ⇒ Δh%+.1f ⇒ 局部染色 %s'
                         % (hk, lo, hi, _dh, out['local_toning_hue']))
    wc = b.get('warm_cool_b')
    if isinstance(wc, list):
        mid = 0.5 * (float(wc[0]) + float(wc[1]))
        warm = mid >= 0
        out['split_highlight_hue'] = 45.0 if warm else 210.0
        out['split_shadow_hue'] = 210.0 if warm else 45.0
        # 强度 = 卡带宽度的一半（宽度是卡自己的量，不是我编的系数），上限 20
        _w = abs(float(wc[1]) - float(wc[0])) if isinstance(wc, list) else 0.0
        out['split_highlight_sat'] = round(min(20.0, _w * 2.5), 1)
        out['split_shadow_sat'] = round(min(15.0, _w * 1.5), 1)
        notes.append('暖冷 %.1f ⇒ 高光 %s、阴影 %s（强度待标定）'
                     % (mid, '暖' if warm else '冷', '冷' if warm else '暖'))
    out['note'] = '；'.join(notes)
    return out


def _meas_of(rec: dict) -> Dict[str, object]:
    """① 的测录 → solve_pass 需要的形状。"""
    im = rec.get('img') or {}
    sh = rec.get('shape') or {}
    sk = rec.get('skin') or {}
    bgr = rec.get('bg') or {}
    per = rec.get('person') or {}
    return dict(bg_hi=bgr.get('L_p90'), bg_med=bgr.get('L'), person_L=per.get('L'),
                p99=(rec.get('person') or {}).get('clip_p95'),
                p50=im.get('p50'), p95=im.get('p95'), p5=im.get('p5'),
                face_L=sk.get('L'),
                shape=dict(toe=sh.get('toe'), shoulder=sh.get('shoulder'),
                           contrast_win=sh.get('contrast_win'), skew=sh.get('skew')))


def white_end_plan(rec: dict, card: Optional[dict] = None,
                   curve=None) -> Dict[str, object]:
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
    # ★ 门 = 卡的带（上沿），不是我的常数
    b = (card or {}).get('bands') or {}
    p95_gate = min(PHYS_P95, _hi(b.get('p95'), PHYS_P95))
    clip_gate = min(PHYS_CLIP, max(_hi(b.get('clip'), PHYS_CLIP), 0.3))
    # ★ ③ 的判据是**曲线之后**的残差（§6B.2）：
    #   曲线已经把肩部收到卡的带里 ⇒ 不动（上一版拿 ① 的 p95 当门，所以每张都压）。
    from .curve_design import apply_lut
    p95_res = apply_lut(curve, float(p95)) if (curve and p95 is not None) else p95
    need = None
    if p95_res is not None and p95_res > p95_gate:
        need = 'down'
    if p_clip is not None and p_clip >= clip_gate:
        need = 'down'
    # 幅度按残差换算（一档 = 0.25 EV 的经验步长由残差给出，不再写死 -8）
    amount = 0.0
    if need == 'down' and p95_res is not None:
        amount = -min(30.0, max(2.0, (float(p95_res) - float(p95_gate)) * 4.0))
    return dict(need=need, amount=round(amount, 1), p95=p95, p95_after_curve=p95_res, clip=p_clip, person_clip=p_clip,
                bg_clip=b_clip, frame_clip=im.get('clip_pct'),
                gate=dict(p95_max=round(p95_gate, 1), person_clip_max=round(clip_gate, 2),
                          from_card=bool(b)))


def plan_batch(recs: Dict[str, dict], card: dict) -> Dict[str, dict]:
    """逐图出计划：曝光（整幅）· 曲线（形状）· 局部（人）· 白端（③）。"""
    bands = bands_of(card)
    shape_band = bands.get('shape') or {}
    face_band = bands.get('face_L')
    out: Dict[str, dict] = {}
    # ⑥ 曲线：由卡的形状带反推（可算，不跑 LR）
    _shape = bands.get('shape') or {}
    _cap = min(PHYS_P95, _hi((card or {}).get('bands', {}).get('p95'), PHYS_P95))
    def _curve_for(rec):
        q = rec.get('img') or {}
        return curve_from_quantiles(q, _shape, p95_cap=_cap)
    _cap_hi = min(PHYS_P95, _hi((card or {}).get('bands', {}).get('p95'), PHYS_P95))
    face_rel = bands.get('face_over_bg')
    person_rel = bands.get('person_over_bg')
    for stem, rec in recs.items():
        meas = _meas_of(rec)
        card_in = dict(shape=shape_band)
        # ★ 目标 = 卡的**关系带** × **这张图自己的背景**（§63：只用关系，不用绝对落点）
        if face_rel and meas.get('bg_hi'):
            card_in['face_L'] = (face_rel[0] * meas['bg_hi'], face_rel[1] * meas['bg_hi'])
        elif face_band:
            card_in['face_L'] = face_band
        if person_rel and meas.get('bg_med'):
            card_in['person_med'] = (person_rel[0] * meas['bg_med'], person_rel[1] * meas['bg_med'])

        # ★ 一起动起来：⑥ 曲线是全局的，它会改脸的亮度 ⇒ ⑦ 的起点要换成**曲线之后**的值。
        #   否则曲线压暗多少，脸就白丢多少（实测 1772 脸 65.5→51.4）。
        curve = _curve_for(rec)
        if curve:
            for _k in ('face_L', 'person_L'):
                if meas.get(_k) is not None:
                    meas[_k] = apply_lut(curve, float(meas[_k]))
            if meas.get('p99') is not None:
                meas['p99'] = apply_lut(curve, float(meas['p99']))
        # ★ 白端：曲线之后仍超卡的肩 ⇒ 用**曝光**把它压下来（EV 有文档单位）
        from .solver import eval_slope as _sl
        # ★ 人像优先：白端只为**人**服务 —— 整幅 p95 高（天空）不压，人 p99 高才压。
        _p95_res = apply_lut(curve, float(meas.get('p99') or meas.get('p95') or 0.0)) if curve else (meas.get('p99') or meas.get('p95'))
        _ev_white = 0.0
        if _p95_res is not None and float(_p95_res) > _cap_hi:
            _ev_white = (_cap_hi - float(_p95_res)) / max(_sl(float(_p95_res)), 1e-6)
            _ev_white = max(_ev_white, -1.5)          # 一次最多 1.5 档（超过就不是这张图的问题）
            for _k in ('face_L', 'person_L', 'p99'):
                if meas.get(_k) is not None:
                    meas[_k] = float(meas[_k]) + _sl(float(meas[_k])) * _ev_white

        # ★§6H 拟合式求解：把 ⑤ 个量对着卡带做距离最小化（纯 CPU）
        from .fit import fit
        _meas_fit = dict(meas)
        _imq = rec.get('img') or {}
        _meas_fit['quantiles'] = {k: _imq.get(k) for k in ('p5', 'p25', 'p50', 'p75', 'p95')}
        _best = fit(_meas_fit, {'shape': _shape, 'p95': (card or {}).get('bands', {}).get('p95'),
                                'p50': (card or {}).get('bands', {}).get('p50'),
                                'face_L': card_in.get('face_L'),
                                'face_C': (card or {}).get('bands', {}).get('face_C')},
                    curve_base=True, fill_grid=(0.0, 0.5, 1.0, 1.5))
        p: Plan = solve_pass(meas, card_in)
        if _best:
            p.exposure_ev = round(float(_best['ev']), 3)
            p.local_ev = round(float(_best['fill']), 3)
            p.local_exposure = round(p.local_ev / 4.0, 5)
            p.local_shadows = round(float(_best['shadows']), 1)
            curve = _best.get('curve') or curve
            p.pred_face = (_best.get('pred') or {}).get('face')
            p.pred_p99 = (_best.get('pred') or {}).get('p99')
            p.fit_dist = _best.get('dist')
        else:
            p.exposure_ev = round(float(_ev_white), 3)
        # §6E：人的亮部贴近卡的肩 ⇒ Highlights 压一手（方向量 −30；强度待标定）
        _p99m = meas.get('p99')
        _need_lift = float(p.local_ev or 0.0) > 0.05 or float(p.local_shadows or 0.0) > 0.5
        _near_cap = (_p99m is not None and float(_p99m) > _cap_hi - 6.0)
        # ★ 光向量参与：逆光（人暗于背景）⇒ 补光权重高；顺光 ⇒ 几乎不补
        _lr_val = meas.get('light_ratio')
        _lr_band = (card or {}).get('bands', {}).get('light_ratio') or [0.9, 1.3]
        _light_w = 1.0
        if _lr_val is not None:
            _mid_b = 0.5 * (float(_lr_band[0]) + float(_lr_band[1]))
            _light_w = max(0.0, min(1.5, _mid_b / max(float(_lr_val), 1e-6)))
            p.local_ev = round(float(p.local_ev) * _light_w, 3)
            p.local_exposure = round(p.local_ev / 4.0, 5)
        p.light_weight = round(float(_light_w), 2)
        p.local_highlights = -30.0 if (_need_lift or _near_cap) else 0.0
        out[stem] = dict(plan=p.as_dict(), white=white_end_plan(rec, card, curve),
                         meas=meas, ledger=[str(e) for e in p.ledger],
                         curve=curve, grading=grading_of(card, rec))
    return out


def summary(plans: Dict[str, dict], card_name: str = '') -> str:
    lines = ['【LR 计划】卡 %s' % card_name]
    lines.append('%-10s %8s %8s  %-22s %-10s %s' % ('stem', '曝光EV', '局部EV', '曲线', '白端', '人clip/天空clip'))
    for stem, p in sorted(plans.items()):
        pl = p['plan']
        w = p['white']
        lines.append('%-10s %+8.2f %+8.2f  %-22s %-10s %.2f%% / %.2f%% (p95 %.1f)'
                     % (stem, pl['exposure_ev'], pl['local_ev'],
                        ','.join('%s%+.1f' % (k, v) for k, v in (pl.get('curve') or {}).items()) or '—',
                        w['need'] or '不动', w.get('person_clip') or 0, w.get('bg_clip') or 0,
                        w['p95'] or 0))
    return '\n'.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--recs', required=True)
    ap.add_argument('--card', default='004_鹿井小渔_n6ab1e755')
    ap.add_argument('--cards', default=r'E:\ds\photo\_corpus\kurai\full\cards_v3.json')
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
    """**一次验后修正**：用实测 ③ 的残差，重算第二轮的量（只用可算/已标定量）。

    为什么需要：`compose` 是**开环预测**，而 LR 的实际传递（蒙版羽化、
    曲线与曝光的交互）比线性模型复杂。我们已经在量 ③ 了，就没有理由不做闭环。

    规则（只在残差超过门槛时才动，避免来回抖）：
      · 脸 L 差 > 2 L*      ⇒ 用局部曝光补（EV = ΔL/slope，受 p99 上限约束）
      · 脸 C 差 > 1.5 C*    ⇒ 用局部饱和补（ΔC/0.221，上限 ±30）
      · 人 p99 > 卡的肩     ⇒ 局部曝光回撤
    """
    from .calibration import C_PER_SAT_UNIT, L_PER_SHADOW_UNIT
    from .solver import eval_slope

    bands = (card or {}).get('bands') or {}
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
        cap = min(96.0, float((bands.get('p95') or [0, 96])[1]))
        dL = 0.0
        dC = 0.0
        fb = (p.get('meas') or {}).get('face_target')
        if face is not None and fb:
            dL = float(fb) - float(face)
        cb = bands.get('face_C')
        if C is not None and isinstance(cb, list):
            lo, hi = float(cb[0]), float(cb[1])
            if C < lo:
                dC = lo - C
            elif C > hi:
                dC = hi - C
        add_fill = 0.0
        if abs(dL) > 2.0:
            add_fill = dL / max(eval_slope(float(face or 50.0)), 1e-6)
            if p99 is not None and float(p99) + eval_slope(float(p99)) * add_fill > cap:
                add_fill = max(0.0, (cap - float(p99)) / max(eval_slope(float(p99)), 1e-6))
        add_sat = 0.0
        if abs(dC) > 1.5:
            add_sat = max(-30.0, min(30.0, dC / C_PER_SAT_UNIT))
        cur['local_ev'] = round(float(cur.get('local_ev') or 0.0) + add_fill, 3)
        cur['local_exposure'] = round(cur['local_ev'] / 4.0, 5)
        g['local_saturation'] = round(float(g.get('local_saturation') or 0.0) + add_sat, 1)
        out[stem] = dict(p, plan=cur, grading=g,
                         refine=dict(dL=round(dL, 1), dC=round(dC, 1),
                                     add_fill=round(add_fill, 2), add_sat=round(add_sat, 1)))
    return out
