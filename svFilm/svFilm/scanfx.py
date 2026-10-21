# -*- coding: utf-8 -*-
"""**扫描仪段补偿**（`scan_film=True` 时用）—— 在 svFilm 这层做，**不动 vendored 的 spektrafilm**。"""
from __future__ import annotations

import numpy as np

# ★ **诊断变量**：本模块的"静默降级"（缺 scipy / 缺 color）**不抛异常**、只原样返回 ⇒
_CHROMA_LAST_ERROR: str | None = None


def _pct(a, q):
    return float(np.percentile(a, q))


def chroma_blur(rgb, cfg):
    """★★★ **扫描色度模糊** —— 补上真实胶片扫描的'色度被模糊'这一层。"""
    global _CHROMA_LAST_ERROR
    _CHROMA_LAST_ERROR = None
    if not bool(getattr(cfg, 'CHROMA_BLUR_ENABLE', False)):
        return rgb
    w_rel = float(getattr(cfg, 'CHROMA_BLUR_W', 0.004) or 0.0)
    if w_rel <= 0.0:
        return rgb
    import sys as _sys
    _here = _sys.modules[__name__].__package__
    _color = __import__(_here + '.color', fromlist=['x'])
    a = np.asarray(rgb, np.float64)
    lab = _color.to_lab(np.clip(a, 0.0, 1.0))
    try:
        from scipy.ndimage import gaussian_filter
    except Exception as e:                                                 # noqa: BLE001
        # ⚠ 不抛（调用方是批处理路径）—— 但**记下来**，否则永远静默失效。
        _CHROMA_LAST_ERROR = ('scipy 不可用（%s: %s）⇒ 色度模糊被跳过；'
                              'CHROMA_BLUR_ENABLE=True 也**不会**生效'
                              % (type(e).__name__, e))
        return rgb
    sig = max(0.3, w_rel * a.shape[1])
    lab[..., 1] = gaussian_filter(lab[..., 1], sig, mode='nearest')
    lab[..., 2] = gaussian_filter(lab[..., 2], sig, mode='nearest')
    out = _color.from_lab(np.clip(lab, [0.0, -128.0, -128.0], [100.0, 127.0, 127.0]))
    return np.clip(out, 0.0, 1.0)


def auto_levels(rgb, cfg, mask=None):
    """每通道自动色阶（模拟扫描仪的负片转正）。`cfg` 键：`SCANFIX_ENABLE`（默认关）·"""
    if not bool(getattr(cfg, 'SCANFIX_ENABLE', False)):
        return rgb
    a = np.asarray(rgb, np.float64)
    lo_q = float(getattr(cfg, 'SCANFIX_LO', 0.5) or 0.0)
    hi_q = float(getattr(cfg, 'SCANFIX_HI', 99.5) or 100.0)
    src = a if mask is None else a[np.asarray(mask, bool)]
    if src.size < 64:
        return rgb
    mode = str(getattr(cfg, 'SCANFIX_MODE', 'per_channel') or 'per_channel')
    if mode == 'luma':
        y = a @ np.array([0.2126, 0.7152, 0.0722])
        yl, yh = _pct(y if mask is None else y[np.asarray(mask, bool)], lo_q), \
            _pct(y if mask is None else y[np.asarray(mask, bool)], hi_q)
        out = (y - yl) / max(yh - yl, 1e-6)
        out = np.clip(out, 0.0, 1.0)[..., None]
        return out * a / np.maximum(y[..., None], 1e-6)
    out = np.empty_like(a)
    for c in range(3):
        v = src[..., c]
        bl, wh = _pct(v, lo_q), _pct(v, hi_q)
        out[..., c] = (a[..., c] - bl) / max(wh - bl, 1e-6)
    out = np.clip(out, 0.0, 1.0)
    clip = float(getattr(cfg, 'SCANFIX_CLIP', 0.0) or 0.0)
    if clip > 0.0:
        out = out * (1.0 - clip)
    return out
