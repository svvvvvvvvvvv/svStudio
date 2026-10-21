# -*- coding: utf-8 -
"""★★★ **本模块是「曝光主人」的落点**：中灰锚 + 它让位给人本。"""
from __future__ import annotations

import numpy as np

_MASK = [None]          # 当前这张的"人"掩膜（0~1，与输入图同尺寸）；渲染是串行的
_INSTALLED = [False]
_REGIME = ['person']    # 'face' / 'person' —— 决定用哪一对 (w, b)；渲染串行，逐张设置

# ★★ 掩膜占比下限（`m.sum()/ref`）：低于它 ⇒ **弃权**走 `matrix`（与外层守卫同一落点）。
_MIN_MASK_COVER = 0.003
_COVER_REF = [None]     # None ⇒ 用 m.size（默认口径，逐位等于修复前）
_ABSTAIN = [0, None]    # [次数, 上次原因] —— 观察者状态，只计数不改画面


def _anchor_wb(regime='person'):
    """读 `regime='face'` 时取 `EXPOSURE_ANCHOR_*_FACE`（缺键 ⇒ 退回人档）；**每次动态读** ⇒"""
    try:
        from . import config as _C
        if regime == 'face':
            wf = getattr(_C, 'EXPOSURE_ANCHOR_W_FACE', None)
            bf = getattr(_C, 'EXPOSURE_ANCHOR_B_FACE', None)
            if wf is not None and bf is not None:
                return float(wf or 0.0), float(bf or 0.0)
        w = float(getattr(_C, 'EXPOSURE_ANCHOR_W', 0.0) or 0.0)
        b = float(getattr(_C, 'EXPOSURE_ANCHOR_B', 0.0) or 0.0)
    except Exception:                                                        # noqa: BLE001
        return 0.0, 0.0
    return w, b


def anchor_ev(ev):
    r"""`ev' = (1 - w) * ev + b` —— **唯一改动点**；`w = b = 0` ⇒ 原样返回。"""
    w, b = _anchor_wb(_REGIME[0])
    if w == 0.0 and b == 0.0:
        return ev
    return (1.0 - w) * ev + b


def regime():
    return _REGIME[0]


def set_mask(m, regime='person', cover_ref=None):
    """渲染前把这张的测光掩膜放进来（`None` ⇒ `subject` 退回原实现）。"""
    _MASK[0] = None if m is None else np.asarray(m, np.float32)
    _REGIME[0] = 'face' if str(regime) == 'face' else 'person'
    _COVER_REF[0] = None if cover_ref is None else float(cover_ref)


def clear():
    _MASK[0] = None
    _REGIME[0] = 'person'
    _COVER_REF[0] = None


def abstain_count():
    r"""掩膜过小 ⇒ 弃权的**次数**（观察者，给计数测试用）。"""
    return int(_ABSTAIN[0])


def abstain_why():
    return _ABSTAIN[1]


def _reset_abstain():
    _ABSTAIN[0], _ABSTAIN[1] = 0, None


def install():
    """把引擎的测光换成「支持 `subject` + 中灰锚」的版本。幂等。"""
    if _INSTALLED[0]:
        return True
    try:
        # ★★ 必须先钉扎，否则 `import spektrafilm` 会被本机 pip 装的那份抢先（`spektra._sf()` 守卫）。
        from . import spektra as _sp
        _sp._sf()
        from spektrafilm.utils import autoexposure as _ae
        from spektrafilm.runtime.stages import filming as _fm
    except Exception:                                                        # noqa: BLE001
        return False
    _orig_disp = _ae.measure_autoexposure_ev
    _orig_raw = getattr(_ae, 'measure_raw_autoexposure_ev', None)

    def _abstain(cover):
        _ABSTAIN[0] += 1
        _ABSTAIN[1] = ('mask_cover=%.5f < %.5f' % (cover, _MIN_MASK_COVER))
        if _ABSTAIN[0] == 1 or _ABSTAIN[0] % 100 == 0:
            import sys as _sys
            print('[svFilm 警告] exposure: 测光掩膜太小（占比 %.5f < %.5f）⇒ '
                  '弃权，退回 matrix（本进程第 %d 次）'
                  % (cover, _MIN_MASK_COVER, _ABSTAIN[0]), file=_sys.stderr)

    def _masked_ev(field, target, mask):
        r"""按掩膜测一个 2D 场的 EV；返回 `(ev|None, cover)`。None = 掩膜太小要弃权。"""
        import cv2
        H, W = field.shape
        m = np.asarray(mask, np.float64)
        if m.shape[:2] != (H, W):                     # 掩膜要缩到引擎这次用的尺寸
            m = cv2.resize(mask, (W, H), interpolation=cv2.INTER_AREA)
        m = np.clip(m.astype(np.float64), 0.0, 1.0)
        # ★★ 掩膜太小时 ⇒ **弃权**退回 `matrix`（同一落点）+ 计数告警。
        _ref = float(m.size if _COVER_REF[0] is None else _COVER_REF[0])
        cover = float(m.sum()) / max(_ref, 1.0)
        if cover < _MIN_MASK_COVER:
            return None, cover
        metered = float(np.sum(field * m) / max(m.sum(), 1e-9))
        ev = -np.log2(max(metered / float(target), 1e-9))
        return (0.0 if not np.isfinite(ev) else float(ev)), cover

    def measure_autoexposure_ev(image, color_space='sRGB', apply_cctf_decoding=True,
                                method='center_weighted'):
        # ★★★ **两条出口都要过锚**（锚对**所有** method 生效）。
        if method != 'subject' or _MASK[0] is None:
            return anchor_ev(_orig_disp(image, color_space, apply_cctf_decoding, method))
        import colour
        xyz = colour.RGB_to_XYZ(np.clip(image, 0, 1), color_space,
                                apply_cctf_decoding=apply_cctf_decoding)
        ev, cover = _masked_ev(xyz[:, :, 1], 0.184, _MASK[0])
        if ev is None:
            _abstain(cover)
            return anchor_ev(_orig_disp(image, color_space, apply_cctf_decoding, 'matrix'))
        return anchor_ev(ev)

    def measure_raw_autoexposure_ev(raw, method='center_weighted', target=1.0):
        """dev 生产路径：`raw` 是胶片曝光（中灰=1），按掩膜测 `mean(raw)`。"""
        if _orig_raw is None:
            return 0.0
        # ★★ 中灰探针**必须最优先判**：`filming._midgray_autoexposure_scale()` 用 1×1 raw
        arr = np.asarray(raw)
        field = np.mean(arr, axis=2) if arr.ndim == 3 else arr
        if field.ndim != 2 or field.size < 16:        # 中灰探针，不是整幅
            return _orig_raw(raw, method, target)
        if method != 'subject' or _MASK[0] is None:
            return anchor_ev(_orig_raw(raw, method, target))
        ev, cover = _masked_ev(field, target, _MASK[0])
        if ev is None:
            _abstain(cover)
            return anchor_ev(_orig_raw(raw, 'matrix', target))
        return anchor_ev(ev)

    _ae.measure_autoexposure_ev = measure_autoexposure_ev
    _ae.measure_raw_autoexposure_ev = measure_raw_autoexposure_ev
    _fm.measure_autoexposure_ev = measure_autoexposure_ev       # 旧版（3bb2c2d）是 from-import 的名字
    if hasattr(_fm, 'measure_raw_autoexposure_ev'):
        _fm.measure_raw_autoexposure_ev = measure_raw_autoexposure_ev   # dev 生产路径
    _INSTALLED[0] = True
    return True
