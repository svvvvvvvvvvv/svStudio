# -*- coding: utf-8 -*-
"""从 `photos_tone_v04.json` 重建卡（cards_v6）：points / bands / wb / hue_bands。

为什么重建：v5 的 points 来自旧 v7 尺子（原生尺寸），v04 是 1080 交付域 +
grain 字段。两把尺子不能混用。v5 保留做回滚，代码优先读 v6。

不写进卡的东西：
  · 介质（卷/纸/grain）→ media/<卡名>.json；
  · peer 目标 → 每次现算；
  · 逐图曲线。

用法：
    & $py -X utf8 analysis/corpus/kurai/build_cards_v6.py
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys

_CORPUS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_CORPUS)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from design.color_math import duv_from_lab  # noqa: E402

TONE = os.path.join(_CORPUS, 'photos_tone_v04.json')
CARDS5 = os.path.join(_CORPUS, 'cards_v5.json')
OUT = os.path.join(_CORPUS, 'cards_v6.json')

WHITE_C_MAX = 8.0
WHITE_N_MIN = 300
NEUTRAL_C_MAX = 6.0
NEUTRAL_MIN_N = 3
THIN_N = 8


def _vals(rows, fn):
    return [float(v) for v in (fn(r) for r in rows) if isinstance(v, (int, float))]


def _med(xs):
    v = sorted(float(x) for x in xs if isinstance(x, (int, float)))
    return float(st.median(v)) if v else None


def _pct(xs, q):
    v = sorted(float(x) for x in xs if isinstance(x, (int, float)))
    if len(v) < 2:
        return None
    return float(v[int(round((len(v) - 1) * q))])


def _band(rows, fn):
    v = _vals(rows, fn)
    if len(v) < 2:
        return None
    return [round(_pct(v, 0.25), 2), round(_pct(v, 0.75), 2)]


def _row_fields():
    """每个 point 字段的取法（逐图 → 卡）。"""
    return {
        'p5': lambda r: (r.get('img') or {}).get('p5'),
        'p25': lambda r: (r.get('img') or {}).get('p25'),
        'p50': lambda r: (r.get('img') or {}).get('p50'),
        'p75': lambda r: (r.get('img') or {}).get('p75'),
        'p95': lambda r: (r.get('img') or {}).get('p95'),
        'contrast': lambda r: (r.get('img') or {}).get('contrast'),
        'clip': lambda r: (r.get('img') or {}).get('clip_pct'),
        'face_L': lambda r: (r.get('skin') or {}).get('L'),
        'face_C': lambda r: (r.get('skin') or {}).get('C'),
        'face_h': lambda r: (r.get('skin') or {}).get('h'),
        'toe': lambda r: (r.get('shape') or {}).get('toe'),
        'shoulder': lambda r: (r.get('shape') or {}).get('shoulder'),
        'contrast_win': lambda r: (r.get('shape') or {}).get('contrast_win'),
        'skew': lambda r: (r.get('shape') or {}).get('skew'),
        'face_over_bg': lambda r: (float((r.get('skin') or {}).get('L'))
                                   / float((r.get('bg') or {}).get('L'))
                                   if (r.get('skin') or {}).get('L') and
                                   (r.get('bg') or {}).get('L') else None),
        'person_over_bg': lambda r: (float((r.get('person') or {}).get('L'))
                                     / float((r.get('bg') or {}).get('L'))
                                     if (r.get('person') or {}).get('L') and
                                     (r.get('bg') or {}).get('L') else None),
        'warm_cool_b': lambda r: r.get('warm_cool_b'),
        'light_ratio': lambda r: r.get('light_ratio'),
    }


def card_wb(rows) -> dict:
    wps = []
    for r in rows:
        wp = r.get('white_point') or {}
        a, b, n = wp.get('a'), wp.get('b'), wp.get('n') or 0
        C = wp.get('C')
        if a is None or b is None or n < WHITE_N_MIN:
            continue
        if C is None:
            C = math.hypot(a, b)
        if C > WHITE_C_MAX:
            continue
        wps.append(dict(L=wp.get('L_thr'), a=float(a), b=float(b), n=int(n)))
    out = dict(policy='card-fixed', role='look-white',
               source='white_point(全幅亮部10%% ∧ C*≤%.0f, n≥%d)'
               % (WHITE_C_MAX, WHITE_N_MIN), n=len(wps),
               white_point=None, neutral_axis=None, duv=None)
    if len(wps) >= 3:
        a = _med([w['a'] for w in wps])
        b = _med([w['b'] for w in wps])
        L = _med([w['L'] for w in wps]) or 95.0
        out['white_point'] = dict(L=round(L, 2), a=round(a, 2), b=round(b, 2),
                                  C=round(math.hypot(a, b), 2),
                                  n=int(sum(w['n'] for w in wps)))
        out['duv'] = round(float(duv_from_lab([L, a, b])), 5)
    bands = []
    for i in range(5):
        vals = []
        for r in rows:
            na = r.get('neutral_axis') or []
            if i >= len(na):
                continue
            a, b = na[i].get('a'), na[i].get('b')
            if a is None or b is None or math.hypot(float(a), float(b)) > NEUTRAL_C_MAX:
                continue
            vals.append((float(a), float(b)))
        if len(vals) >= NEUTRAL_MIN_N:
            bands.append(dict(band=i, n=len(vals),
                              a=round(_med([v[0] for v in vals]), 2),
                              b=round(_med([v[1] for v in vals]), 2)))
    out['neutral_axis'] = bands or None
    return out


def hue_bands(rows):
    n_band = 6
    out = []
    for i in range(n_band):
        shares, cs = [], []
        for r in rows:
            hb = r.get('hue_bands') or []
            if i < len(hb):
                shares.append(hb[i].get('share'))
                cs.append(hb[i].get('C'))
        out.append(dict(share=(round(_med(shares), 4) if _med(shares) is not None else None),
                        C=(round(_med(cs), 2) if _med(cs) is not None else None)))
    return out


def main() -> int:
    tone = json.load(open(TONE, encoding='utf-8'))
    rows = [r for r in (tone.get('per_image') or [])
            if r.get('role') == 'master' and not r.get('err')]
    by = {}
    for r in rows:
        by.setdefault(r.get('card'), []).append(r)

    old = {c['name']: c for c in (json.load(open(CARDS5, encoding='utf-8')).get('cards') or [])}
    fields = _row_fields()
    cards = []
    for name in sorted(by):
        rs = by[name]
        pts = {k: (round(_med(_vals(rs, fn)), 2)
                   if _med(_vals(rs, fn)) is not None else None)
               for k, fn in fields.items()}
        bands = {k: _band(rs, fn) for k, fn in fields.items()}
        n_face = sum(1 for r in rs if (r.get('skin') or {}).get('L') is not None)
        n_skin = sum(1 for r in rs if (r.get('skin') or {}).get('C') is not None)
        n_strict = sum(1 for r in rs if (r.get('skin') or {}).get('src') == 'strict')
        v5 = old.get(name) or {}
        cards.append(dict(
            name=name,
            corpus=v5.get('corpus') or rs[0].get('corpus') or '',
            cond_table=v5.get('cond_table') or '',
            n=len(rs), n_face=n_face, n_skin=n_skin, n_strict=n_strict,
            thin=bool(n_face < THIN_N),
            bands=bands, points=pts,
            neutral_axis=None,
            wb=card_wb(rs),
            hue_bands=hue_bands(rs),
            light=v5.get('light') or {},
            samples=v5.get('samples') or []))
    doc = dict(_meta=dict(n_cards=len(cards),
                          source=['photos_tone_v04.json'],
                          ruler='design.measure_all @ 1080 delivery',
                          note='重建自 v04；v5 保留回滚'),
               cards=cards)
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print('写出 %s：%d 张卡（thin %d）'
          % (OUT, len(cards), sum(1 for c in cards if c['thin'])))
    for c in cards[:3]:
        print('  %-30s n=%-3d p50=%s face_L=%s wp a%+.1f b%+.1f'
              % (c['name'], c['n'], c['points'].get('p50'),
                 c['points'].get('face_L'),
                 (c['wb'].get('white_point') or {}).get('a') or 0.0,
                 (c['wb'].get('white_point') or {}).get('b') or 0.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
