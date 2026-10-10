# -*- coding: utf-8 -*-
r"""⚠⚠ **本模块当前未接线（零调用点）—— 10-11 由教程对照任务实测确认。**

它实现了「以人为本的测光」（`auto_exposure_method="subject"`：对人物掩膜加权测光），
**实测有效**（37 张复验：逆光组脸 +17.6、变差 0 张）。

**但 10-11 起不再走这条路** —— 用户 10-11 目检选定的机制是
`targets.json` 的 `_scene_engine["lighttype=逆光"]` → `enlarger.print_exposure`，
那条**是接上的**（`pipeline.run_from` → `targets.scene_engine`）。

**为什么留着**：它记录了"对人测光"这条路的完整做法与实测数据（含"改 vendor 的替代方案"），
以后若要重新评估测光方式，从这里开始（**不必重新调研**）。
**要用它** ⇒ 渲染前调 `install()` + `set_mask(person_mask)`，渲染后 `clear()`。
r"""**以人为本的测光** —— 让引擎的曝光决策去看"人在哪"，而不是看整张。

## 为什么（用户 10-10 定）
「整张照片该以人为本：引擎曝光要开多少，应该是让**人+脸的曝光好看**，去动态参数，
  **而不是专门去修脸**。」

## 引擎的结构缺陷（读 `utils/autoexposure.py` 得知）
所有测光方式都是同一件事：`exposure = 亮度的加权平均 / 0.184`，区别只是**对画面怎么加权**：
`average` / `median` / `center_weighted`（画面中心高斯）/ `matrix`（5×5 网格中心加权）/
`partial` / `multi_zone` / `highlight_weighted`。
**⇒ 没有一个知道人在哪。** 逆光片里整张平均被亮背景拉高 ⇒ 曝光被压 ⇒ **脸跟着变暗**。
**⇒ 这就是"曝光被背景劫持"。**

## 做法（**不改 vendored 代码**）
`filming.py:40 auto_exposure()` 是普通函数调用 ⇒ 在 svFilm 侧把
`measure_autoexposure_ev` 换成一个**多认识一个方法名 `subject`** 的版本：
`method=='subject'` 时用**人物掩膜**加权，其余方法名原样转给原实现。
★ 只改"测光看哪里"，**整条归一化链（auto print exposure 等）一个字不动**。

## 为什么这条路能绕开掩膜精度问题
"提亮脸"需要**精确**掩膜（只圈脸、不碰头发背景）—— 那个模型不够准，我实测失败。
"**对人物测光**"只需要**粗糙**掩膜（对一个区域取平均，差一点无所谓）
⇒ **项目那条"`person.py` 只做人在哪、不许做人脸检测"的守则，正好就是为这种粗用途定的。**
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
