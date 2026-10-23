# -*- coding: utf-8 -*-
r"""阶段 0 · 量具②：分段读表（"看直方图"的机器实现，纯离线，不碰 LR）。

读两件事：
  形状：每段像素占比 / 端点贴墙 / 断层 / 峰值位置
  颜色：每段里"本该是灰的"像素（C<=8）三通道谁高谁低 —— 直接读 R/G/B 码值中位，
        因为曲线打点是在码值上动的，a*/b* 只是它的换算结果。

输出：屏显表 + json。CLI：python band_read.py <tif|dir> ...
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np

import _boot  # noqa: E402
import paths
paths.ensure_import_path()
import lr_measure as M        # noqa: E402

BANDS = ((0, 20), (20, 40), (40, 60), (60, 80), (80, 100))
NAMES = ('0-20', '20-40', '40-60', '60-80', '80-100')
C_NEUTRAL = 8.0
MIN_PX = 200
MASTER = str(paths.PRESETS / '鹿井预设' / 'bands.json')   # layer2 会按 --profile 覆盖


def make_masks(rgb):
    """在**底片**上算一次掩膜（按 L 分段 + C≤8）。之后所有态都用它。

    ★★★ 为什么必须锁在底片上（10-23 12:27 实测）：
      原来每态都重算 C≤8 的中性掩膜。打点会改变 a*/b*，于是一批像素**进出掩膜**，
      读到的"该段三通道中位"其实是**另一批像素**的中位。
      实测证据：只改 B 通道曲线，R/G 码值也跟着变 4~6 码，且变化量与"中性像素数变化"
      完全同向（dn +1222 ⇒ dR −4.4 / dG −4.8）。蓝通道曲线物理上改不了 R/G。
      ⇒ 那不是打动了，是读的那批像素换人了。**不锁掩膜，所有读数都不成立。**
    """
    lab = M.color.to_lab(np.ascontiguousarray(rgb))
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)
    return {nm: ((L >= lo) & (L < hi) & (C <= C_NEUTRAL))
            for nm, (lo, hi) in zip(NAMES, BANDS)}


def save_masks(path, masks):
    np.savez_compressed(path, **{nm.replace('-', '_'): m for nm, m in masks.items()})
    return path


def load_masks(path):
    z = np.load(path)
    return {nm: z[nm.replace('-', '_')] for nm in NAMES}


def read(path, long_edge=M.LONG, mask_path=None, mask_from=None):
    """mask_from = 另一张图（通常是底片）的路径 ⇒ 在它上面算掩膜，锁死。

    形状量（clip / p50 / 断层）永远用**当前图**的 L —— 那是要反映变化的；
    颜色量（R/G/B 中位、na/nb/nC）用**锁定掩膜**里的那批像素 —— 那才是同一批像素在动。
    """
    rgb = M.load_rgb(path, long_edge)
    lab = M.color.to_lab(np.ascontiguousarray(rgb))
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)

    if mask_path:
        masks = load_masks(mask_path)
    elif mask_from:
        masks = make_masks(M.load_rgb(mask_from, long_edge))
    else:
        masks = None
    if masks is not None and masks[NAMES[0]].shape != L.shape:
        raise ValueError('掩膜尺寸 %s 与当前图 %s 不一致'
                         % (masks[NAMES[0]].shape, L.shape))

    tot = float(L.size)
    rows = []
    for nm, (lo, hi) in zip(NAMES, BANDS):
        m = (L >= lo) & (L < hi)              # 形状量：走当前图的 L
        mn = masks[nm] if masks is not None else (m & (C <= C_NEUTRAL))
        n = int(m.sum())
        r = dict(band=nm, share=100.0 * n / tot, n=n, n_neutral=int(mn.sum()))
        if n >= MIN_PX:
            r['L'] = float(np.median(L[m]))
            r['a'] = float(np.median(a[m]))
            r['b'] = float(np.median(b[m]))
            r['C'] = float(np.median(C[m]))
        if mn.sum() >= MIN_PX:
            px = rgb[mn] * 255.0
            R, G, B2 = (float(np.median(px[:, i])) for i in range(3))
            r.update(R=R, G=G, B=B2, RG=R - G, RB=R - B2, GB=G - B2)
            r['na'] = float(np.median(a[mn]))
            r['nb'] = float(np.median(b[mn]))
            r['nC'] = float(np.median(C[mn]))
        rows.append(r)
    shape = dict(
        clip_lo=float(100.0 * (L <= 2).mean()),
        clip_hi=float(100.0 * (L >= 98).mean()),
        p2=float(np.percentile(L, 2)), p50=float(np.percentile(L, 50)),
        p98=float(np.percentile(L, 98)),
        peak_band=NAMES[int(np.argmax([r['share'] for r in rows]))],
    )
    # 断层：相邻两段占比的落差（>8 个百分点先记一笔，人看直方图时这就是一道谷）
    gaps = []
    for i in range(len(rows) - 1):
        d = rows[i]['share'] - rows[i + 1]['share']
        if abs(d) > 8:
            gaps.append('%s|%s %+.0f' % (NAMES[i], NAMES[i + 1], d))
    shape['gaps'] = gaps
    return dict(file=os.path.basename(path), bands=rows, shape=shape)


def master():
    k = json.load(open(MASTER, encoding='utf-8'))['keys']
    out = {}
    for nm in NAMES:
        key = 'NAb' + nm.replace('-', '_')
        ka = 'NAa' + nm.replace('-', '_')
        out[nm] = dict(a_p50=k[ka]['p50'], a_p25=k[ka]['p25'], a_p75=k[ka]['p75'],
                       b_p50=k[key]['p50'], b_p25=k[key]['p25'], b_p75=k[key]['p75'])
    return out


def show(res, mas=None):
    print('\n== %s  峰值段 %s  clip %.2f/%.2f  p2/p50/p98 %.1f/%.1f/%.1f%s'
          % (res['file'], res['shape']['peak_band'], res['shape']['clip_lo'], res['shape']['clip_hi'],
             res['shape']['p2'], res['shape']['p50'], res['shape']['p98'],
             ('  断层:' + ' '.join(res['shape']['gaps'])) if res['shape']['gaps'] else ''))
    print('%-8s %6s %6s | %6s %6s %6s | %6s %6s %6s | %6s %6s'
          % ('段', '占比%', '中性n', 'R', 'G', 'B', 'R-G', 'R-B', 'G-B', 'a*', 'b*'))
    for r in res['bands']:
        if 'R' not in r:
            print('%-8s %6.1f %6d |  (中性像素不足，跳过颜色)' % (r['band'], r['share'], r['n_neutral']))
            continue
        tag = ''
        if mas:
            m = mas[r['band']]
            da, db = r['na'] - m['a_p50'], r['nb'] - m['b_p50']
            tag = '  大师 a %+.1f b %+.1f  →  差 %+.1f / %+.1f' % (m['a_p50'], m['b_p50'], da, db)
        print('%-8s %6.1f %6d | %6.1f %6.1f %6.1f | %+6.1f %+6.1f %+6.1f | %+6.1f %+6.1f%s'
              % (r['band'], r['share'], r['n_neutral'], r['R'], r['G'], r['B'],
                 r['RG'], r['RB'], r['GB'], r['na'], r['nb'], tag))


def main():
    args = sys.argv[1:]
    files = []
    for a in args:
        if os.path.isdir(a):
            files += sorted(glob.glob(os.path.join(a, '*.tif')))
        else:
            files.append(a)
    if not files:
        raise SystemExit('用法: band_read.py <tif|dir> ...')
    mas = master()
    out = []
    for f in files:
        r = read(f)
        show(r, mas)
        out.append(r)
    dst = os.path.join(os.path.dirname(os.path.abspath(files[0])), 'band_read.json')
    json.dump(dict(master=mas, images=out), open(dst, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % dst)


if __name__ == '__main__':
    main()
