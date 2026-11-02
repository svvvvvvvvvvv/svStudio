# -*- coding: utf-8 -*-
"""Render the medium look book on the author test portrait.

Usage:
  python analysis/medium/lookbook.py
  python analysis/medium/lookbook.py --papers all --out analysis/medium/out
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
    ap.add_argument('--papers', choices=['target', 'all'], default='target')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--no-sheet', action='store_true')
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    look = os.path.join(out, 'lookbook')
    os.makedirs(look, exist_ok=True)

    if args.papers == 'target':
        pairs = common.target_pairs()
    else:
        pairs = [(s, p) for s in common.stocks() for p in common.papers()]
    if args.limit:
        pairs = pairs[:args.limit]

    img = common.load_test_image()
    print('input', img.shape, img.dtype, float(img.min()), float(img.max()))
    rows = []
    for i, (stock, paper) in enumerate(pairs):
        print('[%d/%d] %s + %s' % (i + 1, len(pairs), stock, paper))
        disp = common.render(stock, paper, img)
        path = os.path.join(look, '%s__%s.png' % (stock, paper))
        common.save_png(disp, path)
        rows.append({'stock': stock, 'paper': paper, 'path': path,
                     'metrics': common.metrics(disp)})

    json.dump(rows, open(os.path.join(look, 'lookbook.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('wrote', os.path.join(look, 'lookbook.json'))

    if not args.no_sheet and rows:
        cols = 4
        nrows = (len(rows) + cols - 1) // cols
        fig, axs = plt.subplots(nrows, cols, figsize=(4 * cols, 3.0 * nrows))
        axs = np.atleast_2d(axs)
        for i, r in enumerate(rows):
            ax = axs[i // cols, i % cols]
            ax.imshow(Image.open(r['path']))
            ax.axis('off')
            ax.set_title('%s\n→ %s' % (r['stock'], r['paper']), fontsize=7)
        for j in range(len(rows), nrows * cols):
            axs[j // cols, j % cols].axis('off')
        fig.suptitle('Look book: author test portrait, same auto exposure/PE, grain/halation on', fontsize=11)
        fig.tight_layout()
        fig.savefig(os.path.join(out, 'lookbook_sheet.png'), dpi=160)
        plt.close(fig)
        print('wrote', os.path.join(out, 'lookbook_sheet.png'))


if __name__ == '__main__':
    main()
