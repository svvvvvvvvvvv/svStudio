# -*- coding: utf-8 -*-
"""⑧ 的传递标定（一次对照，把斜率写进代码）。

测两件事：
  · 全局 `Saturation`（UI −100..100）→ **肤色 C\\*** 的斜率
  · 局部 `LocalShadows2012`（XMP 刻度，UI/100）→ **人身上暗部 L** 的斜率

方法：同一张 ①，两组各两个取值，其余全不动；导出后量同一取样点。
产物：design/calibration.py 里写死的两个斜率 + 出处（图、日期、n）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, r'E:\svStudio\svFilm')
sys.path.insert(0, r'E:\ds\photo\_corpus\kurai\full')

import lrstate as LS                      # noqa: E402
from design import measure_all as MA      # noqa: E402
from design.lr import LRPlan              # noqa: E402
from design.lr_xmp import build_xmp, write_tif   # noqa: E402

SRC = r'E:\ds\photo\_run_1029_tex2\engine\DSCF1231_engine.tif'
OUT = r'E:\ds\photo\_run_1030_card004\_calib'


def export(tag: str, *, saturation: float = 0.0, local_shadows: float = 0.0) -> str:
    os.makedirs(OUT, exist_ok=True)
    plan = LRPlan(saturation=saturation)
    xmp = build_xmp(plan.as_xmp_scalars(), curve=None)
    tmp = os.path.join(OUT, 'calib_%s.tif' % tag)
    write_tif(SRC, tmp, xmp)
    print(' ADD %s -> %s' % (tag, LS.run('ADD ' + tmp, timeout=300)[:80]))
    if abs(local_shadows) > 1e-9:
        print(' MASK -> %s' % LS.run('MASK %s subject' % tmp, timeout=300)[:80])
        r = LS.run('MASKPARAM %s LocalShadows2012 %.4f' % (tmp, local_shadows), timeout=180)
        print(' SH   -> %s' % r[:100])
    got = LS.expo(tmp, 'calib_%s' % tag)
    dst = os.path.join(OUT, '%s_final.tif' % tag)
    import shutil
    shutil.copy2(got, dst)
    return dst


def main() -> int:
    rows = {}
    for tag, sat in (('sat0', 0.0), ('sat30', 30.0)):
        p = export(tag, saturation=sat)
        r = MA.measure_one(p)
        sk = r.get('skin') or {}
        rows[tag] = dict(C=sk.get('C'), L=sk.get('L'), h=sk.get('h'))
        print('   %-6s 脸 C=%s L=%s' % (tag, sk.get('C'), sk.get('L')))
    for tag, sh in (('sh0', 0.0), ('sh40', 0.40)):
        p = export(tag, local_shadows=sh)
        r = MA.measure_one(p)
        per = r.get('person') or {}
        rows[tag] = dict(person_L=per.get('L'), person_p25=per.get('p25'))
        print('   %-6s 人 L=%s' % (tag, per.get('L')))
    with open(os.path.join(OUT, 'calib.json'), 'w', encoding='utf-8') as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    # ---- 斜率
    dC = (rows['sat30']['C'] or 0) - (rows['sat0']['C'] or 0)
    dL = (rows['sh40']['person_L'] or 0) - (rows['sh0']['person_L'] or 0)
    print('\nSaturation 30 ⇒ ΔC* %+.2f  (每单位 %+.3f)' % (dC, dC / 30.0))
    print('LocalShadows 0.40 ⇒ Δ人L %+.2f  (每 0.01 单位 %+.3f)' % (dL, dL / 40.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
