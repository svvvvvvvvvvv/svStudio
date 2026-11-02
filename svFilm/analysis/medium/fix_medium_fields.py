# -*- coding: utf-8 -*-
"""After stock/paper change: sync all stock-dependent grain fields and reset stale print filters.

Policy:
- grain: all fields from the current stock's engine preset;
- print_y/m_filter_shift: 0 (old card cast belongs to old medium; re-solve later if needed);
- print_exposure / pe: nominal 1.0 (per-image PE table is authoritative);
- preserves amount / WB / scan endpoints / camera etc.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

GRAIN_FIELDS = [
    'rms_granularity', 'particle_scale_sublayers', 'density_min', 'uniformity',
    'blur', 'mult_usm_sigma', 'mult_usm_amount', 'blur_dye_clouds_um',
    'micro_sublayers',
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    files = sorted(f for f in os.listdir(str(common.CARDS)) if f.endswith('.json'))

    if args.apply:
        backup = os.path.join(out, 'backup_cards_before_fields_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        os.makedirs(backup)
        for f in files:
            with open(os.path.join(str(common.CARDS), f), 'rb') as src, open(os.path.join(backup, f), 'wb') as dst:
                dst.write(src.read())
        print('backup', backup)

    from spektrafilm import init_params, digest_params
    cache = {}
    changed = []
    for f in files:
        path = os.path.join(str(common.CARDS), f)
        c = json.load(open(path, encoding='utf-8'))
        key = (c.get('stock'), c.get('paper'))
        if key not in cache:
            cache[key] = digest_params(init_params(film_profile=key[0], print_profile=key[1]))
        p = cache[key]
        m = c.get('medium') or {}
        g = dict(m.get('grain') or {})
        pg = p.film_render.grain
        for k in GRAIN_FIELDS:
            v = getattr(pg, k, None)
            if v is not None:
                g[k] = list(v) if isinstance(v, (tuple, list)) else v
        g['active'] = bool(g.get('active', True))
        m['grain'] = g
        sim = dict(m.get('simulation') or {})
        sim['print_y_filter_shift'] = 0.0
        sim['print_m_filter_shift'] = 0.0
        sim['print_exposure'] = 1.0
        m['simulation'] = sim
        if isinstance(m.get('_target'), dict):
            m['_target']['filter_shift'] = dict(y=0.0, m=0.0, note='reset after stock/paper change')
        m['_fields_fixed'] = '2026-11-03 stock/paper fields: grain all + filters reset + pe nominal 1.0'
        c['medium'] = m
        c['pe'] = 1.0
        c['pe_why'] = 'nominal 1.0; per-image PE table is authoritative'
        changed.append((c['name'], key, g['blur'], g['mult_usm_amount'], g['blur_dye_clouds_um']))
        if args.apply:
            tmp = path + '.tmp'
            json.dump(c, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            os.replace(tmp, path)
    print('cards', len(files), 'mode', 'APPLY' if args.apply else 'DRY-RUN')
    print('sample', changed[:3])
    json.dump([{'name': n, 'stock': k[0], 'paper': k[1],
                'grain_blur': b, 'grain_usm': u, 'grain_dye': d}
               for n, k, b, u, d in changed],
              open(os.path.join(out, 'fields_fix_report.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
