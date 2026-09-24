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
    """最深阴影 / 阴影 / 高光 三组软权重（用画面自己的分位定断点 ⇒ 内容归一）。

    `w_deep` 是 09-24 加的：量出来**我们最深的阴影偏蓝**（相对整张中位 b* −1.54，
    而小红书那批是 −0.41、鹿井 +1.16），单靠 `w_sh` 那段盖不住最底那一小段。
    """
    lo25, hi75 = np.percentile(L, 25.0), np.percentile(L, 75.0)
    span = max(hi75 - lo25, 1.0)
    w_sh = 1.0 - _ramp(L, lo25, lo25 + 0.75 * span)   # 越暗越接近 1
    w_hi = _ramp(L, hi75 - 0.75 * span, hi75)         # 越亮越接近 1
    p10 = float(np.percentile(L, 10.0))
    w_deep = 1.0 - _ramp(L, p10, p10 + 0.25 * span)   # 只有最底那 10% 附近
    return w_sh, w_hi, w_deep


# ---------------------------------------------------------------------------
# L3 混色：按色相带调彩度（软过渡，不是硬切 8 个色相）
# ---------------------------------------------------------------------------
# ★★ 靶子（09-24 迭代）：**小红书胶片人像话题的观众审美**。
#   参考集：`大师作品/xhs_抓取/`（72 个文件夹 413 张，抽样 80）。
#   口径 = 该色相带的 C 中位 ÷ 整张 C 中位，**内容归一**。
#
#   | 色相带   | 小红书 | 鹿井 | 我们(未迭代) | 我们该动 |
#   |---------|-------|------|------------|---------|
#   | 0-30 红  | 2.27  | 2.99 | 2.57 | ×1.04 ⇒ 不动 |
#   | 30-60 橙 | 2.74  | 3.60 | 2.79 | **不动（已经在位）** |
#   | 60-90 黄 | 2.55  | 3.01 | 2.67 | ×1.05 |
#   | 90-120 黄绿| 2.47 | 2.51 | 2.56 | ×0.82 |
#   | 120-150 绿| 2.28 | 3.08 | 2.51 | ×1.00 |
#   | 150-180 青绿| 1.75| 2.50 | 2.00 | ×1.00 |
#   | 180-210 青| 1.78 | 2.15 | 1.85 | ×1.00 |
#   | 210-240 蓝| 1.92 | 2.31 | 2.05 | ×1.00 |
#   | 240-270 蓝紫| 2.05| 2.43 | 2.18 | ×0.86 |
#   | 270-300 紫| 2.51 | 2.72 | 2.42 | ×0.97 |
#   | 300-330 品红| 1.74| 2.09 | 2.09 | **×0.83** |
#   | 330-360 粉红| 1.82| 2.40 | 2.22 | **×0.82** |
#
#   ⇒ 一句话：**肤色（橙）保持，只压"杂色"（黄绿 / 蓝紫 / 品红 / 粉红）。**
#     这就是那几篇调色教程反复说的「**刻意控制色彩数量**」——
#     干净不是把整张降饱和，是**让少数色相占主导、把边缘色相收掉**。
#   ⚠ 注意跟上一版的区别：上一版按鹿井，是"暖色提、冷色压"；按小红书反过来 ——
#     小红书那批**整体更素**（相对彩度 1.7~2.5），而且**橙正好在位**，不用再加。
BANDS = (
    (15.0, 34.0, -0.08, +3.0),     # 红（小红书 ×1.04，按"不动"处理）
    (45.0, 34.0, +0.20, +2.0),     # 橙 / 肤色（**保留**：量出来正好在位）
    (75.0, 34.0, -0.05, 0.0),      # 黄
    (105.0, 34.0, -0.18, -3.0),    # 黄绿（教程：绿要降饱和）
    (135.0, 34.0, 0.00, 0.0),      # 绿
    (165.0, 34.0, 0.00, +3.0),     # 青绿
    (255.0, 34.0, -0.14, +3.0),    # 蓝紫
    (285.0, 34.0, -0.03, +6.0),    # 紫
    (315.0, 34.0, -0.32, +4.0),    # 品红
    (345.0, 34.0, -0.24, +4.0),    # 粉红
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

def apply(disp, cfg=C, stock=None):
    """在**成片**（显示域）上做分色 + 混色。

    @returns {(numpy.ndarray, dict)} 出图 + 报告（能自查动了多少）
    """
    if not bool(getattr(cfg, 'GRADE_ENABLE', False)):
        return np.clip(np.asarray(disp, np.float64), 0.0, 1.0), dict(applied=False)

    # ★★ 靶按**预设**取（同 tone）
    try:
        from . import targets as _T
        _tg = _T.for_stock(stock)
    except Exception:                                          # noqa: BLE001
        _tg = None
    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    lab = color.to_lab(d)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    Lm, am, bm = float(np.median(L)), float(np.median(a)), float(np.median(b))

    # ---------- L2 色彩分级 ----------
    w_sh, w_hi, w_deep = _sh_hi_weights(L, cfg)
    # ★★ 分色：**逐图往靶收**（跟影调层一个哲学）——
    #   先量"这张图当前的分色"，再补到大师的绝对值。**不是**加一个固定偏移：
    #   ① 固定偏移跟"分色测值"不是 1:1（加 a 会同时动整体中位）
    #   ② 换条预设引擎出来的底就不一样，"固定偏移"立刻失准
    #   ⚠ 分色是**相对量**，不像影调那样"往上没数据" ⇒ 这里**可以双向**补，但要有上限。
    _bg = (_tg or {}).get('band_gain') or [0.0] * 12
    _lim = float(getattr(cfg, 'GRADE_SPLIT_LIMIT', 2.5))
    if _tg and _tg.get('sh_abs'):
        _p25, _p90 = np.percentile(L, 25.0), np.percentile(L, 90.0)
        _msh, _mhi = L <= _p25, L >= _p90
        cur = (float(a[_msh].mean() - am), float(b[_msh].mean() - bm),
               float(a[_mhi].mean() - am), float(b[_mhi].mean() - bm))
        tgt = (float(_tg['sh_abs'][0]), float(_tg['sh_abs'][1]),
               float(_tg['hi_abs'][0]), float(_tg['hi_abs'][1]))
        d4 = [float(np.clip(tgt[i] - cur[i], -_lim, _lim)) for i in range(4)]
        sha, shb, hia, hib = d4
    else:
        sha, shb = float(getattr(cfg, 'GRADE_SH_A', 0.0)), float(getattr(cfg, 'GRADE_SH_B', 0.0))
        hia, hib = float(getattr(cfg, 'GRADE_HI_A', 0.0)), float(getattr(cfg, 'GRADE_HI_B', 0.0))
    dpa, dpb = float(getattr(cfg, 'GRADE_DEEP_A', 0.0)), float(getattr(cfg, 'GRADE_DEEP_B', 0.0))
    da2 = sha * w_sh + hia * w_hi + dpa * w_deep
    db2 = shb * w_sh + hib * w_hi + dpb * w_deep
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
    for bi, (center, half, k, dL) in enumerate(BANDS):
        # ★ 色相带增益 = 预设自带的那份（按大师量出来的），没有专属靶就是 0
        k = k + float(_bg[bi]) if bi < len(_bg) else k
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
    # ★★ 归一：**把整张彩度中位拉回原值**（只重新分配、不改总量）。
    #   为什么必须有这一步：靶子是「某色相带的 C ÷ 整张 C 中位」——
    #   分母一动，所有带的比值都跟着动。09-24 实测过：不归一的时候，
    #   我把黄绿/蓝紫/品红**往下压**，结果"相对彩度"反而**全线上升**
    #   （分母被压小了）⇒ 看数会得出完全反的结论。
    #   归一之后，"哪几个带变艳/变素"才是真的。
    #   `GRADE_SAT` 是**另一个**旋钮：整体更素/更艳（默认 1.0 = 总量不动）。
    # ⚠⚠ 09-24 修了一个我自己引入的 bug：原来 `_m0/_m1` 取的是**彩色像素（live>0.5）的中位**，
    #   但乘的时候乘到了**所有**像素上 ⇒ 灰像素被多乘一次 ⇒ 整张彩度**虚涨 46%**、
    #   画面发飘发白（SV 一眼看出"脸崩了"）。
    #   ⇒ 改成**整张中位**归一 —— 这也正好跟靶的口径一致（靶 = 带内 C ÷ **整张** C 中位）。
    _sat = float(getattr(cfg, 'GRADE_SAT', 1.0))
    _m0 = float(np.median(Cc))
    _m1 = float(np.median(newC))
    newC = newC * (_sat * _m0 / max(_m1, 1e-6)) if _m1 > 1e-6 else newC
    a = a * (newC / nz)
    b = b * (newC / nz)

    # 亮度偏移：只作用在有颜色的地方（灰区不动）
    L = np.clip(L + dl * live, 0.0, 100.0)

    # ===================== L4 肤色（09-24 重做） =====================
    # ★★★ 为什么重做：原来用**色相窗（9~61°）**定位肤色 —— 实测它覆盖的像素里
    #   **只有 10.5% 是真皮肤**，其余 89.5% 是墙/木头/黄叶
    #   ⇒ 调的不是脸、是把背景提亮了（"脸崩了"就是这个来的）。
    #   ⇒ 现在用**人脸皮肤掩膜**（`face.py`，MediaPipe，跟 Sony / rodrigorcz 两家同源）。
    # ★ 三件事都往靶收（不是只调亮度）：相对亮度 · 相对彩度 · **色相角**
    #   （量出来我们跟増田的差是「色相角偏黄 5.7°」+「彩度偏素 0.96」，光调亮度救不了）
    # ★ 脸 / 身体分开：脸严格、身体宽一点（都有掩膜）
    # ★ 提亮量大的时候对肤色区做一次**双边滤波**（rodrigorcz 那篇的做法，治提亮后的色阶断裂）
    _dl = _dc = _dh = 0.0
    _mask_src = 'none'
    if _tg and _tg.get('skin_l') is not None:
        _lim_l = float(getattr(cfg, 'GRADE_SKIN_LIMIT_L', 6.0))
        _lim_c = float(getattr(cfg, 'GRADE_SKIN_LIMIT_C', 0.0))
        _lim_h = float(getattr(cfg, 'GRADE_SKIN_LIMIT_H', 8.0))
        _w = None
        try:                                        # ① 先试真脸掩膜
            from . import face as _F
            _r = _F.parse(np.clip(disp, 0.0, 1.0))
            _mk = (_r or {}).get('masks') or {}
            if _mk.get('face_skin') is not None:
                _w = np.clip(_mk['face_skin'] * 1.6, 0.0, 1.0)          # 脸：严格
                if _mk.get('skin') is not None:
                    _w = np.maximum(_w, np.clip(_mk['skin'], 0.0, 1.0)
                                    * float(getattr(cfg, 'GRADE_SKIN_BODY_W', 0.5)))
                _mask_src = 'face'
        except Exception:                                            # noqa: BLE001
            _w = None
        if _w is None or float(_w.max()) < 0.05:    # ② 没检出脸 ⇒ 退回色相窗（有总比没有好）
            _w = _band_weight(H, 35.0, 26.0) * live * 0.6
            _mask_src = 'hue'
        _sel = _w > 0.5
        if float(_w.max()) > 0.05 and bool(_sel.any()):      # ★ 必须检查非空：空窗口时
            _Cc2 = np.sqrt(a * a + b * b)                   #   np.median([]) = nan ⇒ 整张被写成 nan

            _cL = float(np.median(L[_sel]) - np.median(L))
            _cC = float(np.median(_Cc2[_sel])) / max(float(np.median(_Cc2)), 1e-6)
            _cH = float(np.degrees(np.arctan2(float(np.median(b[_sel])),
                                              float(np.median(a[_sel])))) % 360.0)
            _dl = float(np.clip(float(_tg['skin_l']) - _cL, -_lim_l, _lim_l))
            _dc = float(np.clip(float(_tg['skin_c']) / max(_cC, 1e-6) - 1.0, -_lim_c, _lim_c))
            _dh = float(np.clip(((float(_tg['skin_hue']) - _cH + 180.0) % 360.0) - 180.0,
                                -_lim_h, _lim_h))
            L = np.clip(L + _dl * _w, 0.0, 100.0)
            _k = 1.0 + _dc * _w
            a = a * _k
            b = b * _k
            _th = np.radians(_dh) * _w                    # 色相绕原点转（往靶的色相角）
            _ca, _sa = np.cos(_th), np.sin(_th)
            a, b = a * _ca - b * _sa, a * _sa + b * _ca
            # ★ 提亮量大 ⇒ 对肤色区的 L 做一次弱双边滤波（保边去噪，别把脸磨平）
            if abs(_dl) >= float(getattr(cfg, 'GRADE_SKIN_BILATERAL_EV', 4.0)):
                try:
                    import cv2 as _cv
                    _L8 = np.clip(L * 2.55, 0, 255).astype(np.uint8)
                    _L8 = _cv.bilateralFilter(_L8, 5, 8.0, 5.0)
                    _Lf = _L8.astype(np.float64) / 2.55
                    L = np.clip(L * (1.0 - _w) + _Lf * _w, 0.0, 100.0)
                except Exception:                                # noqa: BLE001
                    pass
    out = np.clip(color.from_lab(np.stack([L, a, b], -1)), 0.0, 1.0)
    info = dict(applied=True,
                L50_in=Lm, L50_out=float(np.median(color.to_lab(out)[..., 0])),
                a_med_in=am, b_med_in=bm,
                d_sh=(float(sha), float(shb)), d_hi=(float(hia), float(hib)),
                c_gain=[(c, (k + (_bg[i] if i < len(_bg) else 0.0))) for i, (c, _, k, _) in enumerate(BANDS)],
                target=(sha, shb, hia, hib), stock=stock,
                skin_dL=_dl, skin_dC=_dc, skin_dH=_dh, skin_mask=_mask_src)
    return out, info
