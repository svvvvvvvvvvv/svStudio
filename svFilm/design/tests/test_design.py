# -*- coding: utf-8 -*-
"""设计层单测：**纯函数，不渲图、不碰 LR**。

跑：
    & $py -X utf8 design/tests/test_design.py

覆盖 v0.3 的链：
  · 色度学 / 曲线（定义式）
  · WB 卡级（引擎解码层）
  · 明暗目标（形状锚在 ① 的 p50；卡/批不匹配要报）
  · Basic 滑杆（实测一阶响应；削顶不动）
  · 肤色（蒙版里的 Point Color）+ 分色
  · LRPlan/XMP（WB 不写 XMP）
  · 目检页（没有机器过/不过）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design import lr_response as R
from design.batch_guard import build as guard_build
from design.color_math import cct_from_lab, duv_from_lab
from design.curve import grid16
from design.curve_design import apply_lut, curve_from_quantiles
from design.lr_plan import LRPlan, Local, PointColor, WhiteBalance
from design.lr_solve import (anchor_target, basic_tone, grading, plan_batch,
                             skin_target, summary, tone_target, wb_of)
from design.lrsink import diagnostic, plan_to_xmp
from design.photometry import code_to_l, ev_for_l, l_after_ev, l_to_code, l_to_y, y_to_l
from design.review import contact_sheet, make_review


# ---------------------------------------------------------------- 小工具

def _rec(p5=10, p25=25, p50=50, p75=75, p95=90, p99=95, face_L=50, face_C=15,
         face_h=45, clip=0.0, person_clip=0.0, wc=0.0, rgb=(210, 160, 130)):
    return dict(
        img=dict(p5=p5, p25=p25, p50=p50, p75=p75, p95=p95, p99=p99,
                 contrast=p95 - p5, clip_pct=clip),
        skin=dict(L=face_L, C=face_C, h=face_h, b=18.0),
        skin_rgb=list(rgb),
        person=dict(clip_pct=person_clip, L=face_L),
        bg=dict(L=40.0, L_p90=60.0),
        white_point=dict(n=5000, a=-1.0, b=-2.0, C=2.2, L_thr=93.0),
        warm_cool_b=wc)


def _card(**kw):
    c = dict(name='T_card', points=dict(
        p5=6.0, p25=18.0, p50=45.0, p75=75.0, p95=94.0, contrast=88.0,
        face_L=60.0, face_C=20.0, face_h=55.0, toe=12.0, shoulder=18.0,
        contrast_win=60.0, skew=0.35, warm_cool_b=1.0), bands={}, samples=[])
    c.update(kw)
    return c


# ---------------------------------------------------------------- 色度学 / 曲线

def test_photometry_roundtrip():
    for L in (0.0, 5.0, 18.0, 50.0, 82.0, 100.0):
        assert abs(y_to_l(l_to_y(L)) - L) < 1e-9
        assert abs(code_to_l(l_to_code(L)) - L) < 0.05


def test_ev_exact():
    for L in (20.0, 50.0, 80.0):
        L1 = l_after_ev(L, 0.7)
        assert abs(ev_for_l(L, L1) - 0.7) < 1e-9
    assert abs(l_after_ev(50.0, 0.0) - 50.0) < 1e-9


def test_cct_neutral_is_6500():
    assert abs(cct_from_lab([95.0, 0.0, 0.0]) - 6500.0) < 120.0


def test_duv_is_distance_to_planckian_locus():
    # D65 自己不是 Duv=0；它在轨迹上方（正=绿）
    d0 = duv_from_lab([95.0, 0.0, 0.0])
    assert 0.002 < d0 < 0.005
    # 加一点品红（a 正、b 负）⇒ Duv 下降
    assert duv_from_lab([95.0, 0.5, -0.5]) < d0
    # 加一点绿（a 负）⇒ Duv 上升
    assert duv_from_lab([95.0, -1.0, 0.0]) > d0


def test_grid16_contains_anchor_codes():
    g = grid16([12.0, 119.0, 250.0])
    assert len(g) == 16 and g[0] == 0.0 and g[-1] == 255.0
    for a in (12.0, 119.0, 250.0):
        assert a in g, (a, g)


def test_curve_keeps_p50_and_is_monotone():
    q = dict(p5=8.0, p25=30.0, p50=55.0, p75=80.0, p95=95.0)
    shape = dict(toe=12.0, contrast_win=50.0, shoulder=15.0)
    pts = curve_from_quantiles(q, shape, p95_cap=94.0)
    assert pts and len(pts) == 16
    ys = [y for _, y in pts]
    assert all(ys[i] <= ys[i + 1] for i in range(len(ys) - 1))
    assert abs(apply_lut(pts, 55.0) - 55.0) < 1.0
    assert abs(apply_lut(pts, 0.0)) < 1e-6
    assert abs(apply_lut(pts, 100.0) - 100.0) < 1e-6


# ---------------------------------------------------------------- 目标（形状锚定）

def test_peer_shape_target():
    peer = dict(peer_n=3, shape=dict(toe=10.0, shoulder=14.0, p95=94.0,
                                    contrast_win=60.0),
                rel=dict(p50=45.0, p95=94.0, contrast=88.0, warm_cool_b=1.0),
                skin=dict(L=60.0, C=20.0, h=55.0))
    t = tone_target(peer, _card())
    assert t and t['source'] == 'peer'
    assert abs(t['contrast'] - 88.0) < 1e-6
    assert abs(t['skew'] - (45.0 - (94.0 - 88.0)) / 88.0) < 1e-6


def test_anchor_uses_own_p50():
    t = anchor_target(dict(contrast=80.0, skew=0.4, toe=10.0, shoulder=14.0),
                      50.0)
    assert t and abs(t['q50'] - 50.0) < 1e-9
    assert abs(t['q5'] - (50.0 - 0.4 * 80.0)) < 1e-9
    assert t['q5'] < t['q25'] < t['q50'] < t['q75'] < t['q95']


def test_anchor_reports_mismatch():
    led = []
    assert anchor_target(dict(contrast=88.0, skew=0.35, toe=12.0, shoulder=18.0),
                         65.0, led) is None
    assert any('卡/批不匹配' in x for x in led), led


def test_skin_target_falls_back_to_card():
    s = skin_target({}, _card())
    assert abs(s['skin']['L'] - 60.0) < 1e-9
    assert abs(s['warm_cool_b'] - 1.0) < 1e-9


# ---------------------------------------------------------------- WB（as-shot + 白点目标）

def test_wb_is_card_level_not_white_point():
    card = _card(wb=dict(cast=dict(a=-1.0, b=0.5),
                         white_point=dict(L=94.0, a=-1.0, b=0.0, C=1.0),
                         duv=0.0005))
    wb = wb_of(card, {'a': _rec()})
    assert not wb.is_set()          # 不落开尔文
    assert wb.card_white is None    # 白点不再当目标
    assert 'cast' in wb.note and '不驱动' in wb.note


def test_wb_missing_is_reported():
    led = []
    wb = wb_of(_card(), {'a': _rec()}, led)
    assert not wb.is_set()
    assert any('无 cast' in x for x in led), led


# ---------------------------------------------------------------- Basic（实测响应）

def test_slider_domain():
    assert R.slider_for_delta('contrast', 50.0, 0.2) is not None
    assert R.slider_for_delta('contrast', 5.0, 0.2) is None      # L 域外
    assert R.slider_for_delta('contrast', 50.0, 99.0) is None    # 超过 ±50


def test_basic_tone_moves_shape():
    rec = _rec(p5=8, p25=20, p50=50, p75=80, p95=92)
    t = anchor_target(dict(contrast=80.0, skew=0.5, toe=20.0, shoulder=20.0),
                      50.0)
    assert t and abs((t['q75'] - t['q25']) - 40.0) < 1e-9
    out = basic_tone(rec, t, [])
    ui = out['ui']
    assert any(abs(v) > 1e-9 for v in ui.values()), ui
    before = abs(60.0 - 40.0)
    after = abs((out['pred']['p75'] - out['pred']['p25']) - 40.0)
    assert after <= before + 1e-6


def test_basic_tone_skips_when_clipped():
    rec = _rec(person_clip=3.0)
    t = anchor_target(dict(contrast=80.0, skew=0.4, toe=10.0, shoulder=14.0), 50.0)
    out = basic_tone(rec, t, [])
    assert all(abs(v) < 1e-9 for v in out['ui'].values()), out['ui']
    assert out['pred']['p50'] == rec['img']['p50']


def test_basic_tone_skips_without_target():
    out = basic_tone(_rec(), None, [])
    assert all(abs(v) < 1e-9 for v in out['ui'].values())


# ---------------------------------------------------------------- 肤色 / 分色

def test_grading_point_color():
    # C/L: 12/50=0.24 → 目标 18/55=0.327 ⇒ SatScale +0.364（SDK -1..1 连续）
    rec = _rec(face_L=50.0, face_C=12.0, face_h=40.0)
    t = dict(skin=dict(L=55.0, C=18.0, h=46.0), warm_cool_b=0.0)
    g = grading(_card(), rec, t, [])
    pc = g['point_color']
    assert pc and pc.is_set()
    assert abs(pc.sat_scale - 0.364) < 1e-9, pc.sat_scale
    assert pc.sat_scale > 0
    # 反向：目标更灰 ⇒ 负 SatScale
    g2 = grading(_card(), rec, dict(skin=dict(L=55.0, C=8.0, h=46.0),
                                    warm_cool_b=0.0), [])
    assert abs(g2['point_color'].sat_scale + 0.394) < 1e-9


def test_grading_split_direction():
    # 近中性的暖/冷高光/暗部 ⇒ 分色按卡方向
    rec = _rec(wc=-5.0)
    card = _card(tone=dict(shadow=dict(lab=[10.0, -0.4, 0.3], hsl_h=210.0),
                           highlight=dict(lab=[90.0, 0.4, 0.3], hsl_h=40.0)))
    t = dict(skin=dict(L=50.0, C=15.0, h=45.0), warm_cool_b=1.0)
    g = grading(card, rec, t, [])
    sp = g['split']
    assert sp and sp['highlight_hue'] == 40.0 and sp['shadow_hue'] == 210.0
    assert sp['shadow_sat'] > 0 and sp['highlight_sat'] > 0


def test_grading_split_skips_scene_colour():
    # 高光是树叶绿（C=2.67/h=99）⇒ 场景色，不做全局分色
    rec = _rec(wc=3.0)
    card = _card(tone=dict(shadow=dict(lab=[13.3, -0.11, 0.76], hsl_h=46.3),
                           highlight=dict(lab=[92.1, -1.86, 1.92], hsl_h=98.9)))
    t = dict(skin=dict(L=58.0, C=21.0, h=58.0), warm_cool_b=1.0)
    g = grading(card, rec, t, [])
    assert g['split']['highlight_sat'] == 0.0
    assert g['split']['shadow_sat'] == 0.0


# ---------------------------------------------------------------- 计划 / XMP

def _plan_example():
    p = LRPlan()
    p.wb = WhiteBalance(temperature=6464.0, note='card')
    p.contrast = -12.0
    p.highlights = -8.0
    p.point_color = PointColor(h=0.08, s=0.35, l=0.6, range=0.5,
                               sat_scale=0.05, rgb=[210, 160, 130])
    p.local = Local(exposure_ev=0.4)
    p.curve = [(0, 0), (16, 16), (255, 255)]
    return p


def test_plan_xmp_has_no_wb_and_has_basic():
    p = _plan_example()
    sc = p.as_xmp_scalars()
    assert sc['Contrast2012'] == -12.0
    assert 'HueAdjustmentOrange' not in sc
    assert 'WhiteBalance' not in sc and 'Temperature' not in sc
    xmp = plan_to_xmp(p)
    assert diagnostic(xmp)['ok'], diagnostic(xmp)
    assert 'AlreadyApplied="False"' in xmp and 'WhiteBalance="As Shot"' in xmp
    assert 'ToneCurveName2012="Custom"' in xmp


def test_summary_mentions_all_sections():
    plans = {'A': _plan_example()}
    text = summary(plans, 'T_card', pe=1.1)
    for word in ('WB', 'PE', '明暗', '肤色', '分色', '局部', '卡偏差'):
        assert word in text, word


def test_plan_batch_end_to_end():
    recs = {'A': _rec(p5=12, p25=28, p50=52, p75=78, p95=92),
            'B': _rec(p5=8, p25=22, p50=48, p75=72, p95=88, person_clip=2.0)}
    card = _card(wb=dict(white_point=dict(L=94.0, a=-1.0, b=1.0),
                         cct_look=6464.0, duv=0.0,
                         temperature=7764.0, tint=1.06))
    peers = {'A': dict(peer_n=3, shape=dict(toe=18.0, shoulder=22.0, p95=94.0),
                       rel=dict(p50=45.0, p95=94.0, contrast=90.0,
                                warm_cool_b=1.0),
                       skin=dict(L=58.0, C=19.0, h=55.0)),
             'B': dict(peer_n=0, shape={}, rel={}, skin={})}
    plans = plan_batch(recs, card, peers, log=lambda *_: None)
    assert set(plans) == {'A', 'B'}
    assert plans['A'].wb.note
    assert plans['A'].point_color.is_set()
    assert abs(plans['B'].contrast) < 1e-9 and abs(plans['B'].highlights) < 1e-9


# ---------------------------------------------------------------- 批级护栏

def test_batch_guard_reports_not_judges():
    recs = {'A': _rec(), 'B': _rec(p50=52), 'C': _rec(p50=48), 'D': _rec(p50=51)}
    g = guard_build(recs, _card())
    assert g['n'] == 4 and 'curve' not in g
    assert any('离散度' in x for x in g['ledger'])


# ---------------------------------------------------------------- 目检页（不判过/不过）

def test_review_page_has_no_verdict():
    a = _rec(face_L=50)
    b = _rec(face_L=58)
    plans = {'A': _plan_example()}
    out = make_review({'A': a}, {'A': b}, plans,
                      os.path.join(os.environ.get('TEMP', '.'), 'sv_test_review'),
                      log=lambda *_: None)
    html = open(out['html'], encoding='utf-8').read()
    assert '人目检' in html
    for bad in ('硬门', '软门', '✓ 全过', '结论：'):
        assert bad not in html, bad
    assert out['n'] == 1 and 'ok' not in out


def test_contact_sheet_rows():
    html = contact_sheet([dict(stem='A', values='曝光 +0.0', note='脸 50→58',
                               diag='p50 偏差 +1.0')],
                         os.path.join(os.environ.get('TEMP', '.'), 'sv_test_sheet.html'),
                         '目检', sub='1 张')
    assert html.endswith('.html')


def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith('test_') and callable(v)]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print('  ✓ %s' % name)
        except Exception as e:                             # noqa: BLE001
            bad += 1
            import traceback
            print('  ✗ %s：%s' % (name, e))
            traceback.print_exc()
    print('\n%d/%d 通过' % (len(tests) - bad, len(tests)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
