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

import os

import numpy as np

from . import color
from . import config as C

# ★ 09-29 晚：`split()` 闭环诊断开关（`SV_SPLIT_DEBUG=1`）。默认关 ⇒ 零成本。
_DBG = bool(os.environ.get('SV_SPLIT_DEBUG'))


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
def _bm(v, mask):
    """带内 **median**（不是 mean —— 口径见 `split()` 的说明）。"""
    import numpy as np
    return float(np.median(v[mask])) if bool(mask.any()) else 0.0


def split(L, a, b, tg, cfg, m):                                # noqa: ARG001
    """**L2 分色** —— 按亮度把 a*/b* 往靶收（**逐图 1:1 收敛**）。

    ## ★★★ 09-29 晚：模型从「三个颜色轮」换成「一条按亮度走的修正曲线」

    ### 原模型为什么不可能对（**结构性**的，不是力度问题）
    原来用三根权重（暗轮 `w_sh` / 亮轮 `w_hi` / 中轮 `w_mid = 1 − w_sh − w_hi`），
    而观测量是「分带 median − 整张 median」。后果：
      · 三根权重**和为 1** ⇒ "给全图同时加常数"这个方向对观测量**完全不可见**
        ⇒ 3 个旋钮只剩 **2 个可见自由度**；
      · 而靶给了 **3 个数**（暗 / 亮 / 中）⇒ **数学上不可能同时对上**；
      · 更糟：`(暗轮 + 亮轮)` 的共同方向增益只有 **0.07**（实测）⇒ 求解出 D ≈ **±25 格**，
        画面会被推花。三色轮自己的 2×2 也是病态的（cond 11~23）。
      ⇒ 这是"**模型少了维度**"，任何"调力度"都救不了。证据见
        `_debug/_split_probe.py`（口径错 10 倍）、`_split_opt.py`（三色轮 D≈22~60）、
        `_split_reach.py`（12 档自由场**可达**，残差 ~0 ⇒ 问题在参数化）。

    ### 新模型：按**亮度秩**走的修正曲线（12 个节点，最小曲率）
      ① 每个像素的亮度换成**秩** `r ∈ [0,1]`（等频 ⇒ 内容归一，与分带口径同源）；
      ② 12 个节点 `j` 上的**帽函数** `hat_j(r) = max(0, 1 − |r·12 − 0.5 − j|)`（`Σ_j hat_j ≡ 1`）；
      ③ 求节点值 `c`（a* / b* 各一组）让三个带的观测量落靶：
         `min ‖c‖² + μ‖Δ²c‖²  s.t.  J·c = (goal − cur)`（KKT）
         —— 一句话「**用最平滑的那条曲线落靶**」；μ 项同时把"常数"这个空方向压掉。
      ④ `J` **数值实测**（给每个节点打一记 `h` 格、看三个带各动多少），**不猜解析式**
         （解析式错了一倍，是之前发散的另一半根源）。
      ⑤ 迭代 + **回溯**（新点残差没变小就减半）+ 场幅上限 ⇒ 单调、不发散。

    ★ 实测（3 图 × 2 通道，`_debug/_split_minfield.py`，含**非线性复核**）：
      残差从旧模型的 **−5.9 / −7.9 格**（滨田暗部）降到 **≤ 1.1 格**；
      所需场幅度 **3.9 ~ 12.2 格**。代价 = 这一层由"3 个旋钮"变成"一条逐图拟合的曲线"。

    ## 口径（不改）
    · 观测量 = 「暗带 `L≤P20` / 亮带 `L≥P80` / 中带 `P25~P75` 的 **median** − **整张 median**」
      —— 与 `targets.json` 的 `sh_abs / hi_abs / mid_abs` **同一把尺子**（B 尺）。
    · 只追「最多补 `_lim` 格」那一段：差得更多的部分**不是风格差、是内容差**。
    · `GRADE_DEEP_A/B`（最暗部推色）当**常数偏移**加在基线上，不参与闭环。
    @returns {(a, b, dict)} 新的 a/b + 本段报告（不改 L）
    """
    import numpy as np
    # ★ 09-29 深夜：分色**单段开关**。`False` ⇒ 这一段原样返回（混色/肤色照跑）。
    #   用途：落地后万一画面"分色过火"，一键回退这一段而不动别的层。
    #   ⚠ 返回的 key 必须齐 —— `apply()` 会读 `d_sh` / `d_hi`（缺了当场 KeyError，08-29 栽过）。
    if not bool(getattr(cfg, 'GRADE_SPLIT_ENABLE', True)):
        return a, b, dict(applied=False, split_model='off', split_iters=0,
                          d_sh=(0.0, 0.0), d_hi=(0.0, 0.0), d_mid=(0.0, 0.0),
                          d_deep=(0.0, 0.0), split_limit=0.0,
                          split_resid=[0.0] * 6,
                          split_field=(0.0, 0.0, 0.0, 0.0),
                          tgt_sh=[0.0, 0.0], tgt_hi=[0.0, 0.0], tgt_mid=[0.0, 0.0])
    _p20, _p80 = np.percentile(L, 20.0), np.percentile(L, 80.0)
    _p25, _p75 = np.percentile(L, 25.0), np.percentile(L, 75.0)
    msh, mhi = L <= _p20, L >= _p80
    mmid = (L >= _p25) & (L <= _p75)
    _bands = (msh, mhi, mmid)

    _lim = float((tg or {}).get('split_limit')
                 if (tg or {}).get('split_limit') is not None
                 else getattr(cfg, 'GRADE_SPLIT_LIMIT', 5.0))
    _n = max(int(getattr(cfg, 'GRADE_SPLIT_NODES', 12) or 12), 4)
    _lc = float(getattr(cfg, 'GRADE_SPLIT_CURV', 1.0) or 0.0)
    _reg = float(getattr(cfg, 'GRADE_SPLIT_REG', 0.02) or 0.0)
    _wmid = float(getattr(cfg, 'GRADE_SPLIT_MIDW', 0.30) or 0.0)
    _rng = float(getattr(cfg, 'GRADE_SPLIT_RANGE', 8.0) or 0.0)
    _IT = int(getattr(cfg, 'GRADE_SPLIT_ITERS', 4) or 4)
    _h = 0.5
    _deep_a = float(getattr(cfg, 'GRADE_DEEP_A', 0.0))
    _deep_b = float(getattr(cfg, 'GRADE_DEEP_B', 0.0))
    w_deep = _sh_hi_weights(L, cfg)[2]

    # ---- 靶 ----
    if tg and tg.get('sh_abs'):
        t_sh = (float(tg['sh_abs'][0]), float(tg['sh_abs'][1]))
        t_hi = (float(tg['hi_abs'][0]), float(tg['hi_abs'][1]))
    else:
        t_sh = (float(getattr(cfg, 'GRADE_SH_A', 0.0)), float(getattr(cfg, 'GRADE_SH_B', 0.0)))
        t_hi = (float(getattr(cfg, 'GRADE_HI_A', 0.0)), float(getattr(cfg, 'GRADE_HI_B', 0.0)))
    _mid = (tg or {}).get('mid_abs')
    t_md = (float(_mid[0]), float(_mid[1])) if _mid else (0.0, 0.0)
    tgt = np.array([[t_sh[0], t_hi[0], t_md[0]], [t_sh[1], t_hi[1], t_md[1]]], np.float64)

    def _cur(av, bv):
        """观测量（自归一化）：分带 median − **整张** median。"""
        _am, _bmm = float(np.median(av)), float(np.median(bv))
        return np.array([[_bm(av, mt) - _am for mt in _bands],
                         [_bm(bv, mt) - _bmm for mt in _bands]], np.float64)

    # ---- 亮度秩 + 帽函数基（等频 ⇒ 内容归一）----
    _flat = np.asarray(L, np.float64).ravel()
    _rk = np.empty(_flat.size, np.float64)
    _rk[np.argsort(_flat, kind='stable')] = np.arange(_flat.size, dtype=np.float64) / \
        max(_flat.size - 1, 1)
    _uu = (_rk * _n - 0.5).reshape(L.shape)
    W = [np.clip(1.0 - np.abs(_uu - j), 0.0, 1.0) for j in range(_n)]

    a_base = a + _deep_a * w_deep
    b_base = b + _deep_b * w_deep
    c0 = _cur(a_base, b_base)
    goal = c0 + np.clip(tgt - c0, -_lim, _lim)

    def _apply(base_arr, cvec):
        f = np.zeros_like(base_arr, np.float64)
        for j in range(_n):
            f += cvec[j] * W[j]
        return base_arr + f, f

    def _jac(bav, bbv):
        J = np.zeros((2, 3, _n), np.float64)
        ca, cb = _cur(bav, bbv)
        for j in range(_n):
            J[0, :, j] = (_cur(bav + _h * W[j], bbv)[0] - ca[0]) / _h
            J[1, :, j] = (_cur(bav, bbv + _h * W[j])[1] - cb[1]) / _h
        return J

    # ★ 加权岭回归的代价项：`λI + λc·Δ²ᵀΔ²`
    #   · `λI` 把"整体平移"这个**空方向**压掉（三根帽函数和为 1 ⇒ 加常数对观测量不可见）；
    #   · `λc·Δ²ᵀΔ²` 让曲线**尽量平滑**（实测同样落靶，平滑解的场幅从 35 格降到 4~12 格）。
    _D2 = np.zeros((_n - 2, _n), np.float64)
    for _i in range(_n - 2):
        _D2[_i, _i], _D2[_i, _i + 1], _D2[_i, _i + 2] = 1.0, -2.0, 1.0
    H = _reg * np.eye(_n) + _lc * (_D2.T @ _D2)
    # ★ 观测权重：**中调那行降权** —— 它的观测量是内容主导的（靶跨张 IQR ≥4），
    #   精确追它会把场推到 12 格；降权后由优化器自己权衡"值不值"。
    Lam = np.diag([1.0, 1.0, _wmid])

    C = np.zeros((2, _n), np.float64)
    a2, b2 = a_base, b_base
    iters, prev = 0, float(np.max(np.abs(goal - c0)))
    for _ in range(max(_IT, 1)):
        iters += 1
        if prev < 0.03:
            break
        r = goal - _cur(a2, b2)
        J = _jac(a2, b2)
        Cnew = C.copy()
        for ch in (0, 1):
            _A = J[ch].T @ Lam @ J[ch] + H
            try:
                step = np.linalg.solve(_A, J[ch].T @ Lam @ r[ch])
            except Exception:                                  # noqa: BLE001
                step = np.linalg.lstsq(_A, J[ch].T @ Lam @ r[ch], rcond=None)[0]
            _fs = float(step.max() - step.min())
            if _rng and _fs > _rng:                       # 单轮步长封顶（按场幅算）
                step = step * (_rng / _fs)
            Cnew[ch] = C[ch] + 0.8 * step
            if _rng:
                _cr = float(Cnew[ch].max() - Cnew[ch].min())
                if _cr > _rng:
                    Cnew[ch] = Cnew[ch] * (_rng / _cr)
        # ★ 回溯（保单调）：新点残差没变小 ⇒ 步长减半重来
        _best = None
        for _bt in range(4):
            _at, _fa = _apply(a_base, Cnew[0])
            _btx, _fb = _apply(b_base, Cnew[1])
            _et = float(np.max(np.abs(goal - _cur(_at, _btx))))
            if _et <= prev + 1e-6 or _bt == 3:
                _best = (Cnew, _at, _btx, _et)
                break
            Cnew = 0.5 * (C + Cnew)
        C, a2, b2, prev = _best
        if _DBG:
            _fa = _apply(a_base, C[0])[1]
            print('[split] it%d err=%.3f  a场[%.2f,%.2f]  b场[%.2f,%.2f]'
                  % (iters, prev, float(_fa.min()), float(_fa.max()),
                     float(_apply(b_base, C[1])[1].min()), float(_apply(b_base, C[1])[1].max())))

    _fa = _apply(a_base, C[0])[1]
    _fb = _apply(b_base, C[1])[1]
    info = dict(d_sh=(float(_bm(_fa, msh)), float(_bm(_fb, msh))),
                d_hi=(float(_bm(_fa, mhi)), float(_bm(_fb, mhi))),
                d_mid=(float(_bm(_fa, mmid)), float(_bm(_fb, mmid))),
                d_deep=(_deep_a, _deep_b),
                split_limit=_lim, split_model='curve',
                split_nodes=_n, split_curv=_lc, split_reg=_reg, split_midw=_wmid,
                split_range=_rng,
                split_iters=iters,
                split_field=(round(float(_fa.min()), 3), round(float(_fa.max()), 3),
                             round(float(_fb.min()), 3), round(float(_fb.max()), 3)),
                split_resid=[round(float(v), 3) for v in (tgt - _cur(a2, b2)).ravel()],
                split_curve_a=[round(float(v), 3) for v in C[0]],
                split_curve_b=[round(float(v), 3) for v in C[1]],
                tgt_sh=list(t_sh), tgt_hi=list(t_hi), tgt_mid=list(t_md))
    return a2, b2, info


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
_OWNER_WARNED = [False]          # ★ 09-29：冲突**只警告一次**（不然每张图刷屏）


def _warn_owner_conflict():
    """`SKIN_ABS_OWNER='grade'` 且脸增益也开着 ⇒ 两处在写同一套靶。**响一次**，不静默。"""
    if _OWNER_WARNED[0]:
        return
    _OWNER_WARNED[0] = True
    import sys as _sys
    _sys.stderr.write(
        '[svFilm] ⚠ 层纪律冲突：`SKIN_ABS_OWNER="grade"` 且 `FACE_GAIN_ENABLE=True` —— '
        '两处都在写"脸的绝对靶(L*/C*/H)"。按 09-29 裁定**脸增益优先**，L4 本次不写。'
        '要让 L4 当主人，请把 `FACE_GAIN_ENABLE` 关掉。\n')


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
                skin_contrast=float((tg or {}).get('skin_contrast', 1.0) or 1.0),
                # ★★ 09-29 新增两个自查字段（`selftest.t_skin` ④ 会盯它们）
                skin_meas_mask='none',       # 「量」那张掩膜：face / union(回退)
                skin_abs_owner='none',       # 脸的绝对靶这一轮归谁写：facegain / grade
                skin_owner_conflict=False)   # True = 两个开关同时在写（非法状态，已被守卫压平）
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
    # ★★ 09-29：总闸 `FACE_STEP_ENABLE=False` ⇒ **不许从后门自己补算掩膜**
    #   （否则"认人/认脸"又回来了，而 SV 已裁定去掉它）。
    if _pzr is None and bool(getattr(cfg, 'FACE_STEP_ENABLE', True)):
        try:                                    # 没传进来就自己算一遍（单独调用时的老行为）
            from . import face as _F
            _pzr = _F.parse(np.clip(disp, 0.0, 1.0))
        except Exception:                                        # noqa: BLE001
            _pzr = None
    # ★★★ 09-29：**「量」和「作用」用两张掩膜**（与 `facegain` 09-29 的修法对齐，见 `config.GRADE_SKIN_MEAS_FACE`）
    #   · `_w`  = **作用**权重 = 脸(1.0) ∪ 身体皮肤 × `GRADE_SKIN_BODY_W`（**逐位不变**）
    #   · `_wf` = **「量」**权重 = **只用脸**
    #   ⇒ 「量」分不开时（没脸掩膜 / 模型不可用）`_wf is _w`，标 'union' 回退，行为与旧版一致。
    _w = None
    _wf = None
    _mask_src = 'none'
    _face_seen = False
    _model_ok = False
    if _pzr is not None:
        _model_ok = True
        _mk = (_pzr or {}).get('masks') or {}
        _fs = _mk.get('face_skin')
        if _fs is not None and float(np.max(_fs)) > 0.05:
            _w = np.clip(np.asarray(_fs, np.float64) * 1.6, 0.0, 1.0)   # 脸：严格
            _wf = _w.copy()                                             # ★「量」= 只用脸
            # ★★★ 09-26 修：**"有没有脸"以前根本没查**（`face_skin` 检不到脸时也非空
            #   ⇒ 只看"非空"就动手 = 假装有脸）。现在按**检测器的结论**分两条：
            #     · 认到脸 ⇒ 脸严格 + 身体皮肤松一点 · 没认到（侧脸/背影/被挡）⇒ **只用 face_skin**，标 `seg`
            if (_pzr or {}).get('face') is not None:
                _face_seen = True
                _mask_src = 'face'
                if _mk.get('skin') is not None:
                    _w = np.maximum(_w, np.clip(np.asarray(_mk['skin'], np.float64), 0.0, 1.0)
                                    * float(getattr(cfg, 'GRADE_SKIN_BODY_W', 0.8)))
            else:
                _mask_src = 'seg'
    if _w is None:
        if _model_ok:
            # ★★★ 09-26：模型**能跑**、但整张没有皮肤 ⇒ **什么都别做**（别退回色相窗 ——
            #   那个窗实测只有 10.5% 是真皮肤，会把木头/黄墙提亮，是当年"脸崩"的来源）。
            _w = np.zeros(L.shape, np.float64)
            _wf = _w
            _mask_src = 'none'
        else:
            # 模型**不可用**（缺依赖/模型文件）⇒ 退回色相窗，有总比没有好
            CcH = np.sqrt(a * a + b * b)
            H = np.degrees(np.arctan2(b, a)) % 360.0
            cmin = float(getattr(cfg, 'GRADE_C_MIN', 12.0))
            _w = _band_weight(H, 35.0, 26.0) * _ramp(CcH, cmin * 0.6, cmin * 1.4) * 0.6
            _wf = _w                       # 色相窗里没有"脸"的概念 ⇒ 分不开，回退成同一张
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
    # ---- ★★★ 09-29：「量」的那张 —— 同样羽化 + 峰值归一；σ 用**脸自己**的等效边长 ----
    #   ★ 为什么必须**单独**做（而不是复用 `_w`）：`_w` 的 σ 是按"脸 ∪ 身体"的面积算的，
    #     比脸大 ⇒ 用它的 σ 去羽化脸，脸的核心会被缩得比脸小、还偏向脸心。
    #     脸增益那边就是这么拆的（`facegain._finish(face0)`）⇒ 两边口径一致。
    _meas_same = _wf is _w                                # 「量」分不开（回退）⇒ 不重复羽化
    if (not _meas_same) and _fe > 0 and _wf is not None and float(_wf.max()) > 0.05:
        from scipy.ndimage import gaussian_filter
        _side_f = float(np.sqrt(max(int((_wf > 0.3).sum()), 1)))
        _wmb = gaussian_filter(_wf, max(1.0, _side_f * _fe))
        _mxf = float(_wmb.max())
        if _mxf > 1e-6:
            _wf = np.clip(_wmb / _mxf, 0.0, 1.0)
    if not bool(getattr(cfg, 'GRADE_SKIN_MEAS_FACE', True)):
        _meas_same = True                                 # 显式退回老行为（量也用并集）
    info['skin_mask'] = _mask_src
    info['skin_face_seen'] = bool(_face_seen)
    info['skin_model_ok'] = bool(_model_ok)
    info['skin_meas_mask'] = 'union(回退：没脸掩膜/模型不可用/开关关了)' if _meas_same else 'face'

    # ★★★ 09-29 修（真 bug）：`_sel` 必须是**「量」**的选区（脸），不是「作用」的选区（脸∪身体）。
    #   旧 `_sel = _w > 0.5` 里 `_w` 已并入身体 `GRADE_SKIN_BODY_W = 0.8 > 0.5`
    #   ⇒ 身体也在 `_sel` 里 ⇒ 下面注释写"脸自己的绝对 L*"的 `_aL` 实际是"脸 ∪ 身体"的中位
    #   ⇒ **身体越暗 ⇒ `_aL` 越低 ⇒ `_dl` 越大 ⇒ 脸被推得越高**（正是"脸太白"的方向）。
    #   注：`_sel` 只用于**量**（`_aL/_cC/_cH/_cL/_mid_f/_wmed`）；**作用**走 `_wn`（仍用 `_w`）⇒ 逐位不变。
    _sel = (_wf if not _meas_same else _w) > 0.5
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
        # ★★★★ 09-29 裁定（`config.SKIN_ABS_OWNER`）—— **脸的绝对靶（L*/C*/H）唯一主人 = 脸增益**。
        #   为什么守卫落在**这里**：L4 与脸增益盯的是**同一套三个数**（`skin_L_abs`/`skin_C_abs`/`skin_hue`）
        #   ⇒ 两处都写 = 打架（`config.py` 09-26 自己标过"最严重，是改来改去打架的根因"）。
        #   规则：① `SKIN_ABS_OWNER != 'grade'` ⇒ 本层**不写**这三个量（`skin_contrast` 照旧，那是脸增益没有的能力）；
        #        ② 脸增益在本图生效时**脸增益优先**（它有靶、有闭环、实测 ΔE00 0.40 收得住）；
        #        ③ 真冲突 ⇒ 记进报告 + 只警告一次，**不静默**。
        #   ⚠ `GRADE_ENABLE=False`（现在）⇒ 本段当前不影响任何一张图，是**前置修复**。
        _owner = str(getattr(cfg, 'SKIN_ABS_OWNER', 'facegain')).lower()
        _fg_on = bool(getattr(cfg, 'FACE_GAIN_ENABLE', False))
        if _owner == 'grade' and not _fg_on:
            info['skin_abs_owner'] = 'grade'
        else:
            info['skin_abs_owner'] = 'facegain'
            if _owner == 'grade' and _fg_on:
                info['skin_owner_conflict'] = True
                _warn_owner_conflict()
            _dl = _dc = _dh = 0.0            # ← 归零 = 本层不写这三个量
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

    # ---- L3 混色 ----  ★★ 09-29 **换序**：混色在前、分色在后。
    #   为什么：`mix()` 把 a*/b* 整体乘以一个系数（`sat` 那一套）。分色若在它前面，
    #   刚加进去的带偏移会被一起缩掉（实测 sat=0.69 ⇒ 缩 31%；`mix` 还会把暗部 a 推回 +0.97）。
    #   ⇒ 分色必须是**最后一个动 a*/b* 的人**。这也是 LR 的面板顺序（HSL/Color Mixer → Color Grading）。
    L, a, b, i3 = mix(disp, L, a, b, tg, cfg, _pz, m)
    # ---- L2 分色 ----
    a, b, i2 = split(L, a, b, tg, cfg, m)
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
                # ★ 09-29 晚：把分色那层的**自检字段**透出来（`apply()` 原先只透 d_sh/d_hi，
                #   结果外部脚本读 `report['grade']['split_resid']` 永远是 None --
                #   排查"到底落没落靶"时白跑了一轮真渲染）。纯新增键，不改任何行为。
                split_model=i2.get('split_model'), split_iters=i2.get('split_iters'),
                split_resid=i2.get('split_resid'), split_field=i2.get('split_field'),
                split_limit=i2.get('split_limit'),
                tgt_sh=i2.get('tgt_sh'), tgt_hi=i2.get('tgt_hi'), tgt_mid=i2.get('tgt_mid'),
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
