# -*- coding: utf-8 -*-
r"""二次调色（胶片引擎**之后**的分色层）—— L2 分色 + L3 混色。

边界：`tone.py` 管**影调**（明度分布），这里管**颜色**。两步都在成片（显示域）上做。

## ★★★ 一条铁律：这里**不做"曝光"**
成片是 8bit 显示域，乘 `2^EV` = 放大已经量化过的数据 ⇒ 提亮 = 拉噪声、高光立刻切白
（反面教材：digitalFilm 的 `ExposureModule` 在显示域每级 clamp，实测 1/6 像素切白）。
**整体亮暗回引擎那一步改**（`pe` / `SPEK_PE_SHIFT`，那里是线性域、有高光余量）。
这一层只做**按亮度/色相加权的染色与塑形**。

## 数值从哪来（去量鹿井 32 张，不靠拍脑袋）
素材：`E:\WorkBuddy\摄影助手\大师作品\鹿井\`（32 张）× 我们的成片（919 十张）。

### L2 色彩分级（Lab 的 a*/b*，都相对整张的中位）
| | 鹿井 32 | 我们 10 | 差（鹿井 − 我们） |
|---|---|---|---|
| 暗部 Δa* | −1.48 | −0.60 | **−0.88** |
| 暗部 Δb* | +0.75 | −2.13 | **+2.88** |
| 亮部 Δa* | +0.77 | +0.61 | +0.17 |
| 亮部 Δb* | +0.23 | +0.74 | −0.51 |

⇒ **我们暗部太蓝（b* 差 2.88）、不够绿（a* 差 0.88）；亮部基本已经在位。**
⚠ 注意这与那份 LR 教程说的「阴影加青蓝」**不一致** —— 教程是作者针对**他自己那张图**的动作，
而这是**量他 32 张成片**得到的平均形状。以量到的为准。

### L3 混色（按色相带，**归一后**：相对彩度 = 带内 C / 全图 C 中位）
| 色相带 | 鹿井 | 我们 | 要动 |
|---|---|---|---|
| 0–30° 红 | 2.97 | 2.52 | C ×1.18 |
| 30–60° 橙/肤 | 3.60 | 2.63 | C ×1.37 |
| 60–90° 黄 | 3.02 | 2.75 | C ×1.10 |
| 90–120° 黄绿 | 2.53 | **2.97** | C ×0.85 |
| 120–150° 绿 | 3.01 | 2.63 | C ×1.14 |
| 150–180° 青绿 | 2.49 | 2.14 | C ×1.16 |
| 180–210° 青 | 2.16 | 2.10 | 不动 |
| 210–240° 蓝 | 2.31 | 2.26 | 不动（只提亮） |
| 240–270° 蓝紫 | 2.43 | **2.67** | C ×0.91 |
| 270–300° 紫 | 2.72 | **2.91** | C ×0.93 |
| 300–360° | ≈ | ≈ | 不动 |

⇒ **暖色（红/橙/绿/青绿）我们彩度不够，冷色（黄绿/蓝紫/紫）太艳。**
这一点跟教程里「绿降饱和、蓝往青走」的**方向**是合的（只是具体数值要按量到的来）。
⚠ 归一之后仍然是我方内容 vs 他的内容，所以**只取"符号一致、量级 >10%"的带**，弱差别不动。
"""
from __future__ import annotations

import numpy as np

from . import color
from . import config as C


# ---------------------------------------------------------------------------
# L2 色彩分级：按亮度加权的 a*/b* 偏移
# ---------------------------------------------------------------------------
def _ramp(x, lo, hi):
    """0→1 的平滑窗（lo 处 0、hi 处 1；lo>hi 时反向）。"""
    if hi == lo:
        return np.zeros_like(x, dtype=np.float64)
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _sh_hi_weights(L, cfg):
    """阴影 / 高光两组软权重（用画面自己的分位定断点 ⇒ 内容归一）。"""
    lo25, hi75 = np.percentile(L, 25.0), np.percentile(L, 75.0)
    span = max(hi75 - lo25, 1.0)
    l0 = lo25                                     # 阴影断点
    h0 = hi75
    w_sh = 1.0 - _ramp(L, l0, l0 + 0.75 * span)   # 越暗越接近 1
    w_hi = _ramp(L, h0 - 0.75 * span, h0)         # 越亮越接近 1
    return w_sh, w_hi


# ---------------------------------------------------------------------------
# L3 混色：按色相带调彩度（软过渡，不是硬切 8 个色相）
# ---------------------------------------------------------------------------
# 每项 (中心色相°, 半宽°, 彩度增益 k, 亮度偏移 ΔL*)
# ⚠ 只放"符号一致且量级 >10%"的带；弱差别不动（避免把内容差当成风格差）
BANDS = (
    (15.0, 34.0, +0.18, +4.0),     # 红
    (45.0, 34.0, +0.30, +2.0),     # 橙 / 肤色
    (75.0, 34.0, +0.10, 0.0),      # 黄
    (105.0, 34.0, -0.15, -3.0),    # 黄绿（教程：绿要降饱和）
    (135.0, 34.0, +0.14, 0.0),     # 绿
    (165.0, 34.0, +0.16, +4.0),    # 青绿
    (255.0, 34.0, -0.09, +4.0),    # 蓝紫
    (285.0, 34.0, -0.07, +6.0),    # 紫
)


def _band_weight(H, center, half):
    """色相软窗（cos 过渡，绕环）。"""
    d = np.abs(((H - center + 180.0) % 360.0) - 180.0)
    if d.max() <= half:
        pass
    w = np.clip(1.0 - (d - half * 0.35) / (half * 0.65), 0.0, 1.0)
    return w * w * (3.0 - 2.0 * w)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def apply(disp, cfg=C):
    """在**成片**（显示域）上做分色 + 混色。

    @returns {(numpy.ndarray, dict)} 出图 + 报告（能自查动了多少）
    """
    if not bool(getattr(cfg, 'GRADE_ENABLE', False)):
        return np.clip(np.asarray(disp, np.float64), 0.0, 1.0), dict(applied=False)

    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    lab = color.to_lab(d)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    Lm, am, bm = float(np.median(L)), float(np.median(a)), float(np.median(b))

    # ---------- L2 色彩分级 ----------
    w_sh, w_hi = _sh_hi_weights(L, cfg)
    sha, shb = float(getattr(cfg, 'GRADE_SH_A', 0.0)), float(getattr(cfg, 'GRADE_SH_B', 0.0))
    hia, hib = float(getattr(cfg, 'GRADE_HI_A', 0.0)), float(getattr(cfg, 'GRADE_HI_B', 0.0))
    da2 = sha * w_sh + hia * w_hi
    db2 = shb * w_sh + hib * w_hi
    a = a + da2
    b = b + db2

    # ---------- L3 混色（按色相带改彩度 / 亮度）----------
    Cc = np.sqrt(a * a + b * b)
    H = np.degrees(np.arctan2(b, a)) % 360.0
    # 灰色像素不动（否则会把中性轴一起推偏 —— digitalFilm 那条教训）
    cmin = float(getattr(cfg, 'GRADE_C_MIN', 12.0))
    live = _ramp(Cc, cmin * 0.6, cmin * 1.4)
    kc = np.zeros_like(Cc)
    dl = np.zeros_like(Cc)
    for center, half, k, dL in BANDS:
        w = _band_weight(H, center, half) * live
        kc += w * k
        dl += w * dL
    # 软归一：多个带重叠时不把增益叠爆
    over = np.maximum(kc, 0.0)
    scale = np.where(over > 0.45, 0.45 / np.maximum(over, 1e-6), 1.0)
    kc = kc * scale
    dl = np.clip(dl * scale, -6.0, 8.0)

    newC = np.maximum(Cc * (1.0 + kc), 0.0)
    nz = np.maximum(Cc, 1e-6)
    a = a * (newC / nz)
    b = b * (newC / nz)

    # 亮度偏移：只作用在有颜色的地方（灰区不动）
    L = np.clip(L + dl * live, 0.0, 100.0)

    out = np.clip(color.from_lab(np.stack([L, a, b], -1)), 0.0, 1.0)
    info = dict(applied=True,
                L50_in=Lm, L50_out=float(np.median(color.to_lab(out)[..., 0])),
                a_med_in=am, b_med_in=bm,
                d_sh=(float(sha), float(shb)), d_hi=(float(hia), float(hib)),
                c_gain=[(c, k) for c, _, k, _ in BANDS])
    return out, info
