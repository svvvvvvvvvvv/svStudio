# -*- coding: utf-8 -*-
r"""摸规律①：鹿井 747 张成片，每张的五段中性轨迹**形状**（不是中位对靶）。

为什么这么做（SV 13:4x 的话：「不要单独修某个…你要摸索出那个规律，
而不是为了去对齐数值而打靶」）：
  · 之前我拿"每段离大师 P50 多远"当判据 ⇒ 那是打靶：换一张图、换一种光线就不成立。
  · 规律应该在**轨迹的关系**里（哪段比哪段绿/暖、峰在第几段、轨迹平不平），
    这跟场景亮度、内容都无关 —— 也就是 ds 那套"判据形态 = 界/序/关系/形状比"。

口径：`refpack\lj_lab.npz`（747 张，384 长边的 uint8 Lab）
  L = u0/255*100 · a = u1−128 · b = u2−128
  ⚠ 384 域比 900 域重采样更狠 ⇒ **a\*/b\* 绝对值会偏低**；但本脚本只比**关系**，不受影响。
  中性 = C\* ≤ 8；每段像素 < MIN_PX 则记 nan。

产物：shape/masters_shape.json + 屏显分布
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import numpy as np

import _boot  # noqa: E402
import paths
paths.ensure_import_path()

REF = str(paths.MASTER.parent / 'photo' / 'refpack')
OUT = str(paths.WORK / 'shape')
BANDS = ((0, 20), (20, 40), (40, 60), (60, 80), (80, 100))
NAMES = ('0-20', '20-40', '40-60', '60-80', '80-100')
C_NEUTRAL = 8.0
MIN_PX = 100


def traj(u):
    L = u[..., 0] / 255.0 * 100.0
    a = u[..., 1] - 128.0
    b = u[..., 2] - 128.0
    C = np.hypot(a, b)
    neu = C <= C_NEUTRAL
    na, nb, share, npx = [], [], [], []
    for lo, hi in BANDS:
        m = (L >= lo) & (L < hi)
        mn = m & neu
        share.append(100.0 * m.sum() / L.size)
        npx.append(int(mn.sum()))
        if mn.sum() >= MIN_PX:
            na.append(float(np.median(a[mn])))
            nb.append(float(np.median(b[mn])))
        else:
            na.append(np.nan)
            nb.append(np.nan)
    return na, nb, share, npx


def shape(na, nb):
    """轨迹的**关系**指标（全部与绝对值无关）。"""
    A, Bv = np.array(na, float), np.array(nb, float)
    ok = np.isfinite(A) & np.isfinite(Bv)
    r = dict(n_ok=int(ok.sum()))
    if ok.sum() < 4:
        return r
    A2, B2 = A[ok], Bv[ok]
    idx = np.where(ok)[0]
    # ① a 沿亮度往上的总爬升（大师应为正：暗更绿、高更中性）
    r['a_rise'] = float(A2[-1] - A2[0])
    # ② a 单调上升的相邻对数（0..n-1）
    r['a_up'] = int(sum(1 for i in range(len(A2) - 1) if A2[i + 1] > A2[i]))
    r['a_n'] = len(A2) - 1
    # ③ b 的峰在第几段（相对位置 0..1）与峰高（相对两端最低点）
    j = int(np.argmax(B2))
    r['b_peak_rel'] = float(idx[j] / 4.0)
    r['b_peak_h'] = float(B2[j] - min(B2[0], B2[-1]))
    # ④ b 中相对两端都更高吗（单峰形）
    r['b_mid_high'] = bool(len(B2) >= 3 and B2[1:-1].max() > max(B2[0], B2[-1]))
    # ⑤ 轨迹平滑度 = 相邻段跳变的最大值（大师应小；锯齿就是这里大）
    r['rough_a'] = float(np.max(np.abs(np.diff(A2))))
    r['rough_b'] = float(np.max(np.abs(np.diff(B2))))
    return r


def pct(v, q=(10, 50, 90)):
    v = np.array([x for x in v if np.isfinite(x)], float)
    if not len(v):
        return 'nan'
    return ' / '.join('%5.2f' % np.percentile(v, x) for x in q)


def main():
    os.makedirs(OUT, exist_ok=True)
    keys = json.load(open(os.path.join(REF, 'lj_labkeys.json'), encoding='utf-8'))
    z = np.load(os.path.join(REF, 'lj_lab.npz'))
    ks = sorted(z.keys())
    assert len(ks) == len(keys)

    rows = []
    for i, nm in enumerate(keys):
        u = z[ks[i]].astype(np.float64)
        na, nb, share, npx = traj(u)
        r = dict(file=nm, group=nm.split('/')[0], na=na, nb=nb, share=share, npx=npx)
        r.update(shape(na, nb))
        rows.append(r)

    good = [r for r in rows if r.get('n_ok', 0) >= 4]
    print('总 %d 张，五段都够像素的 %d 张' % (len(rows), len(good)))
    print()
    print('== 大师轨迹的**关系**（384 域口径，只看形状）')
    print('  a* 沿亮度爬升（高−暗）      P10/中位/P90：', pct([r['a_rise'] for r in good]))
    print('  a* 相邻段上升的对数（满分 4）中位： %.1f ｜ 4/4 齐的占 %.0f%%'
          % (np.median([r['a_up'] for r in good]),
             100 * np.mean([r['a_up'] == 4 for r in good])))
    print('  b* 峰落在第几段（0=最暗,1=最亮）中位： %.2f ｜ 落在中间三段（0.25~0.75）的占 %.0f%%'
          % (np.median([r['b_peak_rel'] for r in good]),
             100 * np.mean([0.24 <= r['b_peak_rel'] <= 0.76 for r in good])))
    print('  b* 中间比两端都高（单峰形）的占         ： %.0f%%'
          % (100 * np.mean([r['b_mid_high'] for r in good])))
    print('  轨迹平滑度 max|相邻段跳变| a*         ：', pct([r['rough_a'] for r in good]))
    print('                          b*         ：', pct([r['rough_b'] for r in good]))
    print()

    # 组内 vs 组间：同一场景（同目录）的形状是不是更一致？
    by = defaultdict(list)
    for r in good:
        by[r['group']].append(r)
    multi = {g: v for g, v in by.items() if len(v) >= 3}
    within = []
    for g, v in multi.items():
        within.append(np.std([r['a_rise'] for r in v]))
        within.append(np.std([r['rough_b'] for r in v]))
    across_rise = np.std([r['a_rise'] for r in good])
    across_rough = np.std([r['rough_b'] for r in good])
    print('== 一致性：同一组（同场景同卷）内 vs 全部')
    print('  a_rise  组内 std 中位 %.2f ｜ 全体 std %.2f' % (np.median(within[0::2]), across_rise))
    print('  rough_b 组内 std 中位 %.2f ｜ 全体 std %.2f' % (np.median(within[1::2]), across_rough))
    print()

    print('== 五段的 a* / b* 中位（384 域，仅作参照；绝对值偏低）')
    print('%-8s %-22s %-22s' % ('段', 'a* P25/P50/P75', 'b* P25/P50/P75'))
    for j, nm2 in enumerate(NAMES):
        A = np.array([r['na'][j] for r in good], float)
        Bv = np.array([r['nb'][j] for r in good], float)
        A, Bv = A[np.isfinite(A)], Bv[np.isfinite(Bv)]
        print('%-8s %s   %s' % (nm2,
                                '%6.2f %6.2f %6.2f' % (np.percentile(A, 25), np.median(A), np.percentile(A, 75)),
                                '%6.2f %6.2f %6.2f' % (np.percentile(Bv, 25), np.median(Bv), np.percentile(Bv, 75))))

    json.dump(dict(bands=NAMES, rows=rows), open(os.path.join(OUT, 'masters_shape.json'), 'w',
              encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % os.path.join(OUT, 'masters_shape.json'))


if __name__ == '__main__':
    main()
