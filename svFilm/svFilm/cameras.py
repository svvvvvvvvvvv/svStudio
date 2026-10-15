
# -*- coding: utf-8 -*-
r"""IDT 机型表 —— **加机型不改代码**，只在这里加一行。

为什么要这张表：相机厂商**故意让 RAW 欠曝**，把"把中点抬回来"留给转换器做（DNG 规范里叫
**baseline exposure**；Adobe 系是"静默"做掉的，滑块显示 0）。LibRaw 作者：相机灰点常在满量程的
7~12%，转换器要把它抬回 18%，且**要用自定义 tone curve 把高光压下去**。

表里存两样，**别混**：① `baseline_ev` = 与该**机型**绑定的常数基线；② 富士 DR 档的**额外**欠曝
（每张都可能不同，逐张从 RAF 的 MakerNote 里读）。补量 = `baseline_ev + FUJI_DR_BIAS[dr]`；若机身
写了精确的 `0x9650 RawExposureBias`，**直接用那个值覆盖**（它是含基础偏移的总量）。⚠ 同一台机身
DR 会变 ⇒ 必须逐张读。`python -m svFilm.cli calib <RAW+JPG 同名若干张>` 会打印建议值。"""
from __future__ import annotations

# key = (make, model) 小写（只写 model 也能命中）；0.72 = 富士的基础偏移（darktable）。

TABLE = {
    'x-t30 iii': dict(baseline_ev=0.72, note='富士基底 0.72；DR 额外量逐张读 tag'),
    'x-t30': dict(baseline_ev=0.72, note='同 X-T30 III，待复核'),
    'x100vi': dict(baseline_ev=0.72, note='40MP；富士基底 0.72，DR 逐张读'),
    'ilce-7m3': dict(baseline_ev=0.0, note='▲ 未标定。LibRaw 说多数机身偏 0.7~1.5EV，别猜，跑 calib'),
    'ilce-7m4': dict(baseline_ev=0.0, note='▲ 未标定。同上'),
}

DEFAULT = dict(baseline_ev=0.0, note='未知机型 ⇒ 不猜，常数项按 0')

# 富士「动态范围」档位的**额外**欠曝（不含 0.72 基底）：100 / 200 / 400 ⇒ 0 / 1 / 2 EV。
FUJI_DR_BIAS = {100: 0.0, 200: 1.0, 400: 2.0}



def lookup(make=None, model=None):
    keys = []
    if make and model:
        keys.append(('%s %s' % (make, model)).strip().lower())
    if model:
        keys.append(str(model).strip().lower())
    for k in keys:
        if k in TABLE:
            return dict(TABLE[k], key=k, hit=True)
    return dict(DEFAULT, key=None, hit=False)
