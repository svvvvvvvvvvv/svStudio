# -*- coding: utf-8 -*-
r"""**光型（二维）** —— 逆光 / 面光 / 缝隙光 落在两个连续量上（只需输入图 + `person` 掩膜）。

★ 为什么要它：`back` 轴比的是「主体 vs 它身后的背景」，但**缝隙光**（树叶 / 栏杆漏下的斑驳光）
  在主体身上是**亮斑 + 暗块并存**、背景那侧看不出区别 ⇒ 判不出来，落进「面光」兜底档。
量：`ratio_p` = 人掩膜内 Y 均值 ÷ 整张 · `ratio_f` = 脸内 Y 均值 ÷ 整张 · `hot` = FACE 掩膜内
  `Y > 1.6×脸中位` 的占比 %（**斑驳程度** = 缝隙光的直接证据）。
判型：`hot ≥ 15%` ⇒ **缝隙光**；否则 `ratio_p < 1.0` ⇒ **逆光**；`ratio_f > 1.15` ⇒ **面光**；
★★ 缝隙光必须单独一类（一半亮斑一半暗 ⇒ 平均落在哪边都不对）。"""
from __future__ import annotations

import numpy as np


def chroma_free_ratio(*a, **k):        # 占位，保持模块可 import
    return None


def measure(disp, person_mask=None, cfg=None, side=1536):
    """量二维光型（`person_mask` 为 `None` 时自己跑 `person.person()`）。
    @returns `ratio_p` / `ratio_f` / `spread_f` / `hot` / `type` / `why`"""
    import colour
    from . import person as P

    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    Y = colour.RGB_to_XYZ(d, 'sRGB', apply_cctf_decoding=True)[:, :, 1]
    Ymean = float(Y.mean())
    pm = person_mask
    if pm is None:
        try:
            pm = P.person(d)
        except Exception:                                                    # noqa: BLE001
            pm = None
    out = dict(ratio_p=None, ratio_f=None, spread_f=None, hot=None,
               type='未知', why=None)
    if pm is None or float(np.asarray(pm).mean()) < 1e-4:
        out['why'] = 'no_person'
        return out
    mp = np.asarray(pm) > 0.5
    out['ratio_p'] = (float(Y[mp].mean()) / max(Ymean, 1e-9)
                      if int(mp.sum()) > 200 else None)
    try:
        fm = P.masks(d, (P.FACE,))[P.FACE]
    except Exception:                                                        # noqa: BLE001
        fm = None
    if fm is not None and int((fm > 0.5).sum()) > 300:
        mf = fm > 0.5
        yf = Y[mf]
        med = float(np.median(yf))
        out['ratio_f'] = float(yf.mean()) / max(Ymean, 1e-9)
        out['spread_f'] = float(np.percentile(yf, 90) - np.percentile(yf, 10)) / max(med, 1e-9)
        out['hot'] = float((yf > 1.6 * med).mean() * 100.0)
    # ---- 判型 ----
    hot = out['hot']
    if hot is not None and hot >= 15.0:
        out['type'] = '缝隙光'
    elif out['ratio_p'] is not None and out['ratio_p'] < 1.0:
        out['type'] = '逆光'
    elif out['ratio_f'] is not None and out['ratio_f'] > 1.15:
        out['type'] = '面光'
    elif out['ratio_p'] is not None:
        out['type'] = '中性'
    else:
        out['type'] = '未知'
    return out


#: 缓存键/自检要用：改了判据就 +1（学 `scene.VERSION`）
VERSION = 1
