# -*- coding: utf-8 -*-
"""单元测试：把**纯函数**的判据钉住（不碰渲染、不碰 LR）。

为什么要它（自审 P3-5）：
  · 不变量判定、曲线插值、可行性判断都是纯函数，最容易在重构中悄悄坏；
  · 它们又恰好是"错了会让整套结论偏掉"的那一层。

跑：
    python -m pytest design/tests -q        （若装了 pytest）
    python design/tests/test_design.py      （直接跑也行）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.curve import _fit_points, _pchip, apply_curve_rgb, displacement, plan_curve
from design.invariants import T0, check_T
from design.lr import eval_slope, ev_for_dL
from design.feasibility import gap_table, verdict


# ---------------------------------------------------------------- 基本断言

def eq(a, b, tol=1e-9):
    assert abs(a - b) <= tol, '%r != %r (tol %r)' % (a, b, tol)


def ok(cond, msg):
    assert cond, msg


# ---------------------------------------------------------------- CIELAB 斜率

def test_lab_slope():
    """dL*/dEV = 0.2310·(L+16)，由 CIELAB 定义推出。"""
    eq(eval_slope(50), 0.2310 * 66, 0.01)
    eq(eval_slope(70), 0.2310 * 86, 0.01)
    # 提 10 L* 在 L=70 处要 ~0.50 档
    eq(ev_for_dL(10, 70), 10 / (0.2310 * 86), 1e-6)
    ok(abs(ev_for_dL(10, 70) - 0.50) < 0.02, 'L70 提 10 L* 应约 0.50 档，实得 %.3f'
       % ev_for_dL(10, 70))


# ---------------------------------------------------------------- PCHIP

def test_pchip_monotone():
    """单调数据上不许过冲（普通三次样条会）。"""
    xs = [0.0, 10.0, 20.0, 30.0]
    ys = [0.0, 1.0, 1.0, 2.0]
    q = _pchip(xs, ys, [i * 0.5 for i in range(61)])
    for i in range(1, len(q)):
        ok(q[i] >= q[i - 1] - 1e-9, 'PCHIP 在单调数据上出现下降：%.4f -> %.4f'
           % (q[i - 1], q[i]))


def test_fit_points_contains_anchors():
    """★ 16 点采样必须**包含锚点位置**：均匀网格会漏掉高光锚点，修正在 LUT 里被抹掉。"""
    pairs = [(0, 0), (92, 92), (187, 187), (230, 230), (250, 246), (254, 246), (255, 255)]
    pts = _fit_points(pairs)
    eq(len(pts), 16)
    xs = [p[0] for p in pts]
    for x in (92, 187, 230, 250, 254):
        ok(x in xs, '锚点 %d 没进 16 点网格：%s' % (x, xs))
    ys = [p[1] for p in pts]
    for i in range(1, len(ys)):
        ok(ys[i] >= ys[i - 1], '曲线不单调')


def test_plan_curve_budget():
    """超预算的位移要被钳到预算内，并报出"给不到的部分"。"""
    src = {'5': 36.0, '25': 73.0, '50': 90.0, '75': 98.0, '95': 99.5}
    dst = dict(src)
    dst['95'] = 80.0                      # 要压 19.5 L*，预算只有 3
    pts, info = plan_curve(src, dst, budget=3.0)
    ok(abs(info['max_abs_delta']) <= 3.0 + 1e-6,
       '位移超预算没被钳：%s' % info['max_abs_delta'])
    ok(info['need_unmet'].get('95'), '应报出 95 锚点给不到')


def test_apply_curve_rgb_identity():
    """空曲线 = 恒等。"""
    rgb = (0.792, 0.698, 0.686)
    out = apply_curve_rgb(rgb, [])
    for a, b in zip(rgb, out):
        eq(a, b, 1e-9)


def test_displacement_zero():
    """恒等曲线处处位移为 0。"""
    pts = [(i * 17, i * 17) for i in range(16)]
    for d in displacement(pts, [25.0, 50.0, 75.0]):
        ok(abs(d) <= 0.5, '恒等曲线应无位移，实得 %r' % d)


# ---------------------------------------------------------------- 不变量

def _rec(**kw):
    base = dict(img=dict(p1=8.0, p5=8.0, p95=90.0, p99=95.0, clip_pct=0.1,
                         p50=60.0, contrast=80.0),
                white_point=dict(a=0.0, b=1.0),
                skin=dict(L=70.0, C=18.0, h=55.0))
    base.update(kw)
    return base


def test_T0_pass():
    r = check_T(_rec(), stage='engine')
    ok(r.ok, '基准片应通过：%s' % [i.name for i in r.failures()])


def test_T0_highlight():
    bad = _rec(img=dict(p1=8.0, p5=8.0, p95=97.0, p99=99.0, clip_pct=0.1,
                        p50=60.0, contrast=80.0))
    names = [i.name for i in check_T(bad, stage='engine').failures()]
    ok('T0-p95' in names, 'p95=97 应判超：%s' % names)


def test_T0_clip():
    bad = _rec(img=dict(p1=8.0, p5=8.0, p95=90.0, p99=99.0, clip_pct=3.0,
                        p50=60.0, contrast=80.0))
    names = [i.name for i in check_T(bad, stage='engine').failures()]
    ok('T0-clip' in names, 'clip=3%% 应判超：%s' % names)


def test_T0_thresholds_from_masters():
    """阈值是**区间**（来自大师实测），不是靶：p95 上沿 93、clip 0.5、p1 1.0。"""
    eq(T0['p95_max'], 93.0)
    eq(T0['clip_max'], 0.5)
    eq(T0['p1_min'], 1.0)


# ---------------------------------------------------------------- 可行性

def test_feasibility_amount_cap():
    """彩度缺口超出 amount 量程时，不许判"可达"（γ_blue 是脏解，不算数）。"""
    batch = dict(L=70.0, C=8.0, h=55.0, p95=92.0)
    target = dict(L=70.0, C=22.0, h=55.0, p95=92.0)
    rows = gap_table(batch, target, headroom_ev=0.2)
    c = [r for r in rows if r.dim == 'C'][0]
    ok(not c.reachable, 'C 缺口 +14 不该判可达（amount 只够 +1.8）')
    ok('换卷' in verdict(rows) or '换纸' in verdict(rows), '应指向换卷/换纸')


def test_feasibility_hue_ok():
    """色相小幅缺口应可由 γ 关掉（γ 量程是绝对值 0.25~4.0）。"""
    batch = dict(L=70.0, C=18.0, h=55.0, p95=92.0)
    target = dict(L=70.0, C=18.0, h=63.0, p95=92.0)
    rows = gap_table(batch, target, headroom_ev=0.2)
    h = [r for r in rows if r.dim == 'h'][0]
    ok(h.reachable, '色相 +8° 应可达（γ 只需 Δ0.055）')


# ---------------------------------------------------------------- runner

def main():
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print('  ✓ %s' % name)
        except Exception:
            bad += 1
            print('  ✗ %s' % name)
            traceback.print_exc()
    print('\n%d/%d 通过' % (len(tests) - bad, len(tests)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
