# -*- coding: utf-8 -*-
"""Sample per-image PE check on 4 RAFs from the xhs batch."""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
sys.path.insert(0, str(common.SVFILM))
from design import chart_calib
from svFilm import presets, io, color

RAFS = r'E:\ds\photo\_mask_test\batch3'
TARGETS = r'E:\ds\photo\_run_1102_peer_pe\peer_targets.json'


def main():
    card = json.load(open(os.path.join(str(common.CARDS), 'xhs（整套）.json'), encoding='utf-8'))
    medium = card['medium']
    wb0 = dict(card.get('wb') or {})
    if wb0.get('temperature') is None:
        wb0 = dict(medium.get('wb') or {})
    wb = {'white_balance': 'custom',
          'temperature': float(wb0.get('temperature') or 0),
          'tint': float(wb0.get('tint') or 1.0)}
    table = json.load(open(os.path.join(str(common.OUT_DEFAULT), 'pe_tables', 'xhs（整套）.json'), encoding='utf-8'))
    targets = json.load(open(TARGETS, encoding='utf-8'))['per_image']
    rows = []
    for f in sorted(os.listdir(RAFS)):
        if not f.lower().endswith('.raf'):
            continue
        stem = os.path.splitext(f)[0]
        if stem not in targets:
            continue
        lin = io.load_raw(os.path.join(RAFS, f), max_side=1080, wb=wb).lin
        target = float(targets[stem]['target_p50'])
        sol = chart_calib.solve_pe(lin, target, table, pe_nominal=1.0)
        disp = presets.render(lin, medium, print_exposure=sol['pe'])
        lab = color.to_lab(np.clip(disp, 0, 1))
        p50 = float(np.median(lab[..., 0]))
        rows.append({'stem': stem, 'target': target, 'p50': p50, 'd': p50 - target,
                     'pe': sol['pe'], 'delta_ev': sol['delta_ev'], 'hit_bound': sol['hit_bound']})
        print(stem, 'target %.1f p50 %.1f d %+.1f pe %.3f hit %s' % (
            target, p50, p50 - target, sol['pe'], sol['hit_bound']))
    json.dump(rows, open(os.path.join(str(common.OUT_DEFAULT), 'pe_batch_check.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    errs = np.abs([r['d'] for r in rows])
    print('n', len(rows), 'median', float(np.median(errs)), 'max', float(np.max(errs)))


if __name__ == '__main__':
    main()
