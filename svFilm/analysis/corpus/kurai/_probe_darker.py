# -*- coding: utf-8 -*-
"""算「整体压暗」能换来多少**真**余量（不渲染：线性域乘 2^-ev 反算）。

为什么这条路和降白端不同：
  · 降白端 = 砍掉天花 → 余量是砍掉的那截（零和），高光没了；
  · 整体压暗 = 把分布整体下移 → 余量是真的（上面的分布还在），代价是暗部堵黑。
  ⇒ 判据：压暗到"黑端刚好顶到卡的 black_max"为止，那一截才是可用余量。
"""
import glob
import json
import os
import sys

sys.path.insert(0, r'E:\svStudio\svFilm')
import numpy as np
import tifffile

M = np.array([[0.4124564, 0.3575761, 0.1804375],
              [0.2126729, 0.7151522, 0.0721750],
              [0.0193339, 0.1191920, 0.9503041]])
WH = np.array([0.95047, 1.0, 1.08883])
D = 6.0 / 29.0


def to_lin(u):
    return np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)


def to_disp(lin):
    return np.where(lin <= 0.0031308, lin * 12.92,
                    1.055 * np.power(np.clip(lin, 0, None), 1 / 2.4) - 0.055)


def lab_L(u):
    lin = to_lin(np.clip(u, 0.0, 1.0))
    xyz = lin @ M.T / WH
    f = np.where(xyz > D ** 3, np.cbrt(xyz), xyz / (3 * D * D) + 4.0 / 29.0)
    return 116.0 * f[..., 1] - 16.0


def stats(u):
    L = lab_L(u).ravel()
    return (float((L < 5).mean() * 100), float((L > 99).mean() * 100),
            float(np.percentile(L, 95)), float(np.percentile(L, 50)))


def load(p):
    a = tifffile.imread(p)
    if a.ndim == 3 and a.shape[-1] > 3:
        a = a[..., :3]
    return a.astype(np.float64) / 65535.0


BLACK_MAX = 5.0          # 想放宽到的黑端预算（卡里现在是 3.0）
print('=' * 108)
print('整体压暗 vs 换来的真余量（黑端预算放宽到 %.1f%%；线性域反算，需真跑确认）' % BLACK_MAX)
print('=' * 108)
print('%-11s %8s %8s %8s %8s %9s %9s' % (
    'stem', '当前黑%', '可压档', '压后黑%', '压后p95', '压后clip%', '压后中位'))
rows = []
for p in sorted(glob.glob('run_919_full/engine/*_engine.tif')):
    k = os.path.basename(p).split('_')[0]
    u = load(p)
    lin = to_lin(u)
    b0, c0, p95_0, p50_0 = stats(u)
    best = 0.0
    for i in range(1, 41):                      # 0.05 档一档搜到 2.0 档
        ev = i * 0.05
        b, _, _, _ = stats(to_disp(np.clip(lin * 2.0 ** (-ev), 0, 1)))
        if b <= BLACK_MAX:
            best = ev
        else:
            break
    if best > 0:
        b1, c1, p95_1, p50_1 = stats(to_disp(np.clip(lin * 2.0 ** (-best), 0, 1)))
    else:
        b1, c1, p95_1, p50_1 = b0, c0, p95_0, p50_0
    rows.append((k, b0, best, b1, p95_1, c1, p50_1))
    print('%-11s %7.2f %8.2f %8.2f %8.2f %9.2f %9.1f' % (
        k, b0, best, b1, p95_1, c1, p50_1))

print('-' * 108)
arr = np.array([[r[2] for r in rows], [r[4] for r in rows], [r[5] for r in rows]])
print('中位：可压 %.2f 档 ｜ 压后 p95 %.2f（T0 限 93）｜ 压后 clip %.2f%%（T0 限 0.5）'
      % (np.median(arr[0]), np.median(arr[1]), np.median(arr[2])))
print('压后 p95 最坏 %.2f ｜ clip 最坏 %.2f%%'
      % (arr[1].max(), arr[2].max()))
