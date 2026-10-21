# -*- coding: utf-8 -*-
"""入口解码的**快速通道**：RAW 只开一次 + 一次 3×3 合并矩阵。"""
from __future__ import annotations

import io as _sio
import os

import numpy as np

from . import config as C

_PROBE = np.float32(0.5)
_MCACHE: dict = {}


def _vendor():
    r"""拿到 vendor 的 `raw_file_processor` 模块（必须已经钉扎过 sys.path）。"""
    from spektrafilm.utils import raw_file_processor as R
    return R


def combined_matrix(adapt, tint, colorspace=None):
    """把「白点适应 + tint + 空间换算」合成一个 3×3（float32）。"""
    import colour

    cs = colorspace or getattr(C, 'PUBLIC_COLORSPACE', 'ProPhoto RGB')
    key = (None if adapt is None else (tuple(np.asarray(adapt[0], np.float64).ravel()),
                                       tuple(np.asarray(adapt[1], np.float64).ravel())),
           None if tint is None else float(tint), str(cs))
    hit = _MCACHE.get(key)
    if hit is not None:
        return hit

    R = _vendor()
    probe = (np.eye(3, dtype=np.float32) * _PROBE).reshape(1, 3, 3)
    x = probe
    if adapt is not None:
        x = R._apply_white_balance_adaptation(x, adapt[0], adapt[1])
    x = R._apply_tint_adjustment(x, tint)
    if str(cs) != 'ACES2065-1':
        x = colour.RGB_to_RGB(
            x,
            input_colourspace=R._ACES_COLOURSPACE,
            output_colourspace=colour.RGB_COLOURSPACES[str(cs)],
            apply_cctf_decoding=False,
            apply_cctf_encoding=False,
        )
    # x[0] 的 3 行 = 3 个单位探针的输出 ⇒ 列拼起来就是矩阵；用 float64 存，
    M = (np.asarray(x, np.float64)[0] / float(_PROBE)).T
    _MCACHE[key] = M
    return M


def _thumb_exif(im_transpose, raw):
    r"""从内嵌缩略图抠 EXIF / 机型；失败一律返回空（不许因为它打断解码）。"""
    import rawpy
    from PIL import Image, ImageOps

    make = model = exif = None
    try:
        th = raw.extract_thumb()
        if th.format == rawpy.ThumbFormat.JPEG:
            tmp = ImageOps.exif_transpose(Image.open(_sio.BytesIO(th.data)))
            ex = tmp.getexif()
            make = str(ex.get(271) or '') or None
            model = str(ex.get(272) or '') or None
            exif = im_transpose(tmp)
    except Exception:                                   # noqa: BLE001
        pass
    return exif, make, model


def decode(path, max_side=None, wb=None, exif_fix=None, use_cache=None):
    """RAW → 场景线性（目标色彩空间，float64）。**一次打开 RAW**。"""
    import rawpy

    R = _vendor()

    _wb = wb or {}
    wb_mode = str(_wb.get('white_balance', getattr(C, 'PUBLIC_WB', 'custom')))
    temp = float(_wb.get('temperature', getattr(C, 'PUBLIC_WB_TEMPERATURE', 5200.0)))
    tint = float(_wb.get('tint', getattr(C, 'PUBLIC_WB_TINT', 1.0)))
    cs = str(getattr(C, 'PUBLIC_COLORSPACE', 'ProPhoto RGB'))

    from . import diskcache as _dc
    if use_cache is None:
        use_cache = bool(getattr(C, 'RAW_CACHE', True)) and _dc.enabled() and max_side is not None
    _k = _dc.raw_key(path, max_side, _wb, cs) if use_cache else None
    if _k is not None:
        hit = _dc.raw_get(_k)
        if hit is not None:
            lin32, exif, make, model = hit
            return np.asarray(lin32, np.float64), exif, make, model

    with rawpy.imread(str(path)) as raw:
        # ① 缩略图（EXIF / 机型）—— 不单开一次 RAW，省一次文件头解析
        exif, make, model = _thumb_exif(exif_fix, raw) if exif_fix else (None, None, None)
        # ② 一次解码到 ACES 线性（16-bit，禁自动提亮，无 CCTF）
        params, adapt, tint_mult = R._postprocess_params(wb_mode, temp, tint)
        rgb = raw.postprocess(**params).astype(np.float32) / np.float32(65535.0)

    # ③ 合并矩阵（一次乘完 WB 适应 + tint + 空间换算），顺序与原链一致：
    M = combined_matrix(adapt, tint_mult).astype(np.float32)
    n = rgb.shape[0] * rgb.shape[1]
    lin = (rgb.reshape(n, 3) @ M.T).reshape(rgb.shape)
    del rgb
    np.clip(lin, 0.0, None, out=lin)

    from . import io as _io
    lin = _io._resize(lin, max_side)
    if _k is not None:
        _dc.raw_put(_k, lin, exif, make, model)
    return np.asarray(lin, np.float64), exif, make, model


def probe_drift(n=256, seed=0):
    """自检用：把随机图同时过「合并矩阵」和「vendor 原链」，报最大/平均差。"""
    import colour

    R = _vendor()
    rng = np.random.default_rng(seed)
    src = rng.random((n, n, 3), dtype=np.float32) * 1.2 - 0.1

    params, adapt, tint = R._postprocess_params(
        getattr(C, 'PUBLIC_WB', 'custom'),
        getattr(C, 'PUBLIC_WB_TEMPERATURE', 5200.0),
        getattr(C, 'PUBLIC_WB_TINT', 1.0))
    cs = str(getattr(C, 'PUBLIC_COLORSPACE', 'ProPhoto RGB'))

    a = src
    if adapt is not None:
        a = R._apply_white_balance_adaptation(a, *adapt)
    a = R._apply_tint_adjustment(a, tint)
    if cs != 'ACES2065-1':
        a = colour.RGB_to_RGB(a, input_colourspace=R._ACES_COLOURSPACE,
                              output_colourspace=colour.RGB_COLOURSPACES[cs],
                              apply_cctf_decoding=False, apply_cctf_encoding=False)
    a = np.clip(np.asarray(a, np.float64), 0.0, None)

    M = combined_matrix(adapt, tint)
    b = (src.reshape(-1, 3).astype(np.float64) @ M.T).reshape(src.shape)
    np.clip(b, 0.0, None, out=b)

    d = np.abs(a - b)
    return float(d.max()), float(d.mean())


def on() -> bool:
    return bool(getattr(C, 'FAST_RAW', True))
