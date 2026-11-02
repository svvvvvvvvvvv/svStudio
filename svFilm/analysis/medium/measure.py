# -*- coding: utf-8 -*-
"""Measure the look book and the four stock/paper cross-pairs.

Usage:
  python analysis/medium/measure.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--lookbook', default='')
    ap.add_argument('--faces', action='store_true', default=True)
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    look = args.lookbook or os.path.join(out, 'lookbook')
    rows = json.load(open(os.path.join(look, 'lookbook.json'), encoding='utf-8'))

    doc = {'engine': 'vendor 0.3.3', 'lookbook': {}, 'cross': {}}
    for r in rows:
        key = '%s__%s' % (r['stock'], r['paper'])
        doc['lookbook'][key] = common.metrics(common.load_png(r['path']))

    cross_dir = os.path.join(out, 'cross')
    os.makedirs(cross_dir, exist_ok=True)
    img = common.load_test_image()
    for stock, paper in common.reference_targets():
        key = '%s__%s' % (stock, paper)
        print('cross', key)
        disp = common.render(stock, paper, img)
        path = os.path.join(cross_dir, key + '.png')
        common.save_png(disp, path)
        doc['cross'][key] = common.metrics(disp)

    with open(os.path.join(out, 'lookbook_metrics.json'), 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    print('wrote', os.path.join(out, 'lookbook_metrics.json'))

    print('\n%-30s %6s %6s %6s %6s %6s' % ('key', 'faceL', 'faceC', 'faceH', 'leafA', 'contr'))
    for k, v in doc['lookbook'].items():
        f, l = v['face'], v['leaf']
        print('%-30s %6.1f %6.1f %6.1f %6.1f %6.1f' % (k, f['L'], f['C'], f['h'], l['a'], v['contrast']))

    if args.faces and rows:
        cols = 4
        nrows = (len(rows) + cols - 1) // cols
        fig, axs = plt.subplots(nrows, cols, figsize=(4 * cols, 3.6 * nrows))
        axs = np.atleast_2d(axs)
        for i, r in enumerate(rows):
            ax = axs[i // cols, i % cols]
            im = Image.open(r['path']).convert('RGB').crop((150, 350, 500, 800))
            ax.imshow(im)
            ax.axis('off')
            ax.set_title('%s\n→ %s' % (r['stock'], r['paper']), fontsize=7)
        for j in range(len(rows), nrows * cols):
            axs[j // cols, j % cols].axis('off')
        fig.suptitle('Look book face crop (same input/PE)', fontsize=11)
        fig.tight_layout()
        fig.savefig(os.path.join(out, 'lookbook_faces.png'), dpi=160)
        plt.close(fig)
        print('wrote', os.path.join(out, 'lookbook_faces.png'))


if __name__ == '__main__':
    main()
