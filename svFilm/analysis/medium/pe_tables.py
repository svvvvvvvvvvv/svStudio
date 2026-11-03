# -*- coding: utf-8 -*-
"""Build per-card PE chart tables for the current medium (stock/paper/WB/amount/scan)."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
sys.path.insert(0, str(common.SVFILM))
from design import chart_calib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--cards', default=str(common.CARDS))
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--long-side', type=int, default=1080)
    ap.add_argument('--pe-values', default='0.0625,0.125,0.25,0.5,1,2,4')
    ap.add_argument('--wedge', default='ramp', choices=('ramp', 'chart'),
                    help='ramp=合成灰阶条（覆盖 12 档，默认）；chart=旧 21 级楔子')
    args = ap.parse_args()
    pe_values = [float(x) for x in args.pe_values.split(',') if x.strip()]
    out = os.path.abspath(args.out)
    tab_dir = os.path.join(out, 'pe_tables')
    os.makedirs(tab_dir, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.cards, '*.json')))
    if args.limit:
        files = files[:args.limit]
    rows = []
    for i, f in enumerate(files):
        c = json.load(open(f, encoding='utf-8'))
        m = json.loads(json.dumps(c['medium']))
        m.setdefault('simulation', {})['print_exposure'] = 1.0
        safe = c['name'].replace('/', '_')
        path = os.path.join(tab_dir, safe + '.json')
        t0 = time.time()
        chart_calib.render_table(m, out_path=path, pe_values=pe_values,
                                 long_side=args.long_side, wedge_mode=args.wedge)
        # tag card/recipe for later
        doc = json.load(open(path, encoding='utf-8'))
        doc['card'] = c['name']
        doc['recipe'] = (c.get('medium') or {}).get('_lookbook', {}).get('recipe')
        doc['stock'] = c.get('stock'); doc['paper'] = c.get('paper')
        doc['long_side'] = args.long_side
        json.dump(doc, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        rows.append({'card': c['name'], 'path': path, 'sec': round(time.time() - t0, 1),
                     'stock': c.get('stock'), 'paper': c.get('paper')})
        print('[%d/%d] %s %.1fs' % (i + 1, len(files), c['name'], time.time() - t0))
    json.dump(rows, open(os.path.join(out, 'pe_tables.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('wrote', os.path.join(out, 'pe_tables.json'))


if __name__ == '__main__':
    main()
