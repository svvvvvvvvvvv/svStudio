# -*- coding: utf-8 -*-
"""模拟 ③：**不驱动 LR**，把计划的曲线套到 ① 上，量出来跟 peer/计划比。

用途
----
③ 的真身要 LR 出（`lr_exec`），但 LR 不一定在跑。这一步让闭环在没有 LR 的时候
也能走一遍：曲线到底把暗部压下去了多少、中位有没有被带跑，这是**像素级**的答案，
不是 L* 上的算术。

边界
----
· 只套**曲线**（Basic 全 0、局部不做）；所以它验证的是"形状"，不是整份计划；
· 产物写到 `final_sim/`，名字带 `_sim`，**永远不和 LR 的 ③ 混**。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np


def _lut_from_curve(curve: List[tuple]) -> Optional[np.ndarray]:
    if not curve:
        return None
    xs = np.array([p[0] for p in curve], float)
    ys = np.array([p[1] for p in curve], float)
    x = np.arange(256, dtype=float)
    y = np.interp(x, xs, ys)
    return np.clip(np.round(y), 0, 255).astype(np.uint8)


def apply_curve(rgb: np.ndarray, curve: List[tuple]) -> np.ndarray:
    lut = _lut_from_curve(curve)
    if lut is None:
        return rgb
    return lut[rgb]


def measure_plans(plans: dict, engine_dir: str, out_dir: str,
                  peer_q: Optional[dict] = None, log=print) -> dict:
    """对已算好的 plans 做模拟 ③（不需要 plans.json）。→ {stem: 实测}。

    plans 可以是 {stem: LRPlan} 或 {stem: dict}；只读 curve。
    peer_q = {stem: peer 的五分位}（给了就顺带算实测暗差）。
    """
    from PIL import Image
    from .batch import measure_tif

    os.makedirs(out_dir, exist_ok=True)
    out = {}
    for stem in sorted(plans):
        src = os.path.join(engine_dir, '%s_engine.tif' % stem)
        if not os.path.isfile(src):
            continue
        pl = plans[stem]
        curve = getattr(pl, 'curve', None) if not isinstance(pl, dict) else pl.get('curve')
        a = np.asarray(Image.open(src).convert('RGB'))
        b = apply_curve(a, curve or [])
        dst = os.path.join(out_dir, '%s_sim.tif' % stem)
        Image.fromarray(b).save(dst)
        m = measure_tif(dst)
        img = m.get('img') or {}
        pq = (peer_q or {}).get(stem) or {}
        p5 = img.get('p5')
        out[stem] = dict(
            stem=stem, curve_pts=(len(curve) if curve else 0),
            p5=p5, p50=img.get('p50'), p95=img.get('p95'),
            peer_p5=pq.get('p5'), peer_p50=pq.get('p50'), peer_p95=pq.get('p95'),
            gap_after=(None if (p5 is None or pq.get('p5') is None)
                       else float(p5) - float(pq.get('p5'))),
            sim=os.path.basename(dst))
    log('  模拟 ③：%d 张（只套曲线）' % len(out))
    return out


def run(run_dir: str, log=print) -> dict:
    """读 plans.json + engine/*.tif → 写 final_sim/*_sim.tif + 量 + 对比。"""
    pj = os.path.join(run_dir, 'plans.json')
    if not os.path.isfile(pj):
        raise RuntimeError('没有 plans.json：先跑 run（会顺手写一份）')
    plans = json.load(open(pj, encoding='utf-8'))
    peer_q = {st: (v or {}).get('peer_q') for st, v in plans.items()}
    out = measure_plans(plans, os.path.join(run_dir, 'engine'),
                        os.path.join(run_dir, 'final_sim'), peer_q=peer_q, log=log)
    return dict(rows=[out[k] for k in sorted(out)],
                dir=os.path.join(run_dir, 'final_sim'))


def table(res: dict) -> str:
    """人看的一行一张：曲线点 / 实测 p5·p50·p95 / peer 的 p5·p50·p95 / 暗差。"""
    out = ['%-9s %4s %7s %7s %7s ｜ %7s %7s %7s ｜ %6s'
           % ('stem', '点数', 'p5', 'p50', 'p95', 'peer5', 'peer50', 'peer95', '暗差')]
    for r in res['rows']:
        def f(v):
            return '—' if v is None else '%.1f' % v
        gap = (None if (r['p5'] is None or r['peer_p5'] is None)
               else r['p5'] - r['peer_p5'])
        out.append('%-9s %4d %7s %7s %7s ｜ %7s %7s %7s ｜ %6s'
                   % (r['stem'], r['curve_pts'], f(r['p5']), f(r['p50']), f(r['p95']),
                      f(r['peer_p5']), f(r['peer_p50']), f(r['peer_p95']),
                      '—' if gap is None else '%+.1f' % gap))
    return '\n'.join(out)
