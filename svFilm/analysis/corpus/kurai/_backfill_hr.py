# -*- coding: utf-8 -*-
"""按**实测天花**给旧 sidecar 补 valid/ceiling_L —— 是重新测量，不是编造。"""
import os, glob, json, sys

sys.path.insert(0, r'E:\svStudio\svFilm')
import numpy as np, tifffile
from design.flow import headroom_report


def load(p):
    a = tifffile.imread(p)
    if a.ndim == 3 and a.shape[-1] > 3:
        a = a[..., :3]
    return a.astype(np.float64) / 65535.0


BATCHES = (
    ('run_919_wl095/engine', 0.95),   # 那一批把卡的白端临时改成 0.95
    ('run_919_full/engine', 1.00),    # 卡原来的值
)

for d, wl in BATCHES:
    n = 0
    for p in sorted(glob.glob(os.path.join(d, '*_headroom.json'))):
        stem = os.path.basename(p).replace('_headroom.json', '')
        tif = os.path.join(d, stem + '_engine.tif')
        if not os.path.exists(tif):
            continue
        hr = headroom_report(load(tif), white_level=wl)
        j = json.load(open(p, encoding='utf-8'))
        j.update(ceiling_L=hr['ceiling_L'], valid=hr['valid'],
                 ceiling_expect_L=hr['ceiling_expect_L'], white_level=wl,
                 tautology_ev=hr['tautology_ev'], why=hr['why'])
        json.dump(j, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        n += 1
    bad = sum(1 for p in glob.glob(os.path.join(d, '*_headroom.json'))
              if json.load(open(p, encoding='utf-8')).get('valid') is False)
    print('%s（白端 %.2f）: 补齐 %d 份 ｜ 判不可信 %d 份' % (d, wl, n, bad))
