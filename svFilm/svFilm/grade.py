# -*- coding: utf-8 -*-
r"""二次调色（胶片引擎**之后**的颜色层）—— **L2 分色 + L3 混色 + L4 肤色**。

## ★★★ 本文件的结构纪律（09-28 拆分，SV：「职责划分干干净净、边界清晰」）

**每一段都是一个"纯函数"**：`(当前图像 + 自己的参数) → (新图像 + 自己的报告)`。
**三段之间不共享可变状态**（09-28 之前是一个 406 行的 `apply`，四件事共用 `L/a/b/Cc/live/_wn`
⇒ 改一段必然碰另一段 —— 那是"改一处另一处动"的物理根因）。

| 段 | 函数 | 只该管 | 不许碰 |
|---|---|---|---|
| **L2** | `split()` | 暗/中/高的 **色偏**（a*/b*）| 亮度、彩度 |
| **L3** | `mix()` | **彩度**（总量 + 按色相分配）| 亮度（除"带内 dL"那一项）、色偏 |
| **L4** | `skin()` | **脸的** 亮度/彩度/色相/明暗对比 | 非脸区域 |
| — | `gamut()` | 出界颜色往中性轴收 | — |
| — | `apply()` | **编排**上面四个 + 汇总报告 | **自己不写任何量** |

★ 验证方式：`_debug/_accept.py`（**统计口径**：拆前 vs 拆后，11 项指标中位数）。
★ 拆分前的基线：`效果debug/2026-09-28/_拆分前基线/`。

## ⚠⚠⚠ 一条重要教训：**"逐位对比"这个验证方法对本管线【不成立】**

09-28 拆分后第一次验收用"逐像素对比"，结果 **90% 的像素不同（最大差 139）** —— 一度以为拆错了。
**查清了：是管线本身有非确定性** —— 用**同一份代码**跑两次，结果同样有 **86~97% 的像素不同**。
原因：**人脸模型（birefnet / YuNet）的推理有非确定性** ⇒ 掩膜轻微抖动 ⇒
经"**边界羽化 + 权重归一化**"扩散 ⇒ 大片区域跟着变。

⇒ **所以本管线的验收必须用【统计口径】**（中位数/分位数，容差 1.0），不能用逐位。
⇒ 本文件拆分的验收结果：**11 项指标最大偏差 0.143**（脸L\* 0.036 · 脸C\* 0.094 · 脸hue 0.143 ·
  暗部 0.001 · L50 0.029 · 跨度 0.005 · 彩度 0.009）⇒ **零行为改变** ✅

## 边界：`tone.py` 管**影调**（明度分布），这里管**颜色**。
## ★★ 一条铁律：这里**不做"曝光"**
成片是显示域，乘 `2^EV` = 放大已经量化过的数据 ⇒ 提亮 = 拉噪声、高光立刻切白。
**整体亮暗回引擎那一步改**（`pe` / `SPEK_PE_SHIFT`，那里是线性域、有高光余量）。
这一层只做**按亮度/色相加权的染色与塑形**。
"""
from __future__ import annotations

import numpy as np

from . import color
from . import config as C


# ---------------------------------------------------------------------------
# 工具
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


def _band_weight(H, center, half):
    """色相软窗（cos 过渡，绕环）。"""
    d = np.abs(((H - center + 180.0) % 360.0) - 180.0)
    if d.max() <= half:
        pass
    w = np.clip(1.0 - (d - half * 0.35) / (half * 0.65), 0.0, 1.0)
    return w * w * (3.0 - 2.0 * w)


# ---------------------------------------------------------------------------
# L3 混色：按色相带调彩度（软过渡，不是硬切 8 个色相）
# ---------------------------------------------------------------------------
# ★★ 靶子（09-24 迭代）：**小红书胶片人像话题的观众审美**。
#   参考集：`大师作品/xhs_抓取/`（72 个文件夹 413 张，抽样 80）。
#   口径 = 该色相带的 C 中位 ÷ 整张 C 中位，**内容归一**。
#   ⇒ 一句话：**肤色（橙）保持，只压"杂色"（黄绿 / 蓝紫 / 品红 / 粉红）。**
#     这就是那几篇调色教程反复说的「**刻意控制色彩数量**」。
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


def _tgt_of(stock, scene):
    """取靶（按预设 + 场景）。失败 ⇒ None（各段自己退回 config 兜底）。"""
    try:
        from . import targets as _T
        return _T.for_stock(stock, scene)
    except Exception:                                          # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# L2 分色：按亮度段把 a*/b* 往靶收
# ---------------------------------------------------------------------------
def split(L, a, b, tg, cfg, m):                                # noqa: ARG001
    """**L2 分色** —— 只改 a*/b*（暗/中/高各一段 + 最深阴影）。

    ★ 口径全部是「**相对整张中位**」（`sh_abs` / `hi_abs` / `mid_abs`）。
    ★ 逐图往靶收（不是加固定偏移）：固定偏移跟测值不是 1:1，换条预设就失准。
    ★ 限幅：靶里 `split_limit` 优先，否则 config 的 `GRADE_SPLIT_LIMIT`。
    @returns {(a, b, dict)} 新的 a/b + 本段报告（不改 L）
    """
    w_sh, w_hi, w_deep = _sh_hi_weights(L, cfg)
    Lm, am, bm = m['Lm'], m['am'], m['bm']
    _lim = float((tg or {}).get('split_limit')
                 if (tg or {}).get('split_limit') is not None
                 else getattr(cfg, 'GRADE_SPLIT_LIMIT', 2.5))
    if tg and tg.get('sh_abs'):
        _p25, _p90 = np.percentile(L, 25.0), np.percentile(L, 90.0)
        _msh, _mhi = L <= _p25, L >= _p90
        cur = (float(a[_msh].mean() - am), float(b[_msh].mean() - bm),
               float(a[_mhi].mean() - am), float(b[_mhi].mean() - bm))
        tgt = (float(tg['sh_abs'][0]), float(tg['sh_abs'][1]),
               float(tg['hi_abs'][0]), float(tg['hi_abs'][1]))
        d4 = [float(np.clip(tgt[i] - cur[i], -_lim, _lim)) for i in range(4)]
        sha, shb, hia, hib = d4
    else:
        sha, shb = float(getattr(cfg, 'GRADE_SH_A', 0.0)), float(getattr(cfg, 'GRADE_SH_B', 0.0))
        hia, hib = float(getattr(cfg, 'GRADE_HI_A', 0.0)), float(getattr(cfg, 'GRADE_HI_B', 0.0))
    dpa, dpb = float(getattr(cfg, 'GRADE_DEEP_A', 0.0)), float(getattr(cfg, 'GRADE_DEEP_B', 0.0))
    # ★ 09-24：**中间调**那一段也能收（原来只有 w_sh / w_hi 两段，L 的 25%~75% **没人管**）。
    #   量鹿井时发现问题恰恰在中间调 —— 他几乎中性，而我们中调 b* 比整张中位高 10 格以上
    #   ⇒ 肤色落在这一段，观感就是"发黄发暖"。
    _mid = (tg or {}).get('mid_abs')
    mma = mmb = 0.0
    if _mid:
        _p25m, _p75m = np.percentile(L, 25.0), np.percentile(L, 75.0)
        _mm = (L >= _p25m) & (L <= _p75m)
        if bool(_mm.any()):
            mma = float(np.clip(float(_mid[0]) - (float(a[_mm].mean()) - am), -_lim, _lim))
            mmb = float(np.clip(float(_mid[1]) - (float(b[_mm].mean()) - bm), -_lim, _lim))
    w_mid = np.clip(1.0 - w_sh - w_hi, 0.0, 1.0)
    # ★★★ 09-28：**中调权重归一化**（和肤色层 `GRADE_SKIN_W_REF` 同一招）。
    #   为什么：`w_sh`/`w_hi` 的过渡带**各占 0.75 个 span** ⇒ 合起来 1.5 span
    #   ⇒ **把中调 `w_mid` 挤得只剩一点点**（实测全图中位 0.000、中调区均值 0.231）
    #   ⇒ 实测后果：`mid_abs[0]` 从 0.77 改到 **−6**、限幅开到 12，中 a* 只挪了 0.67。
    #   **设 1.0 = 关**（逐位回老行为）。
    _wmid_ref = float(getattr(cfg, 'GRADE_SPLIT_W_REF', 1.0) or 1.0)
    if _wmid_ref < 1.0 - 1e-9:
        w_mid = np.minimum(w_mid / max(_wmid_ref, 1e-6), 1.0)
    da2 = sha * w_sh + hia * w_hi + dpa * w_deep + mma * w_mid
    db2 = shb * w_sh + hib * w_hi + dpb * w_deep + mmb * w_mid
    info = dict(d_sh=(float(sha), float(shb)), d_hi=(float(hia), float(hib)),
                d_deep=(float(dpa), float(dpb)), d_mid=(float(mma), float(mmb)),
                split_limit=_lim)
    return a + da2, b + db2, info


# ---------------------------------------------------------------------------
# L3 混色：按色相带改彩度（+ 带内亮度偏移）
# ---------------------------------------------------------------------------
def mix(disp, L, a, b, tg, cfg, parsed, m):
    """**L3 混色** —— 只改彩度（总量 + 按色相分配）+ 色相带内的少量亮度。

    ★ 灰色像素不动（`live` 窗）—— 否则会把中性轴一起推偏（digitalFilm 那条教训）。
    ★ 色相带增益**按语义区域加权**（`GRADE_REGION_SCOPE='env'`）—— 环境增益不落到人身上。
    ★ 最后**归一**（把整张彩度中位拉回 "总量 × sat"）⇒ 「形状归曲线、总量归 sat」。
    @returns {(L, a, b, dict)} 新的 L/a/b + 本段报告
    """
    Lm, am, bm = m['Lm'], m['am'], m['bm']
    Cc = np.sqrt(a * a + b * b)
    H = np.degrees(np.arctan2(b, a)) % 360.0
    cmin = float(getattr(cfg, 'GRADE_C_MIN', 12.0))
    live = _ramp(Cc, cmin * 0.6, cmin * 1.4)
    kc = np.zeros_like(Cc)
    dl = np.zeros_like(Cc)
    _bg = (tg or {}).get('band_gain') or [0.0] * 12
    # ★★ 09-24：色相带的增益**按语义区域加权** —— 环境增益不落到人身上。
    #   原来自查出来的病根：按**色相带**分区 ≈ 用"能算的量（色相）"代替"需要判断的量（这是什么）"。
    _resc = np.ones(L.shape, np.float64)
    try:
        if str(getattr(cfg, 'GRADE_REGION_SCOPE', 'env')).lower() == 'env':
            from . import region as _R
            _rm = _R.weights(np.clip(disp, 0.0, 1.0), cfg, parsed=parsed)   # ★ 复用同一次解析
            _resc = _rm['env_scale']
    except Exception:                                          # noqa: BLE001
        _resc = np.ones(L.shape, np.float64)
    for bi, (center, half, k, dL) in enumerate(BANDS):
        # ★ 色相带增益 = 预设自带的那份（按大师量出来的），没有专属靶就是 0
        k = k + float(_bg[bi]) if bi < len(_bg) else k
        w = _band_weight(H, center, half) * live * _resc
        kc += w * k
        dl += w * dL
    # 软归一：多个带重叠时不把增益叠爆
    over = np.maximum(kc, 0.0)
    scale = np.where(over > 0.45, 0.45 / np.maximum(over, 1e-6), 1.0)
    kc = kc * scale
    dl = np.clip(dl * scale, -6.0, 8.0)

    newC = np.maximum(Cc * (1.0 + kc), 0.0)
    # ★★ 09-26：**彩度压缩曲线**（压中低彩度、保住高彩度）。
    #   为什么需要：`sat` 是**整体等比缩**（中位和 P90 一起拉），而大师的分布比我们**更开**。
    #   ⇒ 形状归曲线、总量归 sat，两个自由度分开。
    #   `k_lo < k_hi` ⇒ 低彩度压得多、高彩度压得少 = 把分布拉开。
    _klo = (tg or {}).get('c_k_lo')
    _khi = (tg or {}).get('c_k_hi')
    if _klo is not None and _khi is not None:
        _Clo = float((tg or {}).get('c_lo', 8.0))
        _Chi = float((tg or {}).get('c_hi', 35.0))
        _t = np.clip((Cc - _Clo) / max(_Chi - _Clo, 1e-6), 0.0, 1.0)
        _t = _t * _t * (3.0 - 2.0 * _t)                       # smoothstep
        _k = float(_klo) + (float(_khi) - float(_klo)) * _t
        # ★★ 09-26：**曲线上的人（脸/身体）豁免**（跟 L3 色相带增益的约定一致）。
        #   为什么必须豁免：曲线本来就是"压中低彩度"，而**脸的彩度本来就低** ⇒ 一起压等于把脸做素。
        _k = 1.0 - _resc * (1.0 - _k)
        newC = newC * _k
    nz = np.maximum(Cc, 1e-6)
    # ★★ 归一：**把整张彩度中位拉回原值**（只重新分配、不改总量）。
    #   ⚠⚠ 09-24 修过一个 bug：原来 `_m0/_m1` 取"彩色像素的中位"但乘到**所有**像素上
    #     ⇒ 灰像素被多乘一次 ⇒ 整张彩度**虚涨 46%**、画面发飘发白。⇒ 改成**整张中位**归一。
    _sat = float((tg or {}).get('sat') if (tg or {}).get('sat') is not None
                 else getattr(cfg, 'GRADE_SAT', 1.0))
    _m0 = float(np.median(Cc))
    _m1 = float(np.median(newC))
    newC = newC * (_sat * _m0 / max(_m1, 1e-6)) if _m1 > 1e-6 else newC
    a2 = a * (newC / nz)
    b2 = b * (newC / nz)
    # 亮度偏移：只作用在有颜色的地方（灰区不动）
    L2 = np.clip(L + dl * live, 0.0, 100.0)
    info = dict(c_gain=[(c, (k + (_bg[i] if i < len(_bg) else 0.0)))
                        for i, (c, _, k, _) in enumerate(BANDS)],
                sat=_sat, dL_bands=(float(np.min(dl)), float(np.max(dl))))
    return L2, a2, b2, info


# ---------------------------------------------------------------------------
# L4 肤色：按人脸掩膜修「脸的亮度 / 彩度 / 色相 / 明暗对比」
# ---------------------------------------------------------------------------
def skin(disp, L, a, b, tg, cfg, parsed):
    """**L4 肤色** —— 只动脸（用 `_wn` 羽化权重），只修"脸自己的"四个量。

    ★ 三件事都往靶收（不是只调亮度）：**绝对 L\\***（`skin_L_abs`）· **绝对彩度**（`skin_C_abs`）·
      **色相角**（`skin_hue`）。为什么用绝对值：相对量会随"整张多暗"漂（实测漂 39 格）。
    ★ 脸 / 身体分开：脸严格、身体宽一点。
    ★ 掩膜边界**羽化**（出处 **CN104038704A**：以人脸区域为边界做亮度平滑过渡）。
    ★ 权重**归一化**（`GRADE_SKIN_W_REF`）：软权重脸上中位只有 ~0.70 ⇒ 修正只做到七成。
    ★ **明暗对比**（`skin_contrast`）：绕脸中位拉开 ⇒ 暗部更暗、亮部更亮（治"脸太平"）。
    @returns {(L, a, b, dict)} 新的 L/a/b + 本段报告
    """
    info = dict(skin_dL=0.0, skin_dC=0.0, skin_dH=0.0, skin_mask='none',
                skin_face_seen=False, skin_model_ok=False, skin_w_med=None,
                skin_limit_l=None, skin_mask_src=('given' if parsed is not None else 'self'),
                skin_contrast=float((tg or {}).get('skin_contrast', 1.0) or 1.0))
    if not (tg and tg.get('skin_l') is not None):
        return L, a, b, info

    # 限幅：**靶里有就用靶、没有才退回 config**（三行写法统一）。
    # ⚠ 用 `if ... is not None` 而不是 `or`：`or` 会把**合法的 0**（= 关掉这一路修正）当成"没设"。
    _lim_l = float((tg or {}).get('skin_limit_l')
                   if (tg or {}).get('skin_limit_l') is not None
                   else getattr(cfg, 'GRADE_SKIN_LIMIT_L', 6.0))
    _lim_c = float((tg or {}).get('skin_limit_c')
                   if (tg or {}).get('skin_limit_c') is not None
                   else getattr(cfg, 'GRADE_SKIN_LIMIT_C', 0.0))
    _lim_h = float((tg or {}).get('skin_limit_h')
                   if (tg or {}).get('skin_limit_h') is not None
                   else getattr(cfg, 'GRADE_SKIN_LIMIT_H', 8.0))
    info['skin_limit_l'] = _lim_l

    _pzr = parsed
    if _pzr is None:
        try:                                    # 没传进来就自己算一遍（单独调用时的老行为）
            from . import face as _F
            _pzr = _F.parse(np.clip(disp, 0.0, 1.0))
        except Exception:                                        # noqa: BLE001
            _pzr = None
    _w = None
    _mask_src = 'none'
    _face_seen = False
    _model_ok = False
    if _pzr is not None:
        _model_ok = True
        _mk = (_pzr or {}).get('masks') or {}
        _fs = _mk.get('face_skin')
        if _fs is not None and float(np.max(_fs)) > 0.05:
            _w = np.clip(np.asarray(_fs, np.float64) * 1.6, 0.0, 1.0)   # 脸：严格
            # ★★★ 09-26 修：**"有没有脸"以前根本没查**（`face_skin` 检不到脸时也非空
            #   ⇒ 只看"非空"就动手 = 假装有脸）。现在按**检测器的结论**分两条：
            #     · 认到脸 ⇒ 脸严格 + 身体皮肤松一点 · 没认到（侧脸/背影/被挡）⇒ **只用 face_skin**，标 `seg`
            if (_pzr or {}).get('face') is not None:
                _face_seen = True
                _mask_src = 'face'
                if _mk.get('skin') is not None:
                    _w = np.maximum(_w, np.clip(np.asarray(_mk['skin'], np.float64), 0.0, 1.0)
                                    * float(getattr(cfg, 'GRADE_SKIN_BODY_W', 0.5)))
            else:
                _mask_src = 'seg'
    if _w is None:
        if _model_ok:
            # ★★★ 09-26：模型**能跑**、但整张没有皮肤 ⇒ **什么都别做**（别退回色相窗 ——
            #   那个窗实测只有 10.5% 是真皮肤，会把木头/黄墙提亮，是当年"脸崩"的来源）。
            _w = np.zeros(L.shape, np.float64)
            _mask_src = 'none'
        else:
            # 模型**不可用**（缺依赖/模型文件）⇒ 退回色相窗，有总比没有好
            CcH = np.sqrt(a * a + b * b)
            H = np.degrees(np.arctan2(b, a)) % 360.0
            cmin = float(getattr(cfg, 'GRADE_C_MIN', 12.0))
            _w = _band_weight(H, 35.0, 26.0) * _ramp(CcH, cmin * 0.6, cmin * 1.4) * 0.6
            _mask_src = 'hue'
    # ★★★ 09-27：**掩膜边界要羽化**（不羽化的话，"提脸"的边界会看出分割感）。
    #   σ = 脸的**等效边长** × `GRADE_SKIN_FEATHER`（默认 1/6 ⇒ 过渡约 ±3σ ≈ 脸宽的一半，对上专利）。
    _fe = float(getattr(cfg, 'GRADE_SKIN_FEATHER', 0.0) or 0.0)
    if _fe > 0 and float(_w.max()) > 0.05:
        from scipy.ndimage import gaussian_filter
        _side = float(np.sqrt(max(int((_w > 0.3).sum()), 1)))     # 脸的等效边长（像素）
        _wb = gaussian_filter(_w, max(1.0, _side * _fe))
        _mx = float(_wb.max())
        if _mx > 1e-6:
            _w = np.clip(_wb / _mx, 0.0, 1.0)                     # 峰值归一 ⇒ 脸中心仍是 1
    info['skin_mask'] = _mask_src
    info['skin_face_seen'] = bool(_face_seen)
    info['skin_model_ok'] = bool(_model_ok)

    _sel = _w > 0.5
    if float(_w.max()) > 0.05 and bool(_sel.any()):      # ★ 必须检查非空：
        _Cc2 = np.sqrt(a * a + b * b)                    #   空窗口时 np.median([]) = nan
        _cL = float(np.median(L[_sel]) - np.median(L))
        _aL = float(np.median(L[_sel]))          # ★ 脸自己的绝对 L*（见下面 skin_L_abs）
        _cC = float(np.median(_Cc2[_sel])) / max(float(np.median(_Cc2)), 1e-6)
        _cH = float(np.degrees(np.arctan2(float(np.median(b[_sel])),
                                          float(np.median(a[_sel])))) % 360.0)
        # ★★ 绝对语义 `skin_L_abs`：**脸的绝对 L\* 才是不变量**（大师 728 张恒定在 67、
        #   而"脸−整张"从他 −4.4 漂到 +34.9）。靶里有就优先，没有则退回老相对语义。
        _sl_abs = (tg or {}).get('skin_L_abs')
        if _sl_abs is not None:
            _dl = float(np.clip(float(_sl_abs) - _aL, -_lim_l, _lim_l))
        else:
            _dl = float(np.clip(float(tg['skin_l']) - _cL, -_lim_l, _lim_l))
        _dc = float(np.clip(float(tg['skin_c']) / max(_cC, 1e-6) - 1.0, -_lim_c, _lim_c))
        # ★★ 绝对语义 `skin_C_abs`（同理：比值靶的分母一动就全变 ⇒ 该锁绝对值）。
        _C_abs = (tg or {}).get('skin_C_abs')
        if _C_abs is not None:
            _dc = float(np.clip(float(_C_abs) / max(float(np.median(_Cc2[_sel])), 1e-6) - 1.0,
                                -_lim_c, _lim_c))
        _dh = float(np.clip(((float(tg['skin_hue']) - _cH + 180.0) % 360.0) - 180.0,
                            -_lim_h, _lim_h))
        # ★ 09-28：把「肤色权重在脸选区内的中位」报出来（诊断用 —— 它就是"修正只做到七成"的答案）。
        try:
            _wmed = float(np.median(_w[_sel]))
        except Exception:                                       # noqa: BLE001
            _wmed = None
        # ★★★ 09-28：**权重归一化**（治"修正永远差三成"）。`W_REF = 1.0` ⇒ 逐位回老行为。
        _wref = float(getattr(cfg, 'GRADE_SKIN_W_REF', 1.0) or 1.0)
        _wn = _w if _wref >= 1.0 - 1e-9 else np.minimum(_w / max(_wref, 1e-6), 1.0)
        # ★★★ 09-28：**脸的「明暗对比」增益**（靶字段 `skin_contrast`）。
        #   为什么加（SV：「肤色光感还是不好」）—— 实测脸明暗跨度 我们 35.8 / 鹿井 50.5
        #   ⇒ **脸太平**。根因：`_dl` 是个**常数偏移** ⇒ 连脸暗部一起提 ⇒ 压平明暗差。
        #   做法：绕**脸自己的中位**拉开（暗部更暗、亮部更亮、中位不动），用 `_wn` 加权。
        _sk_ct = float((tg or {}).get('skin_contrast', 1.0) or 1.0)
        if abs(_sk_ct - 1.0) > 1e-9:
            _mid_f = float(np.median(L[_sel]))
            L = np.clip(L + ((_mid_f + (L - _mid_f) * _sk_ct) - L) * _wn, 0.0, 100.0)
        L = np.clip(L + _dl * _wn, 0.0, 100.0)
        _k = 1.0 + _dc * _wn
        a = a * _k
        b = b * _k
        _th = np.radians(_dh) * _wn                   # 色相绕原点转（往靶的色相角）
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
        info.update(skin_dL=_dl, skin_dC=_dc, skin_dH=_dh, skin_w_med=_wmed)
    return L, a, b, info


# ---------------------------------------------------------------------------
# 色域映射（出界颜色往中性轴收）
# ---------------------------------------------------------------------------
def gamut(L, a, b):
    """★★ 09-24：直接 `clip` 会按通道砍，把**色相也一起改掉**
    —— 实测"色相转 −6°"在高彩度亮色上只有 **1/4** 有效（转到一半就出 sRGB 界被砍）。
    标准做法：出界的颜色**保住亮度、往中性轴（a=b=0）收**，收到进界为止。
    ⚠ `color.from_lab` **内部自己就 clip**（所以"看输出有没有出界"检测不到）
      ⇒ 改用**往返检测**：转出去再转回来，a*/b* 对不上 ⇒ 说明这个颜色被砍过。
    """
    _lab = np.stack([L, a, b], -1)
    for _ in range(5):
        _back = color.to_lab(np.clip(color.from_lab(_lab), 0.0, 1.0))
        _bad = (np.abs(_back[..., 1] - _lab[..., 1]) > 0.5) | \
               (np.abs(_back[..., 2] - _lab[..., 2]) > 0.5)
        if not bool(_bad.any()):
            break
        _lab[..., 1] = np.where(_bad, _lab[..., 1] * 0.80, _lab[..., 1])
        _lab[..., 2] = np.where(_bad, _lab[..., 2] * 0.80, _lab[..., 2])
    return np.clip(color.from_lab(_lab), 0.0, 1.0)


# ---------------------------------------------------------------------------
# 主入口（**编排，自己不写任何量**）
# ---------------------------------------------------------------------------
def apply(disp, cfg=C, stock=None, parsed=None, scene=None):
    """在**成片**（显示域）上做 L2 分色 + L3 混色 + L4 肤色。

    `parsed`：**调用方已经算好的一次** `face.parse(...)`（`pipeline` 在**解码后**那张图上算的）。
      ★ 为什么要传下来：① 链尾画面发白 ⇒ 分割模型认不出脸（实测解码后 12/12、引擎出图后 11/12）
      ② 本函数里 `region.weights` 和 L4 各要一份掩膜，同一份像素调两遍（273 ms/次）。
      `None` ⇒ 本函数自己算（单独调用时的老行为，逐位不变）。

    `scene`：`scene.classify(...)` 的结果 —— **只用来取靶**（`targets._scene` 覆盖）。

    ★★★ 09-28 拆分：本函数**只做编排**，三段各自是纯函数（见文件头那张表）。
    @returns {(numpy.ndarray, dict)} 出图 + 报告（能自查动了多少）
    """
    if not bool(getattr(cfg, 'GRADE_ENABLE', False)):
        return np.clip(np.asarray(disp, np.float64), 0.0, 1.0), dict(applied=False)

    tg = _tgt_of(stock, scene)
    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    lab = color.to_lab(d)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    m = dict(Lm=float(np.median(L)), am=float(np.median(a)), bm=float(np.median(b)))

    # ★★★ 09-26：传进来的那次解析，**只在这一层用一次**（region 与 L4 共用）。
    #   ⚠ 尺寸对不上就不认（掩膜是在另一张同尺寸图上算的；换了渲染尺寸必须现算）。
    _pz = parsed
    if _pz is not None:
        _mk0 = (_pz or {}).get('masks') or {}
        _fs0 = _mk0.get('face_skin')
        if _fs0 is None or tuple(np.shape(_fs0)[:2]) != tuple(L.shape):
            _pz = None

    # ★★★ `GRADE_SCOPE`：'off' / 'skin'（只 L4）/ 'all'（三段全跑，当前）
    #   ⚠ 做法：进 L2/L3 之前存一份原图，若 scope='skin' 就在 L4 之前**还原** ——
    #     这样 L4 是在**没被分色混色动过**的图上做，且 L2/L3 那些量仍然算出来（只用于报告）。
    _scope = str(getattr(cfg, 'GRADE_SCOPE', 'all')).lower()
    _a0, _b0, _L0 = a.copy(), b.copy(), L.copy()

    # ---- L2 分色 ----
    a, b, i2 = split(L, a, b, tg, cfg, m)
    # ---- L3 混色 ----
    L, a, b, i3 = mix(disp, L, a, b, tg, cfg, _pz, m)
    if _scope not in ('all', 'color'):
        a, b, L = _a0, _b0, _L0          # scope='skin' ⇒ 分色/混色不生效（只报告）
    # ---- L4 肤色 ----
    L, a, b, i4 = skin(disp, L, a, b, tg, cfg, _pz)
    # ---- 色域映射 ----
    out = gamut(L, a, b)

    info = dict(applied=True, scope=_scope,
                L50_in=m['Lm'], L50_out=float(np.median(color.to_lab(out)[..., 0])),
                a_med_in=m['am'], b_med_in=m['bm'],
                d_sh=i2['d_sh'], d_hi=i2['d_hi'], target=(i2['d_sh'][0], i2['d_sh'][1],
                                                          i2['d_hi'][0], i2['d_hi'][1]),
                c_gain=i3['c_gain'], stock=stock,
                skin_dL=i4['skin_dL'], skin_dC=i4['skin_dC'], skin_dH=i4['skin_dH'],
                skin_mask=i4['skin_mask'],
                skin_w_med=(round(i4['skin_w_med'], 3) if i4['skin_w_med'] is not None else None),
                skin_limit_l=i4['skin_limit_l'],
                # ★★★ 09-26：`skin_mask` 的四个取值，语义**互斥**、别混：
                #   'face' = 检测器(过三道防假脸闸)**认到脸** + 分割；脸严格、身体松一点
                #   'seg'  = 检测器**没认到**（侧脸/背影/被挡），只用分割的 face_skin
                #   'hue'  = 模型**不可用**，退回色相窗（可信度最低）
                #   'none' = 模型能跑但整张没皮肤 ⇒ **没动手**
                skin_mask_src=i4['skin_mask_src'],
                skin_face_seen=i4['skin_face_seen'],
                skin_model_ok=i4['skin_model_ok'],
                skin_contrast=i4['skin_contrast'],
                # ★ 09-26：这一张命中了哪几条**场景覆盖**（`targets._scene`）——
                #   空 = 一条都没命中（= 跟加场景之前逐位相同）。
                scene_hits=list((tg or {}).get('_scene_hits') or []),
                scene=((tg or {}).get('scene')))
    return out, info
