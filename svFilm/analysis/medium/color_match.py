# -*- coding: utf-8 -*-
"""按**颜色**给 64 张卡选卷/纸配方（颜色优先，目标 = 大师作品的颜色特征）。

两侧同口径（`卷纸测量.md` §3.4 的定义）：
  肤色   face L/C/h     （卡：`skin` 中位；配方：作者 test 人像 face 区）
  树叶   leaf a/b/C/h   （卡：`hue_bands` 60–120° 绿带的 C；配方：leaf 区）
  冷暖   整幅 b*        （卡：`warm_cool_b`；配方：整幅 a/b 均值）
  色偏   中性面 a*/b* @ L*=10/30/50/70/90（卡：`neutral_axis` 中位；配方：灰阶条插值）
  明暗   contrast / face L（**最后**考虑，颜色优先）

距离 = 每项按「配方之间的离散」归一化后加权求和。
输出：out/color_assign.json（{卡 key: 配方码}）+ out/color_match.json（明细）

用法：
  python analysis/medium/color_match.py
  python analysis/medium/color_match.py --write-assign   # 写 assignments_color.json，供 apply_assignments
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

TONE = common.SVFILM / 'analysis' / 'corpus' / 'kurai' / 'photos_tone_v04.json'
LEVELS = [10.0, 30.0, 50.0, 70.0, 90.0]

RECIPE_PAIR = {
    'P160': ('kodak_portra_160', 'kodak_portra_endura'),
    'P400': ('kodak_portra_400', 'kodak_portra_endura'),
    'GOLD': ('kodak_gold_200', 'kodak_portra_endura'),
    'P800': ('kodak_portra_800', 'kodak_portra_endura'),
    'PUSH1': ('kodak_portra_800_push1', 'kodak_portra_endura'),
    'PUSH2': ('kodak_portra_800_push2', 'kodak_portra_endura'),
    'GREEN': ('kodak_portra_400', 'fujifilm_crystal_archive_typeii'),
    'XTRA': ('fujifilm_xtra_400', 'fujifilm_crystal_archive_typeii'),
    'EKTAR': ('kodak_ektar_100', 'kodak_portra_endura'),
}
PAIR_TO_CODE = {v: k for k, v in RECIPE_PAIR.items()}

# 权重：颜色项在前，明暗只在最后
W = dict(skinC=1.0, skinh=0.9, greenC=0.9, greenb=0.6, wcb=0.7,
         cast=1.0, faceL=0.25, contrast=0.15)


def _med(xs):
    xs = [x for x in xs if x is not None]
    return float(np.median(xs)) if xs else None


def card_features():
    tone = json.load(open(TONE, encoding='utf-8'))['per_image']
    by = {}
    for r in tone:
        if r.get('role') != 'master' or r.get('err'):
            continue
        by.setdefault(r['card'], []).append(r)

    out = {}
    for card, rows in by.items():
        cast = {}
        for L in LEVELS:
            aa, bb = [], []
            for r in rows:
                for p in (r.get('neutral_axis') or []):
                    if abs(float(p.get('L', -99)) - L) < 1.5:
                        aa.append(p.get('a'))
                        bb.append(p.get('b'))
            cast[L] = (_med(aa), _med(bb))
        sk = [r.get('skin') for r in rows if r.get('skin')]
        gC, gb = [], []
        for r in rows:
            for band in (r.get('hue_bands') or []):
                if 60.0 <= float(band.get('h0', -1)) <= 120.0:
                    gC.append(band.get('C'))
                    gb.append(band.get('b') if 'b' in band else None)
        contrast = []
        for r in rows:
            im = r.get('img') or {}
            if im.get('p95') is not None and im.get('p5') is not None:
                contrast.append(float(im['p95']) - float(im['p5']))
        out[card] = dict(
            n=len(rows), cast=cast,
            skin_L=_med([s.get('L') for s in sk]),
            skin_C=_med([s.get('C') for s in sk]),
            skin_h=_med([s.get('h') for s in sk]),
            green_C=_med(gC), green_b=_med(gb),
            warm_cool_b=_med([r.get('warm_cool_b') for r in rows]),
            contrast=_med(contrast),
        )
    return out


def recipe_features(sig):
    out = {}
    for key, s in sig.items():
        code = PAIR_TO_CODE.get((s['stock'], s['paper']))
        if code is None:
            continue
        g = s['gray']
        ks = sorted(g)
        Ls = [g[k]['L'] for k in ks]
        As = [g[k]['a'] for k in ks]
        Bs = [g[k]['b'] for k in ks]
        cast = {L: (float(np.interp(L, Ls, As)), float(np.interp(L, Ls, Bs)))
                for L in LEVELS}
        out[code] = dict(code=code, stock=s['stock'], paper=s['paper'], cast=cast,
                         skin_L=s['face']['L'], skin_C=s['face']['C'],
                         skin_h=s['face']['h'],
                         green_C=s['leaf']['C'], green_b=s['leaf']['b'],
                         warm_cool_b=s.get('whole_b'),
                         contrast=s['contrast'])
    return out


def _sd(vals):
    v = np.array([x for x in vals if x is not None], float)
    return float(v.std()) if len(v) > 1 and v.std() > 1e-6 else 1.0


def dist(f, r, sd):
    d = 0.0
    # 色偏曲线（5 个亮度 × a/b）
    for L in LEVELS:
        ca, cb = f['cast'].get(L, (None, None))
        ra, rb = r['cast'][L]
        if ca is not None and cb is not None:
            d += W['cast'] * ((abs(ca - ra) + abs(cb - rb)) / 2.0) / sd['cast']
    if f.get('skin_C') is not None:
        d += W['skinC'] * abs(f['skin_C'] - r['skin_C']) / sd['skinC']
    if f.get('skin_h') is not None:
        dh = abs(f['skin_h'] - r['skin_h'])
        d += W['skinh'] * min(dh, 360 - dh) / sd['skinh']
    if f.get('green_C') is not None:
        d += W['greenC'] * abs(f['green_C'] - r['green_C']) / sd['greenC']
    if f.get('green_b') is not None and r.get('green_b') is not None:
        d += W['greenb'] * abs(f['green_b'] - r['green_b']) / sd['greenb']
    if f.get('warm_cool_b') is not None and r.get('warm_cool_b') is not None:
        d += W['wcb'] * abs(f['warm_cool_b'] - r['warm_cool_b']) / sd['wcb']
    if f.get('skin_L') is not None:
        d += W['faceL'] * abs(f['skin_L'] - r['skin_L']) / sd['faceL']
    if f.get('contrast') is not None:
        d += W['contrast'] * abs(f['contrast'] - r['contrast']) / sd['contrast']
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sig', default=str(common.OUT_DEFAULT / 'color_sig.json'))
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--write-assign', action='store_true')
    args = ap.parse_args()

    cards = card_features()
    rec = recipe_features(json.load(open(args.sig, encoding='utf-8')))
    codes = sorted(rec)

    sd = dict(
        cast=max(_sd([rec[c]['cast'][L][i] for c in codes for L in LEVELS for i in (0, 1)]), 0.3),
        skinC=_sd([rec[c]['skin_C'] for c in codes]),
        skinh=_sd([rec[c]['skin_h'] for c in codes]),
        greenC=_sd([rec[c]['green_C'] for c in codes]),
        greenb=_sd([rec[c]['green_b'] for c in codes]),
        wcb=_sd([rec[c]['warm_cool_b'] for c in codes if rec[c]['warm_cool_b'] is not None]),
        faceL=_sd([rec[c]['skin_L'] for c in codes]),
        contrast=_sd([rec[c]['contrast'] for c in codes]),
    )

    rows = []
    for card, f in sorted(cards.items()):
        scored = sorted((dist(f, rec[c], sd), c) for c in codes)
        best, second = scored[0], scored[1]
        rows.append(dict(card=card, recipe=best[1], dist=round(best[0], 3),
                         second=round(second[0], 3), gap=round(second[0] - best[0], 3),
                         stock=rec[best[1]]['stock'], paper=rec[best[1]]['paper']))

    from collections import Counter
    print('== 颜色匹配（%d 卡）' % len(rows))
    for k, v in Counter(r['recipe'] for r in rows).most_common():
        print('  %-6s %2d 张' % (k, v))
    gaps = [r['gap'] for r in rows]
    print('  与次名的差距：中位 %.2f ｜ >0.15 的 %d/%d' % (float(np.median(gaps)),
                                                       sum(1 for g in gaps if g > 0.15), len(gaps)))

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, 'color_match.json'), 'w', encoding='utf-8') as fh:
        json.dump(dict(weights=W, sd=sd, rows=rows), fh, ensure_ascii=False, indent=1)

    if args.write_assign:
        def key(name):
            if name.startswith('xhs'):
                return 'xhs'
            if name.startswith('滨田'):
                return '滨田'
            return name[:3]
        assign = {key(r['card']): r['recipe'] for r in rows}
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assignments_color.json')
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump(assign, fh, ensure_ascii=False, indent=1)
        print('写出', p, '（%d 条）' % len(assign))


if __name__ == '__main__':
    main()
