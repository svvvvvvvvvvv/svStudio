# -*- coding: utf-8 -*-
"""Vendor engine determinism + author baseline comparison (diagnostic only)."""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def run_case(film, paper, recipe):
    from spektrafilm import init_params, digest_params, simulate
    if recipe == 'gray':
        ramp = np.linspace(0.01, 1.0, 16)
        img = np.ones((16, 16, 3), np.float64) * ramp[None, :, None]
    else:
        img = np.tile(np.array([0.05, 0.40, 0.05], np.float64)[None, None, :], (8, 8, 1))
    p = init_params(film_profile=film, print_profile=paper)
    p.debug.deactivate_spatial_effects = True
    p.debug.deactivate_stochastic_effects = True
    p.settings.use_enlarger_lut = False
    p.settings.use_scanner_lut = False
    p.io.upscale_factor = 1.0
    p.io.crop = False
    p.camera.auto_exposure = False
    p.camera.exposure_compensation_ev = 0.0
    p = digest_params(p)
    return (np.asarray(simulate(img, p, digest_params_first=False), np.float64),
            np.asarray(simulate(img, p, digest_params_first=False), np.float64))


def main():
    cases = [
        ('print_rgb_portra_endura_gray_ramp16', 'kodak_portra_400', 'kodak_portra_endura', 'gray'),
        ('print_rgb_fuji_crystal_gray_ramp16', 'fujifilm_pro_400h', 'fujifilm_crystal_archive_typeii', 'gray'),
        ('print_rgb_portra_endura_green_patch8', 'kodak_portra_400', 'kodak_portra_endura', 'green'),
    ]
    out = {'engine': 'vendor 0.3.3', 'cases': {}}
    for cid, film, paper, recipe in cases:
        a, b = run_case(film, paper, recipe)
        rec = {'shape': list(a.shape), 'finite': bool(np.all(np.isfinite(a))),
               'deterministic_max_abs': float(np.abs(a - b).max())}
        base = os.path.join(r'E:\spektrafilm\tests\baselines', cid + '.npz')
        if os.path.isfile(base):
            exp = np.asarray(np.load(base)['output'], np.float64)
            if exp.shape == a.shape:
                rec['public_baseline_max_abs'] = float(np.abs(a - exp).max())
                rec['public_baseline_mean_abs'] = float(np.abs(a - exp).mean())
        out['cases'][cid] = rec
        print(cid, rec)
    path = os.path.join(str(common.OUT_DEFAULT), 'baseline_vendor.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('wrote', path)


if __name__ == '__main__':
    main()
