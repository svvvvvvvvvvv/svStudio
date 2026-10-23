# -*- coding: utf-8 -*-
"""v9 LookProfile：固定曲线（逐张只换支点 P，不换形状）。"""
from __future__ import annotations

import curve_lib


def curve(P, look, toe_scale=1.0):
    c = (look or {}).get('curve') or {}
    toe = float(c.get('toe', 4.0)) * float(max(0.0, min(2.0, toe_scale)))
    return curve_lib.curve(P, float(c.get('g', 0.90)),
                           toe, float(c.get('shoulder', 4.0)))


_HSL = {'red': 'Red', 'orange': 'Orange', 'yellow': 'Yellow', 'green': 'Green',
        'aqua': 'Aqua', 'blue': 'Blue', 'purple': 'Purple', 'magenta': 'Magenta'}


def global_spec(look):
    """LookProfile 的 HSL + 分级，拼成 GLOBAL 的一段（必须在 CURVES 之前写）。"""
    look = look or {}
    parts = []
    for name, key in _HSL.items():
        v = (look.get('hsl') or {}).get(name)
        if not v:
            continue
        h, s, l = (float(x) for x in (list(v) + [0, 0, 0])[:3])
        if abs(h) > 1e-6:
            parts.append('HueAdjustment%s=%.1f' % (key, h))
        if abs(s) > 1e-6:
            parts.append('SaturationAdjustment%s=%.1f' % (key, s))
        if abs(l) > 1e-6:
            parts.append('LuminanceAdjustment%s=%.1f' % (key, l))
    g = look.get('grading') or {}
    if g:
        parts += ['SplitToningShadowHue=%d' % int(g.get('shadow_hue', 0)),
                  'SplitToningShadowSaturation=%.1f' % float(g.get('shadow_sat', 0)),
                  'SplitToningHighlightHue=%d' % int(g.get('high_hue', 0)),
                  'SplitToningHighlightSaturation=%.1f' % float(g.get('high_sat', 0)),
                  'SplitToningBalance=%.1f' % float(g.get('balance', 0))]
    return ','.join(parts)
