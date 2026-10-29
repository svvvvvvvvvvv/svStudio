# -*- coding: utf-8 -*-
"""一个目录到底是不是"一种风格"？—— 用方差分解回答，不靠嘴。

问题：套内 p50 的 IQR 很大（最大 28 L*），是不是说明"一个目录 ≠ 一个风格"？

分两类量去算**套内方差占比**：
    w = 套内方差 / (套内方差 + 套间方差)
w 小（<0.5）= 这个量主要由"哪一套"决定 ⇒ **风格量**
w 大（>0.5）= 这个量主要由"哪一张"决定 ⇒ **场景量**

如果"落点(p50/p95/clip)"的 w 大、而"形状/色彩/关系"的 w 小，
那结论就是：**目录是风格，落点是场景** —— 卡该收风格量，不该收绝对落点。
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from typing import Dict, List, Optional

FILES = [r'E:\ds\photo\_corpus\kurai\full\m_鹿井_v2.json',
         r'E:\ds\photo\_corpus\kurai\full\m_滨田_v2.json',
         r'E:\ds\photo\_corpus\kurai\full\m_xhs_v2.json']


def _rows(d):
    return list(d.get('per_image') or []) if isinstance(d, dict) else list(d or [])


def _set_of(r):
    p = r.get('file') or r.get('path') or ''
    return os.path.basename(os.path.dirname(p)) or '?'


def _get(r, path):
    cur = r
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


DIMS = {
    # —— 候选"落点/场景量"
    'img.p50': ('img', 'p50'),
    'img.p95': ('img', 'p95'),
    'img.clip_pct': ('img', 'clip_pct'),
    'bg.L_p90': ('bg', 'L_p90'),
    'sky_share_frac': None,
    # —— 候选"风格量"
    'img.contrast': ('img', 'contrast'),
    'shape.toe': ('shape', 'toe'),
    'shape.shoulder': ('shape', 'shoulder'),
    'shape.contrast_win': ('shape', 'contrast_win'),
    'shape.skew': ('shape', 'skew'),
    'bg.b': ('bg', 'b'),
    'bg.a': ('bg', 'a'),
    'warm_cool_b': None,
    'skin.C': ('skin', 'C'),
    'skin.h': ('skin', 'h'),
    'light_ratio': None,
    # —— 关系量（理论上最该稳在套内）
    'rel.face_over_bg': '__rel_face_bg',
    'rel.skinC_over_bgC': '__rel_C',
    'rel.bgP90_minus_face': '__rel_gap',
}


def _val(r, spec):
    if spec is None:
        return r.get(spec) if False else (r.get('sky_share_frac') if False else None)
    return _get(r, spec)


def analyse(path: str) -> Dict[str, dict]:
    d = json.load(open(path, encoding='utf-8'))
    rows = _rows(d)
    by: Dict[str, List[dict]] = {}
    for r in rows:
        by.setdefault(_set_of(r), []).append(r)

    def val(r, name):
        if name == 'sky_share_frac':
            return r.get('sky_share_frac')
        if name == 'warm_cool_b':
            return r.get('warm_cool_b')
        if name == 'light_ratio':
            return r.get('light_ratio')
        if name == '__rel_face_bg':
            a = _get(r, ('skin', 'L')); b = _get(r, ('bg', 'L'))
            return (a / b) if (a and b) else None
        if name == '__rel_C':
            a = _get(r, ('skin', 'C')); b = _get(r, ('bg', 'C'))
            return (a / b) if (a and b) else None
        if name == '__rel_gap':
            a = _get(r, ('bg', 'L_p90')); b = _get(r, ('skin', 'L'))
            return (a - b) if (a is not None and b is not None) else None
        return _get(r, DIMS[name])

    out = {}
    for name in DIMS:
        groups = []
        for s, rs in by.items():
            vs = [val(r, name) for r in rs]
            vs = [float(v) for v in vs if isinstance(v, (int, float))]
            if len(vs) >= 3:
                groups.append(vs)
        if len(groups) < 3:
            continue
        within = st.mean(st.pvariance(g) for g in groups) if len(groups) > 1 else 0.0
        means = [st.mean(g) for g in groups]
        between = st.pvariance(means) if len(means) > 1 else 0.0
        tot = within + between
        w = (within / tot) if tot > 0 else float('nan')
        out[name] = dict(w=w, within=within ** 0.5, between=between ** 0.5,
                         n_sets=len(groups), n=sum(len(g) for g in groups))
    return out


def main() -> int:
    allres = {}
    for p in FILES:
        if not os.path.isfile(p):
            continue
        allres[os.path.basename(p)] = analyse(p)
    names = sorted({k for r in allres.values() for k in r},
                   key=lambda n: st.mean([r[n]['w'] for r in allres.values() if n in r]))
    print('%-20s %s' % ('量', '  '.join('%12s' % k.replace('m_', '').replace('_v2.json', '')
                                        for k in allres)))
    print('-' * (20 + 14 * len(allres)))
    for n in names:
        cells = []
        for k in allres:
            v = allres[k].get(n)
            cells.append('%12s' % ('—' if not v else '%.2f' % v['w']))
        print('%-20s %s' % (n, '  '.join(cells)))
    print()
    print('w = 套内方差 / 总方差')
    print('  w 小 ⇒ 由"哪一套"决定 = **风格量**（卡该收它）')
    print('  w 大 ⇒ 由"哪一张"决定 = **场景量**（卡不该收它，交给不变量与批自己的直方图）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
