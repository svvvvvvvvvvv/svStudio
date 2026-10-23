# -*- coding: utf-8 -*-
r"""第 5 层（分级）的 2 维 QP：一次解阴影轮/高光轮饱和度。

变量：s = (sS, sH)，0..sat_max。
响应：split_gain.json（每 1 格对各段关系 a/b 的效应）。
目标：四段关系带（0-20 / 20-40 / 80-100 相对 40-60）。

用法（离线）：
  python opt/grade_qp.py --tif <layer2 后的 tif> --profile kurai
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
paths.ensure_import_path()
import profile_io                                   # noqa: E402
import qp                                           # noqa: E402
import lr_measure as M                              # noqa: E402
from _probe_grade_rel import bands_of, rels_of      # noqa: E402

RELMAP = {'0-20': '阴影-中调', '20-40': '影子-中调',
          '60-80': '中高-中调', '80-100': '亮部-中调'}


def load_gain(prof):
    d = json.load(open(profile_io.calib(prof, 'split_gain'), encoding='utf-8'))
    g = {}
    for st in d['states']:
        key = (st['wheel'], int(st['hue']))
        g[key] = {r['band']: (r['da'] / st['sat'], r['db'] / st['sat'])
                  for r in st['rows']}
    gs = g[('shadow', int(profile_io.grading(prof)['shadow_hue']))]
    gh = g[('highlight', int(profile_io.grading(prof)['high_hue']))]
    return gs, gh


def solve(tif, prof, sat_max=10.0, ridge=0.01):
    rgb = M.load_rgb(tif, 1024)
    bd = bands_of(rgb)
    rel = rels_of(bd)
    gr = json.load(open(profile_io.calib(prof, 'grade_rel'), encoding='utf-8'))['relation']
    gs, gh = load_gain(prof)
    A, b = np.zeros((0, 2)), np.zeros((0,))
    for band, name in RELMAP.items():
        if name not in rel or band not in gs or band not in gh:
            continue
        ss, sh = gs[band], gh[band]
        # 关系的增益 = 该段增益 − 中调段增益
        sm, hm = gs['40-60'], gh['40-60']
        A = np.vstack([A,
                       [ss[0] - sm[0], sh[0] - hm[0]],
                       [ss[1] - sm[1], sh[1] - hm[1]]])
        b = np.concatenate([b, [gr[name]['a']['p50'] - rel[name][0],
                                gr[name]['b']['p50'] - rel[name][1]]])
    s = qp.solve_box(A, b, 0.0, sat_max, ridge=ridge)
    return s, rel, gr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tif', required=True)
    ap.add_argument('--profile', default='kurai')
    ap.add_argument('--sat-max', type=float, default=10.0)
    a = ap.parse_args()
    prof = profile_io.load(a.profile)
    s, rel, gr = solve(a.tif, prof, a.sat_max)
    print('shadow=%.2f  highlight=%.2f' % (s[0], s[1]))
    for band, name in RELMAP.items():
        if name in rel:
            print('%-6s 现在 a/b %+.2f/%+.2f  靶 %+.2f/%+.2f'
                  % (band, rel[name][0], rel[name][1],
                     gr[name]['a']['p50'], gr[name]['b']['p50']))


if __name__ == '__main__':
    main()
