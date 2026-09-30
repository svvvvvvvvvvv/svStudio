# -*- coding: utf-8 -*-
r"""二次调色（胶片引擎**之后**的颜色层）—— **L2 分色 + L3 混色**。

## ★★★ 本文件的结构纪律（09-28 拆分）

**每一段都是一个"纯函数"**：`(当前图像 + 自己的参数) → (新图像 + 自己的报告)`。
**两段之间不共享可变状态**（09-28 之前是一个 406 行的 `apply`，多件事共用 `L/a/b/Cc/live/_wn`
⇒ 改一段必然碰另一段 —— 那是"改一处另一处动"的物理根因）。

| 段 | 函数 | 只该管 | 不许碰 |
|---|---|---|---|
| **L2** | `split()` | 暗/中/高的 **色偏**（a*/b*）| 亮度、彩度 |
| **L3** | `mix()` | **彩度**（总量 + 按色相分配）| 亮度（除"带内 dL"那一项）、色偏 |
| — | `gamut()` | 出界颜色往中性轴收 | — |
| — | `apply()` | **编排**上面两段 + 汇总报告 | **自己不写任何量** |

★ 验证方式：`_debug/_accept.py`（**统计口径**：拆前 vs 拆后，11 项指标中位数）。
★ 拆分前的基线：`效果debug/2026-09-28/_拆分前基线/`。

## ⚠⚠⚠ 一条重要教训：**"逐位对比"这个验证方法对本管线【不成立】**

09-28 拆分后第一次验收用"逐像素对比"，结果 **90% 的像素不同（最大差 139）** —— 一度以为拆错了。
**查清了：是管线本身有非确定性** —— 用**同一份代码**跑两次，结果同样有 **86~97% 的像素不同**
（引擎的颗粒是随机的）⇒ 经"边界羽化 / 权重归一化"扩散 ⇒ 大片区域跟着变。

⇒ **所以本管线的验收必须用【统计口径】**（中位数/分位数，容差 1.0），不能用逐位。
⇒ 本文件拆分的验收结果：**11 项指标最大偏差 0.143** ⇒ **零行为改变** ✅

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
    # ★★★ 09-30 晚：**约束带 3 → 5**（预设配了 `zone_abs` 时）。
    #   老的三带 = 「暗 P≤20 / 亮 P≥80 / 中 P25~P75」，而 SV 在 LR 直方图上看的是 **5 格**：
    #   观感上的「阴影段」落在 P20 与 P50 之间 ⇒ **三个带谁也不管它**（§163/§167 已锁定）。
    #   实测：白平衡修对之后，残余的偏色恰好就在阴影段（R−B 比鹿井暖 6 格）。
    #   `zone_abs` 的两个新增带（P20~P40 阴影 / P60~P80 次高光）= 眼睛真正看到偏色的那两段。
    #   ⚠ 没配 `zone_abs` 的预设 ⇒ **逐位走老的三带**（向后兼容，`selftest` 有断言）。
    _z5 = (tg or {}).get('zone_abs')
    _nz = 0
    if isinstance(_z5, (list, tuple)) and len(_z5) == 2 \
            and min(len(_z5[0]), len(_z5[1])) == 5:
        _nz = 5
        _qs = np.percentile(L, [0.0, 20.0, 40.0, 60.0, 80.0, 100.0])
        _bands = tuple((L >= _qs[_i]) & (L <= _qs[_i + 1]) for _i in range(5))
    else:
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
    # ★★ 09-30：靶可以配「内容曲线」—— 目标不再是常数，而是**按画面自身的色偏查**。
    #   为什么：三位大师**自己那批图**的暗部 Δb 的 IQR 就有 4~8 格（固定点靶不可达，
    #   追它只会"按下这张、浮起那张"）；而「画面越黄 ⇒ 分带相对越蓝」两位大师都**单调**
    #   （鹿井侧相关 −0.75），物理上也通（画面黄的多 ⇒ 阴影接的天光相对重）。
    #   自变量 = **整张 a*/b* 的 median**：a 轴查整张 a*、b 轴查整张 b*
    #   （实测交叉项不相关：all_b 对 暗Δa 只有 −0.09）。
    #   没配 `_curves` 的预设 ⇒ **行为与以前逐位相同**（仍走 `sh_abs/hi_abs` 的点靶）。
    _cv = (tg or {}).get('_curves') or {}
    if tg and tg.get('sh_abs'):
        t_sh = (float(tg['sh_abs'][0]), float(tg['sh_abs'][1]))
        t_hi = (float(tg['hi_abs'][0]), float(tg['hi_abs'][1]))
    else:
        t_sh = (float(getattr(cfg, 'GRADE_SH_A', 0.0)), float(getattr(cfg, 'GRADE_SH_B', 0.0)))
        t_hi = (float(getattr(cfg, 'GRADE_HI_A', 0.0)), float(getattr(cfg, 'GRADE_HI_B', 0.0)))
    _mid = (tg or {}).get('mid_abs')
    t_md = (float(_mid[0]), float(_mid[1])) if _mid else (0.0, 0.0)

    _cv_used = []
    if _cv:
        _xa, _xb = float(np.median(a)), float(np.median(b))

        def _look(key, x, fb):
            """查曲线；**没配/配得不全 ⇒ 回落 `fb`（点靶）**，并回报"到底用没用上"。

            ★★ 09-30 晚：返回两个值 —— 第二个就是给 `_cv_used` 的。
              老写法**只要配了 `_curves` 就报"四条都在用"**，某条缺了也照样报
              ⇒ 排查"曲线到底生效没有"时会误判（§148 栽过同类坑）。
            """
            e = _cv.get(key) or {}
            xs, ys = e.get('x') or [], e.get('y') or []
            if len(xs) < 2 or len(xs) != len(ys):
                return float(fb), False
            return float(np.interp(x, [float(v) for v in xs], [float(v) for v in ys])), True

        # ★★ 09-30 晚：**中间调也走曲线**。
        #   为什么：与 `targets.json` **同一把尺子**下的三位大师实测 ——
        #   「中 Δa / 中 Δb」是**唯一"该动态却还固定"**的两条（鹿井 IQR 2.47 / 4.42），
        #   且中 Δa 的最强预测子（整张 a*，**+0.72**）比已做成曲线的暗 Δa（−0.60）**还强**。
        #   ⇒ 不把它一起动态，分色就永远在这两段上"按同一个数"。
        _v, u = _look('sh_a', _xa, t_sh[0]);  t_sh = (_v, t_sh[1]);  _cv_used += ['sh_a'] * u
        _v, u = _look('sh_b', _xb, t_sh[1]);  t_sh = (t_sh[0], _v);  _cv_used += ['sh_b'] * u
        _v, u = _look('hi_a', _xa, t_hi[0]);  t_hi = (_v, t_hi[1]);  _cv_used += ['hi_a'] * u
        _v, u = _look('hi_b', _xb, t_hi[1]);  t_hi = (t_hi[0], _v);  _cv_used += ['hi_b'] * u
        # ⚠ 5 带模式下 md 的曲线在**下面**（换 `tgt` 那一列的代码）里查、在那里记账；
        #   这里再记一次就会出现**重复的 `md_a`/`md_b`**（冒烟时实测到，已修）。
        if _nz != 5 and (_cv.get('md_a') or _cv.get('md_b')):
            _v, u = _look('md_a', _xa, t_md[0]);  t_md = (_v, t_md[1]);  _cv_used += ['md_a'] * u
            _v, u = _look('md_b', _xb, t_md[1]);  t_md = (t_md[0], _v);  _cv_used += ['md_b'] * u
    if _nz == 5:
        tgt = np.array(_z5, np.float64)
        # 5 带模式下「中调」那一列（index 2）仍然走**内容曲线**（`md_a`/`md_b`）——
        # 它是 §163 里唯一"该动态却还固定"过的那一条，不要因为扩带把它丢回去。
        if _cv:
            _v, u = _look('md_a', float(np.median(a)), float(tgt[0, 2]))
            tgt[0, 2] = _v; _cv_used += ['md_a'] * u
            _v, u = _look('md_b', float(np.median(b)), float(tgt[1, 2]))
            tgt[1, 2] = _v; _cv_used += ['md_b'] * u
            # ★★ 报告字段 `tgt_mid` 必须**跟着走** —— 它原来只反映老的三带靶 `t_md`，
            #   5 带模式下不更新的话，外部（含 `selftest`）读 `report['grade']['tgt_mid']`
            #   永远是那个固定值 ⇒ **"曲线到底生效没有"根本查不出来**（自检当场红给我看）。
            t_md = (float(tgt[0, 2]), float(tgt[1, 2]))

        # ★★★★ 09-30 深夜：**五段靶也做成"按画面查的曲线"**（= SV 说的"动态预设参数"）。
        #   为什么非做不可：`th=0.74` 之后**逐张**的白点段 R−B 是
        #   −27.0 / −14.8 / −4.2 / +9.1 / +9.4 / +25.9（**跨度 53 格**），而鹿井的目标是 **+3.0**
        #   ⇒ **一个全局固定值绝对扛不住**。
        #   而它明显与「整张色偏」同向（1665 整张b*+6.5 ⇒ 白点+26；1289 整张b*−5.5 ⇒ 白点−27）
        #   ⇒ 走 `_curves` 那条老路：自变量 = **整张 a\*/b\***，因变量 = **该段自己的** Δa/Δb。
        #   ⚠ **只收 |r| ≥ 0.40 的段**（`_j1_zonecurves.py` 逐段标定）——
        #     阴影 Δb（+0.20）、次高光 Δa（+0.16）等**没信号**的段**留在固定靶上**，
        #     不许硬套一条噪声曲线（§164.5 的规矩）。
        _zcv = (tg or {}).get('_zone_curves') or {}
        if _zcv:
            _xza, _xzb = float(np.median(a)), float(np.median(b))
            for _zi in range(len(_bands)):
                for _ai, _xv in ((0, _xza), (1, _xzb)):
                    _key = 'z%d_%s' % (_zi, 'a' if _ai == 0 else 'b')
                    _e = _zcv.get(_key) or {}
                    _xs, _ys = _e.get('x') or [], _e.get('y') or []
                    if len(_xs) >= 2 and len(_xs) == len(_ys):
                        tgt[_ai, _zi] = float(np.interp(_xv, [float(v) for v in _xs],
                                                        [float(v) for v in _ys]))
                        _cv_used += [_key]
            t_md = (float(tgt[0, 2]), float(tgt[1, 2]))     # 中调那格可能被上面改过
    else:
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
        J = np.zeros((2, len(_bands), _n), np.float64)
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
    # ⚠ 维度必须跟着 `_bands` 走（写死 3 ⇒ 5 带时矩阵形状对不上，当场崩）
    _wz = [1.0, 1.0, _wmid, 1.0, 1.0] if _nz == 5 else [1.0, 1.0, _wmid]
    assert len(_wz) == len(_bands)
    Lam = np.diag(_wz)

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
    # ⚠ 老代码直接引 `msh/mhi/mmid` 三个变量 —— 5 带模式下它们不存在（名字换成了元组）。
    #   ⇒ 一律按 `_bands` 取：首 = 暗、尾 = 亮、中 = 中间那个（3 带时即原来的 mmid）。
    _b0, _b1, _bc = _bands[0], _bands[-1], _bands[len(_bands) // 2]
    info = dict(d_sh=(float(_bm(_fa, _b0)), float(_bm(_fb, _b0))),
                d_hi=(float(_bm(_fa, _b1)), float(_bm(_fb, _b1))),
                d_mid=(float(_bm(_fa, _bc)), float(_bm(_fb, _bc))),
                split_zones=_nz,
                d_zones=[(round(float(_bm(_fa, _b)), 3), round(float(_bm(_fb, _b)), 3))
                         for _b in _bands],
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
                tgt_sh=list(t_sh), tgt_hi=list(t_hi), tgt_mid=list(t_md),
                # ★ 09-30：这一张**实际用的曲线靶**（没配 `_curves` ⇒ 空，走老的点靶）
                split_curve_used=_cv_used,
                curve_x=(round(float(np.median(a)), 2), round(float(np.median(b)), 2)))
    return a2, b2, info


# ---------------------------------------------------------------------------
# L3 混色：按色相带改彩度（+ 带内亮度偏移）
# ---------------------------------------------------------------------------
def mix(L, a, b, tg, cfg, m, person=None):
    """**L3 混色** —— 只改彩度（总量 + 按色相分配）+ 色相带内的少量亮度 + **人物区域的整体提亮**。

    ★ 灰色像素不动（`live` 窗）—— 否则会把中性轴一起推偏（digitalFilm 那条教训）。
    ★ 最后**归一**（把整张彩度中位拉回 "总量 × sat"）⇒ 「形状归曲线、总量归 sat」。

    ## ★★ 09-30 晚新增：**按"人在哪"加权**（`person=`）
    为什么：SV 原话「不要算那个脸 去拉亮度，之前做过会很割裂」——
      **脸上的局部提亮会在脸↔脖子↔手臂之间出断层**（L4 肤色层当年就是这么被否掉的）。
      ⇒ 改成**整个"人物区域"一起抬**：掩膜来自 `person.py`（`selfie_multiclass` 的「1 − 背景」，
      本来就在跑的那一次前向，**零额外成本**），软掩膜 ⇒ 边界天然羽化。
    · `GRADE_PERSON_W`（默认 1.0）= **人掩膜的权重**：
      `1.0` ⇒ 只动人身上；`0.0` ⇒ 退化成全局（= 加这个参数之前的行为，用于反例关）。
    · `person_dl`（靶字段，按预设）或 `GRADE_PERSON_DL`（config 兜底）= **整个人物区抬多少 L\\***。
      **不经过 `live` 窗**（暗衣服也要一起抬，否则人身上半亮半暗更割裂）。
      ⚠ `person=None`（检不出人 / 调用方没传）⇒ **这块不生效**（弃权），不许偷偷退化成全局。
    · `band_dl`（靶字段，12 个色相带）= 在 `BANDS` 写死的 `dL` 之上**再加**的量
      —— 与 `band_gain`（彩度）成对；用于"单独提亮橙色亮度"。
    @returns {(L, a, b, dict)} 新的 L/a/b + 本段报告
    """
    Lm, am, bm = m['Lm'], m['am'], m['bm']
    Cc = np.sqrt(a * a + b * b)
    H = np.degrees(np.arctan2(b, a)) % 360.0
    cmin = float(getattr(cfg, 'GRADE_C_MIN', 12.0))
    live = _ramp(Cc, cmin * 0.6, cmin * 1.4)
    kc = np.zeros_like(Cc)
    dl = np.zeros_like(Cc)
    # ★★ 三个"按色相带"的靶字段（都是**加在 `BANDS` 写死的基线之上**的偏移）：
    #   · `band_gain` = **该大师的风格**（按大师那批图量出来的，如沉褐 10 带全 >0）
    #   · `band_dk`   = **我们的修正**（彩度）—— 与 `band_gain` **分开存**，否则会把
    #     "风格"和"修 bug"搅在一起、换个预设就被覆盖掉（`for_stock` 是 preset 盖 `_default`）。
    #   · `band_dl`   = **我们的修正**（亮度）—— "像 HSL 一样单独提亮橙色亮度"就写这儿。
    _bg = (tg or {}).get('band_gain') or [0.0] * 12
    _bk = (tg or {}).get('band_dk') or [0.0] * 12
    _bd = (tg or {}).get('band_dl') or [0.0] * 12
    for bi, (center, half, k, dL) in enumerate(BANDS):
        # ★ 色相带增益 = 预设自带的那份（按大师量出来的），没有专属靶就是 0
        k = k + float(_bg[bi]) if bi < len(_bg) else k
        k = k + float(_bk[bi]) if bi < len(_bk) else k
        dL = dL + float(_bd[bi]) if bi < len(_bd) else dL
        w = _band_weight(H, center, half) * live
        kc += w * k
        dl += w * dL
    # 软归一：多个带重叠时不把增益叠爆
    over = np.maximum(kc, 0.0)
    scale = np.where(over > 0.45, 0.45 / np.maximum(over, 1e-6), 1.0)
    kc = kc * scale
    dl = np.clip(dl * scale, -6.0, 8.0)

    # ---- ★★ 人物区域整体提亮（人掩膜）----
    # 放在 `dl` 的裁剪**之后**：否则大剂量的"抬人"会被那个 ±6/+8 的裁剪吃掉。
    _pz, _pdl = None, 0.0
    _pv = (tg or {}).get('person_dl')
    _pdl = float(_pv) if _pv is not None else float(getattr(cfg, 'GRADE_PERSON_DL', 0.0) or 0.0)
    if person is not None:
        _pw = float(getattr(cfg, 'GRADE_PERSON_W', 1.0) or 0.0)
        _pm = np.clip(np.asarray(person, np.float64), 0.0, 1.0)
        if _pm.shape != L.shape:                    # 尺寸对不上 ⇒ 最近邻缩（掩膜本来就软）
            _iy = (np.arange(L.shape[0]) * _pm.shape[0] / max(L.shape[0], 1)).astype(np.int32)
            _ix = (np.arange(L.shape[1]) * _pm.shape[1] / max(L.shape[1], 1)).astype(np.int32)
            _pm = _pm[np.clip(_iy, 0, _pm.shape[0] - 1)][:, np.clip(_ix, 0, _pm.shape[1] - 1)]
        _pz = _pw * _pm + (1.0 - _pw)
    _lift = (_pdl * _pz) if (_pz is not None and _pdl) else None

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
    # ★★ 09-30 晚：`_lift` 是"整个人物区域"的提亮 —— **不乘 `live`**（暗衣服也要抬），
    #   但要乘人掩膜（人外一点不动）⇒ 不会出现"人身上半亮半暗"或"背景被一起抬"的割裂。
    L2 = np.clip(L + dl * live + (_lift if _lift is not None else 0.0), 0.0, 100.0)
    info = dict(c_gain=[(c, (k + (_bg[i] if i < len(_bg) else 0.0)))
                        for i, (c, _, k, _) in enumerate(BANDS)],
                sat=_sat, dL_bands=(float(np.min(dl)), float(np.max(dl))),
                # ★★ 09-30 晚：「人物区域整体提亮」到底生没生效 —— 只在报告里说清楚，
                #   排查时不用去翻图（`person_on=False` = 没传掩膜 ⇒ 这块**没生效**，弃权）。
                person_on=bool(_pz is not None and _pdl),
                person_dl=float(_pdl),
                person_w=float(getattr(cfg, 'GRADE_PERSON_W', 1.0) or 0.0),
                person_pct=(round(float((np.asarray(person) > 0.5).mean() * 100), 2)
                            if person is not None else None))
    return L2, a2, b2, info


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
def apply(disp, cfg=C, stock=None, scene=None, person=None):
    """在**成片**（显示域）上做 L2 分色 + L3 混色。

    `scene`：`scene.classify(...)` 的结果 —— **只用来取靶**（`targets._scene` 覆盖）。
    `person`：**"人在哪"的软掩膜**（`person.py` 的输出，0~1，与 `disp` 同尺寸）——
      只给 L3 的「人物区域整体提亮」用（`person_dl`）。`None` ⇒ **那块不生效**（弃权）。
      ⚠ 不给默认值退化成全局：没有掩膜就别动，否则又变成"整张提亮"。

    ★★★ 09-28 拆分：本函数**只做编排**，两段各自是纯函数（见文件头那张表）。
    @returns {(numpy.ndarray, dict)} 出图 + 报告（能自查动了多少）
    """
    if not bool(getattr(cfg, 'GRADE_ENABLE', False)):
        return np.clip(np.asarray(disp, np.float64), 0.0, 1.0), dict(applied=False)

    tg = _tgt_of(stock, scene)
    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    lab = color.to_lab(d)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    m = dict(Lm=float(np.median(L)), am=float(np.median(a)), bm=float(np.median(b)))

    # ---- L3 混色 ----  ★★ 09-29 **换序**：混色在前、分色在后。
    #   为什么：`mix()` 把 a*/b* 整体乘以一个系数（`sat` 那一套）。分色若在它前面，
    #   刚加进去的带偏移会被一起缩掉（实测 sat=0.69 ⇒ 缩 31%；`mix` 还会把暗部 a 推回 +0.97）。
    #   ⇒ 分色必须是**最后一个动 a*/b* 的人**。这也是 LR 的面板顺序（HSL/Color Mixer → Color Grading）。
    L, a, b, i3 = mix(L, a, b, tg, cfg, m, person=person)
    # ---- L2 分色 ----
    a, b, i2 = split(L, a, b, tg, cfg, m)
    # ---- 色域映射 ----
    out = gamut(L, a, b)

    info = dict(applied=True,
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
                # ★ 09-30：把「曲线靶」那两项也透出来（`apply()` 是**显式列举**要透传的键，
                #   新加的键不透 ⇒ 外部脚本读 `report['grade']['split_curve_used']`
                #   永远是 None，排查"曲线到底生效没有"时白跑一轮真渲染 —— §148 栽过同类坑）。
                split_curve_used=i2.get('split_curve_used'),
                curve_x=i2.get('curve_x'),
                # ★ 09-30 晚：扩带那两个新键 —— `apply()` 是**显式列举**透传的，
                #   不登记的话外部读 `report['grade']['split_zones']` 永远是 None（§148 同类坑）。
                split_zones=i2.get('split_zones'), d_zones=i2.get('d_zones'),
                # ★ 09-30 深夜：五段曲线（`_zone_curves`）实际命中了几条 —— 见 `split_curve_used`
                zone_curves_used=[k for k in (i2.get('split_curve_used') or []) if k.startswith('z')],
                tgt_sh=i2.get('tgt_sh'), tgt_hi=i2.get('tgt_hi'), tgt_mid=i2.get('tgt_mid'),
                c_gain=i3['c_gain'], stock=stock,
                person_on=i3.get('person_on'), person_dl=i3.get('person_dl'),
                person_w=i3.get('person_w'), person_pct=i3.get('person_pct'),
                # ★ 09-26：这一张命中了哪几条**场景覆盖**（`targets._scene`）——
                #   空 = 一条都没命中（= 跟加场景之前逐位相同）。
                scene_hits=list((tg or {}).get('_scene_hits') or []),
                scene=((tg or {}).get('scene')))
    return out, info
