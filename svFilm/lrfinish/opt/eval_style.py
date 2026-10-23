# -*- coding: utf-8 -*-
r"""分布级评估：大师 vs 我们的成片（脸/肤色/五段/整图）。

为什么不是配对 ΔE：鹿井是真实胶片，没有同帧数字源图（见 v9 §13）。
这里比较的是**分布**，不是像素对。

用法：
  python opt/eval_style.py --master E:\ds\大师成片\鹿井 --ours <final 目录>
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
paths.ensure_import_path()
import band_read as BR                              # noqa: E402
import _probe_skin_zone as SZ                       # noqa: E402

BANDS = BR.NAMES
SCALAR = ('L_skin', 'C_skin', 'a_skin', 'b_skin', 'face_span', 'd_bg',
          'p50', 'p2', 'p98', 'clip_lo', 'clip_hi')


def measure_one(p):
    out = {}
    sk = SZ.measure(p) or {}
    if sk.get('face'):
        for k in ('L_skin', 'C_skin', 'a_skin', 'b_skin', 'face_span', 'd_bg'):
            out[k] = float(sk[k])
    br = BR.read(p)
    sh = br.get('shape') or {}
    for k in ('p50', 'p2', 'p98', 'clip_lo', 'clip_hi'):
        if k in sh:
            out[k] = float(sh[k])
    for b in br.get('bands') or []:
        nm = b['band']
        if 'na' in b:
            out['a_' + nm] = float(b['na'])
        if 'nb' in b:
            out['b_' + nm] = float(b['nb'])
    return out


def collect(files, label):
    rows, miss = [], 0
    for p in files:
        try:
            d = measure_one(p)
        except Exception as e:                       # noqa: BLE001
            print('  %s skip %s' % (label, e)); miss += 1; continue
        if d:
            rows.append(d)
        else:
            miss += 1
    print('%s: %d 张（缺 %d）' % (label, len(rows), miss))
    return rows


def pct(rows, key):
    v = [r[key] for r in rows if key in r and np.isfinite(r[key])]
    if not v:
        return None
    return tuple(float(np.percentile(v, q)) for q in (25, 50, 75))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--master', default=r'E:\ds\大师成片\鹿井')
    ap.add_argument('--ours', required=True)
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    mfiles = []
    seen = set()
    for p in (glob.glob(os.path.join(a.master, '**', '*.jpg'), recursive=True) +
              glob.glob(os.path.join(a.master, '**', '*.JPG'), recursive=True)):
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key); mfiles.append(p)
    mfiles.sort()
    ofiles = glob.glob(os.path.join(a.ours, '**', '*_svFilm_full.tif'), recursive=True)
    best = {}
    for p in ofiles:
        k = os.path.basename(p)
        if k not in best or os.path.getmtime(p) > os.path.getmtime(best[k]):
            best[k] = p
    ofiles = sorted(best.values())
    if not mfiles or not ofiles:
        raise SystemExit('master=%d ours=%d，路径不对' % (len(mfiles), len(ofiles)))

    M = collect(mfiles, 'master')
    O = collect(ofiles, 'ours')

    keys = [k for k in SCALAR if pct(M, k) and pct(O, k)]
    for b in BANDS:
        for c in ('a', 'b'):
            k = '%s_%s' % (c, b)
            if pct(M, k) and pct(O, k):
                keys.append(k)

    print('\n%-12s %8s %8s %8s | %8s | %s'
          % ('指标', 'P25', 'P50', 'P75', 'ours', '在带?'))
    rows = []
    for k in keys:
        m = pct(M, k); o = pct(O, k)
        if not m or not o:
            continue
        ok = m[0] <= o[1] <= m[2]
        rows.append(dict(key=k, p25=m[0], p50=m[1], p75=m[2], ours=o[1], ok=ok))
        print('%-12s %8.2f %8.2f %8.2f | %8.2f | %s'
              % (k, m[0], m[1], m[2], o[1], '✓' if ok else '✗'))

    hit = sum(1 for r in rows if r['ok'])
    print('\n%d/%d 个指标的中位落在大师 P25–P75 带内' % (hit, len(rows)))
    dst = a.out or os.path.join(a.ours, 'eval_style.json')
    json.dump(dict(master=a.master, ours=a.ours, n_master=len(M), n_ours=len(O),
                   rows=rows, hit=hit, total=len(rows)),
              open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('-> %s' % dst)


if __name__ == '__main__':
    main()
