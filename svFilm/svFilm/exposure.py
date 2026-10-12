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
_REGIME = ['person']    # 'face' / 'person' —— 决定用哪一对 (w, b)；渲染串行，逐张设置

# ★★ 掩膜占比下限（`m.sum()/m.size`）：低于它 ⇒ **弃权**，走 `matrix`（= 与
#    `presets._person_metering` 的占比守卫**同一个落点**；生产路径实测软掩膜占比
#    最低 8.63% ⇒ 本条在产线上是死代码，只有喂小区域掩膜（脸/颊）时才会命中）。
_MIN_MASK_COVER = 0.003
_ABSTAIN = [0, None]    # [次数, 上次原因] —— 观察者状态，只计数不改画面


def _anchor_wb(regime='person'):
    r"""读常数：`regime='face'` 读 `EXPOSURE_ANCHOR_*_FACE`（缺键 ⇒ 退回人档那一对）。

    **每次动态读** ⇒ 同一进程里可做 A/B、可回退。
    读不到（老 config / 被删键）⇒ 返回 `(0.0, 0.0)` = **逐位等于修复前**（安全侧）。
    """
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
    r"""`ev' = (1 - w) * ev + b` —— **唯一改动点**（一行）。

    `w/b` 按当前档位（`_REGIME`）取：有脸档 / 人档；`w = b = 0` ⇒ 原样返回（回退点）。
    """
    w, b = _anchor_wb(_REGIME[0])
    if w == 0.0 and b == 0.0:
        return ev
    return (1.0 - w) * ev + b


def regime():
    """当前档位（'face' / 'person'）—— 给测试与排障用。"""
    return _REGIME[0]


def set_mask(m, regime='person'):
    """渲染前把这张的测光掩膜放进来（`None` ⇒ `subject` 退回原实现）。

    `regime` ⇒ 用哪一对 `(w, b)`：`'face'` = 脸掩膜档，`'person'` = 人掩膜档。
    """
    _MASK[0] = None if m is None else np.asarray(m, np.float32)
    _REGIME[0] = 'face' if str(regime) == 'face' else 'person'


def clear():
    _MASK[0] = None
    _REGIME[0] = 'person'


def abstain_count():
    """掩膜过小 ⇒ 弃权的**次数**（观察者；给计数测试与排障用）。"""
    return int(_ABSTAIN[0])


def abstain_why():
    """上次弃权的原因串（没弃权过 ⇒ `None`）。"""
    return _ABSTAIN[1]


def _reset_abstain():
    """只给计数测试用（清观察者状态，不碰画面）。"""
    _ABSTAIN[0], _ABSTAIN[1] = 0, None


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
        # ★★ 掩膜太小时（人太小 / 没检到 / 喂进来的是脸那种小区域）⇒ **弃权**。
        #   ⚠ 2026-10-12 修：原来退回的是 `center_weighted`，而**外层**守卫
        #     （`presets._person_metering` 的占比守卫）退回的是 `matrix` ⇒ 同一件事两条
        #     互不相同的落点，而且**一声不响**（实测：喂脸掩膜时静默退回"看中央"）。
        #   ⇒ 统一到 `matrix`，并加计数 + 告警（阈值见 `_MIN_MASK_COVER`）。
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
        # ★ 与引擎同口径：Y 的掩膜加权平均 ÷ 0.184（18% 中灰）
        exposure = float(np.sum(Y * m) / max(m.sum(), 1e-9)) / 0.184
        ev = -np.log2(max(exposure, 1e-9))
        return anchor_ev(0.0 if not np.isfinite(ev) else float(ev))

    _ae.measure_autoexposure_ev = measure_autoexposure_ev
    _fm.measure_autoexposure_ev = measure_autoexposure_ev   # ★ filming 里是 from-import 的名字
    _INSTALLED[0] = True
    return True
