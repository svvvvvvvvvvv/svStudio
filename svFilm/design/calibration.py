# -*- coding: utf-8 -*-
"""LR 的**单位换算式**（定义/接口事实，不是标定）。

A. 精确单位换算
   · `LocalExposure2012` 文件值 = EV/4        —— 4 档 = 满刻度 1.0
   · 局部族其余滑杆文件值 = UI/100            —— LR 局部 XMP 约定
   出处：autoshade `docs/TECH_STACK.md`「XMP and Lightroom interoperability」
   （对真实 LR 导出实测）＋我们自己的 `_calib_fill` 三档对拍。

B. 标准定义式
   亮度 ↔ 曝光 ↔ 码值在 `photometry`（CIELAB / sRGB）。
   没有公开传递函数的滑杆只在 `lr_response` 里用**实测一阶响应**做规划起点，
   不在这里编斜率。
"""
from __future__ import annotations

# 局部曝光：文件值 = EV/4（4 档 = 满刻度 1.0）
LOCAL_EXPOSURE_PER_EV = 1.0 / 4.0
# 局部其余滑杆：文件值 = UI/100
LOCAL_UI_SCALE = 1.0 / 100.0

# 台账（n<5，只供对拍，**不进剂量公式**）
LEDGER = {
    'local_fill': dict(n=2, note='文件 0.25 的效果约 1.06~1.16 EV'),
    'local_shadows': dict(n=1, note='满档脸 +7.1 L*；画面 p99 只 +0.1'),
    'sat_relative': dict(n=1, note='Saturation +30 在 C*=20 上 +6.63（相对刻度预测 +6.0）'),
    'split_shadow': dict(n=1, note='阴影轮 S=20/40/60 → 背景 b50 −0.198/−0.182/−0.173'),
}


def describe() -> str:
    return ('LR 常数只有两类：单位换算（EV/4、UI/100）· 标准定义式（photometry）；'
            'Basic 滑杆用实测一阶响应做起点，③ 目检验证')
