# -*- coding: utf-8 -*-
r"""LR 收尾 · 写入层：把动作清单翻成桥命令。"""
from __future__ import annotations

import os
import sys

import _boot  # noqa: E402
import paths
paths.ensure_import_path()
from bridge_client import run as lr  # noqa: E402

EXPORT_ROOT = str(paths.WORK / 'finish')


def export(tif, tag, long_edge=None):
    d = os.path.join(EXPORT_ROOT, 'tmp', tag)
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass
    cmd = 'EXPORTTIF ' + d + ((' %d' % long_edge) if long_edge else '')
    r = lr(cmd, 600)
    fs = [os.path.join(d, x) for x in os.listdir(d) if x.lower().endswith(('.tif', '.tiff'))]
    if not fs:
        raise RuntimeError('导出没文件 %s | %s' % (tag, r[:100]))
    return max(fs, key=os.path.getmtime)


def apply_actions(tif, actions):
    """发命令。**所有曲线必须一次写**（逐条写会互相擦），标量走 GLOBAL。"""
    logs = []
    curves = []
    scalars = {}
    for a in actions:
        if a['kind'] == 'curve':
            curves.append('%s:%s' % (a['key'], a['spec']))
        elif a['kind'] == 'scalar':
            scalars[a['key']] = a['value']
    if curves:
        r = lr('CURVES %s %s' % (tif, ';'.join(curves)), 400)
        logs.append(('curves', 'ok' if r.startswith('OK') else r[:100]))
    if scalars:
        spec = ','.join('%s=%.4f' % (k, v) for k, v in scalars.items())
        r = lr('GLOBAL %s %s' % (tif, spec), 300)
        logs.append(('scalars', 'ok' if r.startswith('OK') else r[:100]))
    return logs


def reset(tif):
    lr('SELECT ' + tif, 240)
    lr('RESET ' + tif, 240)
    lr('CLEARMASKS ' + tif, 120)


IDENTITY = ','.join('%d,%d' % (i, i) for i in range(0, 256, 16)) + ',255,255'


def clear_curves(tif):
    """RESET 不保证清掉点曲线：显式写四条 identity（主 + R/G/B）。"""
    acts = [dict(name='id', kind='curve', key=k, spec=IDENTITY)
            for k in ('ToneCurvePV2012', 'ToneCurvePV2012Red',
                      'ToneCurvePV2012Green', 'ToneCurvePV2012Blue')]
    return apply_actions(tif, acts)


def add(tif):
    r = lr('ADD ' + tif, 300)
    if 'ADDED' in r:
        return True
    return 'SELECTED' in lr('SELECT ' + tif, 240)
