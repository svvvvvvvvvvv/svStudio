# -*- coding: utf-8 -*-
r"""学特征①：**他把脸放在哪个亮度**（按内容口径，不是全图统计）。

为什么要这一版（SV 16:5x 定了「同一批跑的就是一组」）：
  · 第 2 层「组内统一」要逐张把曝光归一，判据必须是"脸该落在哪个区"；
  · 行业标准是 Zone System：阳光白净皮肤落 VI 区、散射落 V 区；
  · 但**他的习惯可能是 V 区也可能是 VI 区** —— 那才是他的特征，得先量出来。
  · ⚠ 只有"按内容"量（脸 ∩ 皮肤色相）才是合法的比较；全图统计会被背景骗。

换算（用 L* 好讲人话）：
  Zone V（18% 反射）  ⇒ L* ≈ 50
  Zone VI（18%×2）    ⇒ L* ≈ 66

量三样：
  ① 脸（脸颊两圈 ∩ 肤色）的 L* 中位            —— 他/我们把脸放多亮
  ② 背景（脸框外扩 1.5 倍以外的区域）的 L* 中位 —— 判逆光
  ③ 脸内高光占比（L* > 脸中位+12 的像素比例）   —— 判有没有直射光斑

产物：shape/skin_zone.json
"""
from __future__ import annotations

import json
import os
import sys

import cv2
import numpy as np

import _boot  # noqa: E402
import paths
paths.ensure_import_path()
import svFilm.color as color                     # noqa: E402
from _probe_52_shape import MASTER, OUT          # noqa: E402
from _probe_sf_shape import collect              # noqa: E402

MODEL = str(paths.ENGINE / 'svFilm' / '_models' / 'yunet.onnx')
det = cv2.FaceDetectorYN.create(MODEL, '', (320, 320), 0.5, 0.3, 5000)
LONG = 1024
ZONE_V, ZONE_VI = 49.5, 66.5


def load(p, long=LONG):
    im = cv2.imdecode(np.frombuffer(open(p, 'rb').read(), np.uint8), cv2.IMREAD_UNCHANGED)
    if im is None:
        return None
    sc = 65535.0 if im.dtype == np.uint16 else 255.0
    if im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
    im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float64) / sc
    h, w = im.shape[:2]
    if max(h, w) > long:
        r = long / float(max(h, w))
        im = cv2.resize(im, (int(w * r), int(h * r)), interpolation=cv2.INTER_AREA)
    return np.clip(im, 0.0, 1.0)


def measure(p):
    rgb = load(p)
    if rgb is None:
        return None
    lab = color.to_lab(np.ascontiguousarray(rgb))
    L = lab[..., 0]
    u8 = (rgb * 255).astype(np.uint8)
    bgr = cv2.cvtColor(u8, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    det.setInputSize((w, h))
    _, f = det.detect(bgr)
    if f is None or len(f) == 0:
        return dict(face=False)

    r = max(f, key=lambda z: z[2] * z[3])
    x, y, ww, hh = [int(v) for v in r[:4]]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + ww), min(h, y + hh)

    # 脸颊两圈
    cheek = np.zeros(L.shape, bool)
    R = max(4, int(ww * 0.13))
    for cx in (x + int(ww * 0.30), x + int(ww * 0.70)):
        cv2.circle(cheek.view(np.uint8).reshape(L.shape), (cx, y + int(hh * 0.66)), R, 1, -1)

    # 肤色门（YCbCr）
    yy = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    cb = -0.168736 * rgb[..., 0] - 0.331264 * rgb[..., 1] + 0.5 * rgb[..., 2] + 0.5
    cr = 0.5 * rgb[..., 0] - 0.418688 * rgb[..., 1] - 0.081312 * rgb[..., 2] + 0.5
    skin = cheek & (cb >= 0.28) & (cb <= 0.58) & (cr >= 0.45) & (cr <= 0.78) \
        & (yy >= 0.35 * np.median(yy))
    # ★★★ 肤色只能用「左右脸蛋两圈 ∩ 肤色门」。量不到就报缺，**绝不退回整脸框**
    #     （SV 10-23：「用左右脸蛋口径才是肤色，不是整张脸」——整脸框混着眼睛/眉毛/嘴唇/头发）
    if skin.sum() < 40:
        return dict(face=False, why='脸蛋像素不足(%d)' % int(skin.sum()))

    Ls = float(np.median(L[skin]))
    a_s = float(np.median(lab[..., 1][skin]))
    b_s = float(np.median(lab[..., 2][skin]))
    C_s = float(np.hypot(a_s, b_s))
    # 脸内高光占比（直射光斑 / 油光）
    hi = float(100.0 * (L[skin] > Ls + 12).mean())
    # 脸框的离散度（脸本身光比）
    spa = float(np.percentile(L[skin], 90) - np.percentile(L[skin], 10))

    # 背景：脸框外扩 1.5 倍以外
    bg = np.ones(L.shape, bool)
    cw, ch = ww * 1.5, hh * 1.5
    bx0 = max(0, int(x + ww / 2 - cw / 2)); bx1 = min(w, int(x + ww / 2 + cw / 2))
    by0 = max(0, int(y + hh / 2 - ch / 2)); by1 = min(h, int(y + hh / 2 + ch / 2))
    bg[by0:by1, bx0:bx1] = False
    Lb = float(np.median(L[bg])) if bg.sum() > 500 else np.nan

    return dict(face=True, L_skin=Ls, a_skin=a_s, b_skin=b_s, C_skin=C_s,
                L_bg=Lb, d_bg=float(Lb - Ls) if np.isfinite(Lb) else np.nan,
                hi_frac=hi, face_span=spa, face_px=int(skin.sum()),
                zone=float((Ls - ZONE_V) / (ZONE_VI - ZONE_V)))


def pct(v, q=(25, 50, 75)):
    v = np.array([x for x in v if x is not None and np.isfinite(x)], float)
    if not len(v):
        return 'nan'
    return ' / '.join('%.1f' % np.percentile(v, x) for x in q)


def main():
    ms = json.load(open(os.path.join(OUT, 'masters52_shape.json'), encoding='utf-8'))['rows']
    groups, drop = {}, {'noface': 0, 'small': 0}
    for r in ms:
        p = os.path.join(MASTER, r['file'].replace('/', os.sep))
        if not os.path.exists(p):
            continue
        d = measure(p)
        if not d or not d.get('face'):
            drop['small' if (d and d.get('why')) else 'noface'] += 1
            continue
        d['name'] = r['file']
        groups.setdefault(r['file'].split('/')[0], []).append(d)
    sf, sdrop = {}, {'noface': 0, 'small': 0}
    for stem, run, p in collect():
        d = measure(p)
        if not d or not d.get('face'):
            sdrop['small' if (d and d.get('why')) else 'noface'] += 1
            continue
        d['name'] = stem
        sf.setdefault(run, []).append(d)
    print('肤色口径 = 左右脸蛋两圈 ∩ 肤色门（整脸框不许兜底）')
    print('大师 52：量到 %d，没检到脸 %d，脸蛋像素不足 %d'
          % (sum(len(v) for v in groups.values()), drop['noface'], drop['small']))
    print('SF 21 ：量到 %d，没检到脸 %d，脸蛋像素不足 %d\n'
          % (sum(len(v) for v in sf.values()), sdrop['noface'], sdrop['small']))

    print('== 脸放在哪个亮度（L*，1024 域；Zone V≈49.5  VI≈66.5）')
    print('%-26s %3s %-22s %-22s %-20s' % ('组', 'n', 'L*皮肤 P25/50/75', '背景−脸 P25/50/75', '脸内高光% P50'))
    allm = []
    for g, v in sorted(groups.items()):
        allm += v
        print('%-26s %3d %-22s %-22s %-20.1f'
              % (g[:26], len(v), pct([d['L_skin'] for d in v]),
                 pct([d['d_bg'] for d in v]), np.median([d['hi_frac'] for d in v])))
    print('%-26s %3d %-22s %-22s %-20.1f'
          % ('—— 全部 52', len(allm), pct([d['L_skin'] for d in allm]),
             pct([d['d_bg'] for d in allm]), np.median([d['hi_frac'] for d in allm])))
    print()
    for g, v in sorted(sf.items()):
        print('%-26s %3d %-22s %-22s %-20.1f'
              % (g[:26], len(v), pct([d['L_skin'] for d in v]),
                 pct([d['d_bg'] for d in v]), np.median([d['hi_frac'] for d in v])))
    print()

    # 特征还是巧合：组间中位差 vs 组内散布
    if len(groups) >= 2:
        med = {g: np.median([d['L_skin'] for d in v]) for g, v in groups.items()}
        within = np.median([np.std([d['L_skin'] for d in v]) for g, v in groups.items()])
        print('组间中位：' + ' · '.join('%s=%.1f' % (g[:8], m) for g, m in sorted(med.items())))
        print('组内 std 中位 %.2f ｜ 组间极差 %.2f'
              % (within, max(med.values()) - min(med.values())))
        print('⇒ ' + ('组间差 > 组内散布 ⇒ 光线类型不同，脸该落的地方本来就不同（要用光型分组）'
                      if max(med.values()) - min(med.values()) > within * 2
                      else '组间差 ≈ 组内散布 ⇒ 他把脸放在同一个位置，这是真特征'))

    json.dump(dict(masters={g: v for g, v in groups.items()}, sf={g: v for g, v in sf.items()}),
              open(os.path.join(OUT, 'skin_zone.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % os.path.join(OUT, 'skin_zone.json'))


if __name__ == '__main__':
    main()
