# -*- coding: utf-8 -*-
"""Full per-image PE check through production flow (xhs card)."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import tifffile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
sys.path.insert(0, str(common.SVFILM))
from design import chart_calib, flow
from svFilm import color

DEFAULT_RAF = r'D:\照片库\效果测试1027_2'
DEFAULT_TARGETS = r'E:\ds\photo\_run_1102_peer_pe\peer_targets.json'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--raf-dir', default=DEFAULT_RAF)
    ap.add_argument('--out-dir', default=r'E:\ds\photo\_run_1102_peer_pe\engine_full_1080')
    ap.add_argument('--targets', default=DEFAULT_TARGETS)
    args = ap.parse_args()
    card = json.load(open(os.path.join(str(common.CARDS), 'xhs（整套）.json'), encoding='utf-8'))
    pt = json.load(open(args.targets, encoding='utf-8'))['per_image']
    paths = {}
    for f in sorted(os.listdir(args.raf_dir)):
        if f.lower().endswith('.raf'):
            stem = os.path.splitext(f)[0]
            if stem in pt and pt[stem].get('target_p50') is not None:
                paths[stem] = os.path.join(args.raf_dir, f)
    targets = {k: float(pt[k]['target_p50']) for k in paths}
    print('raws', len(paths), 'targets', len(targets))
    out = flow.render_card(card, paths, args.out_dir, long_side=1080,
                           log=lambda *a: None, targets=targets,
                           table=chart_calib.load_card_table(card['name']))
    rows = []
    for stem, path in out.items():
        arr = tifffile.imread(path).astype(np.float64)
        arr = arr / (65535.0 if arr.max() > 1.5 else 1.0)
        lab = color.to_lab(np.clip(arr, 0, 1))
        p50 = float(np.median(lab[..., 0]))
        target = targets[stem]
        rows.append({'stem': stem, 'target': target, 'p50': p50, 'd': p50 - target,
                     'file': path})
        print('%s target %.1f p50 %.1f d %+.1f' % (stem, target, p50, p50 - target))
    errs = np.abs([r['d'] for r in rows])
    doc = {'n': len(rows), 'median_abs': float(np.median(errs)) if len(errs) else None,
           'max_abs': float(np.max(errs)) if len(errs) else None, 'rows': rows}
    json.dump(doc, open(os.path.join(str(common.OUT_DEFAULT), 'pe_full_check.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('n', doc['n'], 'median', doc['median_abs'], 'max', doc['max_abs'])


if __name__ == '__main__':
    main()
