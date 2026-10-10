# -*- coding: utf-8 -
r"""⚠⚠ **本模块当前未接线（零调用点，10-11 实测确认）** —— 只作记录保留。

它实现「以人为本的测光」（对人物掩膜加权，`method="subject"`），**实测有效**
（37 张复验：逆光组脸 +17.6、变差 0 张），但**当前没有任何调用点**。
用户 10-11 目检选定的机制是 `targets.json` 的
`_scene_engine["lighttype=逆光"] → enlarger.print_exposure`，那条**是接上的**。
**要用它** ⇒ 渲染前 `install()` + `set_mask(person_mask)`，渲染后 `clear()`。
（下面是原始模块文档，保留作完整设计记录。）
"""
from __future__ import annotations

import numpy as np

_MASK = [None]          # 当前这张的"人"掩膜（0~1，与输入图同尺寸）；渲染是串行的
_INSTALLED = [False]


def set_mask(m):
    """渲染前把这张的"人"掩膜放进来（`None` ⇒ `subject` 退回原实现）。"""
    _MASK[0] = None if m is None else np.asarray(m, np.float32)


def clear():
    _MASK[0] = None


def install():
    """把引擎的测光换成支持 `subject` 的版本。幂等。"""
    if _INSTALLED[0]:
        return True
    try:
        # ★★ 必须先钉扎：否则 `import spektrafilm` 会被本机 pip 装的那份抢先
        #    （项目有来源守卫 `spektra._sf()`，它会直接报错"加载到的不是我们指定的那一份"）。
        from . import spektra as _sp
        _sp._sf()
        from spektrafilm.utils import autoexposure as _ae
        from spektrafilm.runtime.stages import filming as _fm
    except Exception:                                                        # noqa: BLE001
        return False
    orig = _ae.measure_autoexposure_ev

    def measure_autoexposure_ev(image, color_space='sRGB', apply_cctf_decoding=True,
                                method='center_weighted'):
        if method != 'subject' or _MASK[0] is None:
            return orig(image, color_space, apply_cctf_decoding, method)
        import cv2
        import colour
        H, W = image.shape[:2]
        m = _MASK[0]
        if m.shape[:2] != (H, W):                     # 掩膜要缩到引擎这次用的尺寸
            m = cv2.resize(m, (W, H), interpolation=cv2.INTER_AREA)
        m = np.clip(m.astype(np.float64), 0.0, 1.0)
        # 掩膜太小时（人太小/没检到）⇒ 弃权，交给原实现
        if m.sum() < 0.02 * m.size * 0.15:
            return orig(image, color_space, apply_cctf_decoding, 'center_weighted')
        xyz = colour.RGB_to_XYZ(np.clip(image, 0, 1), color_space,
                                apply_cctf_decoding=apply_cctf_decoding)
        Y = xyz[:, :, 1]
        # ★ 与引擎同口径：Y 的掩膜加权平均 ÷ 0.184（18% 中灰）
        exposure = float(np.sum(Y * m) / max(m.sum(), 1e-9)) / 0.184
        ev = -np.log2(max(exposure, 1e-9))
        return 0.0 if not np.isfinite(ev) else float(ev)

    _ae.measure_autoexposure_ev = measure_autoexposure_ev
    _fm.measure_autoexposure_ev = measure_autoexposure_ev   # ★ filming 里是 from-import 的名字
    _INSTALLED[0] = True
    return True
