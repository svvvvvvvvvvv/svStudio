# -*- coding: utf-8 -*-
r"""**扫描仪段补偿**（`scan_film=True` 时用）—— 在 svFilm 这层做，**不动 vendored 的 spektrafilm**。

上游 `color_reference.py` 对负片**故意不做黑白校正**（负片没有绝对的黑白参考，黑白由冲扫流程与
**扫描仪软件**定）⇒ `io.scan_film = True` 时引擎输出**没有被锚定**。真实扫描仪做：① 反相（引擎已做）
② 按片基定黑白点 ③ 每通道自动色阶 ⇒ 本模块实现 ② ③：**每通道按分位定黑白点、线性拉伸**（默认**关**）。
"""
from __future__ import annotations

import numpy as np

# ★ **诊断变量**：本模块的"静默降级"（缺 scipy / 缺 color）**不抛异常**、只原样返回 ⇒
#   调用方的 try/except **抓不到** ⇒ 调用前清空、降级时写原因，由 `presets._post_scan` 报警。
_CHROMA_LAST_ERROR: str | None = None


def _pct(a, q):
    return float(np.percentile(a, q))


def chroma_blur(rgb, cfg):
    r"""★★★ **扫描色度模糊** —— 补上真实胶片扫描的"色度被模糊"这一层。

    真实扫描链里色度分辨率天然低于亮度 ⇒ "胶片质感"含"色度更柔"这一层（尺子 =「色度/亮度锐度比」）。
    做法：在 **CIELAB 的 a*/b* 上**做高斯模糊（L* 不动）；σ 用**画面宽度比例**。
    ⚠⚠ 这是**减弱型效果**：失效时（缺 scipy / σ=0 / 开关关）画面只是"更锐"、**不会报警** ⇒ 必须把
      原因记进 `_CHROMA_LAST_ERROR` 让调用方喊一声。"""
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
    r"""每通道自动色阶（模拟扫描仪的负片转正）。`cfg` 键：`SCANFIX_ENABLE`（默认关）·
    `SCANFIX_MODE` `'per_channel'`/`'luma'` · `SCANFIX_LO`/`HI` 分位 · `SCANFIX_ONLY_DARK`（调试）。"""
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
