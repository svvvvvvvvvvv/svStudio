# -*- coding: utf-8 -*-
"""按（卷, 纸）渲一张 —— **不走预设**（用户 10-30：没有预设的概念了）。

路径：作者引擎 `init_params(film, paper)` → 固定档（空间族）→ `digest_params()` → `simulate()`。

用法：
  & $py -X utf8 render_pair.py --raf <RAF> --film kodak_ultramax_400 --paper fujifilm_crystal_archive_typeii --out x.png
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, r'E:\svStudio\svFilm')
sys.path.insert(0, r'E:\svStudio\svFilm\_tools\spektrafilm\src')

SIDE = 1080


def _load_lin(raf: str, side: int) -> np.ndarray:
    from svFilm import io as SIO
    s = SIO.load(raf, side)
    return np.clip(s.lin, 0.0, None)


def render_pair(raf: str, film: str, paper: str, side: int = SIDE,
                overrides: dict | None = None, target_face_L: float | None = None) -> Image.Image:
    import spektrafilm as sf

    p = sf.init_params(film, paper)
    p.camera.film_format_mm = 35.0
    # 空间族固定档（10-30 拍板）
    p.camera.diffusion_filter.active = True
    p.camera.diffusion_filter.filter_family = 'black_pro_mist'
    p.camera.diffusion_filter.strength = 0.25          # BPM 1/4
    p.enlarger.diffusion_filter.active = True
    p.enlarger.diffusion_filter.filter_family = 'black_pro_mist'
    p.enlarger.diffusion_filter.strength = 0.125       # BPM 1/8
    p.print_render.glare.active = True
    p.print_render.glare.percent = 0.03
    p.camera.auto_exposure = False
    p.enlarger.normalize_print_exposure = False
    p.enlarger.print_exposure_compensation = False
    p.io.output_color_space = 'sRGB'
    p.io.output_cctf_encoding = True
    for k, v in (overrides or {}).items():
        obj, _, leaf = k.rpartition('.')
        tgt = p
        for part in obj.split('.'):
            tgt = getattr(tgt, part)
        setattr(tgt, leaf, v)
    lin = _load_lin(raf, side)
    out = sf.simulate(lin, p, digest_params_first=True)
    if target_face_L:
        im0 = Image.fromarray((np.clip(np.asarray(out), 0, 1) * 255 + 0.5).astype(np.uint8))
        L0 = _face_L(im0)
        if L0:
            y_t = ((target_face_L + 16.0) / 116.0) ** 3
            y_0 = ((L0 + 16.0) / 116.0) ** 3
            g = max(0.2, min(5.0, y_t / max(y_0, 1e-6)))
            print('  exposure gain %.3f (face_L %.1f -> %.1f)' % (g, L0, target_face_L))
            out = sf.simulate(np.clip(lin * g, 0.0, None), p, digest_params_first=True)
    a = np.clip(np.asarray(out, dtype=np.float64), 0, 1)
    return Image.fromarray((a * 255 + 0.5).astype(np.uint8))


def _face_L(im) -> float | None:
    import numpy as np
    sys.path.insert(0, r'E:\svStudio\svFilm')
    from design.skin import face_mask as FM
    rgb = np.asarray(im)
    m = FM.face_skin(rgb) > 0.5
    if int(m.sum()) < 300:
        return None
    from svFilm import color
    lab = color.to_lab(np.ascontiguousarray(rgb.astype(np.float64) / 255.0))
    return float(np.median(lab[..., 0][m]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--raf', required=True)
    ap.add_argument('--film', required=True)
    ap.add_argument('--paper', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--target-face-L', type=float, default=None)
    a = ap.parse_args(argv)
    im = render_pair(a.raf, a.film, a.paper, target_face_L=a.target_face_L)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or '.', exist_ok=True)
    im.save(a.out)
    print('OK', a.film, a.paper, '->', a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
