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

from .curve_design import apply_lut, curve_from_shape
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
    """⑧ 彩色：饱和 + 分离色调（量级由卡的色彩带决定，不拍脑袋）。

    · 肤色保护：我们的脸 C 低于卡的脸 C 带 ⇒ 补饱和；高于 ⇒ 收一点
    · 分离色调：卡的暖冷（warm_cool_b 带中点）为正 ⇒ 高光暖、阴影冷；为负则相反
      —— 这是"模仿大师的色调分离"，不是随便给个色相
    """
    b = (card or {}).get('bands') or {}
    out = dict(saturation=0.0, vibrance=0.0,
               split_highlight_hue=0.0, split_highlight_sat=0.0,
               split_shadow_hue=0.0, split_shadow_sat=0.0, split_balance=0.0)
    cb = b.get('face_C')
    sk = (rec.get('skin') or {}).get('C')
    if isinstance(cb, list) and sk is not None:
        lo, hi = float(cb[0]), float(cb[1])
        if sk < lo:
            out['saturation'] = float(min(12.0, (lo - sk) * 3.0))
            out['vibrance'] = float(min(8.0, (lo - sk) * 2.0))
        elif sk > hi:
            out['saturation'] = float(max(-8.0, (hi - sk) * 2.0))
    wc = b.get('warm_cool_b')
    if isinstance(wc, list):
        mid = 0.5 * (float(wc[0]) + float(wc[1]))
        warm = mid >= 0
        out['split_highlight_hue'] = 45.0 if warm else 210.0
        out['split_shadow_hue'] = 210.0 if warm else 45.0
        out['split_highlight_sat'] = float(min(8.0, 4.0 + abs(mid) * 0.5))
        out['split_shadow_sat'] = float(min(6.0, 3.0 + abs(mid) * 0.4))
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
    return dict(need=need, p95=p95, p95_after_curve=p95_res, clip=p_clip, person_clip=p_clip,
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
    curve = curve_from_shape(bands.get('shape') or {})
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
        if curve:
            for _k in ('face_L', 'person_L'):
                if meas.get(_k) is not None:
                    meas[_k] = apply_lut(curve, float(meas[_k]))
            if meas.get('p99') is not None:
                meas['p99'] = apply_lut(curve, float(meas['p99']))
        p: Plan = solve_pass(meas, card_in)

        # ★ 脸是**一级目标**（人像优先）：曲线把脸压暗了，⑦ 必须补回来。
        _fl = meas.get('face_L')
        _fb = card_in.get('face_L')
        if _fl is not None and _fb:
            from .solver import eval_slope as _slope
            _tgt = max(float(_fb[0]), 55.0)        # T4 下界 55；卡的带更宽时取高的那个
            if float(_fl) < _tgt - 0.5:
                _need = (_tgt - float(_fl)) / max(_slope(float(_fl)), 1e-6)
                if _need > p.local_ev:
                    p.ledger.append(Entry(dim='face_L', got=float(_fl), want=(_tgt, 1e9),
                                          gap=float(_tgt - float(_fl)), layer='local',
                                          action='脸低 => 局部 %.2f -> %.2f' % (p.local_ev, _need)))
                    p.local_ev = round(float(_need), 3)
                    p.local_exposure = round(p.local_ev / 4.0, 5)

        # ★ 补光的**可算上限** —— 不许把人的亮部（p99）推过 P95_MAX。
        #   p99_new = p99 + eval_slope(p99) · EV => EV <= (P95_MAX - p99) / eval_slope(p99)
        _cap_hi = min(PHYS_P95, _hi((card or {}).get('bands', {}).get('p95'), PHYS_P95))
        _p99 = meas.get('p99') or (rec.get('person') or {}).get('clip_p95')
        if _p99 is not None and p.local_ev > 0:
            from .solver import eval_slope
            _cap = max(0.0, (_cap_hi - float(_p99)) / max(eval_slope(float(_p99)), 1e-6))
            if p.local_ev > _cap:
                p.ledger.append(Entry(dim='local_cap', got=float(_p99), want=(0.0, _cap_hi),
                                      gap=float(p.local_ev - _cap), layer='local',
                                      action='补光 %.2f -> %.2f（人的亮部不许过卡的 %.0f）'
                                             % (p.local_ev, _cap, _cap_hi)))
                p.local_ev = round(_cap, 3)
                p.local_exposure = round(p.local_ev / 4.0, 5)
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
