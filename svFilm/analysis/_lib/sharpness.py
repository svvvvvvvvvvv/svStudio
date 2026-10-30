# -*- coding: utf-8 -*-
r"""**知觉锐度尺子 v2**（修掉 v1 的三处硬伤）。

## v1 错在哪（保留作教训）
```
① `S` 直接 Σ 10·log10(1+A·CSF) ⇒ **没按 bin 数归一** ⇒ 数值随分辨率/分箱膨胀，无物理意义
② 功率谱没除窗能量、也没做"环带面积"归一 ⇒ **α 不是 PSD 斜率**（实测 3.5~4.5，自然图应 1.8~2.4）
③ 拟合区间开到 64 cyc/deg，**进噪声底**（还有 JPEG 8×8 块状峰）⇒ α 被拉歪
```

## v2 的三条修正（都有依据）
```
① **SQF 式归一**：把"CSF 加权后的幅度"除以"未加权幅度" ⇒ 得到 **0~1 的"可见带内细节占比"**
     SQF_like = Σ(A·CSF) / Σ(A)        （A = √PSD，只在 4~40 cyc/deg 积分）
   ⇒ 与 Kodak SQF（MTF 按人眼加权后积分）**同构**，但归一化 ⇒ 跨图可比、不随分箱膨胀
② **真 PSD**：`P = |F|² / (Σwin² · N)`，再做**环带内求和 / 环带内 bin 数** ⇒ 一维径向密度
③ **拟合只到噪声底之前**：取 P 的高频平台（最后 15% 分箱的中位）当底噪，**只用 P > 5×底噪 的 bin**，
   且区间限制在 **8~40 cyc/deg**；若有效 bin < 5 ⇒ α 报 nan（宁可不给，不给假数）
```

## 用法
```
python _lib/sharpness.py <目录...>
from _lib.sharpness import perceptual_sharpness
```
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np                                                              # noqa: E402

DISP_W_PX = 1080
SCREEN_W_CM = 30.0
VIEW_CM = 40.0
F_LO, F_HI = 4.0, 40.0
FIT_LO, FIT_HI = 8.0, 40.0
FLOOR_K = 5.0


def csf_mannos_sakrison(f_cpd):
    f = np.asarray(f_cpd, np.float64)
    return 2.6 * (0.0192 + 0.114 * f) * np.exp(-np.power(0.114 * f, 1.1))


def _deg_per_px(width_px=DISP_W_PX, screen_cm=SCREEN_W_CM, view_cm=VIEW_CM):
    ppcm = width_px / screen_cm
    return np.degrees(np.arctan((1.0 / ppcm) / view_cm))


def radial_psd(gray):
    """真 PSD：|F|²/(Σwin²·N)，环带内求和再除以 bin 数 ⇒ (f_cyc_per_px, P)"""
    g = np.asarray(gray, np.float64)
    h, w = g.shape
    win = np.outer(np.hanning(h), np.hanning(w))
    F = np.fft.rfft2((g - g.mean()) * win)
    P2 = np.abs(F) ** 2 / (np.sum(win ** 2) * h * w)
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    fr = np.sqrt(fy ** 2 + fx ** 2)
    nb = 96
    edges = np.linspace(0, 0.5, nb + 1)
    idx = np.digitize(fr.ravel(), edges) - 1
    s = np.bincount(idx, weights=P2.ravel(), minlength=nb + 2)[:nb]
    c = np.bincount(idx, minlength=nb + 2)[:nb]
    f = 0.5 * (edges[:-1] + edges[1:])
    ok = (c > 0) & (f > 0)
    return f[ok], s[ok] / c[ok]


def perceptual_sharpness(path, width=DISP_W_PX):
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
    im = im.resize((width, max(1, int(im.height * width / im.width))), Image.LANCZOS)
    g = np.asarray(im.convert('L'), np.float64) / 255.0
    f, P = radial_psd(g)
    A = np.sqrt(np.maximum(P, 0.0))
    dpp = _deg_per_px(width)
    f_cpd = f / dpp
    band = (f_cpd >= F_LO) & (f_cpd <= F_HI) & (A > 0)
    if band.sum() < 5:
        return dict(sqf=float('nan'), alpha=float('nan'), acutance=float('nan'), nband=0, width=width)
    w_csf = csf_mannos_sakrison(f_cpd[band])
    sqf = float(np.sum(A[band] * w_csf) / np.sum(A[band]))       # ★ 归一到 0~1（CSF 均值）
    floor = float(np.median(P[int(len(P) * 0.85):])) if len(P) >= 20 else 0.0
    fit = ((f_cpd >= FIT_LO) & (f_cpd <= FIT_HI) & (P > FLOOR_K * max(floor, 1e-12)))
    alpha = float(-np.polyfit(np.log(f_cpd[fit]), np.log(P[fit]), 1)[0]) if fit.sum() >= 5 else float('nan')
    gx = np.diff(g, axis=1)
    gy = np.diff(g, axis=0)
    acut = float(np.median(np.abs(gx)) + np.median(np.abs(gy)))
    return dict(sqf=sqf, alpha=alpha, acutance=acut, nband=int(band.sum()), width=width)


if __name__ == '__main__':
    import glob
    _PHOTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    args = sys.argv[1:] or [os.path.join(_PHOTO, 'xhs_1018', 'render', 'r5')]
    for a in args:
        fs = sorted(glob.glob(os.path.join(a, '**', '*.jpg'), recursive=True)) + \
             sorted(glob.glob(os.path.join(a, '**', '*.JPG'), recursive=True))
        rows, bad = [], []
        for p in fs[:80]:
            try:
                rows.append(perceptual_sharpness(p))
            except Exception as e:                                           # noqa: BLE001
                # ★ 修（10-18）：原来这里是 `except Exception: pass` —— 80 张全读不了也一声不响，
                #   报告上只剩"无数据"。现在记条数 + 打一行原因（读数口径不变）。
                bad.append('%s: %s: %s' % (os.path.basename(p), type(e).__name__, str(e)[:80]))
        if bad:
            print('  %-36s 读不了 %d/%d 张 ｜ 例：%s'
                  % (os.path.basename(a), len(bad), len(fs), bad[0]))
        rows = [r for r in rows if np.isfinite(r['sqf'])]
        if not rows:
            print('  %-36s 无数据' % os.path.basename(a))
            continue
        med = {k: float(np.median([r[k] for r in rows])) for k in ('sqf', 'alpha', 'acutance')}
        q = lambda k: (float(np.percentile([r[k] for r in rows], 25)), float(np.percentile([r[k] for r in rows], 75)))
        print('  %-36s n=%2d ｜ SQF %.4f ｜ α %4.2f ｜ acut %.4f ｜ SQF[P25,P75]=[%.4f,%.4f] α=[%.2f,%.2f]'
              % (os.path.basename(a), len(rows), med['sqf'], med['alpha'], med['acutance'],
                 q('sqf')[0], q('sqf')[1], q('alpha')[0], q('alpha')[1]))
