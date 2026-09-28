# -*- coding: utf-8 -*-
r"""**脸增益** —— 在负片的 CMY 密度（`cmy_film` tap）上，**只给脸加密度**。

## 一、为什么要有它
引擎的测光（`camera.auto_exposure`）定的是**整张落点** ⇒
**"提脸"必然推亮整张**（实测：`partial`/`median` 能把脸拉到 75~79，但整张也到 73~78）。
⇒ **要"只提脸"，不能在测光上做**，只能在**局部的物理中间态**上做。

## 二、做法 = **闭环反馈 + 迭代到收敛**（有出处）
- **闭环反馈**：*Closed-loop Color Refinement in Camera ISP*（IS&T 2026, HVEI-213）——
  「在**管线末端**评估、和目标感知色比较、把偏差**反馈上游处理块**」；
  并演示了**肤色改进**：取脸区 → 滤非肤 → 算代表肤色 → 和目标比 → 导出调整参数。
  它还点出：**「固定参数无法补偿其他块引入的偏差」** —— 正是"固定 delta 会过头"的原因。
- **迭代到收敛**：*Iterative colour correction*（JVCIR 2010）「迭代若干轮，变化很小时停止」；
  *DeltaE 生产算法*（GenMind）「**反馈精修：迭代到 ΔE 达标**」。
- **判据**：**ΔE00**（出处给的靶 **≤ 3.0**）。

## 三、响应矩阵（标定过，`_calib_resp.py` 跑的）
在"负片密度 +δ"与"脸 (L\*, a\*, b\*) 变化"之间做线性化，实测（4 张中位，每 +1.0 密度）：
```
ΔL*: [ 24.9   58.0   11.8]        C: [ 0.0139  0.0088  0.0031]   ← 逆矩阵
Δa*: [ 62.1 -114.6   16.7]        M: [ 0.0091 -0.0039  0.0004]      （用它反算该加多少）
Δb*: [ 33.7   65.8 -100.6]        Y: [ 0.0106  0.0004 -0.0086]
```
物理检查：加 C ⇒ 更红更黄 · 加 M ⇒ 更绿 · 加 Y ⇒ 更蓝 ✓

## 四、掩膜
**脸 ∪ 身体皮肤**（脸 1.0 / 身体 `BODY_W`）+ **羽化**（σ = 脸等效边长 × 1/6，照 CN104038704A）
—— 只圈脸的话，脸亮而脖子/手臂还暗 ⇒ 接缝一眼可见（SV 09-29 报的"突兀"）。
"""
from __future__ import annotations

import numpy as np

from . import color
from . import config as C

# ---- 标定过的常数 ----
# 逆响应矩阵：`[C,M,Y] 增量 = M^-1 · [ΔL*, Δa*, Δb*]`（从 `_calib_resp.py` 的实测标定）
RESP_INV = np.array([
    [0.0139, 0.0088, 0.0031],
    [0.0091, -0.0039, 0.0004],
    [0.0106, 0.0004, -0.0086],
], np.float64)
DAMP = 0.65               # 阻尼（<1 ⇒ 防过冲；迭代几轮比"一次解方程"稳）
MAX_ITERS = 4             # 最多迭代几轮
STOP_DE = 1.5             # ΔE00 小于它就停（出处给的"好"是 ≤3.0，这里更严一档）
STEP_LIMIT = 0.12         # 单轮单通道最大增量（保险丝）
TOTAL_LIMIT = 0.40        # 累计上限
FEATHER = 1.0 / 6.0       # 掩膜羽化 σ = 脸的等效边长 × 它（照 CN104038704A，与 L4 同一口径）
# ★★ 掩膜必须含"身体皮肤"：只圈脸 ⇒ 脸亮、脖子/手臂暗 ⇒ 接缝可见（SV：「不然太突兀了」）
BODY_W = 0.5              # 脸 1.0 / 身体 0.5（同 `GRADE_SKIN_BODY_W`）


def _mask(pz, shape):
    """脸掩膜 = **脸 ∪ 身体皮肤**（脸 1.0 / 身体 `BODY_W`），羽化 + 峰值归一。"""
    mk = (pz or {}).get('masks') or {}
    fs = mk.get('face_skin')
    if fs is None:
        return None
    m = np.clip(np.asarray(fs, np.float64), 0.0, 1.0)
    sk = mk.get('skin')                       # 身体皮肤（没有就只用脸）
    if sk is not None and np.shape(sk)[:2] == m.shape[:2]:
        m = np.maximum(m, np.clip(np.asarray(sk, np.float64), 0.0, 1.0) * BODY_W)
    if tuple(m.shape[:2]) != tuple(shape[:2]):
        return None                      # 尺寸对不上 ⇒ 不认（换了渲染尺寸必须现算）
    if float(m.max()) <= 0.05:
        return None
    if FEATHER > 0:
        from scipy.ndimage import gaussian_filter
        side = float(np.sqrt(max(int((m > 0.3).sum()), 1)))
        m = gaussian_filter(m, max(1.0, side * FEATHER))
        mx = float(m.max())
        if mx > 1e-6:
            m = np.clip(m / mx, 0.0, 1.0)      # 峰值归一回 1（羽化会降峰）
    return m


def face_lab(out, mask, sel_thr=0.5):
    """成片上「脸」的 (L*, a*, b*) 中位（中位 = 抗噪）。`None` = 量不到。"""
    sel = mask > sel_thr
    if int(sel.sum()) < 200:
        return None
    lab = color.to_lab(np.ascontiguousarray(np.clip(out, 0.0, 1.0)))
    return (float(np.median(lab[..., 0][sel])),
            float(np.median(lab[..., 1][sel])),
            float(np.median(lab[..., 2][sel])))


_DE_METRIC = ['cie2000']


def _de00(lab1, lab2):
    r"""ΔE00（CIEDE2000）。

    ⚠⚠ **`colour` 的方法名是 `'cie2000'`，不是 `'CIEDE2000'`。**
      写成 `'CIEDE2000'` 会**抛 ValueError**，而下面那个 `except` 会把它**静默**换成欧氏距离
      ⇒ 报出来的"ΔE00"其实是 **ΔE76**（数值偏大）—— **09-29 真踩过**（整批 14 个格全是假 ΔE00）。
      ⇒ 现在**把"用的哪把尺子"记进报告**（`info['de_metric']`），**不许再静默**。
    """
    try:
        from colour.difference import delta_E
        _DE_METRIC[0] = 'cie2000'
        return float(delta_E(np.asarray([lab1], np.float64),
                             np.asarray([lab2], np.float64), method='cie2000')[0])
    except Exception:                                          # noqa: BLE001
        _DE_METRIC[0] = 'euclid(ΔE76 回退 —— colour 不可用)'
        return float(np.sqrt(sum((x - y) ** 2 for x, y in zip(lab1, lab2))))


def apply(pl, lin, pz, target_L=None, target_a=None, target_b=None, cfg=C):
    r"""**跑到成片 → 量脸 → 反算 C/M/Y 增量 → 迭代到 ΔE 达标 → 出图。**

    `pl`：`SimulationPipeline`（**调用方负责建 + 保证路径**）
    `target_L / target_a / target_b`：脸的靶（Lab 绝对）。
      · **全空 / `target_L<=0`** ⇒ 不动，**逐位同旧行为**
      · 只给 `target_L` ⇒ 只按亮度做（老的"单值 delta"行为，兼容）
      · 三个都给 ⇒ **分通道迭代**（推荐）

    @returns {(ndarray, dict)} 出图 + 报告（`iters` / `delta` / `lab_before` / `lab_after` / `de00`）
    """
    cmy = pl.process(lin, collect='cmy_film')            # ① 负片 CMY 密度
    out = pl.process(cmy, inject='cmy_film')             # ② 基线成片
    info = dict(applied=False, iters=0, delta=[0.0, 0.0, 0.0],
                lab_before=None, lab_after=None, de00=None, target=[target_L, target_a, target_b])
    if not target_L or target_L <= 0:
        return out, info
    m = _mask(pz, out.shape)
    if m is None:
        info['note'] = '掩膜不可用（尺寸不符 / 没脸）⇒ 不动'
        return out, info
    m3 = m[..., None]
    lab0 = face_lab(out, m)
    if lab0 is None:
        info['note'] = '量不到脸 ⇒ 不动'
        return out, info
    info['lab_before'] = list(lab0)

    # 靶：只给 L* ⇒ a/b 用"当前值"（= 只按亮度做，兼容老行为）
    tgt = np.array([float(target_L),
                    float(target_a) if target_a is not None else lab0[1],
                    float(target_b) if target_b is not None else lab0[2]], np.float64)
    only_L = (target_a is None and target_b is None)

    delta = np.zeros(3, np.float64)
    cur_lab = lab0
    de = _de00(cur_lab, tgt)
    for it in range(1, MAX_ITERS + 1):
        if de <= STOP_DE:
            break
        err = tgt - np.asarray(cur_lab, np.float64)
        if only_L:
            err[1] = err[2] = 0.0                    # 只按亮度做
        step = RESP_INV @ err * DAMP
        step = np.clip(step, -STEP_LIMIT, STEP_LIMIT)
        delta = np.clip(delta + step, -TOTAL_LIMIT, TOTAL_LIMIT)
        out = pl.process(cmy + delta[None, None, :] * m3, inject='cmy_film')
        new_lab = face_lab(out, m)
        if new_lab is None:
            break
        cur_lab = new_lab
        de = _de00(cur_lab, tgt)
        info['iters'] = it
        if abs(step).max() < 1e-4:
            break
    info.update(applied=bool(info['iters'] > 0), delta=[float(x) for x in delta],
                lab_after=list(cur_lab), de00=de, de_metric=_DE_METRIC[0])
    return out, info
