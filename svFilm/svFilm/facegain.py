# -*- coding: utf-8 -*-
r"""**脸增益** —— 在负片的 CMY 密度（`cmy_film` tap）上，**只给脸加密度**。

## 为什么要有它
引擎的测光（`camera.auto_exposure`）定的是**整张落点** ⇒
**"提脸"必然推亮整张**（实测：`partial`/`median` 能把脸拉到 75~79，但整张也到 73~78）。
⇒ **要"只提脸"，不能在测光上做**（那条路不通），只能在**局部的物理中间态**上做。

## 做法（0.3.4 的 tap 系统）
```python
cmy  = pl.process(lin, collect='cmy_film')      # ① 取「负片 CMY 密度」
cmy2 = cmy + delta * mask                       # ② 只在脸上加密度
out  = pl.process(cmy2, inject='cmy_film')      # ③ 继续跑到出图
```
- 通道顺序实测 = **[C, M, Y]**（加 M ⇒ 印相后偏绿 ⇒ hue 106°；加 Y ⇒ 偏品红 ⇒ hue 324°）
- **负片物理**：曝光多 ⇒ 密度高 ⇒ 印出来亮 ⇒ **要脸亮就"加密度"** ✓
- 加 Y 会"减黄"（印相后偏蓝）⇒ 实测 **Y 该【加得比 C/M 少】**（约 0.57 倍）才能保住脸色相

## ★★ 自适应（为什么不能给固定值）
实测固定 `[C.14 M.14 Y.08]`：
`1231` 脸 L\* 47→64（正好）· **`1665` 58→74（过头，SV：「亮得突兀、甚至吓人」）**
⇒ **delta 该按"脸离靶多远"算** ⇒ **本来就到靶的片 delta≈0**。

`DELTA_PER_L = 0.0082` 的来历：实测 δ_L 0.14 ⇒ 脸 L\* **+17.1**（46.9→64.0）⇒ 0.14/17.1。
"""
from __future__ import annotations

import numpy as np

from . import color
from . import config as C

# ---- 标定过的常数 ----
DELTA_PER_L = 0.0082      # 每 1 格 L* 要加多少密度（实测 δ0.14 ⇒ +17.1 格）
Y_RATIO = 0.57            # Y 增量 / C、M 增量（0.08/0.14）—— 加多了会丢黄（印相后偏蓝）
MAX_DELTA = 0.30          # 单次上限（保险丝；再大就是"把脸糊成一片"）
MIN_DELTA = 0.004         # 小于它就当"不用动"（避免给本来就到靶的片加噪声）
FEATHER = 1.0 / 6.0       # 掩膜羽化 σ = 脸的等效边长 × 它（照 CN104038704A，与 L4 同一口径）
# ★★ 09-29（SV：「身体肤色能不能也处理一下，不然太突兀了」）——
#   **掩膜必须含"身体皮肤"**，否则只有脸亮、脖子/手臂还暗 ⇒ 接缝一眼可见。
#   脸权重 1.0、身体 0.5（和 `GRADE_SKIN_BODY_W` 同一个约定）。
BODY_W = 0.5


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


def face_L(out, mask):
    """成片上「脸」的 L* 中位（中位 = 抗噪）。"""
    lab = color.to_lab(np.ascontiguousarray(np.clip(out, 0.0, 1.0)))
    sel = mask > 0.5
    if not bool(sel.any()):
        return None
    return float(np.median(lab[..., 0][sel]))


def apply(pl, lin, pz, target_L, cfg=C):
    r"""**跑到成片 → 量脸 → 按差值算 delta → 带 delta 再跑到成片。**

    `pl`：`spektrafilm.runtime.pipeline.SimulationPipeline`（**调用方负责建 + 保证路径**）
    `target_L`：脸的 L\* 靶（None 或 <=0 ⇒ 关，逐位同旧行为）

    @returns {(numpy.ndarray, dict)} 出图 + 报告（`delta` / `face_L_before` / `face_L_after` / `applied`）
    """
    base = pl.process(lin, collect='cmy_film')          # ① 负片 CMY 密度
    out = pl.process(base, inject='cmy_film')           # ② 基线成片
    info = dict(applied=False, delta=0.0, face_L_before=None, face_L_after=None, target_L=target_L)
    if not target_L or target_L <= 0:
        return out, info
    m = _mask(pz, out.shape)
    if m is None:
        info['note'] = '掩膜不可用（尺寸不符 / 没脸）⇒ 不动'
        return out, info
    L0 = face_L(out, m)
    info['face_L_before'] = L0
    if L0 is None:
        info['note'] = '量不到脸 ⇒ 不动'
        return out, info
    d = float(np.clip((float(target_L) - L0) * DELTA_PER_L, 0.0, MAX_DELTA))
    if d < MIN_DELTA:
        info['note'] = '脸已在靶附近 ⇒ 不动'
        info['face_L_after'] = L0
        return out, info
    delta = np.array([d, d, d * Y_RATIO], np.float64)
    out2 = pl.process(base + delta[None, None, :] * m[..., None], inject='cmy_film')
    info.update(applied=True, delta=float(d))
    info['face_L_after'] = face_L(out2, m)
    return out2, info
