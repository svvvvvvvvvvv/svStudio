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
· 颜色：全批脸 C/h、warm_cool_b 的中位 vs 卡带；剂量沿用 `calibration` 的相对刻度；
· 异质检查：任一 A 量的 IQR > 卡带宽 ⇒ 报"可能该拆批"（§3.2），不偷偷平均。
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


def _band_mid(band) -> Optional[float]:
    if isinstance(band, (list, tuple)) and len(band) == 2:
        return 0.5 * (float(band[0]) + float(band[1]))
    return None


def _band_w(band) -> Optional[float]:
    if isinstance(band, (list, tuple)) and len(band) == 2:
        return float(band[1]) - float(band[0])
    return None


def build(recs: Dict[str, dict], card: dict) -> dict:
    """全批中位 → A 段的参数（一条曲线 + 一套颜色）。"""
    bands = (card or {}).get('bands') or {}
    n = len(recs)
    # ---------- 明暗：每张图的分位取中位 ----------
    med_q = {k: _med([_img(r).get(k) for r in recs.values()]) for k in A_KEYS}
    shape = {}
    for k in ('toe', 'contrast_win', 'shoulder'):
        band = bands.get(k)
        if isinstance(band, (list, tuple)) and len(band) == 2:
            shape[k] = (float(band[0]), float(band[1]))
    cap_band = bands.get('p95')
    cap = min(100.0, float(cap_band[1])) if isinstance(cap_band, (list, tuple)) else 100.0
    curve = None
    if all(v is not None for v in med_q.values()) and shape:
        curve = curve_from_quantiles(med_q, shape, p95_cap=cap)

    # ---------- 颜色：全批中位 vs 卡带 ----------
    med_face_C = _med([_face(r, 'C', 'face_C') for r in recs.values()])
    med_face_h = _med([_face(r, 'h', 'face_h') for r in recs.values()])
    med_wc = _med([r.get('warm_cool_b') for r in recs.values()])
    med_rec = {'face_C': med_face_C, 'face_h': med_face_h,
               'warm_cool_b': med_wc,
               'skin': {'C': med_face_C, 'h': med_face_h}}
    from .lr_solve import grading_of            # 延迟导入，避免循环
    grading = grading_of(card, med_rec)

    # ---------- 异质性：A 量的 IQR vs 卡带宽 ----------
    hetero = []
    checks = [
        ('face_C', [ _face(r, 'C', 'face_C') for r in recs.values() ], bands.get('face_C')),
        ('face_h', [ _face(r, 'h', 'face_h') for r in recs.values() ], bands.get('face_h')),
        ('warm_cool_b', [r.get('warm_cool_b') for r in recs.values()], bands.get('warm_cool_b')),
        ('p95', [_img(r).get('p95') for r in recs.values()], bands.get('p95')),
        ('contrast', [ (float(_img(r).get('p95')) - float(_img(r).get('p5')))
                       for r in recs.values()
                       if _img(r).get('p95') is not None and _img(r).get('p5') is not None ],
         bands.get('contrast')),
    ]
    for key, vals, band in checks:
        iqr, w = _iqr(vals), _band_w(band)
        if iqr is not None and w is not None and iqr > w:
            hetero.append('%s IQR %.2f > 卡带宽 %.2f' % (key, iqr, w))

    ledger = []
    if hetero:
        ledger.append('批内异质：' + '；'.join(hetero) + ' ⇒ 先报人，可能要拆批')
    if med_face_C is not None and _band_mid(bands.get('face_C')) is not None:
        ledger.append('全批脸 C 中位 %.2f，卡带中点 %.2f'
                      % (med_face_C, _band_mid(bands.get('face_C'))))
    if med_wc is not None and _band_mid(bands.get('warm_cool_b')) is not None:
        ledger.append('全批暖冷中位 %.2f，卡带中点 %.2f'
                      % (med_wc, _band_mid(bands.get('warm_cool_b'))))
    return dict(card=(card or {}).get('name'), n=n, med=med_q, shape=shape,
                curve=curve, grading=grading,
                med_color=dict(face_C=med_face_C, face_h=med_face_h, warm_cool_b=med_wc),
                hetero=hetero, ledger=ledger)
