# -*- coding: utf-8 -*-
r"""**脸部 ROI（肤色口径）**：给"脸 = 肤色像素"这个测量口径一个**唯一实现**。

## 为什么要有这个模块
```
把"脸"当成**几何 ROI**（画圆/画框）在实测里失败过 4 版：YuNet 的框与地标在仰拍近景上
系统性偏小偏上 ⇒ 圆落在**眼睛**上（两眼 L* 差 7.3、C* 27 不像皮肤）。
正确的口径是**像素级**的：脸 = FACE 分割 ∩ **肤色似像素**。
  · 生产侧（`person.py`）已经有 sRGB 域的 `skin_like()` 做这件事（测光用）
  · 本模块给**分析/判分侧**一个 **Lab 域** 的同精神实现（窗外参数与实测一致）
⇒ 两侧各在自己的域里自洽；**两套数字不可互相驱动**（纪律）。
```
## 用法
```
from svFilm import face_roi
m = face_roi.skin_mask_lab(lab)             # lab: (H,W,3) L*a*b*；→ bool
mm = face_roi.face_skin(lab, face_mask)     # 再与 FACE 掩膜取交
Lm, n = face_roi.median_lstar(lab, mm)      # 脸亮度（皮肤中位）+ 有效像素数
```
"""
import numpy as np

# 肤色窗（Lab；与 `_lib/measure` 的实测窗一致）
L_MIN, L_MAX = 30.0, 92.0
C_MIN, C_MAX = 5.0, 22.0
A_MIN, A_MAX = 2.0, 26.0
B_MIN, B_MAX = 4.0, 28.0
MIN_PX = 300


def skin_mask_lab(lab, **kw):
    r"""Lab 域"肤色似"布尔掩膜（不含 FACE 约束）。@returns bool (H,W)"""
    lab = np.asarray(lab, np.float64)
    if lab.ndim != 3 or lab.shape[2] < 3:
        raise ValueError('lab must be (H,W,>=3)')
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)
    g = kw.get
    return ((L >= g('l_min', L_MIN)) & (L <= g('l_max', L_MAX))
            & (C >= g('c_min', C_MIN)) & (C <= g('c_max', C_MAX))
            & (a >= g('a_min', A_MIN)) & (a <= g('a_max', A_MAX))
            & (b >= g('b_min', B_MIN)) & (b <= g('b_max', B_MAX)))


def face_skin(lab, face_mask, min_px=MIN_PX):
    r"""脸 = FACE ∩ 肤色似。@returns (mask|None, n)"""
    f = np.asarray(face_mask, bool)
    if f.shape[:2] != np.asarray(lab).shape[:2]:
        raise ValueError('face_mask/lab shape mismatch')
    m = f & skin_mask_lab(lab)
    n = int(m.sum())
    return (m if n >= min_px else None), n


def median_lstar(lab, mask):
    r"""皮肤区 L* 中位。@returns (L_median, n)；mask 为空 ⇒ (nan, 0)"""
    if mask is None:
        return float('nan'), 0
    L = np.asarray(lab)[..., 0][np.asarray(mask, bool)]
    if L.size == 0:
        return float('nan'), 0
    return float(np.median(L)), int(L.size)


def stats(lab, mask):
    r"""皮肤区 (L*, C*, a*, b*, n) 的中位；mask 为空 ⇒ 全 nan"""
    if mask is None:
        return dict(L=float('nan'), C=float('nan'), a=float('nan'), b=float('nan'), n=0)
    m = np.asarray(mask, bool)
    L = np.asarray(lab)[..., 0][m]
    a = np.asarray(lab)[..., 1][m]
    b = np.asarray(lab)[..., 2][m]
    if L.size == 0:
        return dict(L=float('nan'), C=float('nan'), a=float('nan'), b=float('nan'), n=0)
    return dict(L=float(np.median(L)), C=float(np.median(np.hypot(a, b))),
                a=float(np.median(a)), b=float(np.median(b)), n=int(L.size))
