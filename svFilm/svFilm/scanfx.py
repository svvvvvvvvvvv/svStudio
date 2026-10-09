# -*- coding: utf-8 -*-
r"""**扫描仪段补偿**（`scan_film=True` 时用）—— 在 svFilm 这层做，**不动 vendored 的 spektrafilm**。

## 为什么需要（10-09 实测）

上游 `color_reference.py` 在"扫负片"这条路上**故意不做黑白校正**（两处写死 `return`，
注释原文 "do not correct negative film scans"）。原因也合理：**负片没有绝对的黑白参考** ——
它的黑白是由冲扫流程与**扫描仪软件**定的，上游没法替扫描仪做。

⇒ 后果：`io.scan_film = True` 时引擎输出**没有被锚定**。合成图实测（未加本模块）：
   纯白 → L\*32（RGB≈109）· 中灰 b\* **+27**（严重偏黄）⇒ 画面塌掉。

## 这一步补的是什么

**真实冲扫店扫描仪对负片做的事**，就三件：
  ① **反相**（引擎已做：它扫的就是底片密度）
  ② **按片基定黑白点**（片基是未曝光的边缘，**中性灰**）
  ③ **每通道自动色阶**（因为底片的片基密度每通道不同 ⇒ 必须每通道独立拉，否则偏色）

⇒ 本模块实现 ② ③：**每通道按分位定黑白点，线性拉伸到 [0,1]**。
   这正是"Fiona 味"里最物理、最可复现的那一半；**剩下的色彩处理仍交给颜色层**（按 A3 的约定）。

## 用法

在引擎输出之后、颜色层之前调用（`rgb` 为 display-referred 0~1）：

    from svFilm import scanfx
    rgb = scanfx.auto_levels(rgb, cfg)

默认**关**（`SCANFIX_ENABLE=False`），打开才生效 ⇒ 对现有 10 条预设**逐位无影响**。
"""
from __future__ import annotations

import numpy as np


def _pct(a, q):
    return float(np.percentile(a, q))


def auto_levels(rgb, cfg, mask=None):
    """每通道自动色阶（模拟扫描仪的负片转正）。`rgb` 为 0~1 display-referred。

    `cfg` 读这几个键（都在 `config.py`）：
      · `SCANFIX_ENABLE` 总开关（默认关 ⇒ 原样返回）
      · `SCANFIX_MODE`   `'per_channel'`（默认，模拟片基中性）/ `'luma'`（单条亮度拉伸）
      · `SCANFIX_LO` / `SCANFIX_HI`  取哪两个分位当黑白点（默认 0.5 / 99.5）
      · `SCANFIX_CLIP`  拉伸后的软限幅（避免把噪声顶成纯白，默认 0.0 = 不额外限）
      · `SCANFIX_ONLY_DARK` 只抬黑点不压白点（调试用）
    """
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
