# -*- coding: utf-8 -*-
"""Amount (DIR coupler) ramp on the author test portrait.

9 recipes × 0/0.5/1/1.5/2. Default recommendation remains 1.0; this is a diagnostic.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

PAIRS = {
    'P160': ('kodak_portra_160', 'kodak_portra_endura'),
    'P400': ('kodak_portra_400', 'kodak_portra_endura'),
    'GOLD': ('kodak_gold_200', 'kodak_portra_endura'),
    'P800': ('kodak_portra_800', 'kodak_portra_endura'),
    'PUSH1': ('kodak_portra_800_push1', 'kodak_portra_endura'),
    'PUSH2': ('kodak_portra_800_push2', 'kodak_portra_endura'),
    'GREEN': ('kodak_portra_400', 'fujifilm_crystal_archive_typeii'),
    'XTRA': ('fujifilm_xtra_400', 'fujifilm_crystal_archive_typeii'),
    'EKTAR': ('kodak_ektar_100', 'kodak_portra_endura'),
}
AMOUNTS = [0.0, 0.5, 1.0, 1.5, 2.0]


def render_amount(stock, paper, amount, image):
    from spektrafilm import init_params, digest_params, simulate
    p = init_params(film_profile=stock, print_profile=paper)
    p.camera.auto_exposure = True
    p.camera.auto_exposure_method = 'center_weighted'
    p.camera.exposure_compensation_ev = 0.0
    p.enlarger.print_exposure = 1.0
    p.enlarger.print_exposure_compensation = True
    p.enlarger.normalize_print_exposure = True
    p.io.input_color_space = 'ProPhoto RGB'
    p.io.input_cctf_decoding = False
    p.io.output_color_space = 'sRGB'
    p.io.output_cctf_encoding = True
    p.io.upscale_factor = 1.0
    p.io.crop = False
    p.settings.use_enlarger_lut = False
    p.settings.use_scanner_lut = False
    p.film_render.dir_couplers.amount = float(amount)
    p = digest_params(p)
    return np.asarray(simulate(image, p, digest_params_first=False), np.float64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(os.path.join(out, 'amount_ramp'), exist_ok=True)
    img = common.load_test_image()
    doc = {}
    for key, (stock, paper) in PAIRS.items():
        doc[key] = {}
        for a in AMOUNTS:
            disp = render_amount(stock, paper, a, img)
            path = os.path.join(out, 'amount_ramp', '%s_%s.png' % (key, str(a).replace('.', 'p')))
            common.save_png(disp, path)
            m = common.metrics(disp)
            doc[key][str(a)] = {'path': path, 'face_C': m['face']['C'],
                                'face_h': m['face']['h'], 'leaf_a': m['leaf']['a'],
                                'contrast': m['contrast']}
            print(key, a, 'faceC %.1f contrast %.1f' % (m['face']['C'], m['contrast']))
    json.dump(doc, open(os.path.join(out, 'amount_ramp.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('wrote', os.path.join(out, 'amount_ramp.json'))


if __name__ == '__main__':
    main()
