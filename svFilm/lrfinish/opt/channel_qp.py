# -*- coding: utf-8 -*-
r"""第 3 层（通道曲线）的 QP 求解器：一次解 15 个变量。

变量：d[band][channel]，5 段 × R/G/B。
响应：layer2_curve.GAIN（每 1 码对 L/a/b 的效应）。
目标：带内 clamp 后的 (a,b) 残差。
正则/约束：
  · dL=0（通道曲线不改亮度）
  · R≈G（阴影段，教程"红复制到绿"）
  · 相邻段平滑（防止一段跳变）
  · |d|≤max_shift

用法（离线，不写 LR）：
  python opt/channel_qp.py --tif <底片.tif> --profile kurai
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
paths.ensure_import_path()
import profile_io                                   # noqa: E402
import qp                                           # noqa: E402
import band_read as BR                              # noqa: E402
import layer2_curve as LC                           # noqa: E402

BANDS = BR.NAMES


def clamp_target(v, lo, hi, margin=0.10):
    w = max(1e-6, hi - lo)
    a, b = lo + margin * w, hi - margin * w
    if a > b:
        a = b = 0.5 * (lo + hi)
    return float(np.clip(v, a, b))


def solve(cur, mas, max_shift=12.0, ridge=0.02, rg=0.8, smooth=0.05, dl_w=30.0):
    """cur/mas: band_read 的当前/靶带 dict。返回 (d(5,3), targets)。"""
    A, b = np.zeros((0, 15)), np.zeros((0,))
    targets = {}
    for i, nm in enumerate(BANDS):
        m = cur.get(nm)
        t = mas.get(nm)
        if not m or not t or 'na' not in m:
            continue
        ta = clamp_target(m['na'], t['a_p25'], t['a_p75'])
        tb = clamp_target(m['nb'], t['b_p25'], t['b_p75'])
        targets[nm] = (ta, tb)
        g = LC.gain_at(m.get('L'))
        # 行：a 与 b 的残差
        row_a = np.zeros(15)
        row_b = np.zeros(15)
        for j, c in enumerate('RGB'):
            row_a[i * 3 + j] = g[c][1]      # da/d(code)
            row_b[i * 3 + j] = g[c][2]      # db/d(code)
        A = np.vstack([A, row_a, row_b])
        b = np.concatenate([b, [ta - m['na'], tb - m['nb']]])
        # dL≈0
        row_l = np.zeros(15)
        for j, c in enumerate('RGB'):
            row_l[i * 3 + j] = g[c][0]
        A = np.vstack([A, dl_w * row_l])
        b = np.concatenate([b, [0.0]])

    # R≈G：只压阴影两段
    for i, nm in enumerate(BANDS[:2]):
        if nm not in targets:
            continue
        row = np.zeros(15)
        row[i * 3 + 0] = 1.0
        row[i * 3 + 1] = -1.0
        A = np.vstack([A, np.sqrt(rg) * row])
        b = np.concatenate([b, [0.0]])

    # 相邻段平滑
    for i in range(4):
        if BANDS[i] not in targets or BANDS[i + 1] not in targets:
            continue
        for j in range(3):
            row = np.zeros(15)
            row[i * 3 + j] = 1.0
            row[(i + 1) * 3 + j] = -1.0
            A = np.vstack([A, np.sqrt(smooth) * row])
            b = np.concatenate([b, [0.0]])

    d = qp.solve_box(A, b, -max_shift, max_shift, ridge=ridge)
    return d.reshape(5, 3), targets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tif', required=True)
    ap.add_argument('--profile', default='kurai')
    ap.add_argument('--max-shift', type=float, default=12.0)
    a = ap.parse_args()
    prof = profile_io.load(a.profile)
    BR.MASTER = str(profile_io.bands_file(prof))
    cur = {r['band']: r for r in BR.read(a.tif, mask_from=a.tif)['bands']}
    mas = BR.master()
    d, tg = solve(cur, mas, max_shift=a.max_shift)
    print('%-8s %7s %7s %7s | %7s %7s %7s | %5s %5s %5s'
          % ('段', 'a前', 'a后(预测)', '靶a', 'b前', 'b后(预测)', '靶b', 'dR', 'dG', 'dB'))
    for i, nm in enumerate(BANDS):
        m = cur.get(nm) or {}
        t = tg.get(nm)
        if not t or 'na' not in m:
            continue
        g = LC.gain_at(m.get('L'))
        da = sum(g[c][1] * d[i, j] for j, c in enumerate('RGB'))
        db = sum(g[c][2] * d[i, j] for j, c in enumerate('RGB'))
        print('%-8s %7.2f %7.2f %7.2f | %7.2f %7.2f %7.2f | %5.1f %5.1f %5.1f'
              % (nm, m['na'], m['na'] + da, t[0], m['nb'], m['nb'] + db, t[1],
                 d[i, 0], d[i, 1], d[i, 2]))


if __name__ == '__main__':
    main()
