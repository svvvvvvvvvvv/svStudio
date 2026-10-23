# -*- coding: utf-8 -*-
r"""LUT oracle：用前后图对拟合 3D LUT，量 held-out ΔE。

不是交付 LUT；是验收“我们的 LR profile 离参考 look 多远”。

做法（sLUTy 的简化版）：
  ① 源/目标同帧、同尺寸；
  ② 像素对按输入 RGB 分箱到 N³ 网格，取平均；
  ③ 空箱用最近邻填（cKDTree）；
  ④ 三线性插值应用；Lab ΔE76。

用法：
  python opt/oracle_lut.py --source <dir> --target <dir> --size 17
  python opt/oracle_lut.py --source a.tif --target b.tif
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
paths.ensure_import_path()
import svFilm.color as color                        # noqa: E402
from scipy.ndimage import map_coordinates            # noqa: E402
from scipy.spatial import cKDTree                    # noqa: E402


def load(path, edge=512):
    im = cv2.imdecode(np.frombuffer(open(path, 'rb').read(), np.uint8),
                      cv2.IMREAD_UNCHANGED)
    if im is None:
        return None
    if im.dtype == np.uint16:
        im = im.astype(np.float32) / 65535.0
    else:
        im = im.astype(np.float32) / 255.0
    if im.ndim == 2:
        im = np.stack([im] * 3, -1)
    im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    h, w = im.shape[:2]
    s = edge / max(h, w)
    if s < 1:
        im = cv2.resize(im, (int(round(w * s)), int(round(h * s))),
                        interpolation=cv2.INTER_AREA)
    return np.clip(im, 0, 1)


def sample(rgb, step=4):
    return rgb[::step, ::step].reshape(-1, 3)


def fit_rbf(src, tgt, size=17):
    """在 N³ 网格上估残差 (target-source)，用局部 RBF 平滑；identity 时残差≈0。"""
    from scipy.interpolate import RBFInterpolator
    n = int(size)
    idx = np.clip(np.floor(src * (n - 1) + 1e-6).astype(np.int64), 0, n - 1)
    flat = idx[:, 0] * n * n + idx[:, 1] * n + idx[:, 2]
    res = tgt - src
    sums = np.zeros((n ** 3, 3), np.float64)
    cnt = np.zeros(n ** 3, np.int64)
    np.add.at(sums, flat, res)
    np.add.at(cnt, flat, 1)
    filled = cnt > 0
    coords = np.stack(np.unravel_index(np.where(filled)[0], (n, n, n)), 1)
    coords = coords / float(n - 1)
    vals = sums[filled] / cnt[filled, None]
    return RBFInterpolator(coords, vals, neighbors=32,
                           kernel='thin_plate_spline', smoothing=0.01)


def apply_rbf(rgb, model):
    shp = rgb.shape
    flat = rgb.reshape(-1, 3)
    out = flat + model(flat)
    return np.clip(out.reshape(shp), 0, 1)


def delta_e(a, b):
    la = color.to_lab(np.ascontiguousarray(a))
    lb = color.to_lab(np.ascontiguousarray(b))
    return float(np.mean(np.sqrt(np.sum((la - lb) ** 2, axis=-1))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--target', required=True)
    ap.add_argument('--size', type=int, default=17)
    ap.add_argument('--edge', type=int, default=512)
    ap.add_argument('--step', type=int, default=4)
    a = ap.parse_args()

    if os.path.isfile(a.source) and os.path.isfile(a.target):
        pairs = [(a.source, a.target)]
    else:
        srcs = {os.path.basename(p): p for p in glob.glob(os.path.join(a.source, '*.tif'))}
        tgts = {os.path.basename(p): p for p in glob.glob(os.path.join(a.target, '*.tif'))}
        pairs = [(srcs[k], tgts[k]) for k in sorted(set(srcs) & set(tgts))]
    if not pairs:
        raise SystemExit('没有匹配的前后图对')

    print('pairs=%d  size=%d  edge=%d' % (len(pairs), a.size, a.edge))
    errs = []
    for i, (sp, tp) in enumerate(pairs):
        s, t = load(sp, a.edge), load(tp, a.edge)
        if s is None or t is None:
            print('  skip %s' % os.path.basename(sp)); continue
        if s.shape != t.shape:
            t = cv2.resize(t, (s.shape[1], s.shape[0]), interpolation=cv2.INTER_AREA)
        if len(pairs) > 1:
            train = [(x, y) for j, (x, y) in enumerate(pairs) if j != i]
            ss = np.concatenate([sample(load(x, a.edge), a.step) for x, _ in train])
            tt = np.concatenate([sample(load(y, a.edge), a.step) for _, y in train])
        else:
            ss, tt = sample(s, a.step), sample(t, a.step)
        lut = fit_rbf(ss, tt, a.size)
        de = delta_e(apply_rbf(s, lut), t)
        errs.append(de)
        print('  %-28s ΔE %.3f' % (os.path.basename(sp), de))
    if errs:
        print('mean ΔE %.3f  (n=%d)' % (float(np.mean(errs)), len(errs)))


if __name__ == '__main__':
    main()
