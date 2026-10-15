# -*- coding: utf-8 -
r"""★★★ **本模块是「曝光主人」的落点**：中灰锚 + 它让位给人本。

`install()` 把引擎的 `measure_autoexposure_ev` 包一层：`ev' = (1 - w) * ev + b` ⇒ `2**ev'` 不再是
"把测光区拉到 0.184"，而是"**整卷偏置 `+b` 档打底 + 权重 `(1-w)` 让测光逐图微调**"；锚常数设 0.0 ⇒
逐位等于修复前。⚠ `install()` **必须无条件执行**（原来挂在 `PERSON_METERING` 的 if 之后 ⇒ 关掉
"测光看人"会连锚一起关掉）；调用点 = `presets._person_metering()`（按人物掩膜加权）。
"""
from __future__ import annotations

import numpy as np

_MASK = [None]          # 当前这张的"人"掩膜（0~1，与输入图同尺寸）；渲染是串行的
_INSTALLED = [False]
_REGIME = ['person']    # 'face' / 'person' —— 决定用哪一对 (w, b)；渲染串行，逐张设置

# ★★ 掩膜占比下限（`m.sum()/m.size`）：低于它 ⇒ **弃权**走 `matrix`（与外层守卫同一落点）。
_MIN_MASK_COVER = 0.003
_ABSTAIN = [0, None]    # [次数, 上次原因] —— 观察者状态，只计数不改画面


def _anchor_wb(regime='person'):
    r"""读 `regime='face'` 时取 `EXPOSURE_ANCHOR_*_FACE`（缺键 ⇒ 退回人档）；**每次动态读** ⇒
    同一进程可 A/B；读不到 ⇒ `(0.0, 0.0)` = 逐位等于修复前。"""
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


def set_mask(m, regime='person'):
    r"""渲染前把这张的测光掩膜放进来（`None` ⇒ `subject` 退回原实现）。"""
    _MASK[0] = None if m is None else np.asarray(m, np.float32)
    _REGIME[0] = 'face' if str(regime) == 'face' else 'person'


def clear():
    _MASK[0] = None
    _REGIME[0] = 'person'


def abstain_count():
    r"""掩膜过小 ⇒ 弃权的**次数**（观察者，给计数测试用）。"""
    return int(_ABSTAIN[0])


def abstain_why():
    return _ABSTAIN[1]


def _reset_abstain():
    _ABSTAIN[0], _ABSTAIN[1] = 0, None


def install():
    r"""把引擎的测光换成「支持 `subject` + 中灰锚」的版本。幂等。"""
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
    orig = _ae.measure_autoexposure_ev

    def measure_autoexposure_ev(image, color_space='sRGB', apply_cctf_decoding=True,
                                method='center_weighted'):
        # ★★★ **两条出口都要过锚**（锚对**所有** method 生效）。
        if method != 'subject' or _MASK[0] is None:
            return anchor_ev(orig(image, color_space, apply_cctf_decoding, method))
        import cv2
        import colour
        H, W = image.shape[:2]
        m = _MASK[0]
        if m.shape[:2] != (H, W):                     # 掩膜要缩到引擎这次用的尺寸
            m = cv2.resize(m, (W, H), interpolation=cv2.INTER_AREA)
        m = np.clip(m.astype(np.float64), 0.0, 1.0)
        # ★★ 掩膜太小时 ⇒ **弃权**退回 `matrix`（同一落点）+ 计数告警；退回别的 method 就是两条落点。
        cover = float(m.sum()) / max(float(m.size), 1.0)
        if cover < _MIN_MASK_COVER:
            _ABSTAIN[0] += 1
            _ABSTAIN[1] = ('mask_cover=%.5f < %.5f' % (cover, _MIN_MASK_COVER))
            if _ABSTAIN[0] == 1 or _ABSTAIN[0] % 100 == 0:
                import sys as _sys
                print('[svFilm 警告] exposure: 测光掩膜太小（占比 %.5f < %.5f）⇒ '
                      '弃权，退回 matrix（本进程第 %d 次）'
                      % (cover, _MIN_MASK_COVER, _ABSTAIN[0]), file=_sys.stderr)
            return anchor_ev(orig(image, color_space, apply_cctf_decoding, 'matrix'))
        xyz = colour.RGB_to_XYZ(np.clip(image, 0, 1), color_space,
                                apply_cctf_decoding=apply_cctf_decoding)
        Y = xyz[:, :, 1]
        exposure = float(np.sum(Y * m) / max(m.sum(), 1e-9)) / 0.184
        ev = -np.log2(max(exposure, 1e-9))
        return anchor_ev(0.0 if not np.isfinite(ev) else float(ev))

    _ae.measure_autoexposure_ev = measure_autoexposure_ev
    _fm.measure_autoexposure_ev = measure_autoexposure_ev   # ★ filming 里是 from-import 的名字
    _INSTALLED[0] = True
    return True
