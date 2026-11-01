# -*- coding: utf-8 -*-
"""LR-A（批级）：**只报护栏**，不下发公共曲线（`设计真源.md` §2 / `LR设计.md` §1）。

A 不决定单张亮度/曲线；它只回答三件事：
  · 这批是不是异质（face_C / face_h / warm_cool_b / p95 / contrast 的 IQR）；
  · 这批的中位长什么样（人读）；
  · 和卡差多少（只描述）。

曲线/曝光/肤色目标都在 B 段逐图（`lr_solve`），不在 A。
"""
from __future__ import annotations

import statistics as st
from typing import Dict, List, Optional

A_KEYS = ('p5', 'p25', 'p50', 'p75', 'p95')


def _img(rec: dict) -> dict:
    return rec.get('img') or {}


def _med(xs) -> Optional[float]:
    v = [float(x) for x in xs if isinstance(x, (int, float))]
    return float(st.median(v)) if v else None


def _iqr(xs) -> Optional[float]:
    v = sorted(float(x) for x in xs if isinstance(x, (int, float)))
    if len(v) < 4:
        return None
    q1 = v[int(round((len(v) - 1) * 0.25))]
    q3 = v[int(round((len(v) - 1) * 0.75))]
    return float(q3 - q1)


def build(recs: Dict[str, dict], card: dict) -> dict:
    """批级护栏：中位 + IQR + 与卡的差（全部只报告）。"""
    n = len(recs)
    med_q = {k: _med([_img(r).get(k) for r in recs.values()]) for k in A_KEYS}
    face_C = _med([(r.get('skin') or {}).get('C') for r in recs.values()])
    face_h = _med([(r.get('skin') or {}).get('h') for r in recs.values()])
    wc = _med([r.get('warm_cool_b') for r in recs.values()])

    hetero = []
    checks = [
        ('face_C', [(r.get('skin') or {}).get('C') for r in recs.values()]),
        ('face_h', [(r.get('skin') or {}).get('h') for r in recs.values()]),
        ('warm_cool_b', [r.get('warm_cool_b') for r in recs.values()]),
        ('p50', [_img(r).get('p50') for r in recs.values()]),
        ('p95', [_img(r).get('p95') for r in recs.values()]),
        ('contrast', [(_img(r).get('p95') - _img(r).get('p5'))
                      for r in recs.values()
                      if _img(r).get('p95') is not None and _img(r).get('p5') is not None]),
    ]
    for key, vals in checks:
        iqr = _iqr(vals)
        if iqr is not None:
            hetero.append('%s IQR %.2f' % (key, iqr))

    pts = (card or {}).get('points') or {}
    ledger = []
    if hetero:
        ledger.append('批内离散度：' + '；'.join(hetero) + ' ⇒ 交人决定拆不拆批')
    if face_C is not None and isinstance(pts.get('face_C'), (int, float)):
        ledger.append('全批脸 C 中位 %.2f ｜ 卡 %.2f（只描述）'
                      % (face_C, float(pts['face_C'])))
    if wc is not None and isinstance(pts.get('warm_cool_b'), (int, float)):
        ledger.append('全批暖冷中位 %.2f ｜ 卡 %.2f（只描述）'
                      % (wc, float(pts['warm_cool_b'])))
    return dict(card=(card or {}).get('name'), n=n, med=med_q,
                med_color=dict(face_C=face_C, face_h=face_h, warm_cool_b=wc),
                hetero=hetero, ledger=ledger)
