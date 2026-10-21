# -*- coding: utf-8 -*-
"""svFilm 的全部可调参数 —— **一个文件，别处不许写死数**。"""
from __future__ import annotations

import os

VERSION = '3.0'



def key_signature(mod=None):
    """把「会影响出图的 config 值」压成一个**可哈希、可比较**的指纹（用途：段缓存键 /"""
    import sys as _sys
    m = mod if mod is not None else _sys.modules[__name__]
    out = []
    for k in dir(m):
        if k.startswith('_'):
            continue
        if k in CACHE_KEY_EXTRA or k.startswith(CACHE_KEY_PREFIXES):
            v = getattr(m, k)
            try:
                hash(v)
                out.append((k, v))
            except TypeError:
                out.append((k, repr(v)))
    return tuple(sorted(out))

# ===========================================================================
STOCK = '鹿井预设'

# ===========================================================================
PUBLIC_WB = 'custom'
# ★★ 10-16 重标（偏黄绿专题）：**色温 / 色调必须一起动** ——
PUBLIC_WB_TEMPERATURE = 4600.0
PUBLIC_WB_TINT = 0.97
PUBLIC_COLORSPACE = 'ProPhoto RGB'

# **入口解码快速通道**（`fastraw.py`）：RAW 只开一次 + 白点适应/tint/空间换算合成一次 3×3。
FAST_RAW = True

# **磁盘缓存**（`diskcache.py`）：跨进程/重启，只存「与胶片参数无关」的东西 ——
RAW_CACHE = True                # 解码缓存
CACHE_DIR = None                # None ⇒ `<仓库>/.cache`；给空串 ⇒ 关掉磁盘缓存
CACHE_MAX_MB = 8000             # 超过就按最后访问时间清（8 GB ≈ 240 张 @2048）

# **扫描仪段补偿**（`scan_film=True` 时用；默认关）：上游 `color_reference.py` 在"扫负片"这条
SCANFIX_ENABLE = False          # ← 打开才生效
SCANFIX_MODE = 'per_channel'    # 'per_channel'（模拟片基中性）| 'luma'（单条亮度拉伸）
SCANFIX_LO = 0.5                # 黑点取哪个分位（%）
SCANFIX_HI = 99.5               # 白点取哪个分位（%）
SCANFIX_CLIP = 0.0              # 拉伸后统一压一点，避免噪声顶成纯白（0 = 不压）

# **扫描色度模糊**：尺子「色度/亮度 锐度比」大师 **0.60**、我们 **1.03**（相机直出 0.71）⇒
CHROMA_BLUR_ENABLE = True
CHROMA_BLUR_W = 0.0015          # σ = 该比例 × 画面宽度（0.0015 × 2000px ≈ 3px）





# ========== 输入 ==========
MAX_SIDE = 2048                 # 工作分辨率（长边）

# ★★★ `film_render.grain.blur` 的**标定基准长边**（改了会让预览 = 交付物）：它在 vendor 里是
GRAIN_BLUR_REF_LONG_SIDE = 6264

# ★★★ `scanner.lens_blur` 的**标定基准长边**（与 `grain.blur` 同一套机制）：vendor
LENS_BLUR_REF_LONG_SIDE = 6264

# ★★★ halation 的**绿层核宽度 ÷ 红层核宽度**（物理常数，不是风格旋钮）：真实 halation 是
HALATION_G_SIGMA_RATIO = 0.45
# ★★★ **测光看人**（`subject`）：**只能改「看哪里」（测光方式，输入侧），不能改「看多少」
PERSON_METERING = True
#   ★★★ **它是否在岗取决于锚的 `w`**：`w = 1.0` 时 `ev` 被换成常数 ⇒ 本键成 **no-op**；

# ===========================================================================
EXPOSURE_ANCHOR_W = 0.6         # 锚打底 + 人本（测光拿回 40%）；1.0 = 锚独裁
EXPOSURE_ANCHOR_B = 1.1181      # 1.578 - 0.4x1.1498（整卷 52 张生产口径读数均值）
EXPOSURE_ANCHOR_W_FACE = 0.1186
EXPOSURE_ANCHOR_B_FACE = 0.0398





# ---- 出图缓存（只给常驻服务用）----
CACHE_ENABLE = True
CACHE_MAX_SETS = 4              # 最多留几套（一套 = 一张图 × 一卷 × 一组参数，约 16 MB）


# ==========================================================================
PRESET_APPLY_STOCK_SPECIFICS = False
# ★★★ **配平基准 = 引擎数据库**（`neutral` 是"18% 灰在印相里完全中性"的起始设置），
PRESET_FILTER_M_TRIM = 0.0
# ──────────────────────────────────────────────────────────────────────────
PRESET_PE_SHIFT_FROM_SPEK = True    # 预设自带 pe 是否再乘 `SPEK_PE_SHIFT`
# 全部预设的落点平移系数。1.0 = 照用预设自带的印相曝光。
SPEK_PE_SHIFT = 1.0

# ---- ★★ spektrafilm 的加速开关（vendor 出厂是关的，我们打开）----
SPEK_USE_LUT = True
SPEK_FAST_STATS = True

# ---- io.py ----

# ===========================================================================
JPEG_QUALITY = 95
