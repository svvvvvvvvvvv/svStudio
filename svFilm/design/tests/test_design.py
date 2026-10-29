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
    """脸低于下界 ⇒ 只推到"刚进区 + 余量"，**不是推到 72**。"""
    from design.lr import EDGE_MARGIN_L, solve_exposure
    ex = solve_exposure(_lr_rec(skin=dict(L=45.0, C=18.0, h=55.0),
                                headroom_ev=5.0))
    ok(ex.want_ev > 0.2, 'L45 应要提亮：%s' % ex.want_ev)
    newL = 45.0 + ex.want_ev * eval_slope(45.0)
    ok(abs(newL - (55.0 + EDGE_MARGIN_L)) < 0.6,
       '提到刚落区附近 %.1f，不该到 72' % newL)


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


def test_light_state():
    """H4 光位：脸/背景比 <1.0 逆光、>1.3 顺光、其它 unknown。"""
    from design.lr import light_state
    ok(light_state(_lr_rec(light_ratio=0.8)) == 'backlit', '应判逆光')
    ok(light_state(_lr_rec(light_ratio=1.5)) == 'frontlit', '应判顺光')
    ok(light_state(_lr_rec(light_ratio=1.1)) == 'unknown', '中间应 unknown')
    ok(light_state(dict(img={}, skin={})) == 'unknown', '没这个量应 unknown')


def test_backlit_note():
    """逆光出区要提亮，且理由里写明"提完必须预备收白"。"""
    from design.lr import solve_exposure
    ex = solve_exposure(_lr_rec(skin=dict(L=48.0, C=18.0, h=55.0),
                                light_ratio=0.8, headroom_ev=1.0))
    ok('逆光' in ex.why and '收白' in ex.why, '逆光说明缺：%s' % ex.why)


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
    ok('clip' in (note or ''), '要写明出片量什么：%s' % note)
    ok(loud['white_over_L'] > 0, '要量出上端过了多少 L*：%s' % loud)


def test_next_highlights_ladder():
    """§32 ③：不达标 ⇒ 加大；压过头 ⇒ 回退；到尽头仍不达标 ⇒ 交给引擎（记欠账）。"""
    from design.lr import HL_END, HL_SEARCH_MID, next_highlights
    # 仍超 ⇒ 往尽头走
    v, why = next_highlights(HL_SEARCH_MID, 8.0)
    ok(v is not None and v < HL_SEARCH_MID, '压得不够应加大：%s %s' % (v, why))
    # 一路对分下去：−50 → −75 → −87.5 → −100
    v2, why2 = next_highlights(-75.0, 8.0)
    ok(v2 is not None and v2 < -75.0, '继续加大：%s %s' % (v2, why2))
    v2b, _ = next_highlights(v2, 8.0)
    eq(v2b, HL_END)
    # 尽头仍不达标 ⇒ 记账回引擎（不许再编一个数）
    v3, why3 = next_highlights(HL_END, 8.0)
    ok(v3 is None and '回引擎' in why3, '尽头还超 ⇒ 回引擎：%s %s' % (v3, why3))
    # 压过头（上端被压到比 ① 还低）⇒ 回退，不许继续加大
    v4, why4 = next_highlights(HL_SEARCH_MID, 0.0, p95_after=88.0, p95_before=95.0)
    ok(v4 is not None and abs(v4) < abs(HL_SEARCH_MID), '压过头应回退：%s' % v4)
    # 达标 ⇒ 不动
    v5, _ = next_highlights(HL_SEARCH_MID, 0.3, p95_after=95.0, p95_before=95.0)
    ok(v5 is None, '达标就不该再动：%s' % v5)


def test_solve_one_order():
    """整条链 §31 ②→③：脸低 ⇒ ② 把曝光给满；③ 在 ①' 上判、收白并记欠账。"""
    from design.lr import solve_one
    rec = _lr_rec(skin=dict(L=45.0, C=18.0, h=55.0), headroom_ev=0.0,
                  light_ratio=0.8)
    plan = solve_one(rec, log=lambda *_: None)
    ok(plan.exposure_ev > 0.5, '脸 L45 就该给（余量 0 不是理由）：%s' % plan.exposure_ev)
    ok('收白' in plan.notes.get('exposure', ''),
       '② 越过白平台要交代给 ③：%s' % plan.notes.get('exposure'))
    ok(plan.highlights < 0.0, '② 给满后上端必爆 ⇒ ③ 必须收白：%s' % plan.highlights)
    ok('highlights' in plan.notes, '③ 的理由要落进 notes：%s' % list(plan.notes))
    ok('highlight_verify' in plan.notes, '③ 的验收量要写清：%s' % list(plan.notes))
    ok('逆光' in plan.notes.get('light', '') or 'backlit' in plan.notes.get('lookat', ''),
       '光位要落进 notes：%s' % plan.notes)


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
