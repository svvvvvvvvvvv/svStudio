# -*- coding: utf-8 -*-
"""色调曲线：只搬形状、不动 p50，且每个锚点的位移受预算约束（S3）。

为什么要有预算（分工设计 §9.1）：
  曲线是单调 LUT，**什么都能搬**——正因为它太能干，若放任它把介质形状的错也补掉，
  就永远查不出"是哪一层不对"。预算 = 诊断边界。

口径：
  · 锚点用 L*（0..100），曲线内部是 0..255 码值
  · p50 不允许动（曝光归 LR 的曝光滑杆，不归曲线）
  · 每锚点 |Δ| ≤ budget（默认 3 L*）

用法：
    from design.curve import plan_curve, lstar_to_code, displacement
    pts, info = plan_curve(src_q, dst_q, budget=3.0)
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

ANCHOR_Q = (5, 25, 50, 75, 95)


def lstar_to_code(L: float) -> int:
    """朴素映射：L* → 0..255（与引擎/暗房链路一致的单调近似，够用于锚点）。"""
    return max(0, min(255, int(round(L * 255.0 / 100.0))))


def code_to_lstar(c: int) -> float:
    return max(0.0, min(100.0, c * 100.0 / 255.0))


def _fit_points(pairs: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """把 (code_in, code_out) 锚点插值成 16 点（0 与 255 端点锁死、单调）。"""
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    if xs[0] > 0:
        xs = [0] + xs
        ys = [0] + ys
    if xs[-1] < 255:
        xs = xs + [255]
        ys = ys + [255]
    pts = []
    for i in range(16):
        c = int(round(i * 255.0 / 15.0))
        # 线性插值
        for j in range(1, len(xs)):
            if c <= xs[j]:
                x0, x1, y0, y1 = xs[j - 1], xs[j], ys[j - 1], ys[j]
                t = 0.0 if x1 == x0 else (c - x0) / float(x1 - x0)
                pts.append((c, int(round(y0 + t * (y1 - y0)))))
                break
        else:
            pts.append((c, 255))
    # 单调化（不许出现反转）
    for i in range(1, len(pts)):
        if pts[i][1] < pts[i - 1][1]:
            pts[i] = (pts[i][0], pts[i - 1][1])
    return pts


def plan_curve(src_q: Dict[str, float], dst_q: Dict[str, float],
               budget: float = 3.0) -> Tuple[Optional[List[Tuple[int, int]]], dict]:
    """按"只搬形状、不动 p50、每锚点 ≤ budget"造曲线。

    src_q / dst_q: {'5': L*, '25': L*, '50': L*, '75': L*, '95': L*}（测量给的整幅分位）
    返回 (16 点曲线 | None, 说明)。None = 需要的位移超预算 ⇒ **该回引擎换卷/纸**。
    """
    info = dict(src={k: round(src_q.get(k, 0), 2) for k in map(str, ANCHOR_Q)},
                dst={k: round(dst_q.get(k, 0), 2) for k in map(str, ANCHOR_Q)},
                budget=budget)
    if not src_q or not dst_q:
        info['why'] = '缺分位'
        return None, info

    p50 = float(src_q.get('50', 50.0))
    deltas = {}
    pairs = []
    for q in ANCHOR_Q:
        k = str(q)
        s, d = float(src_q.get(k, p50)), float(dst_q.get(k, p50))
        if q == 50:
            d = p50                                   # p50 不动（曝光归曝光滑杆）
        d = max(s - budget, min(s + budget, d))       # 预算钳位
        deltas[k] = round(d - s, 2)
        pairs.append((lstar_to_code(s), lstar_to_code(d)))
    info['delta'] = deltas
    info['max_abs_delta'] = max(abs(v) for v in deltas.values())
    info['clamped'] = (abs(float(dst_q.get('95', p50)) - p50) > budget) or \
                      (abs(float(dst_q.get('5', p50)) - p50) > budget)
    # 需要而给不到的部分（诊断用）：超过预算的差额
    need = {k: round(float(dst_q.get(k, p50)) - float(src_q.get(k, p50)), 2)
            for k in map(str, ANCHOR_Q) if k != '50'}
    info['need_unmet'] = {k: v for k, v in need.items() if abs(v) > budget}
    pts = _fit_points(pairs)
    return pts, info


def displacement(pts: List[Tuple[int, int]], Ls: List[float]) -> List[float]:
    """曲线上若干 L* 处的位移（L* 单位），用于 S3 复查。"""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    out = []
    for L in Ls:
        c = lstar_to_code(L)
        y = 0
        for j in range(1, len(xs)):
            if c <= xs[j]:
                x0, x1, y0, y1 = xs[j - 1], xs[j], ys[j - 1], ys[j]
                t = 0.0 if x1 == x0 else (c - x0) / float(x1 - x0)
                y = y0 + t * (y1 - y0)
                break
        out.append(round(code_to_lstar(y) - code_to_lstar(c), 2))
    return out
