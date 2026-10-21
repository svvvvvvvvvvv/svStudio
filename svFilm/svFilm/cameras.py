
# -*- coding: utf-8 -*-
"""IDT 机型表 —— **加机型不改代码**，只在这里加一行。"""
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
