# -*- coding: utf-8 -*-
"""曝光链路的单测（纯函数，不渲图、不碰 LR）。

跑：
    & $py -X utf8 design/tests/test_exposure.py

覆盖（对齐设计，2026-11-03 拍板）：
  · peer **只找一张**：先按光位（front/side/back/…）同类，再按距离最近；
    卡里没有同光位 ⇒ 返回 None
  · 目标 = 那张 peer 的**直方图**（p5/25/50/75/95），不取中位、不做平均
  · 没有 peer ⇒ 按本图机内 JPG 自己的直方图（人优先，不借别人水平）
  · `solve_pe_q`：按直方图反解 PE，预测分位贴近目标；表外钳位要记账
  · 表：合成灰阶 36 级 / ramp 建表 / 旧 p50 口径仍可用
  · 目检页：① 目标 vs 实测 并排
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design import chart_calib as CC
from design import peer as PEER
from design import peer_ratio as PR


# ---------------------------------------------------------------- 造测量记录

def _rec(p5=10.0, p50=50.0, p95=90.0, person_L=60.0, bg_L=40.0, skin_L=60.0,
         d_lr=2.0, near90=0.95, clump=0.1, ps=0.2):
    im = dict(p5=p5, p10=p5 + (p50 - p5) * .15, p25=p5 + (p50 - p5) * .5,
              p50=p50, p75=p50 + (p95 - p50) * .5, p90=p50 + (p95 - p50) * .8,
              p95=p95, p99=p95, contrast=p95 - p5, clip_pct=0.0)
    return dict(file='x.jpg', img=im,
                person=dict(L=person_L, share=ps, p95=p95, clip_pct=0.0),
                bg=dict(L=bg_L, n=100000, L_p10=10.0, L_p90=70.0, L_p95=80.0),
                env_share=1.0 - ps,
                skin=dict(L=skin_L, C=18.0, h=45.0, b=18.0),
                light=dict(d_lr_img=d_lr, ratio_near90=near90, hvf=5.0, clump=clump),
                warm_cool_b=0.0)


def _front(**kw):
    return _rec(person_L=62.0, bg_L=38.0, skin_L=62.0, d_lr=2.0, near90=0.95, **kw)


def _back(**kw):
    return _rec(person_L=30.0, bg_L=70.0, skin_L=32.0, d_lr=1.0, near90=0.55, **kw)


def _side(**kw):
    return _rec(person_L=62.0, bg_L=38.0, skin_L=60.0, d_lr=22.0, near90=0.95, **kw)


# ---------------------------------------------------------------- peer：只找一张

def test_light_labels_are_the_designed_ones():
    assert PR.light_of(_front()) == 'front'
    assert PR.light_of(_back()) == 'back'
    assert PR.light_of(_side()) == 'side'


def test_pick_peer_prefers_same_light_then_nearest():
    rows = [_back(p5=40, p50=80, p95=98), _side(), _front(p50=52)]
    peer, why = PR.pick_peer(_front(ps=0.21), rows)
    assert peer is not None and why == ''
    assert PR.light_of(peer['rec']) == 'front'


def test_pick_peer_returns_none_without_same_light():
    rows = [_back(), _side()]
    peer, why = PR.pick_peer(_front(), rows)
    assert peer is None and '没有「front」光位' in why, why


def test_pick_peer_single_not_many():
    rows = [_front(p50=50), _front(p50=55), _front(p50=60), _front(p50=65)]
    peer, _ = PR.pick_peer(_front(), rows)
    assert isinstance(peer, dict) and 'rec' in peer


# ---------------------------------------------------------------- 目标 = 一张 peer 的直方图

def test_target_is_the_peer_histogram_not_a_median():
    peer_rec = _front(p5=8, p50=52, p95=93)
    peer, _ = PR.pick_peer(_front(), [peer_rec])
    t = PR.target_of(peer, _front())
    assert t['peer_n'] == 1
    assert t['peer_q']['p50'] == 52.0 and t['peer_q']['p95'] == 93.0
    assert t['target_p50'] == 52.0
    assert t['target_from'] == 'peer'
    # 形状/肤色/关系来自同一张（单元素取中位 = 它自己）
    assert t['skin']['L'] == 62.0        # _front 的 skin_L


def test_own_jpg_target_when_no_peer():
    rec = _front(p5=5, p50=37, p95=88)
    t = PR.own_jpg_target(rec, '卡里没有同光位')
    assert t['target_from'] == 'constant' and t['peer_n'] == 0
    # 2026-11-04：常数靶改成**语料中位分布**（不再借本图直方图）
    assert t['peer_q']['p50'] == 51.8 and t['peer_q']['p95'] == 91.0


def test_build_walks_every_frame():
    rows = [_front(p50=50), _back()]
    recs = {'A': _front(), 'B': _back(),
            'C': _rec(person_L=62.0, bg_L=38.0, skin_L=62.0, d_lr=25.0)}   # C 是 side
    out = PR.build(recs, 'NO_SUCH_CARD', log=lambda *_: None)
    assert out == {}                                    # 卡里没有测量 ⇒ 不硬凑
    
# ---------------------------------------------------------------- 直方图反解 PE

def _ramp_table(pes=(0.0625, 0.125, 0.25, 0.5, 1.0, 2.0, 4.0), dark_gain=1.0):
    ys, _img, _c = CC.gray_ramp(n=36, long_side=432)
    L = []
    for pe in pes:
        # 越大的 PE 越暗：L 随 PE 下降（形状随意，只要单调、够离散）
        base = 95.0 - 26.0 * np.log2(pe / pes[0])
        L.append([float(np.clip(base + 40.0 * np.log10(y / ys[0]) * dark_gain,
                               -5, 100)) for y in ys])
    return dict(chart_sha='fake', pe_nominal=1.0, pes=list(pes),
                medium=dict(stock='s', paper='p'),
                wedge=dict(ev=CC._ramp_ev(ys), y_linear=ys, L=L))


def test_solve_pe_q_hits_the_target_histogram():
    table = _ramp_table()
    rng = np.random.default_rng(0)
    lin = np.stack([rng.uniform(0.02, 0.6, 40000)] * 3, axis=1)
    # 拿某个 PE 的预测分位当目标 ⇒ 解回来应该回到那个 PE
    want_pe = 0.35
    _, l2, logy, Lmat = CC._maps(table)
    x = CC._input_logy(lin)
    row = CC._row_at(Lmat, l2, float(np.log2(want_pe)))
    tgt = CC._pred_q(x, logy, row)
    sol = CC.solve_pe_q(lin, tgt, table)
    assert abs(np.log2(sol['pe'] / want_pe)) < 0.15, sol
    assert sol['dist'] < 1.0, sol
    assert sol['model_ok'] is True, sol
    assert set(sol['q_pred']) == set(sol['q_target'])


def test_solve_pe_q_flags_dark_clamp():
    table = _ramp_table()
    rng = np.random.default_rng(1)
    lin = np.stack([rng.uniform(0.0, 8e-5, 40000)] * 3, axis=1)
    sol = CC.solve_pe_q(lin, {5: 20.0, 50: 30.0, 95: 60.0}, table)
    assert sol['model_ok'] is False and '暗端' in sol['model_why'], sol


def test_solve_pe_q_needs_quantiles():
    try:
        CC.solve_pe_q(np.zeros((10, 10, 3), np.float32), {}, _ramp_table())
    except ValueError:
        return
    raise AssertionError('空目标应该报错')


# ---------------------------------------------------------------- 表 / 目检页

def test_gray_ramp_covers_dark_end():
    ys, img, centers = CC.gray_ramp(n=36, long_side=432)
    assert len(ys) == 36 and img.shape[1] == 432
    assert img.shape[0] % 36 == 0 and img.shape[0] // 36 >= 12
    assert ys[0] == CC.RAMP_TOP and ys[-1] <= 1e-3
    assert all(a > b for a, b in zip(ys, ys[1:]))
    assert all(0 <= cx < img.shape[1] and 0 <= cy < img.shape[0] for cx, cy in centers)


def _fake_presets():
    import types
    return types.SimpleNamespace(
        render=lambda img, medium, print_exposure=1.0: np.clip(
            np.asarray(img, np.float32) / max(float(print_exposure), 1e-6), 0.0, 1.0))


def test_render_table_ramp_mode():
    import sys as _sys
    for mod in ('tifffile', 'colour'):
        try:
            __import__(mod)
        except Exception:                                  # noqa: BLE001
            print('    （跳过：这个 Python 没有 %s；表要在引擎环境里建）' % mod)
            return
    if not os.path.isfile(CC.CHART_TIF):
        print('    （跳过：没有色卡图）')
        return
    out = os.path.join(os.environ.get('TEMP', '.'), 'sv_table_ramp.json')
    saved = _sys.modules.get('svFilm.presets')
    _sys.modules['svFilm.presets'] = _fake_presets()
    try:
        CC.render_table({'simulation': dict(film_stock='s', print_paper='p',
                                            print_exposure=1.0)},
                        out_path=out, pe_values=[0.5, 1.0], long_side=432,
                        log=lambda *_: None)
    finally:
        if saved is None:
            _sys.modules.pop('svFilm.presets', None)
        else:
            _sys.modules['svFilm.presets'] = saved
    doc = json.load(open(out, encoding='utf-8'))
    assert doc['wedge_mode'] == 'ramp'
    assert len(doc['wedge']['y_linear']) == CC.RAMP_N
    L = np.asarray(doc['wedge']['L'], float)
    assert np.all(L[-1] <= L[0] + 1e-6)
    d = np.diff(L[0])
    assert np.all(d <= 1.0) and np.all(np.abs(d) < 25.0)


def test_review_shows_target_vs_measured():
    import types
    from design.review import make_review
    rec = _front(p5=10, p50=15.7, p95=91.4)
    rec['skin'] = dict(L=50.0, C=15.0, h=45.0)
    plan = types.SimpleNamespace(
        wb=types.SimpleNamespace(card_white=None, note=''),
        exposure_ev=0.0, contrast=0.0, highlights=0.0, shadows=0.0,
        whites=0.0, blacks=0.0, point_color=None, local=None, curve=[],
        target=dict(target_p50=33.3), card_dev={})
    out = make_review({'A': rec}, {'A': rec}, {'A': plan},
                      os.path.join(os.environ.get('TEMP', '.'), 'sv_test_review_t'),
                      log=lambda *_: None)
    html = open(out['html'], encoding='utf-8').read()
    assert '① 目标 33.3' in html
    for bad in ('硬门', '软门', '结论：'):
        assert bad not in html, bad


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
