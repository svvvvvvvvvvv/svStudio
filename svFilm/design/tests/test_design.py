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

def same(a, b, msg=''):
    assert a == b, '%r != %r %s' % (a, b, msg)


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


# ---------------------------------------------------------------- LR：以人为本
# 钉住 2026-10-29 补的四件事（H2 降级链 / L1 脸是约束 / H3 两道预算 / H4 光位）

def _lr_rec(**kw):
    """① 的一份测量（够 LR 那几步用）。默认：脸在区内、余量 0、不爆、顺光。"""
    base = dict(img=dict(p1=5.0, p5=6.0, p25=17.0, p50=45.0, p75=77.0,
                         p95=96.0, p99=99.9, clip_pct=0.2, contrast=90.0),
                skin=dict(L=70.0, C=18.0, h=55.0, rgb=[0.6, 0.45, 0.34]),
                headroom_ev=0.0, light_ratio=1.5,
                face_w_native=120.0, face_mask_share=0.03)
    base.update(kw)
    return base


def test_lookat_face():
    from design.lr import pick_lookat
    la = pick_lookat(_lr_rec())
    eq_str = la.kind
    ok(eq_str == 'face' and la.source == 'skin.L', '有脸够大应看脸：%s' % la)


def test_lookat_small_face_degrades_with_reason():
    """脸太小 ⇒ 降级整幅，并且**写明人读数没接**（不假装有 person 档）。"""
    from design.lr import pick_lookat
    la = pick_lookat(_lr_rec(face_w_native=12.0))
    ok(la.kind == 'matrix', '脸太小应降级整幅：%s' % la)
    ok('人的读数测量层还没接' in la.why, '降级必须写理由：%s' % la.why)


def test_lookat_no_face_gives_up():
    """没脸 ⇒ 整幅且**弃权**（整幅落点没有不变量），不许静默算一个落点。"""
    from design.lr import pick_lookat, solve_exposure
    rec = _lr_rec(skin=dict(L=None, C=None, h=None), face_w_native=0.0,
                  face_mask_share=0.0)
    la = pick_lookat(rec)
    ok(la.kind == 'matrix', la)
    ex = solve_exposure(rec)
    eq(ex.ev, 0.0, 1e-9)
    ok('弃权' in ex.why, '弃权要写出来：%s' % ex.why)


def test_exposure_face_in_zone_does_nothing():
    """★ 脸在 55~85 内 ⇒ 0 档。这是「脸是约束不是目标」（§28 L1）。"""
    from design.lr import solve_exposure
    for L in (56.0, 70.0, 84.0):
        ex = solve_exposure(_lr_rec(skin=dict(L=L, C=18.0, h=55.0)))
        eq(ex.ev, 0.0, 1e-9)
        eq(ex.want_ev, 0.0, 1e-9)


def test_exposure_low_face_targets_zone_not_72():
    """脸低于下界 ⇒ 推到「下界 + 落点容差」，**不是推到 72**，也不是贴边。"""
    from design.lr import LAND_TOL_L, solve_exposure
    ex = solve_exposure(_lr_rec(skin=dict(L=45.0, C=18.0, h=55.0),
                                headroom_ev=5.0))
    ok(ex.want_ev > 0.2, 'L45 应要提亮：%s' % ex.want_ev)
    newL = 45.0 + ex.want_ev * eval_slope(45.0)
    ok(abs(newL - (55.0 + LAND_TOL_L)) < 0.6,
       '应推到 %.1f（下界+容差），实际 %.1f' % (55.0 + LAND_TOL_L, newL))
    ok(newL < 65.0, '不该往 72 追')


def test_land_tolerance_covers_measured_error():
    """★ 落点容差必须 ≥ 实测落点误差 —— 否则贴边、误差一来就掉出区。

    2026-10-29 真跑 n=5：|残差| 中位 0.92 / P90 2.30 / 最大 **2.88**
    （DSCF1627：给 +0.88 档，模型 56.5，实测 54.2 ⇒ 差 0.8 没进区）。
    """
    from design.lr import LAND_TOL_L
    ok(LAND_TOL_L >= 2.88, '容差 %.1f < 实测最大误差 2.88 ⇒ 会重犯贴边的错' % LAND_TOL_L)
    ok(LAND_TOL_L <= 6.0, '容差 %.1f 太大，会把脸一路推到区间中上段' % LAND_TOL_L)


def test_exposure_backlit_uses_its_own_zone():
    """A 方案（2026-10-29）：逆光**换一套区间**（45~70），不额外往亮推。

    依据：目录卡逆光片的脸 L 中位只有 50.4（P10~P90 42~58），
    比顺光的 77.8 暗 27 格 —— 逆光不是"把脸提亮"，是"脸暗、彩度高、留轮廓光"。
    """
    from design.lr import LAND_TOL_L, T4_BACKLIT, solve_exposure
    # 逆光：脸 46.8 已在 45~70 内 ⇒ 不动
    ex = solve_exposure(_lr_rec(skin=dict(L=46.8, C=18.0, h=55.0)), light='backlit')
    eq(ex.want_ev, 0.0, 1e-9)
    # 逆光：脸 43.7 低于 45 ⇒ 只推到 45+容差
    ex2 = solve_exposure(_lr_rec(skin=dict(L=43.7, C=18.0, h=55.0)), light='backlit')
    L2 = 43.7 + ex2.want_ev * eval_slope(43.7)
    ok(abs(L2 - (T4_BACKLIT['L_lo'] + LAND_TOL_L)) < 0.6, '逆光目标 %.1f' % L2)
    # 同一张脸按顺光判，就要提到 58.5 —— 两套区间真的不同
    ex3 = solve_exposure(_lr_rec(skin=dict(L=43.7, C=18.0, h=55.0)), light='frontlit')
    ok(ex3.want_ev > ex2.want_ev + 0.3, '顺光该提更多：%.2f vs %.2f' % (ex3.want_ev, ex2.want_ev))


def test_exposure_headroom_is_not_a_cap():
    """★★ 引擎余量**不再**充当曝光上限（§19 分工：曝光归 LR）。

    旧行为是"两道预算取小"（余量 0.2 / 白端 0.1 ⇒ 只给 0.1，还记欠账）。
    新行为：脸要 +0.8 就给 +0.8；引擎余量只作记录，越过白平台的那截
    **写明交给 ③ Highlights 收白**，不在这里砍。
    """
    from design.lr import solve_exposure
    rec = _lr_rec(skin=dict(L=45.0, C=18.0, h=55.0), headroom_ev=0.2,
                  img=dict(p1=5.0, p5=6.0, p25=17.0, p50=45.0, p75=77.0,
                           p95=97.5, p99=99.9, clip_pct=0.2, contrast=90.0))
    ex = solve_exposure(rec)
    cap_w = (100.0 - 97.5) / eval_slope(97.5)
    ok(abs(ex.cap_white - cap_w) < 1e-6, 'p95 到白平台的距离算错：%s' % ex.cap_white)
    eq(ex.cap_headroom, 0.2)                       # 只记录
    eq(ex.ev, ex.want_ev)                          # 给满，不被两道预算砍
    ok(ex.ev > 0.5, '脸 L45 至少要 +0.5 档：%.3f' % ex.ev)
    ok(ex.ev > ex.cap_white, '本例白端近 ⇒ 必然越过白平台')
    ok('收白' in ex.why and '③' in ex.why, '越过白平台必须点明交给 ③：%s' % ex.why)
    eq(ex.debt_ev, 0.0)                            # 欠账不在 ② 记，在 ③ 收白收不回时记


def test_exposure_overruns_white_but_hands_over():
    """曝光可以越过白平台 —— 但**必须写明这一截交给 ③**；没越过就不许瞎报收白。"""
    from design.lr import solve_exposure
    for p95 in (98.0, 97.0, 90.0, 60.0):
        rec = _lr_rec(skin=dict(L=40.0, C=18.0, h=55.0), headroom_ev=0.0,
                      img=dict(p1=5.0, p5=6.0, p25=17.0, p50=45.0, p75=77.0,
                               p95=p95, p99=99.9, clip_pct=0.2, contrast=90.0))
        ex = solve_exposure(rec)
        eq(ex.ev, ex.want_ev)                      # 任何 p95 都不砍曝光
        over = ex.ev - ex.cap_white
        if over > 1e-9:
            ok('收白' in ex.why, 'p95=%.1f 越过 %.2f 档 ⇒ 要点明收白：%s'
               % (p95, over, ex.why))
        else:
            ok('收白' not in ex.why, 'p95=%.1f 没越过（余 %.2f 档）⇒ 不该报收白：%s'
               % (p95, -over, ex.why))


def test_light_state_comes_from_person():
    """★ 光位**由人给**，不由测量猜（两种自动判据都出过反例，10-29）。

    反例1：脸÷背景中位 ⇒ 1608（真逆光）被算成顺光（逆光时背景中位落在暗部）。
    反例2：脸 vs 背景亮部 p90 ⇒ 1307（脸被太阳直照）被算成逆光（画面里有天空）。
    """
    from design.lr import light_state
    rec = dict(skin=dict(L=47.0), light_ratio=0.9, face_dark_share40=0.56,
               bg=dict(L=29.0, L_p90=71.5, L_p10=9.0))
    same(light_state(dict(rec)), 'unknown')        # 不给人 ⇒ 不猜
    same(light_state(dict(rec), given='backlit'), 'backlit')
    same(light_state(dict(rec), given='frontlit'), 'frontlit')
    # 明确要求自动时才用近似判据，并把旁证记下来
    r2 = dict(rec)
    same(light_state(r2, given='auto'), 'backlit')
    ok(abs(r2['light_gap'] - 24.5) < 0.3, '要记旁证 gap：%s' % r2.get('light_gap'))
    # 双区统计的反例回归：脸亮、脸内几乎没有暗部 ⇒ 顺光（1307 那种）
    r3 = dict(rec, skin=dict(L=73.5), face_dark_share40=0.17,
              bg=dict(L=61.8, L_p90=90.8, L_p10=7.7))
    same(light_state(r3, given='auto'), 'unknown')   # 中间地带：宁可 unknown
    r4 = dict(rec, skin=dict(L=78.8), face_dark_share40=0.10,
              bg=dict(L=56.8, L_p90=87.0, L_p10=7.0))
    same(light_state(r4, given='auto'), 'frontlit')


def test_backlit_note():
    """逆光：理由里要写明"提完必须预备收白"（逆光 = 提曝光 + 收白 两步）。"""
    from design.lr import solve_exposure
    # 逆光区 45~70：脸 43 出区 ⇒ 会提；提完若越过白平台就交给 ③
    ex = solve_exposure(_lr_rec(skin=dict(L=43.0, C=18.0, h=55.0),
                                headroom_ev=0.0), light='backlit')
    ok('逆光' in ex.why or '收白' in ex.why, '逆光说明缺：%s' % ex.why)


def test_after_ev_hue_invariant():
    """曝光是线性增益 ⇒ 色相不动、L 按局部斜率走（可推，不是拟合）。"""
    from design.expose import after_ev
    rec = _lr_rec(skin=dict(L=50.0, C=18.0, h=55.0))
    p = after_ev(rec, 0.5)
    eq(p['pred']['skin']['h'], 55.0, 1e-9)
    eq(p['pred']['skin']['L'], 50.0 + 0.5 * eval_slope(50.0), 1e-6)


def test_after_ev_clip_lower_bound():
    """分位被推出 100 ⇒ 爆白**下界**至少那么多（不等式，不是模型）。"""
    from design.expose import after_ev
    rec = _lr_rec(img=dict(p1=5.0, p5=6.0, p25=17.0, p50=45.0, p75=77.0,
                           p95=98.0, p99=99.9, clip_pct=0.1, contrast=90.0))
    p = after_ev(rec, 0.2)
    ok(p['clip_lb'] >= 5.0, 'p95 推过 100 应给 ≥5%% 的下界：%s' % p['clip_lb'])
    q = after_ev(rec, 0.0)
    ok(q['clip_lb'] <= 0.11, '不推就不该编出爆白：%s' % q['clip_lb'])


def test_after_ev_budget():
    """超引擎余量 ⇒ over_ev 记出来，并要求 ③ 验 clip。"""
    from design.expose import after_ev
    rec = _lr_rec(headroom_ev=0.0)
    p = after_ev(rec, 0.4)
    ok(not p['in_budget'] and abs(p['over_ev'] - 0.4) < 1e-6, p)
    ok(any('验 clip' in n for n in p['notes']), '要写明待验收：%s' % p['notes'])


def test_highlights_judged_after_exposure():
    """收白要在 ①'（提完曝光之后）上判 —— 不能拿 ① 的旧数先收。"""
    from design.expose import after_ev
    from design.lr import solve_highlights
    rec = _lr_rec(img=dict(p1=5.0, p5=6.0, p25=17.0, p50=45.0, p75=77.0,
                           p95=97.8, p99=99.9, clip_pct=0.1, contrast=90.0),
                  headroom_ev=0.0)
    quiet = after_ev(rec, 0.0)
    hv0, _ = solve_highlights(quiet)
    eq(hv0, 0.0, 1e-9)                      # 不提就不该压
    loud = after_ev(rec, 0.5)               # 提了 ⇒ 上端被推爆
    hv1, note = solve_highlights(loud)
    ok(hv1 < 0, '提爆了就该收白：%s' % hv1)
    ok('对分' in (note or ''), '要写明这是搜索起点、不是算出来的幅度：%s' % note)
    ok('脸' in (note or ''), '要写明停止条件是脸：%s' % note)
    ok(loud['white_over_L'] > 0, '要量出上端过了多少 L*：%s' % loud)


def test_next_highlights_ladder():
    """③ 的阶梯：不达标 ⇒ 加大；压过头 ⇒ 回退；吃到尽头 ⇒ 接受（不回引擎）。"""
    from design.lr import HL_END, HL_SEARCH_MID, next_highlights
    # 仍超 ⇒ 往尽头走
    v, why = next_highlights(HL_SEARCH_MID, 8.0)
    ok(v is not None and v < HL_SEARCH_MID, '压得不够应加大：%s %s' % (v, why))
    # 一路对分下去：−50 → −75 → −87.5 → −100
    v2, why2 = next_highlights(-75.0, 8.0)
    ok(v2 is not None and v2 < -75.0, '继续加大：%s %s' % (v2, why2))
    v2b, _ = next_highlights(v2, 8.0)
    eq(v2b, HL_END)
    # 尽头仍不达标 ⇒ **接受背景过曝**，不许再说"回引擎"
    v3, why3 = next_highlights(HL_END, 8.0)
    ok(v3 is None, '尽头就不该再动：%s' % v3)
    ok('接受' in why3 and '回引擎' not in why3, '尽头应接受背景爆，不再回引擎：%s' % why3)
    # 压过头（没脸读数时的降级判据）⇒ 回退
    v4, why4 = next_highlights(HL_SEARCH_MID, 0.0, p95_after=88.0, p95_before=95.0)
    ok(v4 is not None and abs(v4) < abs(HL_SEARCH_MID), '压过头应回退：%s' % v4)
    # 达标 ⇒ 不动
    v5, _ = next_highlights(HL_SEARCH_MID, 0.3, p95_after=95.0, p95_before=95.0)
    ok(v5 is None, '达标就不该再动：%s' % v5)


def test_next_highlights_face_has_priority():
    """★★ 脸优先：收白把脸压暗超过 1 L* ⇒ **回退**，哪怕背景还爆着。

    回引擎那条出口已删：回引擎只会整幅压暗 = 拿脸换背景，与优先级相反。
    """
    from design.lr import HL_SEARCH_MID, next_highlights
    # 脸掉 2.4 L*，背景还爆 ⇒ 回退（不是"停着不动"，不动等于默认接受脸已经暗了）
    v, why = next_highlights(HL_SEARCH_MID, 9.0, face_after=62.0, face_before=64.4)
    ok(v is not None and abs(v) < abs(HL_SEARCH_MID), '脸掉了应回退：%s' % v)
    ok('脸' in why and '回退' in why, '要说清是脸的原因：%s' % why)
    # 脸只掉 0.4 L*（噪声内）⇒ 可以继续
    v2, why2 = next_highlights(HL_SEARCH_MID, 9.0, face_after=64.0, face_before=64.4)
    ok(v2 is not None and v2 < HL_SEARCH_MID, '脸没掉就能继续：%s %s' % (v2, why2))
    # 回退到最小步长以内 ⇒ 直接归零
    v3, why3 = next_highlights(-10.0, 9.0, face_after=60.0, face_before=64.0)
    eq(v3, 0.0)
    ok('归零' in why3, why3)


def test_highlights_face_blown_is_not_background_debt():
    """脸贴顶 = 主体丢了（单列）；背景爆 = 取舍（可接受）。别混。"""
    from design.expose import after_ev
    from design.lr import solve_highlights
    rec = _lr_rec(skin=dict(L=99.4, C=10.0, h=55.0), headroom_ev=0.0,
                  img=dict(p1=8.0, p5=12.0, p25=30.0, p50=60.0, p75=85.0,
                           p95=99.0, p99=99.9, clip_pct=6.0, contrast=87.0))
    _, note = solve_highlights(after_ev(rec, 0.0))
    ok('主体丢了' in (note or ''), '脸贴顶要单列：%s' % note)
    rec2 = _lr_rec(skin=dict(L=70.0, C=12.0, h=55.0), headroom_ev=0.0,
                   img=dict(p1=8.0, p5=12.0, p25=30.0, p50=60.0, p75=85.0,
                            p95=99.0, p99=99.9, clip_pct=6.0, contrast=87.0))
    _, note2 = solve_highlights(after_ev(rec2, 0.0))
    ok('主体丢了' not in (note2 or ''), '脸没顶就不该说主体丢：%s' % note2)
    ok('可接受' in (note2 or ''), '背景爆应写成取舍：%s' % note2)


def test_curve_never_fixes_clip_in_lr_chain():
    """★ §33：爆白只归 ③ Highlights，曲线**不许**再压一次肩部。

    旧实现里这个分支是"碰巧没触发"（`after_ev` 的 pred 里没有 `clip_pct`），
    那是碰巧对；现在显式关掉，防止有人"修好"它之后两处同时压。
    """
    from design.lr import solve_curve
    rec = _lr_rec(img=dict(p1=12.0, p5=20.0, p25=40.0, p50=60.0, p75=80.0,
                           p95=99.5, p99=99.9, clip_pct=18.0, contrast=79.5))
    pts, info = solve_curve(rec, allow_clip=False)
    ok(pts is None, '爆白时曲线不该动（交给 ③）：%s' % info)
    pts2, info2 = solve_curve(rec, allow_clip=True)      # 独立工具（cli curve）仍可压
    ok(pts2 is not None and '压白端' in str(info2.get('fixed')), info2)


def test_solve_one_order():
    """整条链 §31 ②→③：脸低 ⇒ ② 把曝光给满；③ 在 ①' 上判、收白并记欠账。"""
    from design.lr import solve_one
    rec = _lr_rec(skin=dict(L=45.0, C=18.0, h=55.0), headroom_ev=0.0)
    plan = solve_one(rec, light='frontlit', log=lambda *_: None)
    ok(plan.exposure_ev > 0.5, '脸 L45 就该给（余量 0 不是理由）：%s' % plan.exposure_ev)
    ok('收白' in plan.notes.get('exposure', ''),
       '② 越过白平台要交代给 ③：%s' % plan.notes.get('exposure'))
    ok(plan.highlights < 0.0, '② 给满后上端必爆 ⇒ ③ 必须收白：%s' % plan.highlights)
    ok('highlights' in plan.notes, '③ 的理由要落进 notes：%s' % list(plan.notes))
    ok('highlight_verify' in plan.notes, '③ 的验收量要写清：%s' % list(plan.notes))
    ok('顺光' in plan.notes.get('light', '') or 'frontlit' in plan.notes.get('lookat', ''),
       '光位（或旁证）要落进 notes：%s' % plan.notes)


# ---------------------------------------------------------------- 余量的可信性（10-29 实测钉住）

def _disp_with_ceiling(top_l, n=4000):
    """造一张**上端被硬切在 top_l** 的灰阶图（模拟 scan_white_level < 1）。"""
    import numpy as np
    # 显示码从 0 线性排到"天花对应的码"，全部是中性灰 ⇒ L* 最高就是 top_l
    y = ((top_l + 16.0) / 116.0) ** 3
    u = 1.055 * (y ** (1 / 2.4)) - 0.055 if y > 0.0031308 else y * 12.92
    ramp = np.linspace(0.0, u, n)
    return np.repeat(ramp[:, None], 3, axis=1).reshape(n, 1, 3)


def test_headroom_report_marks_cut_ceiling_invalid():
    """★★★ 降白端换来的"余量"必须被判为不可信 —— 它是恒等式，不是收益。

    2026-10-29 实测：scan_white_level 1.0→0.95 ⇒ 天花 L*100→95.5，
    报出来的余量 10/10 吻合 −log2(Y(天花))，差的只是取整；
    代价是中位 4.65% 的像素被从 ≥96 压到 95.5 以下（直方图 96+ 段精确归零）。
    """
    from design.flow import headroom_report
    bad = headroom_report(_disp_with_ceiling(95.5), white_level=0.95)
    ok(bad['valid'] is False, '天花 95.5 应判不可信：%s' % bad)
    ok(bad['ceiling_L'] < 96.0, '天花应量到 95.5 附近：%s' % bad['ceiling_L'])
    # 恒等式：报的余量 ≈ 砍掉的高度
    ok(abs(bad['headroom_ev'] - bad['tautology_ev']) <= 0.06,
       '余量应≈砍掉的高度（恒等式）：ev=%s 预言=%s' % (bad['headroom_ev'], bad['tautology_ev']))
    ok('scan_white_level' in bad['why'], '话要说清是谁砍的：%s' % bad['why'])


def test_headroom_report_accepts_full_ceiling():
    """天花在 100 附近 ⇒ 余量可信，且恒等式预言值应为 0（没被砍）。"""
    from design.flow import headroom_report
    good = headroom_report(_disp_with_ceiling(99.7), white_level=1.0)
    ok(good['valid'] is True, '天花 99.7 应判可信：%s' % good)
    ok(good['tautology_ev'] < 0.02, '没砍过 ⇒ 预言余量应≈0：%s' % good['tautology_ev'])


def test_headroom_report_low_key_is_not_cut():
    """★ 低反差图不是"被砍"：白端是 1.0 时天花低只说明这张上端到不了顶。

    实测反例：DSCF1726 白端=1.0 但天花只有 L*98.4、DSCF1289 99.25、DSCF1608 99.35。
    若拿"天花 <99.5 就算砍"当判据，这三张会全成误报。
    反之白端一旦 <1，余量就被那条线限住（与图像内容无关），所以只按白端判。
    """
    from design.flow import _code_to_L, headroom_report
    ok(_code_to_L(1.0) > 99.9, '白端 1.0 的预期天花应≈100：%s' % _code_to_L(1.0))
    ok(_code_to_L(0.95) < 96.0, '白端 0.95 的预期天花应≈95.5：%s' % _code_to_L(0.95))
    low = headroom_report(_disp_with_ceiling(93.0), white_level=1.0)
    ok(low['valid'] is True, '白端=1.0 的暗图不该判被砍：%s' % low['why'])
    # 同一张图，白端设成 0.95 就必须判不可信（说明问题在锯子，不在图）
    same = headroom_report(_disp_with_ceiling(93.0), white_level=0.95)
    ok(same['valid'] is False, '白端=0.95 时余量必须判不可信：%s' % same['why'])


def test_exposure_invalid_headroom_is_only_recorded():
    """★ 余量不可信 **不再拦 LR 曝光** —— 那是引擎的事（§21 中灰归一就是余量机制）。

    `headroom_valid=False` 只说明"引擎那边天花被砍了"，与"脸该不该提"无关。
    旧行为是拒算 + 让脸留在区外，等于拿引擎的账卡死 LR 的活。
    """
    from design.lr import solve_exposure
    rec = _lr_rec(skin=dict(L=45.0, C=18.0, h=55.0), headroom_ev=0.15,
                  headroom_valid=False, headroom_why='天花只有 L*95.5',
                  img=dict(p1=5.0, p5=6.0, p25=17.0, p50=45.0, p75=77.0,
                           p95=92.0, p99=95.0, clip_pct=0.0, contrast=90.0))
    ex = solve_exposure(rec)
    ok(ex.ev > 0.5, '余量不可信不该让脸提不起来：%s' % ex.ev)
    eq(ex.ev, ex.want_ev)
    ok('拒算' not in ex.why, '不该再拒算：%s' % ex.why)
    eq(ex.cap_headroom, 0.15)                  # 读数照样记下来（报告要用）



# ---------------------------------------------------------------- ③ 的验收与目检（G1/G2/G3）

def _p3(ev=0.0, curve=None):
    class _P:
        exposure_ev = ev
    _P.curve = curve
    return _P()


def test_verify_face_first():
    """硬门只看脸与"有没有把脸改坏"；背景爆只进软门。"""
    from design.review import verify
    r1 = dict(img=dict(p1=5.0, clip_pct=0.2), skin=dict(L=62.0, C=14.0))
    # ③ 脸没动、背景更爆 ⇒ 硬门全过，clip 落软门
    v = verify(r1, dict(img=dict(p1=5.0, clip_pct=9.0), skin=dict(L=62.4, C=14.0)), _p3())
    ok(v['ok'], '脸没动就该过硬门：%s' % v['hard'])
    ok(any((not x[1]) for x in v['soft']), '背景爆要进软门：%s' % v['soft'])
    # ③ 把脸压暗 2 L* ⇒ 硬门破（S1 残差）
    v2 = verify(r1, dict(img=dict(p1=5.0, clip_pct=1.0), skin=dict(L=60.0, C=14.0)), _p3())
    ok(not v2['ok'], '把脸压暗必须破硬门')
    ok(any(x[0].startswith('S1') and not x[1] for x in v2['hard']), v2['hard'])
    # 脸出区 ⇒ T4 破
    v3 = verify(r1, dict(img=dict(p1=5.0, clip_pct=1.0), skin=dict(L=50.0, C=14.0)), _p3())
    ok(any(x[0].startswith('T4') and not x[1] for x in v3['hard']), v3['hard'])


def test_verify_exposure_move_is_not_a_violation():
    """曝光本来就该改脸的亮度：位移等于曝光该带来的量 ⇒ 不算违规。"""
    from design.review import verify
    r1 = dict(img=dict(p1=5.0, clip_pct=0.2), skin=dict(L=50.0, C=14.0))
    ev = 0.8
    L3 = 50.0 + ev * eval_slope(50.0)
    v = verify(r1, dict(img=dict(p1=5.0, clip_pct=0.3), skin=dict(L=L3, C=14.0)), _p3(ev))
    ok(all(x[1] for x in v['hard']), '曝光带来的位移不该破门：%s' % v['hard'])


def test_verify_does_not_punish_engine_leftovers():
    """① 本身就堵黑/白点偏 ⇒ 只要求 ③ **不比 ① 更差**（不许拿引擎的账罚 LR）。"""
    from design.review import verify
    r1 = dict(img=dict(p1=0.8, clip_pct=0.2), skin=dict(L=60.0, C=14.0),
              white_point=dict(a=3.0, b=1.0))
    ok3 = dict(img=dict(p1=0.9, clip_pct=0.2), skin=dict(L=60.0, C=14.0),
               white_point=dict(a=3.2, b=1.0))
    v = verify(r1, ok3, _p3())
    ok(all(x[1] for x in v['hard']), '① 的旧账不该算在 ③ 头上：%s' % v['hard'])
    ok(any(x[0].startswith('T3') and x[1] for x in v['hard']), '白点要看"相对位移"')
    # 但 ③ 把 ① 的窟窿捅更大就要罚
    worse = dict(img=dict(p1=0.2, clip_pct=0.2), skin=dict(L=60.0, C=14.0),
                 white_point=dict(a=3.0, b=1.0))
    v2 = verify(r1, worse, _p3())
    ok(any(x[0].startswith('T2') and not x[1] for x in v2['hard']), v2['hard'])
    # 白点被 LR 挪走 > 2 也要罚
    moved = dict(img=dict(p1=0.9, clip_pct=0.2), skin=dict(L=60.0, C=14.0),
                 white_point=dict(a=6.0, b=1.0))
    v3 = verify(r1, moved, _p3())
    ok(any(x[0].startswith('T3') and not x[1] for x in v3['hard']), v3['hard'])


def test_match_sheet_is_a_decision_page():
    """选卡页要能让人做决定：像哪套 / 缺口关不关得掉 / 每张卡的代价，三样缺一不可。"""
    import os as _os
    import tempfile
    import types
    from design.review import match_sheet

    def _card(name, stock, paper, rms, amount, fmt):
        return types.SimpleNamespace(
            name=name, stock=stock, paper=paper,
            texture=dict(rms_granularity=(rms, rms, rms)),
            style=dict(amount=amount, film_format_mm=fmt, diffusion='off'),
            safety=dict(p95_max=96.0), label=lambda: '%s（%s + %s）' % (name, stock, paper))

    def _choice(c, score):
        hit = types.SimpleNamespace(card=c, score=score, contrib=dict(shoulder=0.1),
                                   top=lambda k=2: [('shoulder', 0.1)])
        return types.SimpleNamespace(card=c, hit=hit)

    c1 = _card('卡A', 'gold_200', 'endura', 10.0, 1.2, 36)
    c2 = _card('卡B', 'portra_400', 'endura', 21.0, 1.3, 35)
    c3 = _card('卡C', 'portra_400', 'endura', 9.0, 0.65, 70)
    offer = types.SimpleNamespace(choices=[_choice(c1, 0.14), _choice(c2, 0.0), _choice(c3, 0.0)],
                                  new_card_candidates=['vision3_250d'])
    ref = types.SimpleNamespace(label=lambda: '鹿井/001', n=18, diff={'L': 8.0, 'C': -12.4})
    hit = types.SimpleNamespace(score=2.82, ref=ref, diff=ref.diff)

    d = tempfile.mkdtemp()
    p = _os.path.join(d, '选卡.html')
    match_sheet('测批', dict(L=77.3, C=8.5, h=27.9, p50=59.0, p95=96.6), [hit], offer,
                gap_rows=['C 缺口 +12.4 ⇒ 不可达'], gap_verdict='换卷/换纸', out_html=p,
                n_img=9, caliber='abc123')
    html = open(p, encoding='utf-8').read()
    ok('<table' not in html, '不许用 table')
    ok('这批画像' in html and '77.3' in html, '要给出这批画像')
    ok('最像哪一套' in html and '鹿井/001' in html, '要给出最像哪套')
    ok('缺口' in html, '要给出缺口可达性')
    ok('卡B' in html and '卡C' in html, '三张卡都要在')
    ok('同卷同纸' in html, '同卷同纸必须点出来（否则分数会骗人）')
    ok('rms 21.0→9.0' in html or 'rms 9.0→21.0' in html, '要把真正的差别摆出来：质感档')
    ok('--card' in html, '要告诉人怎么把它选回去')
    ok('建议' not in html and '推荐' not in html, '页面上不许替他做决定')


def test_point_color_backlit_adds_chroma():
    """逆光肤色发闷 ⇒ 点颜色给一点彩度（风格剂量，不是靶）。"""
    from design.lr import PC_BACKLIT_SAT, solve_point_color
    rec = dict(skin=dict(C=20.0, L=60.0, h=55.0))
    a = solve_point_color(rec, backlit=False)
    b = solve_point_color(rec, backlit=True)
    ok(abs((b['scale'] - a['scale']) - PC_BACKLIT_SAT) < 1e-6,
       '逆光应多加 %.2f：%s vs %s' % (PC_BACKLIT_SAT, a, b))
    ok(abs(a['lum']) < 1e-9 and abs(b['lum']) < 1e-9, '没有脸差额就不动亮度')


def test_point_lum_fills_face_deficit():
    """⑥ 肤色亮度：补脸的最后一段，按实测灵敏度给，推满就记欠账。"""
    from design.lr import PC_LUM_L_PER_UNIT, PC_LUM_MAX, next_point_lum
    v, why = next_point_lum(0.0, 50.0, 58.5)
    ok(v is not None and 0 < v <= PC_LUM_MAX, '差 8.5 格应给一档：%s %s' % (v, why))
    # ★ 单步封顶（灵敏度随起点变，单点线性会过冲）
    from design.lr import PC_LUM_STEP
    ok(abs(v - min(PC_LUM_MAX, PC_LUM_STEP, 8.5 / PC_LUM_L_PER_UNIT)) < 1e-3, v)
    ok(v <= PC_LUM_STEP + 1e-9, '第一步不许超过单步上限：%s' % v)
    ok(next_point_lum(0.5, 60.0, 58.5)[0] is None, '到位就不该动')
    v3, why3 = next_point_lum(PC_LUM_MAX, 50.0, 58.5)
    ok(v3 is None and '欠账' in why3, '推满仍差 ⇒ 记欠账：%s %s' % (v3, why3))
    ok(next_point_lum(0.0, None, 58.5)[0] is None, '没脸读数不动')


def test_contact_sheet_layout():
    """目检页：一图一行、占满宽、不放 <table>。"""
    import os as _os
    import tempfile
    from design.review import contact_sheet
    d = tempfile.mkdtemp()
    p = _os.path.join(d, '目检.html')
    rows = [dict(stem='A', final_jpg='preview/A_3.jpg', engine_jpg='preview/A_1.jpg',
                 values='曝光 +0.2 ｜ 收白 -50', note='脸 L 62.0 → 62.3 ｜ clip 5% → 1%',
                 verdict='✓ 全过'),
            dict(stem='B', final_jpg='preview/B_3.jpg', engine_jpg=None,
                 values='曝光 +0.0', note='', verdict='硬门破 1：T4 脸L')]
    contact_sheet(rows, p, title='测试', sub='副标题')
    html = open(p, encoding='utf-8').read()
    ok('<table' not in html, '不许用 table（会撑破手机屏）')
    ok(html.count('<figure>') == 2, '一张图一节')
    ok('preview/A_3.jpg' in html and 'preview/B_3.jpg' in html, '图要引到')
    ok('看① 引擎底片' in html, '① 底片要能展开看')
    ok(html.count('<img') == 3, '两张成片 + 一张①')


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
