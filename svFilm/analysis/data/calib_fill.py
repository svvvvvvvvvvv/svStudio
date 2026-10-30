# -*- coding: utf-8 -*-
"""标定**局部 fill（LocalExposure2012）→ 脸 L* / 人 p99**（替掉 fit 里 0.3 那个假设）。

三档：0 / 1.0 / 1.5 档（写进 LR 是 /4 ⇒ 0 / 0.25 / 0.375）
两张图：DSCF1231（中调脸）、DSCF1665（暗脸，v11 出问题的那张）
量：脸 L、人的 p99（clip_p95）
"""
from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, r'E:\svStudio\svFilm')
sys.path.insert(0, r'E:\ds\photo\_corpus\kurai\full')

import lrstate as LS                      # noqa: E402
from design import measure_all as MA      # noqa: E402
from design.lr import LRPlan              # noqa: E402
from design.lr_xmp import build_xmp, write_tif   # noqa: E402

OUT = r'E:\ds\photo\_run_1030_card004\_calib_fill'
STEMS = ('DSCF1231', 'DSCF1665')
SRC_DIR = r'E:\ds\photo\_run_1029_tex2\engine'
EVS = (0.0, 1.0, 1.5)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    rows = {}
    for st in STEMS:
        src = os.path.join(SRC_DIR, '%s_engine.tif' % st)
        if not os.path.isfile(src):
            print('缺 ①：%s' % src)
            continue
        for ev in EVS:
            tag = '%s_f%.1f' % (st, ev)
            tmp = os.path.join(OUT, 'in_%s.tif' % tag)
            write_tif(src, tmp, build_xmp(LRPlan().as_xmp_scalars(), curve=None))
            LS.run('ADD ' + tmp, timeout=300)
            if ev > 0:
                LS.run('MASK %s subject' % tmp, timeout=300)
                r = LS.run('MASKPARAM %s LocalExposure2012 %.5f' % (tmp, ev / 4.0), timeout=180)
                print('  %s %s' % (tag, r[:90]))
            got = LS.expo(tmp, 'calib_%s' % tag)
            dst = os.path.join(OUT, '%s.tif' % tag)
            shutil.copy2(got, dst)
            m = MA.measure_one(dst)
            sk = m.get('skin') or {}
            per = m.get('person') or {}
            rows[tag] = dict(face=sk.get('L'), p99=per.get('clip_p95'), C=sk.get('C'))
            print('   %-14s 脸 %s ｜ 人 p99 %s' % (tag, sk.get('L'), per.get('clip_p95')))
    json.dump(rows, open(os.path.join(OUT, 'calib_fill.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    # 斜率
    for st in STEMS:
        a = rows.get('%s_f0.0' % st)
        b = rows.get('%s_f1.5' % st)
        if a and b and a['face'] and b['face']:
            print('%s: fill 0→1.5 脸 %+.1f L* ⇒ %.3f L*/档 ｜ p99 %+.1f ⇒ %.3f L*/档'
                  % (st, b['face'] - a['face'], (b['face'] - a['face']) / 1.5,
                     (b['p99'] or 0) - (a['p99'] or 0), ((b['p99'] or 0) - (a['p99'] or 0)) / 1.5))
    return 0


if __name__ == '__main__':
    sys.exit(main())
