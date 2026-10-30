# -*- coding: utf-8 -*-
"""从**作者的数据/代码**里读出每种介质的事实（不渲染、不试）。

读的东西：
  · 每个卷的 `info`：type / use / antihalation / **target_print**（作者自己配的纸）
  · 密度曲线：gamma（最大斜率）/ Dmax —— 卷定色调，纸定印相反差
  · `neutral_print_filters.json`：(纸, 光源, 卷) → (C, M, Y) 中性滤片
  · `params_builder._HALATION_PRESETS`：(use, antihalation) → 光晕 σ / strength

用法：
  & $py -X utf8 -m design.profile_table            # 打表
  & $py -X utf8 -m design.profile_table --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List, Optional

import numpy as np

import spektrafilm

PROF = os.path.join(os.path.dirname(spektrafilm.__file__), 'data', 'profiles')


def _profiles() -> Dict[str, dict]:
    out = {}
    for f in sorted(os.listdir(PROF)):
        if f.endswith('.json'):
            d = json.load(open(os.path.join(PROF, f), encoding='utf-8'))
            out[d['info']['stock']] = d
    return out


def _curve_stats(d: dict) -> dict:
    le = np.asarray(d['data']['log_exposure'], dtype=float)
    dc = np.asarray(d['data']['density_curves'], dtype=float)
    if dc.ndim != 2 or dc.shape[1] != 3:
        return {}
    g, dmax = [], []
    for c in range(3):
        y = dc[:, c]
        s = np.gradient(y, le)
        # 只在中段找最大斜率（避开两端数值噪声）
        lo, hi = int(len(le) * 0.15), int(len(le) * 0.85)
        g.append(float(s[lo:hi].max()))
        dmax.append(float(y.max()))
    return dict(gamma=[round(v, 3) for v in g],
                gamma_med=round(float(np.median(g)), 3),
                dmax=[round(v, 2) for v in dmax])


def table() -> dict:
    from spektrafilm.runtime.params_builder import _HALATION_PRESETS
    from spektrafilm.utils.io import read_neutral_print_filters
    nf = read_neutral_print_filters()
    prof = _profiles()

    films, papers = [], []
    for name, d in prof.items():
        i = d['info']
        row = dict(stock=name, type=i.get('type'), use=i.get('use'),
                   ah=i.get('antihalation'), target_print=i.get('target_print'))
        row.update(_curve_stats(d))
        row['halation'] = _HALATION_PRESETS.get((i.get('use'), i.get('antihalation')))
        films.append(row)
        if i.get('support') == 'paper':
            papers.append(dict(stock=name, gamma_med=row.get('gamma_med'),
                               dmax=(row.get('dmax') or [None])[0]))

    # 中性滤片：{纸: {光源: {卷: [C,M,Y]}}}
    illum = None
    for p in nf.values():
        illum = list(p)[0]
        break
    combos = []
    for f in films:
        tp = f.get('target_print')
        if not tp:
            continue
        for paper in [tp] + [p['stock'] for p in papers if p['stock'] != tp]:
            y = (nf.get(paper) or {}).get(illum, {}).get(f['stock'])
            if not y:
                continue
            combos.append(dict(film=f['stock'], paper=paper, official=(paper == tp),
                               neutral=[round(float(v), 2) for v in y],
                               film_gamma=f.get('gamma_med'), paper_gamma=next(
                                   (p['gamma_med'] for p in papers if p['stock'] == paper), None),
                               ah=f.get('ah'), halation=f.get('halation')))
    return dict(illuminant=illum, films=films, papers=papers, combos=combos)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default='')
    a = ap.parse_args(argv)
    t = table()

    print('光源 %s ｜ 卷 %d ｜ 纸 %d ｜ 组合 %d'
          % (t['illuminant'], len(t['films']), len(t['papers']), len(t['combos'])))
    print('\n%-24s %-9s %-6s %-6s %-34s %s'
          % ('卷', 'type', 'AH', 'γ卷', 'target_print', 'halation σ/strength'))
    for f in t['films']:
        h = f.get('halation') or {}
        print('%-24s %-9s %-6s %-6s %-34s %s'
              % (f['stock'], f['type'], f['ah'], f['gamma_med'], f['target_print'],
                 (h.get('sigma_h'), h.get('strength')) if h else '—'))

    print('\n印相纸：')
    for p in t['papers']:
        print('  %-36s γ %-6s Dmax %s' % (p['stock'], p['gamma_med'], p['dmax']))

    print('\n官方配对（target_print）的中性滤片 C/M/Y：')
    for c in t['combos']:
        if c['official']:
            print('  %-24s + %-34s %s  (γ卷 %s / γ纸 %s)'
                  % (c['film'], c['paper'], c['neutral'], c['film_gamma'], c['paper_gamma']))

    if a.json:
        json.dump(t, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('\n写出 %s' % a.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
