# -*- coding: utf-8 -*-
r"""C2 lrbase 自检：不依赖样片，纯合成图 + 元数据/ TIFF 校验。

跑法：`python -m svFilm.lrbase_selftest`
"""
from __future__ import annotations

import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from . import lr_meta
from .lrbase import (fit_base_curve, invert_negative, save_tiff16,
                     srgb_decode, srgb_encode)

FAIL = []


def check(name, fn):
    try:
        fn()
        print('PASS  %s' % name)
    except Exception as e:                                  # noqa: BLE001
        FAIL.append((name, e))
        print('FAIL  %s  %s: %s' % (name, type(e).__name__, e))


def t_invert_roundtrip():
    pos = np.linspace(0.05, 0.95, 256, dtype=np.float64)
    img = np.repeat(pos[None, :, None], 3, axis=2)
    img = np.repeat(img, 8, axis=0)
    neg = 1.0 / img
    out = invert_negative(neg, lo_q=0.0, hi_q=100.0)
    exp = (img - img.min()) / (img.max() - img.min())
    err = float(np.max(np.abs(out - exp)))
    assert err < 5e-3, 'roundtrip max err %.5f' % err
    assert out.min() >= 0.0 and out.max() <= 1.0


def t_invert_headroom():
    pos = np.linspace(0.05, 0.95, 128, dtype=np.float64)
    img = np.repeat(pos[None, :, None], 3, axis=2)
    neg = 1.0 / img
    out = invert_negative(neg, lo_q=0.0, hi_q=100.0, headroom=0.02)
    assert out.min() >= 0.02 - 1e-9, 'min %.6f' % out.min()
    assert out.max() <= 0.98 + 1e-9, 'max %.6f' % out.max()


def t_invert_rejects_bad_shape():
    try:
        invert_negative(np.zeros((4, 4), np.float64))
    except ValueError:
        return
    raise AssertionError('2D input should raise')


def t_srgb_roundtrip():
    x = np.linspace(0, 1, 1000)
    y = srgb_decode(srgb_encode(x))
    assert float(np.max(np.abs(x - y))) < 1e-6


def t_tiff16():
    try:
        import tifffile
    except Exception:
        print('SKIP  tiff16 (no tifffile)')
        return
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'a.tif'
        x = np.linspace(0, 1, 64 * 32 * 3).reshape(32, 64, 3)
        save_tiff16(x, p)
        arr = tifffile.imread(str(p))
        assert arr.dtype == np.uint16, arr.dtype
        assert arr.shape == (32, 64, 3), arr.shape
        with tifffile.TiffFile(str(p)) as tf:
            page = tf.pages[0]
            bps = page.bitspersample
            assert bps == 16 or bps == (16, 16, 16), bps
            assert 34675 in [t.code for t in page.tags.values()], 'no ICC tag'


def _curve(n=16):
    xs = np.linspace(0, 255, n)
    ys = 0.15 * xs + 0.85 * (xs / 255.0) ** 2 * 255
    ys = np.maximum.accumulate(np.clip(ys, 0, 255))
    ys[0], ys[-1] = 0, 255
    return [[int(round(a)), int(round(b))] for a, b in zip(xs, ys)]


def t_metadata():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img = td / 'x.tif'
        try:
            import tifffile
            tifffile.imwrite(str(img), np.zeros((8, 8, 3), np.uint16))
        except Exception:
            img.write_bytes(b'not-a-tiff')
        report = {'stock': '鹿井预设', 'stock_label': '鹿井预设',
                  'camera': {'model': 'X-T30 III'}, 'ms': 1.0,
                  'flat': {'lo_q': 1.0, 'hi_q': 99.5, 'headroom': 0.01}}
        bc = {'curves': {'ToneCurvePV2012': _curve(),
                         'ToneCurvePV2012Red': _curve(),
                         'ToneCurvePV2012Green': _curve(),
                         'ToneCurvePV2012Blue': _curve()},
              'fit': {'median_abs_dL': 0.95}}
        meta = lr_meta.build_metadata(report, img, mode='flat', base_curve=bc,
                                      preset_name='svFilm test')
        assert meta['schema'] == 'svfilm.lr/1'
        assert meta['baked']['tone_curve'] is False
        assert meta['lr']['settings']['GrainAmount'] == 0
        for k in ('ToneCurvePV2012', 'ToneCurvePV2012Red'):
            pts = meta['lr']['settings'][k]
            assert len(pts) == 16 and pts[0][0] == 0 and pts[-1][0] == 255
        jp = td / 'x.svfilm.json'
        lr_meta.write_json(meta, jp)
        assert json.loads(jp.read_text(encoding='utf-8'))['schema'] == 'svfilm.lr/1'
        xp = td / 'x.xmp'
        lr_meta.write_sidecar_xmp(meta, xp)
        ET.parse(str(xp))
        pp = td / 'x.lrpreset.xmp'
        lr_meta.write_preset_xmp(meta, pp)
        root = ET.parse(str(pp)).getroot()
        assert root.tag.endswith('xmpmeta')
        lp = td / 'x.svfilm.lua'
        lr_meta.write_lua(meta, lp)
        txt = lp.read_text(encoding='utf-8')
        assert txt.startswith('return {') and 'settings = {' in txt
        assert 'ToneCurvePV2012 = {0, 0,' in txt
        tp = td / 'x.svfilm.lr.txt'
        lr_meta.write_lr_txt(meta, tp)
        ttxt = tp.read_text(encoding='utf-8')
        assert 'ToneCurvePV2012=0,0,' in ttxt and 'schema=svfilm.lr/1' in ttxt
        # 复合：identity 与 x 复合应等于 x
        c = _curve()
        assert lr_meta.compose([[0, 0], [255, 255]], c) == c


def t_fit_curve():
    try:
        import tifffile
        from PIL import Image
        from scipy.interpolate import PchipInterpolator  # noqa: F401
    except Exception:
        print('SKIP  fit_curve (缺 tifffile/scipy/PIL)')
        return
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        x = np.linspace(0, 1, 256)
        flat = np.repeat(x[None, :, None], 256, axis=0)
        flat = np.repeat(flat, 3, axis=2)
        # print = flat^1.2（单调、可被一条平滑曲线表达）
        pr = flat ** 1.2
        flat16 = np.round(flat * 65535).astype(np.uint16)
        tifffile.imwrite(str(td / 'flat.tif'), flat16, photometric='rgb')
        Image.fromarray((pr * 255 + 0.5).astype(np.uint8)).save(td / 'print.png')
        curve = fit_base_curve(td / 'print.png', td / 'flat.tif')
        assert curve['schema'] == 'svfilm.lr.base_curve/1'
        for k, pts in curve['curves'].items():
            assert len(pts) == 16 and pts[0][0] == 0 and pts[-1][0] == 255, (k, pts)
            assert all(b[0] > a[0] for a, b in zip(pts, pts[1:])), k
            assert all(b[1] >= a[1] for a, b in zip(pts, pts[1:])), k
        fit = curve['fit']
        assert fit['median_abs_dL'] is not None and fit['median_abs_dL'] < 3.0, fit


def main():
    check('invert roundtrip', t_invert_roundtrip)
    check('invert headroom', t_invert_headroom)
    check('invert bad shape', t_invert_rejects_bad_shape)
    check('srgb roundtrip', t_srgb_roundtrip)
    check('tiff16 + ICC', t_tiff16)
    check('metadata/json/xmp/compose', t_metadata)
    check('fit base curve', t_fit_curve)
    if FAIL:
        print('\n%d 项失败' % len(FAIL))
        return 1
    print('\n全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
