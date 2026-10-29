# -*- coding: utf-8 -*-
"""套级聚合（设计真源 §65.3）：每张先算，**再在套内取分位** → 生成匹配卡的画像。

    & $py -X utf8 -m design.cards_build --out cards_v3.json

分组（§63.6）：
  · 鹿井：一个子目录一张卡
  · 滨田 / 小红书：整个语料一张卡（"整套"）

产出每张卡：
  分布带（p10/p25/p50/p75/p90 of p50·p95·p5·contrast·clip）
  形状带（toe/shoulder/contrast_win/skew）· 肤色带（L/C/h 的 P10~P90）
  关系带（脸÷背景亮部 · 人÷背景中位 · 光比 · 暖冷）
  色彩（中性轴五段 · 色相六段）· 光分布（label 计数）· 介质/样片 · n / n_skin / n_strict
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from typing import Dict, List, Optional

import numpy as np

_SV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SV not in sys.path:
    sys.path.insert(0, _SV)

CORPUS_DIR = r'E:\ds\photo\_corpus\kurai\full'
CORPORA = ('鹿井', '滨田', 'xhs')


def _band(xs, lo=10, hi=90):
    xs = [float(x) for x in xs if x is not None and np.isfinite(x)]
    if len(xs) < 3:
        return None
    return [round(float(np.percentile(xs, lo)), 2), round(float(np.percentile(xs, hi)), 2)]


def _med(xs):
    xs = [float(x) for x in xs if x is not None and np.isfinite(x)]
    return round(float(np.median(xs)), 2) if xs else None


def light_label(r: dict) -> str:
    """由四个分量离散成标签（阈值取 light_probe v1：clump 0.53 · back 0.78 · back_side 0.78 · dhalf 8）。

    ★ 这是**近似**：light_probe 用的是自己的探针，这里只有四分量。
    """
    li = r.get('light') or {}
    clump, rn, dl = li.get('clump'), li.get('ratio_near90'), li.get('d_lr_img')
    if clump is not None and clump > 0.53:
        return 'dappled'
    if rn is not None and rn < 0.78:
        return 'back'
    if rn is not None and rn < 0.95:
        return 'back_side'
    if dl is not None and abs(dl) > 8.0:
        return 'side'
    if rn is None:
        return 'unknown'
    return 'front'


def set_key(r: dict, corpus: str) -> str:
    """§63.6：鹿井按子目录；其它语料整套一张。"""
    if corpus == '鹿井':
        return os.path.basename(os.path.dirname(r['file'])) or '(顶层)'
    return '%s（整套）' % corpus


def build() -> List[dict]:
    cards: Dict[str, dict] = {}
    for corpus in CORPORA:
        p = os.path.join(CORPUS_DIR, 'm_%s_v3.json' % corpus)
        if not os.path.isfile(p):
            continue
        d = json.load(open(p, encoding='utf-8'))
        for r in d.get('per_image') or []:
            k = set_key(r, corpus)
            c = cards.setdefault(k, dict(name=k, corpus=corpus, rows=[]))
            c['rows'].append(r)

    out: List[dict] = []
    for k, c in sorted(cards.items()):
        rows = c['rows']
        sk = [r['skin'] for r in rows if r.get('skin')]
        n_skin = len(sk)
        n_strict = sum(1 for s in sk if s.get('src') == 'strict')
        # ---- 分布带（只用有脸的行）
        wit = [r for r in rows if (r.get('face') or {}).get('n')]
        img = lambda f, key: [f(r) for r in wit if f(r) is not None]
        get_img = lambda key: img(lambda r: (r.get('img') or {}).get(key), key)
        shape = lambda key: img(lambda r: (r.get('shape') or {}).get(key), key)
        # ---- 关系
        rel_fb = [(s['L'] / r['bg']['L_p90']) for r, s in
                  ((r, r.get('skin')) for r in rows)
                  if s and (r.get('bg') or {}).get('L_p90')]
        rel_pb = [((r['person'] or {}).get('L') / (r['bg'] or {}).get('L'))
                  for r in rows if (r.get('person') or {}).get('L') and (r.get('bg') or {}).get('L')]
        # ---- 色彩：中性轴五段 / 色相六段（逐图取中位再在套内取中位）
        na = []
        for i in range(5):
            a = [r['neutral_axis'][i]['a'] for r in rows
                 if len(r.get('neutral_axis') or []) > i and r['neutral_axis'][i].get('a') is not None]
            b = [r['neutral_axis'][i]['b'] for r in rows
                 if len(r.get('neutral_axis') or []) > i and r['neutral_axis'][i].get('b') is not None]
            na.append(dict(a=_med(a), b=_med(b)))
        hb = []
        for i in range(6):
            sh = [r['hue_bands'][i]['share'] for r in rows
                  if len(r.get('hue_bands') or []) > i]
            cc = [r['hue_bands'][i]['C'] for r in rows
                  if len(r.get('hue_bands') or []) > i and r['hue_bands'][i].get('C') is not None]
            hb.append(dict(share=_med(sh), C=_med(cc)))
        labels = {}
        for r in rows:
            if (r.get('face') or {}).get('n'):
                lb = light_label(r)
                labels[lb] = labels.get(lb, 0) + 1
        samples = [r['file'] for r in rows][:3]
        out.append(dict(
            name=k, corpus=c['corpus'], n=len(rows), n_face=len(wit),
            n_skin=n_skin, n_strict=n_strict,
            bands=dict(
                p50=_band(get_img('p50')), p95=_band(get_img('p95')), p5=_band(get_img('p5')),
                contrast=_band(get_img('contrast')), clip=_band(get_img('clip_pct')),
                face_L=_band([s['L'] for s in sk]), face_C=_band([s['C'] for s in sk]),
                face_h=_band([s['h'] for s in sk]),
                toe=_band(shape('toe')), shoulder=_band(shape('shoulder')),
                contrast_win=_band(shape('contrast_win')), skew=_band(shape('skew')),
                face_over_bg=_band(rel_fb), person_over_bg=_band(rel_pb),
                light_ratio=_band([r.get('light_ratio') for r in rows]),
                warm_cool_b=_band([r.get('warm_cool_b') for r in rows]),
            ),
            neutral_axis=na, hue_bands=hb, light=labels, samples=samples,
        ))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(CORPUS_DIR, 'cards_v3.json'))
    a = ap.parse_args(argv)
    cards = build()
    with open(a.out, 'w', encoding='utf-8') as fh:
        json.dump(dict(_meta=dict(n_cards=len(cards),
                                  source=['m_%s_v3.json' % c for c in CORPORA]),
                       cards=cards), fh, ensure_ascii=False, indent=1)
    print('写出 %s ｜ 卡 %d 张' % (a.out, len(cards)))
    # ---- 摘要
    thin = [c for c in cards if c['n_skin'] < 8]
    print('n_skin < 8 的卡：%d 张' % len(thin))
    print('%-30s %5s %6s %7s  %-14s %s' % ('卡', 'n', 'n_skin', 'n_strict', '脸L 带', 'p50 带'))
    for c in sorted(cards, key=lambda x: -(x['n_skin'] or 0))[:8]:
        b = c['bands']
        print('%-30s %5d %6d %7d  %-14s %s'
              % (c['name'][:30], c['n'], c['n_skin'], c['n_strict'],
                 str(b['face_L']), str(b['p50'])))
    wide = [(c['name'], b['p50'], b['face_L']) for c in cards
            for b in [c['bands']] if b['p50'] and (b['p50'][1] - b['p50'][0]) > 20]
    print('\np50 带宽 >20 L* 的卡：%d / %d （§63.3：带宽大说明落点由"拍什么"决定）'
          % (len(wide), len(cards)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
