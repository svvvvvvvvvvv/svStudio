# -*- coding: utf-8 -*-
"""v9 LookProfile：固定曲线（逐张只换支点 P，不换形状）。"""
from __future__ import annotations

import curve_lib


def curve(P, look, toe_scale=1.0):
    c = (look or {}).get('curve') or {}
    toe = float(c.get('toe', 4.0)) * float(max(0.0, min(2.0, toe_scale)))
    return curve_lib.curve(P, float(c.get('g', 0.90)),
                           toe, float(c.get('shoulder', 4.0)))
