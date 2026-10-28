# -*- coding: utf-8 -*-
"""LR 的数值模型：**滑杆值 ↔ 效果**，只收能查到出处的那几条。

原则（2026-10-29 SV 定）：
  · 不做"在 LR 里试出来"的标定 —— 大量数值概念和公式是**可以查到的**；
  · 查不到就明说"没有公开闭式"，**不给公式、不编斜率**；
  · 每条都必须能指到来源。

已核的（有出处）
----------------
E1 曝光滑杆的单位 = **档（f-stop / EV）**
   Adobe：「The exposure values are in increments equivalent to f-stop (aperture)
   increments in your camera.」
   → https://helpx.adobe.com/lightroom-classic/help/image-tone-color.chromeless.html
   ⇒ 滑杆 +1 ⇒ 线性域 ×2。这是**精确**关系，不是近似。

E2 色调曲线 `ToneCurveName2012` 的取值：`Linear` / `Custom`（写自定义点曲线时置 Custom）
   → Adobe Camera Raw 的 XMP 规范（`crs:ToneCurveName2012`）
   ⇒ 我们写 16 点时必须同时把名字置 Custom，否则 LR 忽略点集。

E3 点颜色的 XMP 数字排布：`SatScale, LumScale, RangeAmount` 然后是
   `HueRange, SatRange, LumRange` 三组窗口
   → 社区实现 `autoshade/src/recipe.rs`（`SatScale, LumScale, RangeAmount, then
     HueRange, SatRange and LumRange as`）
   ⇒ 与我们实测的 19 数字布局一致（3 标量 + 16 窗口数）。

E4 `crs:PointColors` **必须配 `crs:ColorVariance`**，否则 LR 把色票归一成 19 个 −1
   → 本机实测（10-28），见 `LR参数手册 §11`。

E5 `SrcHue` 的合法范围是 **0~6 弧度**（不是 0~360 度）；超出会被静默丢弃
   → 本机实测（10-28）：hue 343.8°~360°（6.0~6.283）越界 ⇒ 夹到 5.95 才生效。

没有公开闭式的（**不许编斜率**）
-------------------------------
H1 `Highlights` / `Shadows` / `Whites` / `Blacks`
   Adobe 只说"按亮度范围起作用、会保护其它区域"，**没有公开的传递函数**。
   ⇒ 我们只把它当**方向**用（压/抬），幅度靠"加到目标为止"的验收，不拿公式算。
H2 `Saturation` / `Vibrance`
   Adobe 只说 Vibrance 会保护高饱和与肤色；**没有公开公式**。
   ⇒ 同上：只当方向；本机实测 Vibrance 在肤色上几乎无效（旧账_1028 §A5）。
H3 HSL / ColorMixer 的 ±100 映射
   旧账里记的是"hue ±100 = ±30°、sat=100(C_w/C−1)、lum=200(L_w/L−1)"，
   那是从公开模型推的**约定**，不是 Adobe 文档值 ⇒ 引用时必须写明"约定，非文档"。
"""
from __future__ import annotations

from typing import Dict, Optional

# ---- 常数的唯一出处（改这里就够了） ----
EXPOSURE_UNIT = 'stop'          # E1：滑杆单位 = 档
HW_LIMIT = 100.0                # 高光/阴影那组滑杆的上下限
HSL_HUE_DEG_PER_100 = 30.0      # H3：约定（非文档），旧账_1028
SRC_HUE_MAX = 5.95              # E5：实测，0~6 弧度制，夹 5.95


def linear_gain(ev: float) -> float:
    """E1：+1 档 = 线性域 ×2。**精确关系**。"""
    return 2.0 ** float(ev)


def stops_for_delta_L(dL: float, slope_L_per_ev: float) -> float:
    """把"要改多少 L*"换成档数。slope 必须来自实测（中间调 ~30、高光 8~16）。"""
    if abs(slope_L_per_ev) < 1e-6:
        raise ValueError('slope 不能为 0；中间调与高光端斜率不同，必须分别给')
    return float(dL) / float(slope_L_per_ev)


def clamp_slider(v: float, lo: float = -HW_LIMIT, hi: float = HW_LIMIT) -> float:
    return max(lo, min(hi, float(v)))


def hsl_hue_from_deg(deg: float) -> float:
    """H3：把"要转多少度"换成 HSL 色相滑杆值（约定，非文档）。"""
    return clamp_slider(deg * 100.0 / HSL_HUE_DEG_PER_100)


def src_hue_rad(deg: float) -> float:
    """E5：色相角（度）→ 点颜色 SrcHue（弧度，0~2π，夹到 5.95）。"""
    r = (float(deg) % 360.0) * 3.141592653589793 / 180.0
    return min(SRC_HUE_MAX, max(0.0, r))


def describe_limits() -> str:
    """把"哪些能算、哪些不能算"一次说清（给人和给代码看同一份）。"""
    lines = [
        '能算（有出处）：',
        '  曝光      +1 档 = 线性 ×2                [Adobe LR 帮助]',
        '  曲线      写点集必须置 ToneCurveName2012=Custom',
        '  点颜色    19 数字 = 3 标量 + 16 窗口数；必须配 ColorVariance；SrcHue 0~6 弧度',
        '',
        '不能算（无公开闭式，只当方向）：',
        '  Highlights / Shadows / Whites / Blacks   （Adobe 未公开传递函数）',
        '  Saturation / Vibrance                    （同上；Vibrance 在肤色上实测几乎无效）',
        '  HSL 的 ±100 映射                          （约定，非 Adobe 文档值）',
    ]
    return '\n'.join(lines)
