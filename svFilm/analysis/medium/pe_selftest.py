# -*- coding: utf-8 -*-
"""Self-test: each per-card PE table should recover its own target p50 at PE=1.0."""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
sys.path.insert(0, str(common.SVFILM))
from design import chart_calib


def main():
    out = os.path.join(str(common.OUT_DEFAULT), 'pe_tables.json')
    rows = json.load(open(out, encoding='utf-8'))
    chart, meta, sha = chart_calib.load_chart()
    first = json.load(open(rows[0]['path'], encoding='utf-8')) if rows else {}
    long_side = int(first.get('long_side') or 540)
    chart, centers, _ = chart_calib._resize_chart(chart, meta, long_side)
    doc = {'chart_sha': sha, 'long_side': long_side, 'n': 0, 'errors': [], 'hit': 0}
    for r in rows:
        if not os.path.isfile(r['path']):
            continue
        table = json.load(open(r['path'], encoding='utf-8'))
        pes = [float(x) for x in table['pes']]
        if 1.0 not in pes:
            continue
        idx = pes.index(1.0)
        L = np.asarray(table['wedge']['L'][idx], np.float64)
        target = float(np.median(L))
        res = chart_calib.solve_pe(chart, target, table, pe_nominal=1.0)
        err = float(res['p50_pred'] - target)
        doc['n'] += 1
        doc['errors'].append({'card': r['card'], 'target': target,
                              'pred': res['p50_pred'], 'pe': res['pe'],
                              'delta_ev': res['delta_ev'], 'err': err,
                              'hit_bound': res['hit_bound']})
        doc['hit'] += 0 if res['hit_bound'] else 1
    errs = np.asarray([abs(e['err']) for e in doc['errors']])
    doc['median_abs'] = float(np.median(errs)) if len(errs) else None
    doc['max_abs'] = float(np.max(errs)) if len(errs) else None
    doc['hit_frac'] = doc['hit'] / doc['n'] if doc['n'] else None
    json.dump(doc, open(os.path.join(str(common.OUT_DEFAULT), 'pe_selftest.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('n', doc['n'], 'median_abs', doc['median_abs'], 'max_abs', doc['max_abs'],
          'hit_frac', doc['hit_frac'])
    for e in doc['errors'][:5]:
        print(e)


if __name__ == '__main__':
    main()
