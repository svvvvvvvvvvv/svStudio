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
                overrides: dict | None = None) -> Image.Image:
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
    a = np.clip(np.asarray(out, dtype=np.float64), 0, 1)
    return Image.fromarray((a * 255 + 0.5).astype(np.uint8))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--raf', required=True)
    ap.add_argument('--film', required=True)
    ap.add_argument('--paper', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    im = render_pair(a.raf, a.film, a.paper)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or '.', exist_ok=True)
    im.save(a.out)
    print('OK', a.film, a.paper, '->', a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
