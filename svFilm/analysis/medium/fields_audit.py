# -*- coding: utf-8 -*-
"""Audit that stock-dependent medium fields and per-card PE tables are consistent."""
from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

GRAIN_FIELDS = ['rms_granularity', 'particle_scale_sublayers', 'density_min',
                'uniformity', 'blur', 'mult_usm_sigma', 'mult_usm_amount',
                'blur_dye_clouds_um', 'micro_sublayers']


def norm(v):
    if isinstance(v, (list, tuple)):
        return [round(float(x), 5) for x in v]
    if isinstance(v, (int, float)):
        return [round(float(v), 5)]
    return v


def main():
    from spektrafilm import init_params, digest_params
    cache = {}
    rows = []
    for f in sorted(glob.glob(os.path.join(str(common.CARDS), '*.json'))):
        c = json.load(open(f, encoding='utf-8'))
        key = (c.get('stock'), c.get('paper'))
        if key not in cache:
            cache[key] = digest_params(init_params(film_profile=key[0], print_profile=key[1]))
        p = cache[key]
        m = c.get('medium') or {}
        g = m.get('grain') or {}
        s = m.get('simulation') or {}
        table_path = os.path.join(str(common.SVFILM), 'analysis', 'corpus', 'kurai', 'pe_tables', c['name'] + '.json')
        table = json.load(open(table_path, encoding='utf-8')) if os.path.isfile(table_path) else {}
        miss = [k for k in GRAIN_FIELDS if norm(g.get(k)) != norm(getattr(p.film_render.grain, k, None))]
        rows.append(dict(
            name=c['name'], stock=key[0], paper=key[1],
            grain_mismatch=miss,
            filters=[s.get('print_y_filter_shift'), s.get('print_m_filter_shift')],
            pe_nominal=s.get('print_exposure'), pe=float(c.get('pe') or 0),
            table_exists=bool(table), table_pes=table.get('pes'),
            table_ok=(table.get('pes') == [0.0625, 0.125, 0.25, 0.5, 1.0, 2.0, 4.0]),
        ))
    bad_grain = [r['name'] for r in rows if r['grain_mismatch']]
    bad_filters = [r['name'] for r in rows if r['filters'] != [0.0, 0.0]]
    bad_tables = [r['name'] for r in rows if not r['table_ok']]
    doc = dict(n=len(rows), bad_grain=bad_grain, bad_filters=bad_filters,
               bad_tables=bad_tables, rows=rows)
    path = os.path.join(str(common.OUT_DEFAULT), 'fields_audit.json')
    json.dump(doc, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('cards', len(rows))
    print('grain mismatch', len(bad_grain), bad_grain[:5])
    print('filter non-zero', len(bad_filters), bad_filters[:5])
    print('table not 7-grid', len(bad_tables), bad_tables[:5])
    print('wrote', path)


if __name__ == '__main__':
    main()
