# -*- coding: utf-8 -*-
"""成片域颗粒度量 + 引擎链 pilot（只读，不写卡/不写测量库）。

为什么单独一个文件
------------------
设计里颗粒归引擎（`grain.rms_granularity`，ISO 6328），但"粗细"能不能从
1080 成片里量出来、量出来能不能分卡，必须先证明。这个脚本只做两件事：

  1. `metrics(path)`：在 1080 交付域量 4 个量
        amp       高通残差 σ（L*）——颗粒强度
        acf1      高通残差 lag-1 自相关——越大越"成团"（粗）
        coarse_px 0.25 / 频谱质心（cycles/px）——越大越粗；白噪声≈1
        ratio     σ(σ=1 高通) / σ(σ=3 高通)
        edge      边缘梯度中位 / 边缘对比中位
  2. `pilot`：同一介质渲 rms 网格 + 量几张大师，打印动态范围；
     用来决定"逐卡颗粒目标"是否成立，不写任何生产数据。

跑：
  & $py -X utf8 analysis/corpus/kurai/texture_probe.py pilot
"""
from __future__ import annotations

import argparse
import os
import statistics as st
import sys

import cv2
import numpy as np
from PIL import Image

_CORPUS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_CORPUS)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

LONG = 1080
RUN_MIN = 64          # 一行里参与 ACF 的最短平区
MAX_LAG = 8


# ---------------------------------------------------------------- 基础

def _lab_L(path: str) -> np.ndarray:
    im = Image.open(path).convert('RGB')
    w, h = im.size
    k = LONG / float(max(w, h))
    if k < 1.0:
        im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    a = np.asarray(im).astype(np.float32) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    Y = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    return np.where(Y > 0.008856, 116.0 * np.cbrt(Y) - 16.0, 903.3 * Y)


def _runs(mask_row: np.ndarray, min_len: int):
    """一行里的连续 True 段 [start, end)。"""
    out = []
    x = 0
    n = len(mask_row)
    while x < n:
        if not mask_row[x]:
            x += 1
            continue
        y = x
        while y < n and mask_row[y]:
            y += 1
        if y - x >= min_len:
            out.append((x, y))
        x = y
    return out


def metrics(path: str) -> dict:
    """1080 交付域上的颗粒/锐度度量（只做相对比较）。"""
    L = _lab_L(path)
    gx = cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3)
    g = cv2.magnitude(gx, gy)
    flat = (L > 30.0) & (L < 85.0) & (g < np.percentile(g, 60))
    if int(flat.sum()) < 20000:
        return dict(err='flat_too_small', n_flat=int(flat.sum()))
    r1 = L - cv2.GaussianBlur(L, (0, 0), 1.0)
    r3 = L - cv2.GaussianBlur(L, (0, 0), 3.0)
    amp = float(np.std(r1[flat]))
    ratio = amp / max(float(np.std(r3[flat])), 1e-6)

    ac1, ac2, cents = [], [], []
    for row in range(L.shape[0]):
        runs = _runs(flat[row], RUN_MIN)
        if not runs:
            continue
        seg_row = r1[row]
        for x0, x1 in runs:
            seg = seg_row[x0:x1].astype(np.float64)
            seg = seg - seg.mean()
            den = float((seg * seg).sum())
            if den <= 1e-9:
                continue
            ac1.append(float((seg[:-1] * seg[1:]).sum()) / den)
            if len(seg) > 2:
                ac2.append(float((seg[:-2] * seg[2:]).sum()) / den)
            # 频谱质心（cycles/px）
            win = np.hanning(len(seg))
            f = np.fft.rfft(seg * win)
            P = (f.real ** 2 + f.imag ** 2)
            s = float(P.sum())
            if s > 1e-12:
                freqs = np.fft.rfftfreq(len(seg))
                cents.append(float((freqs * P).sum() / s))

    if not ac1 or not cents:
        return dict(err='no_runs', n_flat=int(flat.sum()))
    acf1 = float(st.median(ac1))
    acf2 = float(st.median(ac2)) if ac2 else None
    cen = float(st.median(cents))
    em = g > np.percentile(g, 99.5)
    edge = (float(np.median(g[em]) / max(float(np.std(L[em])), 1e-6))
            if int(em.sum()) >= 500 else None)
    return dict(amp=round(amp, 3), acf1=round(acf1, 4),
                acf2=(round(acf2, 4) if acf2 is not None else None),
                centroid=round(cen, 4),
                coarse_px=round(0.25 / max(cen, 1e-6), 3),
                ratio=round(ratio, 3),
                edge=(round(edge, 4) if edge is not None else None),
                n_flat=int(flat.sum()))


# ---------------------------------------------------------------- pilot

def _pick_master(card_name: str) -> str:
    from design.matchcard import card_samples
    for p in card_samples(card_name):
        if os.path.isfile(p):
            return p
    return ''


def _render_rms(raw: str, card_name: str, rms: float, out_dir: str) -> str:
    """同一介质、同一 WB/PE，只改 grain.rms_granularity，渲一张 1080。"""
    import copy
    from design import flow
    from design.card_render import medium_of
    from design.matchcard import cards
    from design.texture import plan_texture, to_overrides

    mc = next(c for c in cards() if c.name == card_name)
    m = copy.deepcopy(medium_of(mc))
    g = dict(m.get('grain') or {})
    g['rms_granularity'] = [float(rms)] * 3
    m['grain'] = g
    wb = dict(m.get('wb') or {})
    cw = (mc.wb or {})
    if cw.get('temperature'):
        wb['white_balance'] = 'custom'
        wb['temperature'] = float(cw['temperature'])
    ov = to_overrides(plan_texture(mc.stock, mc.paper, long_side=LONG), long_side=LONG)
    os.makedirs(out_dir, exist_ok=True)
    _stem, dst, _hr, _msgs = flow._render_one(
        ('rms%03d' % round(rms), raw, m, out_dir, ov, 0.98, LONG, wb))
    return dst


def pilot(args) -> int:
    cards = args.cards.split(',') if args.cards else [
        '001_鹿井小渔_n66f7fa31',
        '002_鹿井小渔_n67ea66c3',
        '108_鹿井小渔_n6a657e88',
    ]
    rms_list = [float(v) for v in (args.rms or '8,12,21,30,45').split(',')]
    out = args.out or os.path.join(r'%TEMP%', 'svfilm_grain_pilot')
    out = os.path.expandvars(out)

    print('== 大师（3 张不同卡）')
    master = {}
    for name in cards:
        p = _pick_master(name)
        if not p:
            print('  ! %s 没有样片' % name)
            continue
        m = metrics(p)
        master[name] = m
        print('  %-30s %s ｜ %s' % (name, os.path.basename(p),
                                   ' '.join('%s=%s' % (k, m.get(k))
                                            for k in ('amp', 'acf1', 'coarse_px', 'ratio'))))
    print('\n== 引擎链（同一介质/PE/WB，只改 rms）')
    eng = {}
    for r in rms_list:
        dst = _render_rms(args.raw, args.card or cards[-1], r, os.path.join(out, 'rms%03d' % r))
        m = metrics(dst)
        eng[r] = m
        print('  rms=%-5.1f %s' % (r, ' '.join('%s=%s' % (k, m.get(k))
                                              for k in ('amp', 'acf1', 'coarse_px', 'ratio'))))

    print('\n== 动态范围（引擎链）')
    for key in ('amp', 'acf1', 'coarse_px', 'ratio'):
        vals = [eng[r].get(key) for r in rms_list if eng[r].get(key) is not None]
        if len(vals) < 2:
            continue
        med = st.median(vals)
        rng = (max(vals) - min(vals)) / med if med else 0.0
        print('  %-9s min %.3f ｜ max %.3f ｜ 动态范围 %.1f%%'
              % (key, min(vals), max(vals), rng * 100.0))
    print('\n大师之间（3 张卡）：')
    for key in ('amp', 'acf1', 'coarse_px'):
        vals = [master[k].get(key) for k in master if master[k].get(key) is not None]
        if vals:
            print('  %-9s min %.3f ｜ max %.3f ｜ 极差/中位 %.1f%%'
                  % (key, min(vals), max(vals),
                     (max(vals) - min(vals)) / st.median(vals) * 100.0))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['pilot', 'metrics'])
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--raw', default=r'D:\照片库\效果测试1027_2\DSCF2500.RAF')
    ap.add_argument('--card', default='108_鹿井小渔_n6a657e88')
    ap.add_argument('--cards', default='')
    ap.add_argument('--rms', default='8,12,21,30,45')
    ap.add_argument('--out', default='')
    a = ap.parse_args(argv)
    if a.cmd == 'metrics':
        for p in a.paths:
            print(p, metrics(p))
        return 0
    return pilot(a)


if __name__ == '__main__':
    sys.exit(main())
