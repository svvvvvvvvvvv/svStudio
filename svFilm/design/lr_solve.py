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

from .solver import Plan, solve_pass

# T1 的白端门（成片）——与设计文档 §4.1 一致
P95_MAX = 93.0
CLIP_MAX = 0.5


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


def _meas_of(rec: dict) -> Dict[str, object]:
    """① 的测录 → solve_pass 需要的形状。"""
    im = rec.get('img') or {}
    sh = rec.get('shape') or {}
    sk = rec.get('skin') or {}
    bgr = rec.get('bg') or {}
    per = rec.get('person') or {}
    return dict(bg_hi=bgr.get('L_p90'), bg_med=bgr.get('L'), person_L=per.get('L'),
                p50=im.get('p50'), p95=im.get('p95'), p5=im.get('p5'),
                face_L=sk.get('L'),
                shape=dict(toe=sh.get('toe'), shoulder=sh.get('shoulder'),
                           contrast_win=sh.get('contrast_win'), skew=sh.get('skew')))


def white_end_plan(rec: dict) -> Dict[str, object]:
    """③ 白端：① 的 p95/clip 说明要不要压、压多少（方向 + 一档）。

    半可算（Whites 无公开传递函数）⇒ 只给**方向与档级**，出片后一次验（§52.1）。
    """
    im = rec.get('img') or {}
    p95, clip = im.get('p95'), im.get('clip_pct')
    need = None
    if p95 is not None and p95 > P95_MAX:
        need = 'down'
    if clip is not None and clip >= CLIP_MAX:
        need = 'down'
    return dict(need=need, p95=p95, clip=clip, gate=dict(p95_max=P95_MAX, clip_max=CLIP_MAX))


def plan_batch(recs: Dict[str, dict], card: dict) -> Dict[str, dict]:
    """逐图出计划：曝光（整幅）· 曲线（形状）· 局部（人）· 白端（③）。"""
    bands = bands_of(card)
    shape_band = bands.get('shape') or {}
    face_band = bands.get('face_L')
    out: Dict[str, dict] = {}
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

        p: Plan = solve_pass(meas, card_in)
        out[stem] = dict(plan=p.as_dict(), white=white_end_plan(rec),
                         meas=meas, ledger=[str(e) for e in p.ledger])
    return out


def summary(plans: Dict[str, dict], card_name: str = '') -> str:
    lines = ['【LR 计划】卡 %s' % card_name]
    lines.append('%-10s %8s %8s  %-22s %s' % ('stem', '曝光EV', '局部EV', '曲线', '白端'))
    for stem, p in sorted(plans.items()):
        pl = p['plan']
        w = p['white']
        lines.append('%-10s %+8.2f %+8.2f  %-22s %s(p95 %.1f / clip %.2f)'
                     % (stem, pl['exposure_ev'], pl['local_ev'],
                        ','.join('%s%+.1f' % (k, v) for k, v in (pl.get('curve') or {}).items()) or '—',
                        w['need'] or '不动', w['p95'] or 0, w['clip'] or 0))
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
