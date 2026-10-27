# -*- coding: utf-8 -*-
r"""第 1 层前置诊断：**打爆的脸还能不能救**。

为什么先问这个（不是先调曲线）：
  · 2532 脸中位 88.3、2891 92.6，已经贴白端；
  · 如果那一块已经顶到 255 并且**没有纹理起伏**，那就是信息没了 ——
    Highlights / 曲线都只是把一片白压成一片灰，救不回来，只能归上游（拍摄 / 引擎）；
  · 如果还有纹理（局部有起伏、只是整体被推得很高），那才是第 1 层能做的事。

量四样（都在脸框内，只用明度，不判肤色）：
  ① `p50/p90/p99`        脸的亮度分布
  ② `clip95`             L\* > 95 的像素占比（贴白）
  ③ `top255`             三通道任一 ≥ 254 的像素占比（真顶死）
  ④ `tex`               **局部纹理**：L\* 减去 3×3 中值滤波后的标准差
                          —— 这是"还有没有细节"的直接量具；糊成一片 ⇒ 接近 0

对照：鹿井 52 张 + 我们三批。

用法：python opt/_probe_clip.py
产物：shape/clip.json + 屏显
"""
from __future__ import annotations

import glob
import json
import os
import sys

import cv2
import numpy as np

import _boot  # noqa: E402
import paths
paths.ensure_import_path()
import svFilm.color as color                      # noqa: E402
from skin_zone import load, det            # noqa: E402

BATCH = str(paths.BATCH_ROOT / '_b2048_1022')


def face_box(rgb):
    u8 = (rgb * 255).astype(np.uint8)
    bgr = cv2.cvtColor(u8, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    det.setInputSize((w, h))
    _, f = det.detect(bgr)
    if f is None or len(f) == 0:
        return None
    r = max(f, key=lambda z: z[2] * z[3])
    x, y, ww, hh = [int(v) for v in r[:4]]
    return max(0, x), max(0, y), min(w, x + ww), min(h, y + hh)


def clip_stats(p):
    r"""**真剪贴**：读原尺寸，数真的顶到 255 / 压到 0 的像素。

    ★★★ 为什么要单独一把尺（10-23 18:4x 踩过）：
      「贴白（L\*>95）」量的是"很亮"，**不是"丢了"**。两者被当成一件事，
      就会把"位置放错"判成"信息没了"，从而放弃一堆本来能救的底片。
      实测：21 张底片 贴白最高 41%，但**真顶到 255 的最多 0.171%、真压到 0 的全是 0.000%**
      ⇒ 一张都没丢信息，只是被压扁/放错了位置。
    ⚠ 必须在**原尺寸**上数：缩图会做区域平均，把已经剪贴的像素抹平，低估剪贴。
    """
    im = cv2.imdecode(np.frombuffer(open(p, 'rb').read(), np.uint8), cv2.IMREAD_UNCHANGED)
    if im is None:
        return dict(clip255=float('nan'), clip0=float('nan'))
    a = im if im.ndim == 3 else im[..., None]
    sc = 65535.0 if im.dtype == np.uint16 else 255.0
    mx = a.max(axis=2)
    return dict(clip255=float(100.0 * (mx >= sc - 1).mean()),
                clip0=float(100.0 * (mx <= 0).mean()))


def measure(p, mask_path=None):
    r"""mask_path：把中调掩膜**锁在底片上**（老坑：每态重算 C≤8 会让一批像素进出掩膜，
    读的是另一批人。铁证：只改 B 通道曲线，R/G 也掉 4~6 码，且与中性像素数变化同向）。"""
    rgb = load(p)
    if rgb is None:
        return None
    lab = color.to_lab(np.ascontiguousarray(rgb))
    L = lab[..., 0].astype(np.float32)
    # 整片中调锚（低彩度中调；曝光锚，见 group_unify.py）
    C = np.hypot(lab[..., 1], lab[..., 2])
    if mask_path and os.path.exists(mask_path):
        m0 = load(mask_path)
        if m0 is not None:
            lb = color.to_lab(np.ascontiguousarray(m0))
            cb = np.hypot(lb[..., 1], lb[..., 2])
            mb = (cb <= 8.0) & (lb[..., 0] >= 25.0) & (lb[..., 0] <= 75.0)
            if mb.shape == L.shape:
                neu = mb
            else:
                neu = (C <= 8.0) & (L >= 25.0) & (L <= 75.0)
        else:
            neu = (C <= 8.0) & (L >= 25.0) & (L <= 75.0)
    else:
        neu = (C <= 8.0) & (L >= 25.0) & (L <= 75.0)
    L_neu = float(np.median(L[neu])) if int(neu.sum()) >= 300 else float('nan')
    box = face_box(rgb)
    if box is None:
        return dict(face=False)
    x0, y0, x1, y1 = box
    core = L[y0:y1, x0:x1]
    if core.size < 200:
        return dict(face=False, why='脸框小')
    u8 = (rgb * 255).astype(np.uint8)[y0:y1, x0:x1]

    # 背景：脸框外扩 1.5 倍以外（判光型用）
    x, y, ww, hh = x0, y0, x1 - x0, y1 - y0
    h, w = L.shape[:2]
    bg = np.ones(L.shape, bool)
    cw, ch = ww * 1.5, hh * 1.5
    bx0 = max(0, int(x + ww / 2 - cw / 2)); bx1 = min(w, int(x + ww / 2 + cw / 2))
    by0 = max(0, int(y + hh / 2 - ch / 2)); by1 = min(h, int(y + hh / 2 + ch / 2))
    bg[by0:by1, bx0:bx1] = False
    L_bg = float(np.median(L[bg])) if bg.sum() > 500 else float('nan')

    # 局部纹理：L - median3x3 的标准差（糊成一片 ⇒ ≈0）
    med = cv2.medianBlur(core, 3)
    tex = float(np.std(core - med))

    # 脸颊两圈 ∩ 肤色门（与 _probe_skin_zone 同口径）：只量皮肤，不含白花/头纱/泡泡
    che = np.zeros(L.shape, np.uint8)
    R = max(4, int(ww * 0.13))
    for cx in (x0 + int(ww * 0.30), x0 + int(ww * 0.70)):
        cv2.circle(che, (cx, y0 + int(hh * 0.66)), R, 1, -1)
    yy = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    cb = -0.168736 * rgb[..., 0] - 0.331264 * rgb[..., 1] + 0.5 * rgb[..., 2] + 0.5
    cr = 0.5 * rgb[..., 0] - 0.418688 * rgb[..., 1] - 0.081312 * rgb[..., 2] + 0.5
    skin = ((che > 0) & (cb >= 0.28) & (cb <= 0.58) & (cr >= 0.45) & (cr <= 0.78)
            & (yy >= 0.35 * np.median(yy)) & (L >= 20))
    n_skin = int(skin.sum())
    if n_skin < 40:
        return dict(face=False, why='脸颊像素不足(%d)' % n_skin)
    Ls = L[skin]
    L_skin = float(np.median(Ls))
    skin_span = float(np.percentile(Ls, 90) - np.percentile(Ls, 10))
    clip95_skin = float(100.0 * (Ls > 95).mean())
    top255_skin = float(100.0 * ((rgb.max(axis=2) >= 254.0 / 255.0)[skin]).mean())

    return dict(face=True,
                p10=float(np.percentile(core, 10)),
                p50=float(np.percentile(core, 50)),
                p90=float(np.percentile(core, 90)),
                p99=float(np.percentile(core, 99)),
                span=float(np.percentile(core, 90) - np.percentile(core, 10)),
                clip95=float(100.0 * (core > 95).mean()),
                top255=float(100.0 * (u8.max(axis=2) >= 254).mean()),
                # 脸的口径 = 左右脸蛋两圈 ∩ 肤色门（不是整张脸框）
                L_skin=L_skin,
                skin_span=skin_span,
                skin_p10=float(np.percentile(Ls, 10)),
                skin_p90=float(np.percentile(Ls, 90)),
                skin_p99=float(np.percentile(Ls, 99)),
                clip95_skin=clip95_skin,
                top255_skin=top255_skin,
                n_skin=n_skin,
                tex=tex,
                L_neu=L_neu,
                n_neu=int(neu.sum()),
                L_bg=L_bg,
                d_bg=float(L_bg - np.median(core)) if np.isfinite(L_bg) else float('nan'),
                # 全图两端
                white=float(100.0 * (L > 95).mean()),
                black=float(100.0 * (L < 5).mean()))


def main():
    os.makedirs(OUT, exist_ok=True)

    print('== 我们的这批（按脸框看，哪里已经不可救）')
    print('%-10s %6s %6s %6s %7s %7s %6s %7s %7s'
          % ('张号', 'p50', 'p90', 'p99', '贴白%', '顶死%', '纹理', '全图贴白', '全图压黑'))
    mine = []
    for p in sorted(glob.glob(os.path.join(BATCH, '*_svFilm_full.tif'))):
        stem = os.path.basename(p).replace('_svFilm_full.tif', '')
        d = measure(p)
        if not d or not d.get('face'):
            print('%-10s   %s' % (stem, (d or {}).get('why', '没检到脸')))
            continue
        d['stem'] = stem
        mine.append(d)
        print('%-10s %6.1f %6.1f %6.1f %7.2f %7.2f %6.2f %7.2f %7.2f'
              % (stem, d['p50'], d['p90'], d['p99'], d['clip95'], d['top255'],
                 d['tex'], d['white'], d['black']))

    print('\n== 鹿井 52（同一把尺，看"正常"是什么样）')
    ms = json.load(open(os.path.join(OUT, 'masters52_shape.json'), encoding='utf-8'))['rows']
    ref = []
    for r in ms:
        p = os.path.join(MASTER, r['file'].replace('/', os.sep))
        if not os.path.exists(p):
            continue
        d = measure(p)
        if d and d.get('face'):
            ref.append(d)
    if ref:
        for k in ('p50', 'p90', 'p99', 'clip95', 'top255', 'tex', 'white', 'black'):
            v = np.array([d[k] for d in ref], float)
            print('  %-8s P10/50/90 = %6.2f / %6.2f / %6.2f'
                  % (k, np.percentile(v, 10), np.percentile(v, 50), np.percentile(v, 90)))

    print('\n== 判决：这两张打爆的脸还能不能救')
    for d in mine:
        if d['top255'] >= 1.0 or (d['clip95'] > 20 and d['tex'] < 3.0):
            print('  %-10s ★ 不可救：贴白 %.1f%%、顶死 %.2f%%、纹理 %.2f'
                  % (d['stem'], d['clip95'], d['top255'], d['tex']))
            print('             ⇒ Highlights 只能把一片白压成一片灰，细节回不来。归上游。')
        elif d['clip95'] > 20:
            print('  %-10s 可救：贴白 %.1f%% 但还有纹理 %.2f ⇒ 第 1 层能压回细节'
                  % (d['stem'], d['clip95'], d['tex']))
        else:
            print('  %-10s 正常：贴白 %.1f%%、纹理 %.2f' % (d['stem'], d['clip95'], d['tex']))

    json.dump(dict(mine=mine, masters_n=len(ref)),
              open(os.path.join(OUT, 'clip.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % os.path.join(OUT, 'clip.json'))


