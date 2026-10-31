# -*- coding: utf-8 -*-
"""A 段（批级）：**全批一次、全批同参**的色调/颜色残差。

为什么单独一层（2026-10-30 评审）
--------------------------------
设计真源 §2/§5 把 LR 分两段：
    A 批级：颜色（中性/肤色）、明暗层次（曲线）、冷暖的**参数** —— 每批一次、全批同参；
    B 图级：曝光、脸/背景、白黑端、冷暖的**落地** —— 每图。
而旧 `lr_solve.plan_batch` 是**逐图**造曲线、逐图算颜色 ⇒ A 段事实上不存在，
且违反"批内一次算"（设计 §3.3）。本模块把 A 段收回来：

    batch = build(recs, card)          # 全批中位 → 一条曲线 + 一套颜色参数
    plans = plan_batch(recs, card, batch=batch)

B 段（曝光/白端/局部）仍在 `lr_solve.plan_batch` 里逐图算。

口径
----
· 位置：全批每张图自己的分位取**中位**（不是把所有像素混在一起）；
· 形状：卡的 toe / contrast_win / shoulder 带；每锚点累计位移 ≤3 L*（S3）；
· 颜色：A 段只给方向；落地在 B 段逐图（`grading_of`）；
· 异质检查：只报 IQR 数值，交人决定拆不拆批（不拿卡的带宽当门）。
"""
from __future__ import annotations

import statistics as st
from typing import Dict, List, Optional

from .curve_design import curve_from_quantiles

A_KEYS = ('p5', 'p25', 'p50', 'p75', 'p95')


def _img(rec: dict) -> dict:
    return rec.get('img') or {}


def _skin(rec: dict) -> dict:
    return rec.get('skin') or {}


def _face(rec: dict, key: str, alt: str):
    return _skin(rec).get(key, rec.get(alt))


def _med(xs: List[float]) -> Optional[float]:
    xs = [float(x) for x in xs if isinstance(x, (int, float))]
    return float(st.median(xs)) if xs else None


def _iqr(xs: List[float]) -> Optional[float]:
    xs = sorted(float(x) for x in xs if isinstance(x, (int, float)))
    if len(xs) < 4:
        return None
    q = [xs[int(round((len(xs) - 1) * p))] for p in (0.25, 0.75)]
    return float(q[1] - q[0])


def build(recs: Dict[str, dict], card: dict) -> dict:
    """全批中位 → A 段的参数（一条曲线 + 一套颜色）。"""
    points = (card or {}).get('points') or {}
    n = len(recs)
    # ---------- 明暗：每张图的分位取中位 ----------
    med_q = {k: _med([_img(r).get(k) for r in recs.values()]) for k in A_KEYS}
    shape = {}
    for k in ('toe', 'contrast_win', 'shoulder'):
        v = points.get(k)
        if isinstance(v, (int, float)):
            shape[k] = float(v)
    cap = float(points['p95']) if isinstance(points.get('p95'), (int, float)) else 100.0
    curve = None
    if all(v is not None for v in med_q.values()) and shape:
        curve = curve_from_quantiles(med_q, shape, p95_cap=cap)

    # ---------- 颜色：A 段**只给方向**；落地在 B 段逐图（不在这里算剂量） ----------
    med_face_C = _med([_face(r, 'C', 'face_C') for r in recs.values()])
    med_face_h = _med([_face(r, 'h', 'face_h') for r in recs.values()])
    med_wc = _med([r.get('warm_cool_b') for r in recs.values()])

    # ---------- 异质性：只报 IQR，交人决定拆不拆批（不拿卡的带宽当门） ----------
    hetero = []
    checks = [
        ('face_C', [_face(r, 'C', 'face_C') for r in recs.values()]),
        ('face_h', [_face(r, 'h', 'face_h') for r in recs.values()]),
        ('warm_cool_b', [r.get('warm_cool_b') for r in recs.values()]),
        ('p95', [_img(r).get('p95') for r in recs.values()]),
        ('contrast', [(float(_img(r).get('p95')) - float(_img(r).get('p5')))
                       for r in recs.values()
                       if _img(r).get('p95') is not None and _img(r).get('p5') is not None]),
    ]
    for key, vals in checks:
        iqr = _iqr(vals)
        if iqr is not None:
            hetero.append('%s IQR %.2f' % (key, iqr))

    ledger = []
    if hetero:
        ledger.append('批内离散度：' + '；'.join(hetero) + ' ⇒ 交人决定拆不拆批')
    if med_face_C is not None and isinstance(points.get('face_C'), (int, float)):
        ledger.append('全批脸 C 中位 %.2f，卡的点值 %.2f'
                      % (med_face_C, float(points['face_C'])))
    if med_wc is not None and isinstance(points.get('warm_cool_b'), (int, float)):
        ledger.append('全批暖冷中位 %.2f，卡的点值 %.2f'
                      % (med_wc, float(points['warm_cool_b'])))
    return dict(card=(card or {}).get('name'), n=n, med=med_q, shape=shape,
                curve=curve,
                med_color=dict(face_C=med_face_C, face_h=med_face_h, warm_cool_b=med_wc),
                hetero=hetero, ledger=ledger)
