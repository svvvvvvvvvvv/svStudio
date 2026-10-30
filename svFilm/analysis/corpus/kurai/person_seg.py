# -*- coding: utf-8 -*-
"""person_over_bg：人（身体）与背景的亮度比（补测 person 区域）。

背景：已有的 `m_*_v4.json` 里的 bg.L 中位（同一把尺子）；
人像：mediapipe selfie segmentation 的 person 概率 > 0.5。
比值 = person_L / bg_L（与 face_over_bg 同构）。

用法：
  & $py -X utf8 person_seg.py --corpus 鹿井 --n 20      # 试点
  & $py -X utf8 person_seg.py --corpus 鹿井 --all --out person_鹿井.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, r'E:\svStudio\svFilm\analysis')
from _lib.measure import open_rgb, to_lab900  # noqa: E402

M = r'E:\svStudio\svFilm\analysis\corpus\kurai\m_%s_v4.json'
BASE = {'鹿井': r'E:\ds\大师成片\鹿井', '滨田': r'E:\ds\大师成片\滨田', 'xhs': r'E:\ds\大师成片\xhs'}
SIDE = 1080


def _seg():
    import mediapipe as mp
    return mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', default='鹿井')
    ap.add_argument('--n', type=int, default=0)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--out', default='')
    a = ap.parse_args(argv)

    d = json.load(open(M % a.corpus, encoding='utf-8'))
    rows = [r for r in d.get('per_image') or [] if r.get('file')]
    if not a.all and a.n > 0:
        rows = rows[:a.n]

    seg = _seg()
    out, bad, ok = [], 0, 0
    for r in rows:
        p = os.path.join(BASE[a.corpus], r['file'].replace('/', os.sep))
        try:
            im = open_rgb(p)
            k = SIDE / float(max(im.size))
            im = im.resize((max(1, int(round(im.width * k))), max(1, int(round(im.height * k)))))
            lab = to_lab900(im, side=SIDE, do_exif=False)
            m = seg.process(np.asarray(im)).segmentation > 0.5
            share = float(m.mean())
            bgL = ((r.get('bg') or {}).get('L'))
            rec = dict(file=r['file'], person_share=round(share, 4), bg_L=bgL)
            if share >= 0.02 and bgL:
                Lp = float(np.median(lab[..., 0][m]))
                rec.update(person_L=round(Lp, 2), person_over_bg=round(Lp / bgL, 3),
                           ok=True)
                ok += 1
            else:
                rec['ok'] = False
            out.append(rec)
        except Exception as e:                                # noqa: BLE001
            bad += 1
            out.append(dict(file=r['file'], ok=False, err=str(e)[:80]))
    print('%s：%d 张，可用 %d（%.0f%%），坏 %d' % (a.corpus, len(out), ok, 100.0 * ok / max(len(out), 1), bad))
    v = [r['person_over_bg'] for r in out if r.get('person_over_bg') is not None]
    if v:
        print('  person_over_bg 中位 %.2f ｜ IQR %.2f ｜ 范围 %.2f–%.2f'
              % (np.median(v), np.subtract(*np.percentile(v, [75, 25])), min(v), max(v)))
    if a.out:
        json.dump(dict(corpus=a.corpus, n=len(out), ok=ok, per_image=out),
                  open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('写出 %s' % a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
