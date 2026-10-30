# -*- coding: utf-8 -*-
"""全量 v7 测量（给 cards 用）：肤色用 design/skin 的 v7 掩膜（脸皮肤第 3 类），
其余字段沿用 measure_all 的口径（1080 交付域）。

产出结构尽量对齐 m_*_v4.json 的 per_image，供 cards_build 直接读。

用法：
  & $py -X utf8 measure_v7_full.py --corpus 鹿井 --out m_鹿井_v7.json [--n 20]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image

sys.path.insert(0, r'E:\svStudio\svFilm')
from design.skin import face_mask as FM  # noqa: E402

BASE = r'E:\svStudio\svFilm\analysis\corpus\kurai'
MASTERS = {'鹿井': r'E:\ds\大师成片\鹿井', '滨田': r'E:\ds\大师成片\滨田', 'xhs': r'E:\ds\大师成片\xhs'}
SIDE = 1080


def _lab(rgb):
    from svFilm import color
    a = np.asarray(rgb, np.float64) / 255.0
    return color.to_lab(np.ascontiguousarray(np.clip(a, 0, 1)))


def measure(path, base):
    im = Image.open(path).convert('RGB')
    k = SIDE / float(max(im.size))
    if abs(k - 1.0) > 1e-6:
        im = im.resize((max(1, int(round(im.width * k))), max(1, int(round(im.height * k)))),
                       Image.LANCZOS)
    rgb = np.asarray(im)
    lab = _lab(rgb)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)
    fs = FM.face_skin(rgb) > 0.5
    ps = FM.person(rgb)
    bgm = ~ps

    def med(m, arr):
        return float(np.median(arr[m])) if int(m.sum()) >= 300 else None

    q = np.percentile(L, [1, 5, 10, 25, 50, 75, 90, 95, 99])
    hi, lo = L > 80, L < 20
    rec = dict(
        file=os.path.relpath(path, base).replace(os.sep, '/'),
        img=dict(p1=float(q[0]), p5=float(q[1]), p10=float(q[2]), p25=float(q[3]),
                 p50=float(q[4]), p75=float(q[5]), p90=float(q[6]), p95=float(q[7]),
                 p99=float(q[8]), contrast=float(q[7] - q[1]),
                 clip_pct=float((L >= 99.5).mean() * 100)),
        shape=dict(toe=float(q[3] - q[1]), shoulder=float(q[7] - q[5]),
                   contrast_win=float(q[7] - q[3]),
                   skew=float(np.subtract(*np.percentile(L, [75, 25])))),
        skin=dict(L=med(fs, L), C=med(fs, C), b=med(fs, b), n=int(fs.sum()),
                  h=(float(np.degrees(np.arctan2(np.median(b[fs]), np.median(a[fs]))) % 360)
                     if int(fs.sum()) >= 300 else None)),
        bg=dict(L=med(bgm, L), L_p10=float(np.percentile(L[bgm], 10)) if bgm.sum() > 300 else None,
                L_p90=float(np.percentile(L[bgm], 90)) if bgm.sum() > 300 else None),
        person_L=med(ps, L),
        sky_share_frac=float(((L > 60) & (b < -6)).mean()),
        env_share=float(bgm.mean()),
        face_size=float(fs.mean()),
        warm_cool_b=(float(np.median(b[hi]) - np.median(b[lo]))
                     if hi.sum() > 300 and lo.sum() > 300 else None),
    )
    if rec['bg']['L'] and rec['skin']['L']:
        rec['face_over_bg'] = round(rec['skin']['L'] / rec['bg']['L'], 3)
    if rec['bg']['L'] and rec['person_L']:
        rec['person_over_bg'] = round(rec['person_L'] / rec['bg']['L'], 3)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', default='鹿井')
    ap.add_argument('--n', type=int, default=0)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    src = os.path.join(BASE, 'm_%s_v4.json' % a.corpus)
    rows = [r for r in json.load(open(src, encoding='utf-8'))['per_image'] if r.get('file')]
    if a.n:
        rows = rows[:a.n]
    out = []
    for i, r in enumerate(rows, 1):
        p = os.path.join(MASTERS[a.corpus], r['file'].replace('/', os.sep))
        try:
            out.append(measure(p, MASTERS[a.corpus]))
        except Exception as e:                                # noqa: BLE001
            out.append(dict(file=r['file'], err=str(e)[:80]))
        if i % 100 == 0:
            print('  %d/%d' % (i, len(rows)), flush=True)
    ok = sum(1 for x in out if x.get('skin', {}).get('C') is not None)
    print('%s：%d 张，有肤色 %d' % (a.corpus, len(out), ok))
    json.dump(dict(per_image=out), open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('写出', a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
