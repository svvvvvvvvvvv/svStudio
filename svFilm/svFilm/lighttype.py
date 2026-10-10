# -*- coding: utf-8 -*-
r"""**光型（二维）** —— 把「逆光 / 面光 / 缝隙光」变成**两个连续量**上的位置。

## 为什么要它
`scene.py` 的 `back` 轴比较的是「**主体 vs 它身后的背景**」。但**缝隙光**（树叶/栏杆/窗户
漏下来的斑驳光）在主体身上是**亮斑 + 暗块并存**的 —— 背景那一侧看不出区别
⇒ 分类器判不出来，**落进「面光/顺平光」这个兜底档**。

10-10 实测（29 张有脸的）证实了这件事：
  `back` 判成「面光/顺平光」的那批里，**脸离散 2.1~4.6、脸亮斑 15.8~37.7%**
  ⇒ 它们其实是**缝隙光**，被混进了"面光"。

## 两个量（零成本：只要有输入图 + `person` 掩膜）
| 量 | 含义 | 算法 |
|---|---|---|
| `ratio_p` | **人相对整张**亮不亮 | 人物掩膜内 Y 的均值 ÷ 整张 Y 的均值 |
| `hot`     | **脸上斑驳程度**（缝隙光的直接证据） | FACE 皮肤掩膜内 `Y > 1.6×脸中位` 的像素占比 % |
| `spread_f`| 脸的明暗跨度（辅助） | 脸内 (P90−P10) ÷ 脸中位 |
| `ratio_f` | 脸相对整张 | 脸内 Y 均值 ÷ 整张 Y 均值 |

## 判型（实测阈值）
```
hot ≥ 15%                  ⇒ 缝隙光    （没有单一正确曝光）
hot < 15% 且 ratio_p < 1.0 ⇒ 逆光      （人均匀地暗 ⇒ 对人测光成立）
hot < 15% 且 ratio_f > 1.15⇒ 面光      （人均匀地亮）
其余                        ⇒ 中性
```

## 为什么"缝隙光"必须单独一类
**它决定「对人测光」成不成立**：缝隙光下人身上一半被亮斑打亮、一半在暗处
⇒ **平均落在哪边都不对** ⇒ 对人测光**前提不成立**。10-10 实测：`subject` 测光
变差最大的两张（DSCF1526 −12.2、DSCF1289 −2.7）**都是缝隙光**（hot 28.4% / 33.3%）。
"""
from __future__ import annotations

import numpy as np


def chroma_free_ratio(*a, **k):        # 占位，保持模块可 import
    return None


def measure(disp, person_mask=None, cfg=None, side=1536):
    """量二维光型。`person_mask` 为 `None` 时**内部自己跑** `person.person()`。

    @returns dict：`ratio_p` / `ratio_f` / `spread_f` / `hot` / `type` / `why`
    """
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


def pick_method(info):
    """按光型选测光方式。**只在一处做决定**（和 `scene.token()` 同样的"一个函数负责词形"哲学）。

    缝隙光 ⇒ `matrix`（没有单一正确曝光，别动）
    逆光   ⇒ `subject`（对人测光；实测 +18~+21）
    其余   ⇒ `matrix`
    """
    return 'subject' if (info or {}).get('type') == '逆光' else 'matrix'


#: 缓存键/自检要用：改了判据就 +1（学 `scene.VERSION`）
VERSION = 1
