# -*- coding: utf-8 -*-
"""Card-level WB review. No per-image WB; no writes."""
from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np

OUT = r'E:\svStudio\svFilm\analysis\medium\out'
CARDS = r'E:\svStudio\svFilm\analysis\corpus\kurai\cards'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()
    rows = []
    for f in sorted(glob.glob(os.path.join(CARDS, '*.json'))):
        c = json.load(open(f, encoding='utf-8'))
        wb = c.get('wb') or {}
        m = c.get('medium') or {}
        mwb = m.get('wb') or {}
        t = wb.get('temperature'); g = wb.get('tint')
        if t is None:
            t = mwb.get('temperature'); g = mwb.get('tint')
        rows.append({
            'name': c['name'],
            'stock': c.get('stock'), 'paper': c.get('paper'),
            'temperature': t, 'tint': g,
            'wb_source': 'card' if wb.get('temperature') is not None else ('medium' if mwb.get('temperature') is not None else 'none'),
            'white_point': wb.get('white_point'),
            'neutral_axis': wb.get('neutral_axis'),
        })

    temps = [r['temperature'] for r in rows if isinstance(r['temperature'], (int, float))]
    tints = [r['tint'] for r in rows if isinstance(r['tint'], (int, float))]
    report = {
        'n': len(rows),
        'missing_temperature': sum(r['temperature'] is None for r in rows),
        'unique_temperature': sorted({round(float(v), 1) for v in temps}),
        'unique_tint': sorted({round(float(v), 3) for v in tints}),
        'temperature_range': [min(temps), max(temps)] if temps else None,
        'tint_range': [min(tints), max(tints)] if tints else None,
        'rows': rows,
    }
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, 'wb_review.json')
    json.dump(report, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('cards', len(rows), 'missing temp', report['missing_temperature'])
    print('unique temp', report['unique_temperature'], 'unique tint', report['unique_tint'])
    print('wrote', path)


if __name__ == '__main__':
    main()
