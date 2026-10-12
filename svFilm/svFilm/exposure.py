# -*- coding: utf-8 -
r"""★★★ **本模块是「曝光主人」的落点**：中灰锚（10-11）+ 它让位给人本（10-12）。

`install()` 把引擎的 `measure_autoexposure_ev` 包一层，返回值改成
    `ev' = (1 - w) * ev + b`          （`w/b` = `config.EXPOSURE_ANCHOR_W/B`）
⇒ `filming.auto_exposure()` 乘出去的 `2**ev'` 不再是"把测光区拉到 0.184"，
而是"**整卷偏置 `+b` 档打底 + 权重 `(1-w)` 让测光逐图微调**"。
现值 `w = 0.6 / b = 1.1181`（10-12 用户拍板「锚打底 + 人本」）：`ev` 是**逐图**的，
而 `b` 已按 `b = 1.578 - (1-w)·ēv` 重标过 ⇒ **整卷平均落点守恒**（`mean(ev') = 1.578`）。
⚠ `w = 1.0` 是"**锚独裁**"（`ev' ≡ b`，测光算完没人听）——
**那一版没有经过用户拍板**，见 `config.py` 的 `_note_anchor_1012`。
依据 / 推导 / 52 张复验 / 代价：`E:\ds\photo\中灰锚_1011\中灰锚研究.md`，
两版常数的来历写在 `config.py` 的 `_note_anchor_1011` / `_note_anchor_1012` 两段。
**回退**：`config.EXPOSURE_ANCHOR_W = EXPOSURE_ANCHOR_B = 0.0` ⇒ 逐位等于修复前。

## 调用点（**不是"未接线"** —— 旧文件头那句话已作废）
`presets._person_metering()`（`presets.render()` 每张都走）→ `install()`。
⚠ 它原来挂在 `PERSON_METERING` 那个 if **之后** ⇒ 关掉"测光看人"会**连锚一起关掉**
（两件事本来无关）。10-11 已把 `install()` 提到最前面无条件执行。

## 本模块的另一半：「以人为本的测光」
对人物掩膜加权（`method="subject"`；`presets.py:825` 把 method 切过去），**实测有效**
（37 张复验：逆光组脸 +17.6、变差 0 张）。
- 10-11 `w = 1.0` 期间它**曾是 no-op**（`ev` 被换成常数 ⇒ "看哪里"不影响曝光）；
  **10-12 `w = 0.6` 起它重新在岗** —— 锚只占 6 成，`(1-w)·ev` 就是逐图分量。
- ⚠ 旧头部写的"`presets.render_metered()` / `service` 还有调用方" **10-12 核实是假的**：
  `render_metered()` 全仓**零调用方**（只有定义，`presets.py:682`）。
- 不经过 `presets.render()` 的自动接线、要直接用 ⇒ 渲染前 `install()` +
  `set_mask(person_mask)`，渲染后 `clear()`。
"""
from __future__ import annotations

import numpy as np

_MASK = [None]          # 当前这张的"人"掩膜（0~1，与输入图同尺寸）；渲染是串行的
_INSTALLED = [False]


def _anchor_wb():
    r"""读 `config.EXPOSURE_ANCHOR_W/B`（**每次动态读** ⇒ 同一进程里可做 A/B、可回退）。

    读不到（老 config / 被删键）⇒ 返回 `(0.0, 0.0)` = **逐位等于修复前**（安全侧）。
    """
    try:
        from . import config as _C
        w = float(getattr(_C, 'EXPOSURE_ANCHOR_W', 0.0) or 0.0)
        b = float(getattr(_C, 'EXPOSURE_ANCHOR_B', 0.0) or 0.0)
    except Exception:                                                        # noqa: BLE001
        return 0.0, 0.0
    return w, b


def anchor_ev(ev):
    r"""`ev' = (1 - w) * ev + b` —— **唯一改动点**（一行）。

    `w = b = 0` ⇒ 原样返回（逐位等于修复前，回退点）。
    """
    w, b = _anchor_wb()
    if w == 0.0 and b == 0.0:
        return ev
    return (1.0 - w) * ev + b


def set_mask(m):
    """渲染前把这张的"人"掩膜放进来（`None` ⇒ `subject` 退回原实现）。"""
    _MASK[0] = None if m is None else np.asarray(m, np.float32)


def clear():
    _MASK[0] = None


def install():
    """把引擎的测光换成「支持 `subject` + 中灰锚」的版本。幂等。"""
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
        # ★★★ 10-11：**两条出口都要过锚** —— 锚必须对**所有** method 生效
        #   （它修的是"分母写死 0.184"，与"看哪里"无关）。
        if method != 'subject' or _MASK[0] is None:
            return anchor_ev(orig(image, color_space, apply_cctf_decoding, method))
        import cv2
        import colour
        H, W = image.shape[:2]
        m = _MASK[0]
        if m.shape[:2] != (H, W):                     # 掩膜要缩到引擎这次用的尺寸
            m = cv2.resize(m, (W, H), interpolation=cv2.INTER_AREA)
        m = np.clip(m.astype(np.float64), 0.0, 1.0)
        # 掩膜太小时（人太小/没检到）⇒ 弃权，交给原实现
        if m.sum() < 0.02 * m.size * 0.15:
            return anchor_ev(orig(image, color_space, apply_cctf_decoding, 'center_weighted'))
        xyz = colour.RGB_to_XYZ(np.clip(image, 0, 1), color_space,
                                apply_cctf_decoding=apply_cctf_decoding)
        Y = xyz[:, :, 1]
        # ★ 与引擎同口径：Y 的掩膜加权平均 ÷ 0.184（18% 中灰）
        exposure = float(np.sum(Y * m) / max(m.sum(), 1e-9)) / 0.184
        ev = -np.log2(max(exposure, 1e-9))
        return anchor_ev(0.0 if not np.isfinite(ev) else float(ev))

    _ae.measure_autoexposure_ev = measure_autoexposure_ev
    _fm.measure_autoexposure_ev = measure_autoexposure_ev   # ★ filming 里是 from-import 的名字
    _INSTALLED[0] = True
    return True
