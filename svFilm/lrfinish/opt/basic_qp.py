# -*- coding: utf-8 -*-
r"""Basic 六旋钮的 6 维有界最小二乘（用 cal_basic.json 的响应矩阵）。

x = (Exposure, Contrast, Highlights, Shadows, Whites, Blacks)
B = d(metric)/d(knob)（4×6：脸 p50 / 光比 / 贴白 / 压黑）
目标 = 脸落带、光比 P50、两端不超限；ridge 压动作。

用法（离线）：
  python opt/basic_qp.py --tif <底片.tif> --profile kurai
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
paths.ensure_import_path()
import profile_io                                   # noqa: E402
import qp                                           # noqa: E402

KEYS = ('Exposure2012', 'Contrast2012', 'Highlights2012',
        'Shadows2012', 'Whites2012', 'Blacks2012')
METRICS = ('p50', 'span', 'white', 'black')


def load_gain(path=None):
    path = path or (paths.ROOT / 'calibration' / 'basic_gain.json')
    d = json.load(open(path, encoding='utf-8'))
    B = np.zeros((len(METRICS), len(KEYS)))
    for j, k in enumerate(KEYS):
        st = d['steps'].get(k)
        if not st:
            continue
        for i, m in enumerate(METRICS):
            B[i, j] = st['d'].get(m, 0.0) / st['delta']
    return B, d.get('base') or {}


def solve(base, B, prof, ridge=0.01):
    t = profile_io.tone(prof)
    lo, hi = float(t['face_lo']), float(t['face_hi'])
    span_t = float(t['span_target'])
    wht, blk = float(t['white_p90']), float(t['black_p90'])
    p50 = float(base.get('p50', 60))
    target = np.array([
        min(max(p50, lo + 1.0), hi - 1.0),
        max(float(base.get('span', 0)), span_t),
        min(float(base.get('white', 0)), wht),
        min(float(base.get('black', 0)), blk),
    ])
    w = np.array([1.0, 1.0, 5.0, 5.0])
    A = B * w[:, None]
    b = (target - np.array([base.get(m, 0) for m in METRICS])) * w
    # 硬护栏行：贴白/压黑不许比底片更差（权重 20）
    for mi, lim in ((2, wht), (3, blk)):
        row = B[mi]
        A = np.vstack([A, 20.0 * row])
        b = np.concatenate([b, [20.0 * (lim - float(base.get(METRICS[mi], 0)))]])
    lb = np.array([-2.0] + [-100.0] * 5)
    ub = np.array([2.0] + [100.0] * 5)
    x = qp.solve_box(A, b, lb, ub, ridge=ridge)
    pred = np.array([base.get(m, 0) for m in METRICS]) + B @ x
    return x, target, pred


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tif', required=True)
    ap.add_argument('--profile', default='kurai')
    ap.add_argument('--cal', default='')
    a = ap.parse_args()
    B, base = load_gain(a.cal or None)
    if not base:
        # 没有标定基准：用传进来的图现量一次（调用层负责，这里只提示）
        raise SystemExit('basic_gain.json 缺 base；先跑 cal_basic.py')
    prof = profile_io.load(a.profile)
    x, target, pred = solve(base, B, prof)
    print('  '.join('%s=%+.2f' % (k.replace('2012', ''), v) for k, v in zip(KEYS, x)))
    print('指标     底片     目标     预测')
    for i, m in enumerate(METRICS):
        print('%-6s %7.2f %7.2f %7.2f' % (m, base.get(m, 0), target[i], pred[i]))


if __name__ == '__main__':
    main()
