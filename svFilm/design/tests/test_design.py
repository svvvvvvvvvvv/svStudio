# -*- coding: utf-8 -*-
"""设计层单测：**纯函数，不渲图、不碰 LR**。

跑：
    & $py -X utf8 design/tests/test_design.py

覆盖：
  · 色度学 / 曲线（定义式）
  · WB 卡级（引擎解码层）
  · 曝光残差（人/脸优先；背景 clip 只诊断）
  · 形状目标（peer → hero → 卡；Basic 按肩趾给粗形状）
  · 肤色 Point Color（C/L 动态 SatScale）+ 分色
  · 局部（face_p99 余量、只抬不压）
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
from design.lr_solve import (grading, plan_batch, shape_target, skin_target,
                             summary, tone_solve, wb_of)
from design.lrsink import diagnostic, plan_to_xmp
from design.peer import _d, target_from_rec, targets as peer_targets
from design.photometry import code_to_l, ev_for_l, l_after_ev, l_to_code, l_to_y, y_to_l
from design.review import contact_sheet, make_review


# ---------------------------------------------------------------- 小工具

def _rec(p5=10, p25=25, p50=50, p75=75, p95=90, p99=95,
         face_L=50, face_C=15, face_h=45,
         person_p99=90, face_p99=90, person_clip=0.0, img_clip=0.0,
         wc=0.0, rgb=(210, 160, 130)):
    return dict(
        img=dict(p5=p5, p25=p25, p50=p50, p75=p75, p95=p95, p99=p99,
                 contrast=p95 - p5, clip_pct=img_clip),
        skin=dict(L=face_L, C=face_C, h=face_h, b=18.0, p99=face_p99),
        skin_rgb=list(rgb),
        face_L=face_L, face_C=face_C, face_h=face_h,
        face_p99=face_p99,
        person=dict(clip_pct=person_clip, L=face_L, p99=person_p99),
        person_p99=person_p99, person_clip_pct=person_clip,
        bg=dict(L=40.0, L_p10=15.0, L_p90=55.0, L_p95=70.0),
        bg_L=40.0,
        white_point=dict(n=5000, a=-1.0, b=-2.0, C=2.2, L_thr=93.0),
        warm_cool_b=wc)


def _card(**kw):
    c = dict(name='T_card', points=dict(
        p5=6.0, p25=18.0, p50=45.0, p75=75.0, p95=94.0, contrast=88.0,
        face_L=60.0, face_C=20.0, face_h=55.0, toe=12.0, shoulder=18.0,
        contrast_win=60.0, skew=0.35, warm_cool_b=1.0,
        face_over_bg=2.0), bands={}, samples=[])
    c.update(kw)
    return c


def _target(shape=(0.30, 0.38), skin=(58.0, 19.0, 55.0),
            p99=94.0, face_p99=94.0, clip=0.0,
            face_over_bg=2.0, wc=1.0, source='hero'):
    return dict(
        source=source,
        shape=dict(toe=shape[0], shoulder=shape[1],
                   contrast_win=50.0),
        skin=dict(L=skin[0], C=skin[1], h=skin[2]),
        rel=dict(face_over_bg=face_over_bg, warm_cool_b=wc),
        bg=dict(L_p10=20.0, L_p90=65.0, L_p95=80.0),
        exposure=dict(person_p99=p99, face_p99=face_p99,
                      person_clip_pct=clip),
        peer_n=0)


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
    d0 = duv_from_lab([95.0, 0.0, 0.0])
    assert 0.002 < d0 < 0.005
    assert duv_from_lab([95.0, 0.5, -0.5]) < d0
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


def test_curve_avoids_adjacent_anchors():
    q = dict(p5=8.0, p25=30.0, p50=70.0, p75=96.0, p95=96.5)
    pts = curve_from_quantiles(q, dict(toe=12.0, contrast_win=50.0,
                                     shoulder=20.0), p95_cap=96.5)
    xs = [x for x, _ in pts]
    assert min(b - a for a, b in zip(xs, xs[1:])) >= 4.0


# ---------------------------------------------------------------- 肤色目标 / WB

def test_skin_target_has_no_card_fallback():
    s = skin_target({}, _card())
    assert s['skin'] == dict(L=None, C=None, h=None)
    assert s['warm_cool_b'] is None


def test_skin_target_prefers_hero():
    s = skin_target(_target(skin=(58.0, 19.0, 55.0)), _card())
    assert abs(s['skin']['L'] - 58.0) < 1e-9
    assert abs(s['skin']['C'] - 19.0) < 1e-9


def test_wb_is_card_level_not_white_point():
    card = _card(wb=dict(cast=dict(a=-1.0, b=0.5),
                         white_point=dict(L=94.0, a=-1.0, b=0.0, C=1.0),
                         duv=0.0005))
    wb = wb_of(card, {'a': _rec()})
    assert not wb.is_set()
    assert wb.card_white is None
    assert 'cast' in wb.note and '不驱动' in wb.note


def test_wb_missing_is_reported():
    led = []
    wb = wb_of(_card(), {'a': _rec()}, led)
    assert not wb.is_set()
    assert any('无 cast' in x for x in led), led


# ---------------------------------------------------------------- 曝光 / 形状

def test_tone_solve_fills_exposure_residual():
    rec = _rec(person_p99=80.0)
    out = tone_solve(rec, _card(), _target(p99=94.0), [])
    assert out['ev'] == 0.0
    assert out['curve'], out
    assert all(abs(v) < 1e-9 for v in out['ui'].values()), out['ui']


def test_tone_solve_person_clip_stops_global():
    rec = _rec(person_clip=2.0, face_p99=99.0)
    out = tone_solve(rec, _card(), _target(p99=96.0), [])
    assert out['ev'] == 0.0
    assert out['curve'] is None
    assert all(abs(v) < 1e-9 for v in out['ui'].values())


def test_tone_solve_background_clip_is_diagnostic_only():
    rec = _rec(img_clip=20.0, person_clip=0.0, person_p99=80.0)
    out = tone_solve(rec, _card(), _target(p99=90.0), [])
    assert out['ev'] == 0.0, out
    assert out['curve'], out


def test_tone_solve_without_exposure_target_keeps_pe():
    rec = _rec()
    target = _target()
    target['exposure'] = {}
    target['bg'] = {}
    out = tone_solve(rec, _card(), target, [])
    assert out['ev'] == 0.0
    assert all(abs(v) < 1e-9 for v in out['ui'].values())


def test_shape_target_prefers_peer_hero():
    sh = shape_target(_card(), _target(shape=(0.22, 0.44), source='hero'))
    assert abs(sh['toe'] - 0.22) < 1e-9
    assert abs(sh['shoulder'] - 0.44) < 1e-9
    assert sh['source'] == 'hero'


def test_shape_target_has_no_card_fallback():
    assert shape_target(_card(), {}) is None


def test_curve_rejects_color_shifting_slope():
    # 2902: p50=20、p75=34、p95=92，peer shoulder=.32 ⇒ 曲线 68->133 过陡
    from design.lr_solve import curve_for
    q = dict(p5=1.18, p25=7.06, p50=20.39, p75=34.12, p95=92.55)
    led = []
    out = curve_for(q, dict(toe=0.222, shoulder=0.318), led)
    assert out is None
    assert any('斜率' in x for x in led), led


# ---------------------------------------------------------------- 肤色 / 分色

def test_grading_point_color_dynamic():
    rec = _rec(face_L=50.0, face_C=12.0, face_h=40.0)
    t = dict(skin=dict(L=55.0, C=18.0, h=46.0), warm_cool_b=0.0)
    g = grading
    out = g(_card(), rec, t, [])
    pc = out['point_color']
    assert pc and pc.is_set()
    assert pc.sat_scale > 0.0
    assert abs(pc.sat_scale) <= 0.35
    assert pc.raw > 0.0


def test_grading_point_color_zero_when_relation_matches():
    rec = _rec(face_L=50.0, face_C=12.0)
    t = dict(skin=dict(L=50.0, C=12.0, h=45.0), warm_cool_b=0.0)
    g = grading
    out = g(_card(), rec, t, [])
    assert out['point_color'] is not None
    assert abs(out['point_color'].sat_scale) < 1e-9


def test_grading_split_is_off():
    rec = _rec(wc=-5.0)
    card = _card(tone=dict(shadow=dict(lab=[10.0, -0.4, 0.3], hsl_h=210.0),
                           highlight=dict(lab=[90.0, 0.4, 0.3], hsl_h=40.0)))
    t = dict(skin=dict(L=50.0, C=15.0, h=45.0), warm_cool_b=1.0)
    sp = grading(card, rec, t, [])['split']
    assert sp['highlight_sat'] == 0.0 and sp['shadow_sat'] == 0.0


def test_grading_split_skips_scene_colour():
    rec = _rec(wc=3.0)
    card = _card(tone=dict(shadow=dict(lab=[13.3, -0.11, 0.76], hsl_h=46.3),
                           highlight=dict(lab=[92.1, -1.86, 1.92], hsl_h=98.9)))
    t = dict(skin=dict(L=58.0, C=21.0, h=58.0), warm_cool_b=1.0)
    g = grading
    sp = g(card, rec, t, [])['split']
    assert sp['highlight_sat'] == 0.0
    assert sp['shadow_sat'] == 0.0


# ---------------------------------------------------------------- 局部

def test_local_face_uses_face_headroom_not_global():
    from design.lr_solve import local_face
    rec = _rec(face_L=55.0, face_p99=88.0)
    rec['bg_L'] = 40.0
    rec['headroom_ev'] = 0.10
    plan = LRPlan()
    led = []
    local_face(plan, rec, dict(face_over_bg=2.0), led)
    assert plan.local.is_set(), led
    assert plan.local.exposure_ev > 0.3, (plan.local.exposure_ev, led)


def test_local_face_skips_without_face_p99():
    from design.lr_solve import local_face
    rec = _rec(face_L=55.0)
    rec['face_p99'] = None
    rec['skin']['p99'] = None
    plan = LRPlan()
    led = []
    local_face(plan, rec, dict(face_over_bg=2.0), led)
    assert not plan.local.is_set()
    assert any('face_p99' in x for x in led), led


# ---------------------------------------------------------------- peer / hero

def _comp(**kw):
    base = dict(framing='half', person_share=0.2, face_x=0.5, face_y=0.5,
                light_vec=dict(d_lr_img=10.0, ratio_near90=0.9,
                               hvf=5.0, clump=0.1))
    base.update(kw)
    return base


def test_peer_light_vector_changes_distance():
    a = _comp()
    b = _comp()
    c = _comp(light_vec=dict(d_lr_img=-40.0, ratio_near90=0.4,
                             hvf=-25.0, clump=0.6))
    assert _d(a, b) < _d(a, c)


def test_peer_targets_include_exposure_and_ratio_shape():
    row = dict(img=dict(p5=10.0, p25=25.0, p50=50.0, p75=75.0, p95=95.0),
               person_p99=96.0, face_p99=94.0, person_clip_pct=0.0,
               skin=dict(L=55.0, C=18.0, h=45.0), bg=dict(L=40.0),
               warm_cool_b=1.0)
    t = peer_targets([dict(rec=row, distance=0.0)])
    assert 0.0 < t['shape']['toe'] < 1.0
    assert 0.0 < t['shape']['shoulder'] < 1.0
    assert t['exposure']['person_p99'] == 96.0
    assert abs(t['rel']['face_over_bg'] - 55.0 / 40.0) < 1e-9


def test_hero_target_from_rec():
    row = dict(img=dict(p5=10.0, p25=25.0, p50=50.0, p75=75.0, p95=95.0),
               person_p99=96.0, face_p99=94.0, person_clip_pct=0.0,
               skin=dict(L=55.0, C=18.0, h=45.0), bg=dict(L=40.0),
               warm_cool_b=1.0)
    t = target_from_rec(row)
    assert t['source'] == 'hero' and t['peer_n'] == 0
    assert t['exposure']['person_p99'] == 96.0


# ---------------------------------------------------------------- 计划 / XMP

def _plan_example():
    p = LRPlan()
    p.wb = WhiteBalance(temperature=6464.0, note='card')
    p.exposure_ev = 0.2
    p.point_color = PointColor(h=0.08, s=0.35, l=0.6, range=0.5,
                               sat_scale=0.05, rgb=[210, 160, 130])
    p.local = Local(exposure_ev=0.4)
    p.curve = [(0, 0), (16, 16), (255, 255)]
    return p


def test_plan_xmp_has_no_wb_and_has_exposure():
    p = _plan_example()
    sc = p.as_xmp_scalars()
    assert sc['Exposure2012'] == 0.2
    assert 'HueAdjustmentOrange' not in sc
    assert 'WhiteBalance' not in sc and 'Temperature' not in sc
    xmp = plan_to_xmp(p)
    assert diagnostic(xmp)['ok'], diagnostic(xmp)
    assert 'AlreadyApplied="False"' in xmp and 'WhiteBalance="As Shot"' in xmp
    assert 'ToneCurveName2012="Custom"' in xmp


def test_summary_mentions_all_sections():
    plans = {'A': _plan_example()}
    text = summary(plans, 'T_card', pe=1.1)
    for word in ('WB', 'PE', '曝光/曲线', '肤色', '分色', '局部', '卡偏差'):
        assert word in text, word


def test_plan_batch_peer_hero_order():
    recs = {'A': _rec(person_p99=80.0), 'B': _rec(person_p99=80.0),
            'C': _rec(person_p99=80.0)}
    card = _card()
    peer_a = _target(shape=(0.3, 0.38), source='peer')
    peer_a['peer_n'] = 3
    peers = {'A': peer_a, 'B': dict(peer_n=1, shape={}, rel={}, skin={}),
             'C': {}}
    hero = _target(shape=(0.25, 0.4), source='hero')
    plans = plan_batch(recs, card, peers, hero_target=hero,
                       log=lambda *_: None)
    assert plans['A'].target['source'] == 'peer'
    assert plans['B'].target['source'] == 'hero'
    assert plans['C'].target['source'] == 'hero'


def test_plan_batch_without_peer_or_hero_raises():
    try:
        plan_batch({'C': _rec(person_p99=80.0)}, _card(), log=lambda *_: None)
    except RuntimeError:
        return
    raise AssertionError('expected RuntimeError（卡中位不再当靶）')


def test_plan_batch_ignores_card_parts_for_basic():
    # 卡里即使有 parts，也不该再驱动 Basic 四根
    card = _card(parts=dict(
        white=dict(points=dict(L_p50=92.0, L_p95=97.0)),
        hair=dict(points=dict(L_p50=26.0)),
        skin=dict(points=dict(L_p25=48.0))))
    rec = _rec()
    rec['parts'] = dict(white=dict(L_p50=80.0), hair=dict(L_p50=10.0),
                        skin=dict(L_p25=30.0))
    pt = _target(shape=(0.30, 0.38), source='peer')
    pt['peer_n'] = 1
    plans = plan_batch({'A': rec}, card, {'A': pt}, log=lambda *_: None)
    p = plans['A']
    assert p.target['source'] == 'peer'
    assert p.exposure_ev == 0.0


def test_per_card_json_has_all_fields():
    from design.matchcard import raw_cards
    c = raw_cards()[0]
    assert c.get('name')
    assert isinstance(c.get('medium'), dict) and c['medium'].get('simulation')
    assert c.get('pe') is not None
    assert c.get('tone')
    assert c.get('content')
    assert c.get('stock') and c.get('paper')


# ---------------------------------------------------------------- 批级护栏

def test_batch_guard_reports_not_judges():
    recs = {'A': _rec(), 'B': _rec(p50=52), 'C': _rec(p50=48), 'D': _rec(p50=51)}
    g = guard_build(recs, _card())
    assert g['n'] == 4 and 'curve' not in g
    assert any('离散度' in x for x in g['ledger'])


# ---------------------------------------------------------------- 目检页

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
    html = contact_sheet([dict(stem='A', values='曝光 +0.2', note='脸 50→58',
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
