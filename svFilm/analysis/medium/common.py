# -*- coding: utf-8 -*-
"""Shared utilities for the medium look-book measurement.

Only the author engine's profiles/rendering are used. No old presets, no card statistics.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SVFILM = Path(__file__).resolve().parents[2]
ENGINE = SVFILM / '_tools' / 'spektrafilm'
ENGINE_SRC = ENGINE / 'src'
TEST_IMAGE = ENGINE / 'img' / 'test' / 'portrait_leaves_32bit_linear_prophoto_rgb.tif'
CARDS = SVFILM / 'analysis' / 'corpus' / 'kurai' / 'cards'
MASTER_ROOTS = {
    '鹿井': Path(r'E:\ds\大师成片\鹿井'),
    '滨田': Path(r'E:\ds\大师成片\滨田'),
    'xhs': Path(r'E:\ds\大师成片\xhs'),
}
OUT_DEFAULT = Path(__file__).resolve().parent / 'out'

for _p in (str(ENGINE), str(ENGINE_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# sRGB D65 Lab
M = np.array([[0.4124564, 0.3575761, 0.1804375],
              [0.2126729, 0.7151522, 0.0721750],
              [0.0193339, 0.1191920, 0.9503041]])
WP = np.array([0.95047, 1.0, 1.08883])

FACE = [(250, 430, 400, 520), (300, 620, 380, 700), (390, 620, 460, 700)]
LEAF = [(500, 80, 650, 250), (0, 650, 100, 800)]
WHOLE = [(0, 0, 667, 1000)]


def srgb_to_lab(disp):
    x = np.clip(np.asarray(disp, np.float64), 0, 1)
    lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    xyz = (lin @ M.T) / WP
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def region(lab, boxes):
    px = np.concatenate([lab[y0:y1, x0:x1].reshape(-1, 3) for x0, y0, x1, y1 in boxes], 0)
    L, a, b = np.median(px, 0)
    return dict(L=float(L), a=float(a), b=float(b),
                C=float(np.hypot(a, b)),
                h=float(np.degrees(np.arctan2(b, a)) % 360.0))


def metrics(disp):
    lab = srgb_to_lab(disp)
    L = lab[..., 0]
    return dict(face=region(lab, FACE), leaf=region(lab, LEAF),
                whole_L=float(np.mean(L)),
                whole_C=float(np.mean(np.hypot(lab[..., 1], lab[..., 2]))),
                contrast=float(np.percentile(L, 95) - np.percentile(L, 5)))


def load_test_image():
    from spektrafilm.utils.io import load_image_oiio
    return load_image_oiio(str(TEST_IMAGE))


def render(stock: str, paper: str, image=None):
    from spektrafilm import init_params, digest_params, simulate
    if image is None:
        image = load_test_image()
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
    p = digest_params(p)
    return np.asarray(simulate(image, p, digest_params_first=False), np.float64)


def save_png(disp, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((np.clip(disp, 0, 1) * 255 + 0.5).astype(np.uint8)).save(path)


def load_png(path):
    return np.asarray(Image.open(path).convert('RGB'), np.float64) / 255.0


def profile_infos():
    from spektrafilm import load_profile
    prof_dir = ENGINE / 'src' / 'spektrafilm' / 'data' / 'profiles'
    out = {}
    for f in sorted(prof_dir.glob('*.json')):
        try:
            info = load_profile(f.stem).info
        except Exception:
            continue
        out[f.stem] = info
    return out


def stocks():
    return [name for name, info in profile_infos().items()
            if info.stage == 'filming' and info.use == 'still' and info.type == 'negative']


def papers():
    return [name for name, info in profile_infos().items()
            if info.stage == 'printing' and info.use == 'still']


def target_pairs():
    infos = profile_infos()
    return [(s, infos[s].target_print) for s in stocks() if infos[s].target_print]


def reference_targets():
    """Canonical cross-pair set: (stock, paper) for paper/stock separation."""
    return [
        ('kodak_portra_400', 'kodak_portra_endura'),
        ('kodak_portra_400', 'fujifilm_crystal_archive_typeii'),
        ('fujifilm_c200', 'kodak_portra_endura'),
        ('fujifilm_c200', 'fujifilm_crystal_archive_typeii'),
    ]
