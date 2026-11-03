# -*- coding: utf-8 -*-
"""按**颜色**给 64 张卡选卷/纸配方（颜色优先，目标 = 大师作品的颜色特征）。

口径（两侧同定义，才可比）：
  · 色偏曲线：中性面在 L*≈10/30/50/70/90 处的 a*/b*（卡：`photos_tone_v04` 的
    `neutral_axis` 中位；配方：灰阶条渲出来的 a*/b* 插值到同样的 L*）
  · 肤色：L/C/h（卡：`skin` 中位；配方：作者 test 人像的 face 区域）
  · 树叶：绿相带的 C 与色相（卡：`hue_bands` 的 60–120°；配方：leaf 区域）
  · 冷暖：`warm_cool_b`（卡）/ 整幅 b* 均值（配方）

距离 = 各项按**配方间离散**归一化后相加（颜色项在前，明暗只占很小权重）。

用法：
  python analysis/medium/color_match.py                 # 只出报告
  python analysis/medium/color_match.py --apply         # 写卡（先备份）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

TONE = common.SVFILM / 'analysis' / 'corpus' / 'kurai' / 'photos_tone_v04.json'
LEVELS = [10.0, 30.0, 50.0, 70.0, 90.0]


def _med(xs):
    xs = [x for x in xs if x is not None]
    return float(np.median(xs)) if xs else None


def card_features():
    """→ {card: 特征}"""
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
        green_C = []
        for r in rows:
            for band in (r.get('hue_bands') or []):
                if 60.0 <= float(band.get('h0', -1)) <= 120.0:
                    green_C.append(band.get('C'))
        out[card] = dict(
            n=len(rows),
            cast=cast,
            skin_L=_med([s.get('L') for s in sk]),
            skin_C=_med([s.get('C') for s in sk]),
            skin_h=_med([s.get('h') for s in sk]),
            green_C=_med(green_C),
            warm_cool_b=_med([r.get('warm_cool_b') for r in rows]),
        )
    return out


def recipe_features(sig):
    """把 color_sig 的灰阶条插值到 LEVELS，并补整幅 b*（warm/cool）。"""
    out = {}
    for key, s in sig.items():
        g = s['gray']
        Ls = [g[k]['L'] for k in sorted(g)]
        As = [g[k]['a'] for k in sorted(g)]
        Bs = [g[k]['b'] for k in sorted(g)]
        cast = {L: (float(np.interp(L, Ls, As)), float(np.interp(L, Ls, Bs)))
                for L in LEVELS}
        leaf = s['leaf']
        out[key] = dict(
            stock=s['stock'], paper=s['paper'], cast=cast,
            skin_L=s['face']['L'], skin_C=s['face']['C'], skin_h=s['face']['h'],
            leaf_C=leaf['C'], leaf_h=leaf['h'],
            contrast=s['contrast'],
            warm_cool_b=float(np.mean(Bs)),
        )
    return out


def _spread(values):
    v = np.array([x for x in values if x is not None], float)
    return float(v.std()) if len(v) > 1 and v.std() > 1e-6 else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sig', default=str(common.OUT_DEFAULT / 'color_sig.json'))
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    cards = card_features()
    rec = recipe_features(json.load(open(args.sig, encoding='utf-8')))
    keys = sorted(rec)

    # 归一化尺度：用配方之间的离散（颜色项都是同口径的 Lab 单位）
    sd_cast = _spread([rec[k]['cast'][L][i] for k in keys for L in LEVELS for i in (0, 1)])
    sd_skinC = _spread([rec[k]['skin_C'] for k in keys])
    sd_skinh = _spread([rec[k]['skin_h'] for k in keys])
    sd_greenC = _spread([rec[k]['leaf_C'] for k in keys])
    sd_greenh = _spread([rec[k]['leaf_h'] for k in keys])
    sd_wcb = _spread([rec[k]['warm_cool_b'] for k in keys])

    # 权重：颜色在前（色偏曲线最重），明暗只在最后一点点
    W = dict(cast=1.0, skinC=0.8, skinh=0.6, greenC=0.5, greenh=0.4, wcb=0.7, contrast=0.15)

    rows = []
    for card, f in sorted(cards.items()):
        # 卡侧的色偏曲线：先减掉自身均值 ⇒ 只剩**形状**（随亮度怎么变），
        # 绝对色偏交给卡级 WB（`卷纸测量.md` §5.5 已有规则），不靠换卷纸硬扛。
        cvals = [(f['cast'].get(L) or (None, None)) for L in LEVELS]
        ok = [(a, b) for a, b in cvals if a is not None and b is not None]
        if not ok:
            continue
        ca0 = float(np.mean([a for a, _ in ok]))
        cb0 = float(np.mean([b for _, b in ok]))
        shape_c = [(a - ca0, b - cb0) for a, b in ok]
        best = None
        second = None
        for k in keys:
            r = rec[k]
            rv = [r['cast'][L] for L in LEVELS]
            ra0 = float(np.mean([a for a, _ in rv]))
            rb0 = float(np.mean([b for _, b in rv]))
            shape_r = [(a - ra0, b - rb0) for a, b in rv]
            d = 0.0
            for (a1, b1), (a2, b2) in zip(shape_c, shape_r):
                d += (abs(a1 - a2) + abs(b1 - b2)) / 2.0
            d /= max(len(shape_c), 1)
            if best is None or d < best[0]:
                second = best
                best = (d, k, r['stock'], r['paper'])
            elif second is None or d < second[0]:
                second = (d, k, r['stock'], r['paper'])
        rows.append(dict(card=card, dist=round(best[0], 3),
                         second=round(second[0], 3) if second else None,
                         offset_a=round(-ca0, 2), offset_b=round(-cb0, 2),
                         recipe=best[1], stock=best[2], paper=best[3]))

    from collections import Counter
    cnt = Counter(r['recipe'] for r in rows)
    print('== 颜色匹配结果（%d 卡）' % len(rows))
    for k, v in cnt.most_common():
        print('  %-42s %2d 张' % (k, v))

    os.makedirs(args.out, exist_ok=True)
    rep = os.path.join(args.out, 'color_match.json')
    with open(rep, 'w', encoding='utf-8') as fh:
        json.dump(dict(weights=W, spreads=dict(cast=sd_cast, skinC=sd_skinC, skinh=sd_skinh,
                                              greenC=sd_greenC, greenh=sd_greenh, wcb=sd_wcb),
                       rows=[{k: v for k, v in r.items() if k != 'feats'} for r in rows]),
                  fh, ensure_ascii=False, indent=1)
    print('写出', rep)

    if args.apply:
        stamp = time.strftime('%Y%m%d_%H%M')
        backup = os.path.join(args.out, 'backup_cards_color_%s' % stamp)
        os.makedirs(backup, exist_ok=True)
        changed = 0
        for r in rows:
            p = os.path.join(str(common.CARDS), r['card'] + '.json')
            if not os.path.isfile(p):
                continue
            shutil.copy2(p, os.path.join(backup, r['card'] + '.json'))
            j = json.load(open(p, encoding='utf-8'))
            md = j.setdefault('medium', {})
            if md.get('stock') == r['stock'] and md.get('paper') == r['paper']:
                continue
            md['stock'] = r['stock']
            md['paper'] = r['paper']
            with open(p, 'w', encoding='utf-8') as fh:
                json.dump(j, fh, ensure_ascii=False, indent=1)
            changed += 1
        print('写卡 %d 张（备份 %s）' % (changed, backup))


if __name__ == '__main__':
    main()
