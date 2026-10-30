# -*- coding: utf-8 -*-
"""LR 基本面板的**色调模型**：滑杆 → 一条单调色调 LUT（显示伽马域）。

⚠⚠ 2026-10-31 矩阵否掉：这个 borrowed 模型不能当 LR 的传递函数。
   250 个实测点：MAE 2.61 / P90 6.42 / max 15.83 L*；典型错是 Highlights 在
   L≈60 被它判成"区域外权重 0"，实测却有 3~4 L*。生产请用 `lr_response.py`
   的**实测 k(L)**（MAE 0.74~1.21）。本文件只保留作结构参考/对拍，
   除非重新标定，否则不要再拿它算剂量。

为什么有这个模块（2026-10-30）
------------------------------
LR 没有公开 `Shadows/Highlights/Whites/Blacks/Contrast` 的传递函数（Adobe 只说
"按亮度区域自适应"）。我们过去用"每个滑杆 1 点 = 多少 L\\*"的**线性系数**去算剂量：
`Shadows 0.147 L*/点`、`Blacks 0.15`、`曲线暗部 0.30`、`Highlights −30` …
这些系数有两个硬伤：

1. **量纲不对**：滑杆作用在"显示码"上、按亮度区域加权，同一个滑杆在 L*=30 和
   L*=70 处抬的 L* 本来就不一样；一个常数必然在其中一端错。
2. **样本不够**：n=1~2 的标定按纪律（复核 2）只能进台账，不能进常数表。

现在改成**结构化模型**：滑杆 = 在 8 个色调结点上加减位移，结点之间用单调三次
Hermite 插值；每个滑杆的"区域权"是一条 smoothstep 基函数（阴影只动暗部、
高光只动亮部、Whites/Blacks 只钉两端、Contrast 是反对称 S）。这样剂量由
**这张图自己的 L\\*** 决定，不再有"L*/点"。

出处与性质（重要，别把它当成 Adobe 的算法）
--------------------------------------------
本模型的结构、结点位置、基函数与权值来自开源实现 **autoshade**
（`src/render/tone.rs::tone_slider_basis` / `tone_exposure_curve` / `build_tone_lut`，
文档 `docs/TECH_STACK.md`「Develop pipeline and tone model」）：
    · 结点 x = [0, .10, .25, .50, .66, .82, .92, 1]（designed knot layout）
    · 曝光 = 线性光增益后过 sRGB 传递（`srgb_to_linear(x)·2^EV` 再编码）——**精确**
    · 插值 = Fritsch–Carlson 单调三次 Hermite（标准算法，无过冲）
    · 权值 A_SHADOW=0.33 / A_HIGHLIGHT=0.34 / A_CONTRAST=0.20 / A_WB=0.32
      （autoshade 的"全推能移动多少结点"的权威值，用于对齐 LR 的滑杆手感）
**它不是 Adobe 的内部实现**，是一个"成熟、可复现、和我们同样面向 LR"的模型。
按纪律 §3：查不到 Adobe 的闭式 ⇒ 不编斜率；这里给的是**可替换的模型**，
预测值必须由 ③ 出片验收（模型的边界写在 `predict` 的注释里）。

授权：autoshade 为 MIT License（Copyright (c) 2026 skymanbp）。本文件是
**按公开文档与源码结构独立重写**的 Python 实现（未拷贝其 Rust 源码）；
保留此声明以遵守 MIT 的署名要求。

坐标系
------
x 是**显示伽马编码**（0..1，= sRGB 码/255），不是 L\\*。L\\* 只在进出时用
`photometry` 换算；这与 LR 的 ToneCurve / Basic 面板在同一域里工作一致。
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Sequence, Tuple

from design.photometry import code_to_l, l_to_code, srgb_decode, srgb_encode

# 结点（autoshade `TONE_KNOTS_X`；0.66 是"中亮水面"、0.82 是肩、0.92 是 whites）
KNOTS_X: Tuple[float, ...] = (0.0, 0.10, 0.25, 0.50, 0.66, 0.82, 0.92, 1.0)

# 滑杆顺序固定：contrast, highlights, shadows, whites, blacks（与 XMP 同名）
SLIDERS: Tuple[str, ...] = ('contrast', 'highlights', 'shadows', 'whites', 'blacks')

# 全推 ±100 时，结点能移动多少（显示码比例；autoshade 的"authority"）
A_CONTRAST = 0.20
A_HIGHLIGHT = 0.34
A_SHADOW = 0.33
A_WHITE_BLACK = 0.32


def smoothstep(e0: float, e1: float, x: float) -> float:
    """标准 smoothstep：0 在 e0、1 在 e1，中间 C¹ 过渡。"""
    if e1 <= e0:
        return 0.0 if x < e0 else 1.0
    t = min(1.0, max(0.0, (float(x) - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def slider_basis(x: float) -> Tuple[float, float, float, float, float]:
    """在显示码 x 处，五个滑杆全推 ±100 时各自的结点位移（比例，−/+）。

    对应 autoshade `tone_slider_basis`；2026-10-30 逐行对照过。
    """
    x = float(x)
    w_shadow = smoothstep(0.0, 0.25, x) * (1.0 - smoothstep(0.25, 0.50, x))
    # highlights 在 0.82 达峰、0.50 与 1.0 两点**钉死为 0** ⇒ 永不动白点
    w_high = smoothstep(0.60, 0.82, x) * (1.0 - smoothstep(0.82, 1.0, x))
    # contrast = 肩叶 − 趾叶 ⇒ 反对称，两端与 0.50 处为 0
    w_contrast = (smoothstep(0.50, 0.75, x) * (1.0 - smoothstep(0.75, 1.0, x))
                  - w_shadow)
    # whites/blacks 只钉字面端点 + 各带一点相邻结点
    w_white = 1.0 if x >= 0.999 else (0.45 if abs(x - 0.92) < 1e-3 else 0.0)
    w_black = 1.0 if x <= 0.001 else (0.45 if abs(x - 0.10) < 1e-3 else 0.0)
    return (A_CONTRAST * w_contrast, A_HIGHLIGHT * w_high, A_SHADOW * w_shadow,
            A_WHITE_BLACK * w_white, A_WHITE_BLACK * w_black)


def exposure_curve(x: float, ev: float) -> float:
    """曝光：显示码 → 线性光 ×2^EV → 回显示码（精确的 sRGB 往返）。"""
    return srgb_encode(srgb_decode(float(x)) * (2.0 ** float(ev)))


def _fc_tangents(xs: Sequence[float], ys: Sequence[float]) -> list:
    """Fritsch–Carlson 单调三次 Hermite 的结点斜率（无过冲）。"""
    n = len(xs)
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0.0:
            m[i] = 0.0
        else:
            w1 = 2.0 * (xs[i + 1] - xs[i]) + (xs[i] - xs[i - 1])
            w2 = (xs[i + 1] - xs[i]) + 2.0 * (xs[i] - xs[i - 1])
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    # 圆判据 α²+β² ≤ 9（Fritsch–Carlson 原文的充分条件）
    for i in range(n - 1):
        if d[i] == 0.0:
            m[i] = m[i + 1] = 0.0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            s = a * a + b * b
            if s > 9.0:
                t = 3.0 / math.sqrt(s)
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    return m


def _hermite(xs: Sequence[float], ys: Sequence[float], m: Sequence[float], x: float) -> float:
    n = len(xs)
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = 0
    while i < n - 2 and x > xs[i + 1]:
        i += 1
    h = xs[i + 1] - xs[i]
    t = (x - xs[i]) / h
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i]
            + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1])


def knot_weights(ev: float = 0.0) -> list:
    """滑杆在**当前曝光**下的权威（autoshade `tone_knot_weights`）。

    曝光把某段压到 0 或推到 1 之后，那一带的结点已经没有"间隔"可动；
    再让滑杆硬推只会造出台阶。所以权威 = 相邻基曲线间隔的健康度：
    间隔 > 1% 满权威，间隔 → 0 权威 → 0。ev=0 时全部 = 1（逐位等价）。
    """
    base = [exposure_curve(x, ev) for x in KNOTS_X]
    out = []
    for i in range(len(base)):
        left = base[i] - base[i - 1] if i > 0 else 0.0
        right = base[i + 1] - base[i] if i < len(base) - 1 else 0.0
        out.append(min(1.0, max(0.0, max(left, right) / 0.01)))
    return out


def knot_values(ev: float = 0.0, sliders: Dict[str, float] = None) -> list:
    """8 个结点的输出值（0..1）：曝光曲线 + 各滑杆 × 基函数 × 权威。"""
    s = {k: 0.0 for k in SLIDERS}
    s.update({k: max(-100.0, min(100.0, float(v)))
              for k, v in (sliders or {}).items() if k in s})
    ys = []
    w = knot_weights(ev)
    for i, x in enumerate(KNOTS_X):
        b = slider_basis(x)
        v = exposure_curve(x, ev)
        v += w[i] * (b[0] * s['contrast'] + b[1] * s['highlights']
                     + b[2] * s['shadows'] + b[3] * s['whites']
                     + b[4] * s['blacks']) / 100.0
        ys.append(min(1.0, max(0.0, v)))
    # 单调兜底：滑杆本身有量程，正常组合不会反折；这一步只防越界输入
    for i in range(1, len(ys)):
        if ys[i] < ys[i - 1]:
            ys[i] = ys[i - 1]
    return ys


def tone_lut(ev: float = 0.0, sliders: Dict[str, float] = None,
             n: int = 256) -> list:
    """整条色调 LUT：x=i/(n−1)（显示码）→ y（显示码，0..1）。"""
    ys = knot_values(ev, sliders)
    m = _fc_tangents(KNOTS_X, ys)
    return [_hermite(KNOTS_X, ys, m, i / float(n - 1)) for i in range(n)]


def sample_lut(lut: Sequence[float], x: float) -> float:
    """在 LUT 上线性取样（x 0..1）。"""
    n = len(lut)
    if n == 0:
        return float(x)
    pos = min(1.0, max(0.0, float(x))) * (n - 1)
    i = int(math.floor(pos))
    if i >= n - 1:
        return float(lut[-1])
    t = pos - i
    return float(lut[i]) * (1.0 - t) + float(lut[i + 1]) * t


def predict_l(L: float, sliders: Dict[str, float] = None, ev: float = 0.0) -> float:
    """一个 L* 过完"曝光 + 基本面板"之后的 L*（模型预测，不是 Adobe 闭式）。"""
    lut = tone_lut(ev, sliders)
    x = min(1.0, max(0.0, l_to_code(float(L)) / 255.0))
    return code_to_l(255.0 * sample_lut(lut, x))


def predict_quantiles(quantiles: Dict[str, float], sliders: Dict[str, float] = None,
                      ev: float = 0.0) -> Dict[str, float]:
    """一组 L* 量（p5/p50/p95/脸 L…）过同一个单调 LUT。

    单调映射与分位数可交换 ⇒ 不需要"每档多少 L*"的传递系数；
    这里逐点取 LUT，和白端截断一起自然发生。
    """
    out = {}
    for k, v in (quantiles or {}).items():
        out[k] = None if v is None else round(predict_l(float(v), sliders, ev), 2)
    return out


def slider_for_target(L: float, target_L: float, which: str = 'whites',
                      ev: float = 0.0) -> Optional[float]:
    """反解：要让 `L` 落到 `target_L`，`which` 这根滑杆给多少（UI −100..100）。

    模型对每根滑杆是**线性**的（结点值 = 基值 + Σ 基函数·滑杆/100；单调兜底在
    正常量程内不触发）⇒ 在 x 处取"滑杆 0"和"滑杆 100"两条 LUT 线性内插即可，
    不用搜索。若 100 还不够 / 方向相反，返回 None 交给上层记账。
    """
    which = str(which)
    if which not in SLIDERS:
        raise ValueError('未知滑杆 %r' % which)
    x = min(1.0, max(0.0, l_to_code(float(L)) / 255.0))
    y0 = sample_lut(tone_lut(ev, {which: 0.0}), x)
    want = min(1.0, max(0.0, l_to_code(float(target_L)) / 255.0))
    # ★ 必须在目标的一侧取区间：+100 那一端结点会被 1.0 截断（白点不能更白），
    #   拿它当斜率会得到荒谬的小分母（实测 95→90 会算出 −99）。
    def _y(v: float) -> float:
        return sample_lut(tone_lut(ev, {which: float(v)}), x)

    if want < y0:
        y1 = _y(-100.0)
        if y0 - y1 < 1e-9:
            return None
        v = -100.0 * (y0 - want) / (y0 - y1)
    elif want > y0:
        y1 = _y(100.0)
        if y1 - y0 < 1e-9:
            return None
        v = 100.0 * (want - y0) / (y1 - y0)
    else:
        return 0.0
    # ★ 线性内插只是起点：Hermite 的斜率 m 是结点值的**非线性**函数，
    #   所以"值对滑杆"不是严格线性。用 3~5 步割线收敛（纯函数、不搜索网格）。
    v = min(100.0, max(-100.0, v))
    for _ in range(6):
        y = _y(v)
        err = y - want
        if abs(err) < 2e-4:
            break
        h = 1.0 if v < 99.0 else -1.0
        dy = _y(v + h) - y
        if abs(dy) < 1e-9:
            break
        v = min(100.0, max(-100.0, v - err * h / dy))
    if abs(_y(v) - want) > 1e-3:
        return None          # 顶到量程/被白点截断，交上层记账
    return round(v, 1)


def describe() -> str:
    """口径一句话（报告里引用）。"""
    return ('滑杆模型 = 8 结点单调 Hermite（autoshade tone.rs 结构，非 Adobe 闭式）；'
            '曝光先过 sRGB 线性光增益；预测必须由 ③ 验收')
