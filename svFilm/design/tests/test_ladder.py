# -*- coding: utf-8 -*-
"""引擎梯子 + LR 交接的测试。

梯子 = 引擎处理「各种情况」的唯一结构：
  求根 → 不可达 → 检不到脸（找有效域）→ 没 peer（常数靶）→ 约束到顶
这里只测**结构与分支**（用假的渲染函数），不测具体数值。
"""
import numpy as np

from design import flow
from design.curve_design import curve_from_quantiles


def _fake_flow(monkeypatch, fn):
    monkeypatch.setattr(flow, '_render_measure_face', fn)
    return flow


def test_ladder_converges(monkeypatch):
    """正常情况：两点割线 → 落到靶上，status=ok。"""
    def fake(lin, medium, ov, pe, stem, out_dir):                  # noqa: ANN001
        mid = 80.0 - 30.0 * np.log2(float(pe) / 0.2)
        return dict(face_p5=mid - 5, face_p95=mid + 5, img=dict(p5=20)), None
    flow = _fake_flow(monkeypatch, fake)
    pe, disp, rec, evals, why = flow._solve_pe_face(
        np.zeros((4, 4)), None, {}, 60.0, 0.2, 'X', '.')
    assert why == 'ok'
    mid = (rec['face_p5'] + rec['face_p95']) / 2
    assert abs(mid - 60.0) <= 1.5, (mid, pe, evals)


def test_ladder_unreachable(monkeypatch):
    """响应为零 ⇒ 判不可达，取已测点，不硬凑。"""
    def fake(lin, medium, ov, pe, stem, out_dir):                  # noqa: ANN001
        return dict(face_p5=65, face_p95=75, img=dict(p5=20)), None
    flow = _fake_flow(monkeypatch, fake)
    pe, disp, rec, evals, why = flow._solve_pe_face(
        np.zeros((4, 4)), None, {}, 60.0, 0.2, 'X', '.')
    assert why == 'face_unreachable', why
    assert abs((rec['face_p5'] + rec['face_p95']) / 2 - 70.0) < 1e-6


def test_ladder_finds_valid_domain(monkeypatch):
    """起点检不到脸 ⇒ 先找有效域再求根，不能直接放弃。"""
    def fake(lin, medium, ov, pe, stem, out_dir):                  # noqa: ANN001
        if float(pe) < 0.25:
            return {}, None                                       # 太暗，没脸
        mid = 80.0 - 30.0 * np.log2(float(pe) / 0.2)
        return dict(face_p5=mid - 5, face_p95=mid + 5, img=dict(p5=20)), None
    flow = _fake_flow(monkeypatch, fake)
    pe, disp, rec, evals, why = flow._solve_pe_face(
        np.zeros((4, 4)), None, {}, 60.0, 0.2, 'X', '.')
    assert why == 'ok' and float(pe) >= 0.25, (why, pe)


def test_curve_stretches_shadow_toward_peer():
    """LR 接引擎的 shadow_gap：曲线把 q5 往 peer 拉，且不超过上限。"""
    q = dict(p5=30, p25=40, p50=55, p75=70, p95=88)
    shape = dict(toe=12, contrast_win=28, shoulder=15)
    m1 = {}
    curve_from_quantiles(q, shape, p95_cap=88, q5_target=8.0, meta=m1)
    assert m1['new5'] == 10.0            # 30 − MD_TOE_MAX(20)
    m2 = {}
    curve_from_quantiles(q, shape, p95_cap=88, q5_target=25.0, meta=m2)
    assert m2['new5'] == 25.0            # 目标更亮 ⇒ 压到 25
    m3 = {}
    curve_from_quantiles(q, shape, p95_cap=88, q5_target=None, meta=m3)
    assert m3['new5'] == 30.0            # 没给目标 ⇒ 不动


def test_curve_keeps_p50():
    """拉暗部不许动中位（位置归曝光，形状归曲线）。"""
    q = dict(p5=30, p25=40, p50=55, p75=70, p95=88)
    m = {}
    curve_from_quantiles(q, dict(toe=12, contrast_win=28, shoulder=15),
                         p95_cap=88, q5_target=8.0, meta=m)
    assert m['q5'] == 30 and m['new5'] < m['q5']


def main():
    import inspect
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith('test_') and callable(v)]
    bad = 0
    for name, fn in tests:
        try:
            if 'monkeypatch' in inspect.signature(fn).parameters:
                class MP:
                    def setattr(self, obj, attr, val):
                        old = getattr(obj, attr)
                        setattr(obj, attr, val)
                        self._undo = getattr(self, '_undo', []) + [(obj, attr, old)]

                    def undo(self):
                        for o, a, v in reversed(getattr(self, '_undo', [])):
                            setattr(o, a, v)
                mp = MP()
                try:
                    fn(mp)
                finally:
                    mp.undo()
            else:
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
