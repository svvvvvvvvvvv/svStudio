# -*- coding: utf-8 -*-
"""v7 口径测量（复用 design/skin，不再自造）。

量什么：face_L/C/h（v7 脸皮肤掩膜 + 分量中位）、person_over_bg、face_over_bg、
        contrast(p95−p5)、warm_cool_b（亮部 b − 暗部 b）。

用法：
  & $py -X utf8 measure_v7.py --dir <目录> --out x.json
  & $py -X utf8 measure_v7.py --card 004  --out x.json     # 只测某张卡的目录
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, r'E:\svStudio\svFilm')
from design.skin import face_mask as FM  # noqa: E402

SIDE = 1080
MASTERS = r'E:\ds\大师成片\鹿井'


def _lab(rgb: np.ndarray) -> np.ndarray:
    from svFilm import color
    a = np.asarray(rgb, np.float64) / 255.0
    return color.to_lab(np.ascontiguousarray(np.clip(a, 0, 1)))


def measure_one(path: str) -> dict:
    im = Image.open(path).convert('RGB')
    k = SIDE / float(max(im.size))
    if k != 1.0:
        im = im.resize((max(1, int(round(im.width * k))), max(1, int(round(im.height * k)))))
    rgb = np.asarray(im)
    lab = _lab(rgb)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)
    out = {'file': path, 'n_px': int(L.size)}

    fs = FM.face_skin(rgb) > 0.5
    ps = FM.person(rgb)
    out['face_px'] = int(fs.sum())
    out['person_px'] = int(ps.sum())
    bgm = ~ps
    out['bg_px'] = int(bgm.sum())

    def med(m, arr):
        return float(np.median(arr[m])) if int(m.sum()) >= 300 else None

    out['face_L'] = med(fs, L)
    out['face_C'] = med(fs, C)
    out['face_b'] = med(fs, b)
    out['face_h'] = (float(np.degrees(np.arctan2(np.median(b[fs]), np.median(a[fs]))) % 360)
                     if int(fs.sum()) >= 300 else None)
    out['person_L'] = med(ps, L)
    out['bg_L'] = med(bgm, L)
    if out['face_L'] and out['bg_L']:
        out['face_over_bg'] = round(out['face_L'] / out['bg_L'], 3)
    if out['person_L'] and out['bg_L']:
        out['person_over_bg'] = round(out['person_L'] / out['bg_L'], 3)
    q = np.percentile(L, [1, 5, 25, 50, 75, 95, 99])
    out['img'] = {'p%d' % k: round(float(v), 2)
                  for k, v in zip((1, 5, 25, 50, 75, 95, 99), q)}
    out['img']['clip_pct'] = round(float((L > 99).mean() * 100.0), 3)
    out['contrast'] = round(float(q[5] - q[1]), 2)
    # ★ 2026-10-30 补：脸/人的分位（尤其 p99）—— 局部补光的白端预算要**人 p99**，
    #   不是整幅 p99（整幅会被天空/窗光顶死；见 LR数值来源 §2.1 #17）。
    if int(fs.sum()) >= 300:
        out['face_p95'] = round(float(np.percentile(L[fs], 95)), 2)
        out['face_p99'] = round(float(np.percentile(L[fs], 99)), 2)
        out['face_p5'] = round(float(np.percentile(L[fs], 5)), 2)
    if int(ps.sum()) >= 300:
        out['person_p95'] = round(float(np.percentile(L[ps], 95)), 2)
        out['person_p99'] = round(float(np.percentile(L[ps], 99)), 2)
    if int(bgm.sum()) >= 300:
        out['bg_L_p90'] = round(float(np.percentile(L[bgm], 90)), 2)
        out['bg_L_p95'] = round(float(np.percentile(L[bgm], 95)), 2)
    hi, lo = L > 80, L < 20
    if hi.sum() > 300 and lo.sum() > 300:
        out['warm_cool_b'] = round(float(np.median(b[hi]) - np.median(b[lo])), 2)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='')
    ap.add_argument('--card', default='')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)

    files = []
    if a.dir:
        for ext in ('*.png', '*.jpg', '*.jpeg'):
            files += sorted(glob.glob(os.path.join(a.dir, ext)))
    if a.card:
        d = os.path.join(MASTERS, a.card)
        files = sorted(glob.glob(os.path.join(d, '*.jpg')))
    rows = []
    for f in files:
        try:
            rows.append(measure_one(f))
        except Exception as e:                                # noqa: BLE001
            rows.append({'file': f, 'err': str(e)[:80]})
    ok = [r for r in rows if r.get('face_C') is not None]
    print('%d 张，可用 %d' % (len(rows), len(ok)))
    for k in ('face_L', 'face_C', 'face_h', 'face_over_bg', 'person_over_bg', 'contrast', 'warm_cool_b'):
        v = [r[k] for r in ok if r.get(k) is not None]
        if v:
            print('  %-16s 中位 %8.2f  IQR %6.2f' % (k, np.median(v), np.subtract(*np.percentile(v, [75, 25]))))
    json.dump(dict(per_image=rows), open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('写出', a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
