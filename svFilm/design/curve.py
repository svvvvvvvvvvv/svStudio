# -*- coding: utf-8 -*-
"""色调曲线：只搬形状、不动 p50，且每个锚点的位移受预算约束（S3）。

为什么要有预算（分工设计 §9.1）：
  曲线是单调 LUT，**什么都能搬**——正因为它太能干，若放任它把介质形状的错也补掉，
  就永远查不出"是哪一层不对"。预算 = 诊断边界。

口径：
  · 锚点用 L*（0..100），曲线内部是 0..255 **sRGB 显示码**；
    L*↔码必须走 sRGB 传输函数（`photometry`），不是 ×2.55
  · p50 不允许动（曝光归 LR 的曝光滑杆，不归曲线）
  · 每锚点 |Δ| ≤ budget（默认 3 L*）

用法：
    from design.curve import plan_curve, lstar_to_code, displacement
    pts, info = plan_curve(src_q, dst_q, budget=3.0)
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .photometry import code_to_l, l_to_code

ANCHOR_Q = (5, 25, 50, 75, 95)


def lstar_to_code(L: float) -> int:
    """L* → sRGB 8-bit 码（**精确**：L*→Y→sRGB 传输函数）。

    2026-10-30 修：原来写 `L*255/100`，等于把 L* 当线性码。L*=50 的码是 119，
    不是 127.5 —— 按旧映射，曲线的每个锚点在显示域里都往右偏了 7~8 码，
    暗部锚点偏差最大（L*=5 → 码 17 而不是 12.75）。
    """
    return max(0, min(255, int(round(l_to_code(L)))))


def code_to_lstar(c: int) -> float:
    """sRGB 8-bit 码 → L*（与 `lstar_to_code` 互逆，同一套 sRGB 定义）。"""
    return max(0.0, min(100.0, code_to_l(c)))


def _pchip(xs, ys, xq):
    """单调三次插值（Fritsch-Carlson），无过冲。不依赖 scipy。

    为什么不用线性：16 点之间线性连会出**阶梯**（相邻斜率突变），
    LR 的 PV2012 曲线是平滑的；而普通三次样条会**过冲**（单调数据上产生反向斜率），
    所以用保单调的 PCHIP。
    """
    n = len(xs)
    if n < 2:
        return [float(ys[0])] * len(xq)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] if h[i] else 0.0 for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0                       # 极值点 => 斜率归零（保单调）
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    out = []
    for x in xq:
        if x <= xs[0]:
            out.append(float(ys[0]))
            continue
        if x >= xs[-1]:
            out.append(float(ys[-1]))
            continue
        i = 0
        while i < n - 2 and x > xs[i + 1]:
            i += 1
        t_ = (x - xs[i]) / h[i] if h[i] else 0.0
        t2, t3 = t_ * t_, t_ * t_ * t_
        out.append((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t_) * h[i] * m[i]
                   + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h[i] * m[i + 1])
    return out


def grid16(anchor_codes) -> List[float]:
    """16 点采样网格：**锚点优先占位**，剩余名额用均匀网格里离已有点最远的补。

    2026-10-30 提取成公共函数（`curve_design` 也要用同一套）：
    均匀 16 点（0,17,34…255）会漏掉 code 250/254 这种高光锚点，
    修正在 LUT 里被抹掉（2026-10-29 实测发现）。
    """
    grid = [0.0, 255.0] + [float(x) for x in anchor_codes if 0.0 < float(x) < 255.0]
    cand = [i * 255.0 / 15.0 for i in range(1, 15)]
    while len(grid) < 16 and cand:
        best = max(cand, key=lambda c: min(abs(c - g) for g in grid))
        grid.append(best)
        cand.remove(best)
    grid = sorted(set(round(x, 6) for x in grid))[:16]
    if grid[0] != 0.0:
        grid = [0.0] + grid
    if grid[-1] != 255.0:
        grid = grid + [255.0]
    return grid


def _fit_points(pairs: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """把 (code_in, code_out) 锚点用**单调三次**插成 16 点（端点锁死、单调）。"""
    xs = [float(p[0]) for p in pairs]
    ys = [float(p[1]) for p in pairs]
    if xs[0] > 0:
        xs = [0.0] + xs
        ys = [0.0] + ys
    if xs[-1] < 255:
        xs = xs + [255.0]
        ys = ys + [255.0]
    ux, uy = [xs[0]], [ys[0]]
    for x, y in zip(xs[1:], ys[1:]):
        if x <= ux[-1]:
            x = ux[-1] + 1e-6
        ux.append(x)
        uy.append(y)
    grid = grid16([x for x in ux if 0 < x < 255])
    q = _pchip(ux, uy, grid)
    pts = [(int(round(g)), int(round(max(0.0, min(255.0, v)))))
           for g, v in zip(grid, q)]
    for i in range(1, len(pts)):              # 取整兜底
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


def apply_curve_rgb(rgb, pts: List[Tuple[int, int]]) -> Tuple[float, float, float]:
    """把曲线当作**逐通道 LUT** 作用在一个 sRGB 取样色（0..1）上。

    为什么这样就够（用于算点颜色的取样色，不是用于出片）：
      · LR 的色调曲线就是一条单调 LUT，作用在编码值上；
      · 我们要的是"过完曲线之后，这个像素在 LR 眼里是什么颜色"，
        逐通道查表在数学上就是这件事（出片还需考虑工作空间，这里不需要）。
    """
    if not pts:
        return tuple(float(v) for v in rgb)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    out = []
    for v in rgb[:3]:
        c = max(0, min(255, int(round(float(v) * 255.0))))
        y = 0
        for j in range(1, len(xs)):
            if c <= xs[j]:
                x0, x1, y0, y1 = xs[j - 1], xs[j], ys[j - 1], ys[j]
                k = 0.0 if x1 == x0 else (c - x0) / float(x1 - x0)
                y = y0 + k * (y1 - y0)
                break
        out.append(max(0.0, min(1.0, y / 255.0)))
    return tuple(out)
