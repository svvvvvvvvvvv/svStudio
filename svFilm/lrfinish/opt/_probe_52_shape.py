# -*- coding: utf-8 -*-
r"""摸规律③：**只取鹿井的 52 张精选成片**（不是 747 张全集），重算形状分布。

为什么（SV 14:00：「鹿井的成片收敛到那 ds 目录里的 50 多张」）：
  · `lj_lab.npz` 的 747 张是**全集**，混着大量同场景连拍/试片，它们会把界撑宽；
  · 他真正认可的是 `E:\ds\大师成片\鹿井\{128,130,_旧_001}` 里那 **52 张**；
  · 目标应该是"落进这 52 张的分布"，不是"落进 747 张的分布"。

数据来源：`_测量.json` 的 `per_image`（52 条，键名与 refpack 的 labkeys 一致）
          + `refpack\lj_lab.npz`（384 域 uint8 Lab，与全集同口径）。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = r'E:\lr_agent\opt'
sys.path.insert(0, HERE)
from _probe_masters_shape import NAMES, traj, shape, pct    # noqa: E402
import lr_measure as M                                      # noqa: E402

REF = r'E:\ds\photo\refpack'
MASTER = r'E:\ds\大师成片\鹿井'
OUT = r'E:\Debug_svStudio\lr_agent_out\2026-10-23\shape'
DOMAIN = 384            # npz 是 384 长边的 uint8 Lab，磁盘路径必须同域才能混用


def load_lab384(path):
    """从磁盘读 → 384 长边 → Lab → **编码成与 npz 同一种 uint8**。
    编码：L = u0/255*100 · a = u1−128 · b = u2−128（1 码 = 1 个 a*/b* 单位）。"""
    rgb = M.load_rgb(path, long_edge=DOMAIN)
    lab = M.color.to_lab(np.ascontiguousarray(rgb))
    u = np.empty(lab.shape, np.uint8)
    u[..., 0] = np.clip(np.rint(lab[..., 0] / 100.0 * 255.0), 0, 255)
    u[..., 1] = np.clip(np.rint(lab[..., 1] + 128.0), 0, 255)
    u[..., 2] = np.clip(np.rint(lab[..., 2] + 128.0), 0, 255)
    return u


def load_selected():
    """52 张一律**从磁盘读**（`_旧_001部分_15张` 那 26 张不在 npz 里）。
    同时把在 npz 里的那部分也解一遍，用来自证两条路量的是同一件事。"""
    keys_all = json.load(open(os.path.join(REF, 'lj_labkeys.json'), encoding='utf-8'))
    pos = {k: i for i, k in enumerate(keys_all)}
    meas = json.load(open(os.path.join(MASTER, '_测量.json'), encoding='utf-8'))['per_image']
    z = np.load(os.path.join(REF, 'lj_lab.npz'))
    ks = sorted(z.keys())
    rows, miss, chk = [], [], []
    for k in meas:
        p = os.path.join(MASTER, k.replace('/', os.sep))
        if not os.path.exists(p):
            miss.append(k); continue
        na, nb, share, npx = traj(load_lab384(p).astype(np.float64))
        r = dict(file=k, group=k.split('/')[0], na=na, nb=nb, share=share)
        r.update(shape(na, nb))
        rows.append(r)
        i = pos.get(k)
        if i is not None:                      # 自证：两条路同一张图
            na2, nb2, _, _ = traj(z[ks[i]].astype(np.float64))
            da = [abs(x - y) for x, y in zip(na, na2) if np.isfinite(x) and np.isfinite(y)]
            db = [abs(x - y) for x, y in zip(nb, nb2) if np.isfinite(x) and np.isfinite(y)]
            chk.append((k, max(da) if da else np.nan, max(db) if db else np.nan))
    return rows, miss, chk


def main():
    rows, miss, chk = load_selected()
    print('52 张从磁盘读到 %d 张，缺 %d 张%s'
          % (len(rows), len(miss), ('（%s…）' % miss[0]) if miss else ''))
    if chk:
        da = np.nanmax([c[1] for c in chk]); db = np.nanmax([c[2] for c in chk])
        print('自证：其中 %d 张 npz 里也有，两条路量同一张图 —— a* 最大差 %.2f，b* 最大差 %.2f'
              % (len(chk), da, db))
        if max(da, db) > 1.0:
            print('  ⚠ 差 >1 个 a*/b* 单位，说明两条路不是同一口径，下面数字只能各自内部比')
    good = [r for r in rows if r.get('n_ok', 0) >= 4]
    print('五段都够像素的 %d 张\n' % len(good))

    full = json.load(open(os.path.join(OUT, 'masters_shape.json'), encoding='utf-8'))['rows']
    fullg = [r for r in full if r.get('n_ok', 0) >= 4]

    print('%-26s %-24s %-24s' % ('关系量', '52 张精选 P10/P50/P90', '747 张全集 P10/P50/P90'))
    for key in ('a_rise', 'rough_a', 'rough_b', 'b_peak_h'):
        print('%-26s %-24s %-24s'
              % (key, pct([r[key] for r in good]), pct([r[key] for r in fullg])))
    print('%-26s %-24s %-24s'
          % ('a_up 中位（满分4）',
             '%.1f ｜ 4/4齐 %.0f%%' % (np.median([r['a_up'] for r in good]),
                                      100 * np.mean([r['a_up'] == 4 for r in good])),
             '%.1f ｜ 4/4齐 %.0f%%' % (np.median([r['a_up'] for r in fullg]),
                                      100 * np.mean([r['a_up'] == 4 for r in fullg]))))
    print('%-26s %-24s %-24s'
          % ('b 单峰占比',
             '%.0f%%' % (100 * np.mean([r['b_mid_high'] for r in good])),
             '%.0f%%' % (100 * np.mean([r['b_mid_high'] for r in fullg]))))
    print()

    # ★ 规律要跨场景成立：三组分别看，而不是只看 52 张合起来的平均
    print('== 分组（三个不同场景）—— 规律必须各组成立，否则只是平均出来的假象')
    print('%-22s %3s %-18s %-18s %-18s' % ('组', 'n', 'a_rise P10/50/90', 'rough_a P10/50/90', 'rough_b P10/50/90'))
    by = {}
    for r in good:
        by.setdefault(r['group'], []).append(r)
    for g, v in sorted(by.items()):
        print('%-22s %3d %-18s %-18s %-18s'
              % (g[:22], len(v),
                 pct([r['a_rise'] for r in v]),
                 pct([r['rough_a'] for r in v]),
                 pct([r['rough_b'] for r in v])))
    print()

    print('== 52 张的五段（384 域）')
    print('%-8s %-24s %-24s' % ('段', 'a* P25/P50/P75', 'b* P25/P50/P75'))
    bands = {}
    for j, nm in enumerate(NAMES):
        A = np.array([r['na'][j] for r in good], float)
        Bv = np.array([r['nb'][j] for r in good], float)
        A, Bv = A[np.isfinite(A)], Bv[np.isfinite(Bv)]
        bands[nm] = dict(a=(np.percentile(A, 25), np.median(A), np.percentile(A, 75)),
                         b=(np.percentile(Bv, 25), np.median(Bv), np.percentile(Bv, 75)))
        print('%-8s %s   %s' % (nm,
                                '%6.2f %6.2f %6.2f' % bands[nm]['a'],
                                '%6.2f %6.2f %6.2f' % bands[nm]['b']))
    print()

    # 我们的底片落哪
    ours_p = os.path.join(OUT, 'ours_shape.json')
    if os.path.exists(ours_p):
        ours = json.load(open(ours_p, encoding='utf-8'))['ours']
        print('== 我们（384 域）落没落进这 52 张的分布')
        print('%-16s %8s %8s %8s %6s | 判' % ('case', 'a_rise', 'rough_a', 'rough_b', 'a_up'))
        lim = {k: (np.percentile([r[k] for r in good], 10), np.percentile([r[k] for r in good], 90))
               for k in ('a_rise', 'rough_a', 'rough_b')}
        for r in ours:
            flags = []
            for k in ('a_rise', 'rough_a', 'rough_b'):
                v = r.get(k)
                if v is None or not np.isfinite(v):
                    continue
                if v < lim[k][0] or v > lim[k][1]:
                    flags.append('%s 出界' % k)
            if r.get('a_up', 0) < 2:
                flags.append('a_up<2')
            print('%-16s %8.2f %8.2f %8.2f %6s | %s'
                  % (r['case'], r.get('a_rise', np.nan), r.get('rough_a', np.nan),
                     r.get('rough_b', np.nan), r.get('a_up', ''),
                     '、'.join(flags) if flags else '在范围内'))
        print('  界：' + ' · '.join('%s [%.1f, %.1f]' % (k, lim[k][0], lim[k][1]) for k in lim))

    json.dump(dict(n=len(good), rows=good, bands=bands), open(
        os.path.join(OUT, 'masters52_shape.json'), 'w', encoding='utf-8'),
        ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % os.path.join(OUT, 'masters52_shape.json'))


if __name__ == '__main__':
    main()
