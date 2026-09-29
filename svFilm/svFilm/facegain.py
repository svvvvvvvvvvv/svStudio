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
**★ 「作用」和「量」是两张不同的掩膜**（09-29 拆开，理由见 `_masks` 的 docstring）：
- **作用** = 脸 ∪ 身体皮肤（脸 1.0 / 身体 `BODY_W`）+ 羽化 + 峰值归一
  —— 只圈脸的话，脸亮而脖子/手臂还暗 ⇒ 接缝一眼可见（SV 09-29 报的"突兀"）。
- **量** = **只用脸**（认不到脸才回退成并集）—— 有靶的是脸，闭环必须盯脸。
★★ **闸门卡在「最终掩膜」的像素数上**（`config.FACE_GAIN_MIN_MASK_PX = 2000`）：
掩膜小到量不准就不动。**不是**"真脸为 0 就不动"——
`DSCF1629` 实测真脸 0 px（掩膜全靠身体皮肤撑）而它是**收住**的一张（ΔE00 0.95）
⇒ 那条闸门会把好案例筛掉。**掩膜 = 脸 ∪ 身体，闸门也看这个并集。**

## 五、★★★ 09-29（C2）：**「身体」也有自己的闭环了**

### 为什么（C1 三点定级查出来的，不是猜）
- **场景本身没这个差**：RAW 上「身体彩度 ÷ 脸彩度」中位 **1.15**，把 RAW 提到与成片同亮度后
  **仍是 1.15** ⇒ 不是"手臂本来就在暖光里"。
- 差是**管线造**的，而且**两级都造**：6 张里「引擎基线」是大头 3 张、「脸增益」是大头 2 张。
- 根子：**脸被闭环钉在靶上**（成片脸 C 6 张全在 18~20），**身体只拿 0.8 倍「同一个密度增量」、
  没有自己的靶** ⇒ 停在 **16~52**（3.3 倍跨度）⇒ **差 ≈ 「19 − 身体的彩度」**（逐张都对得上）。

### 做法
1. **掩膜换到「引擎基线」上算**（`config.FACE_GAIN_MASK_SRC = 'baseline'`，见那个键的注释）：
   在很暗的 RAW 显示图上，mediapipe 会把**白裙子 / 游乐设施**当身体皮肤（实测 +54% / +89%），
   还会漏掉真胳膊 —— 而脸增益正拿这张掩膜决定"给哪里加密度"。
2. **第二段闭环**（靶 = `targets.skin_gap_target()`，语义**脸 − 身体**，按挂着的作者取）：
   - **量**：`(skin>0.5) & ~(face_skin>0.5)` 的**硬核心**（不羽化、不归一）—— 与外面那把尺子同构；
   - **作用**：`skin − face_skin` 羽化（σ 上限 = 长边 2%）＋**乘 `(1 − 脸掩膜)` 在脸处归零**
     ⇒ **脸一个像素都不动**（脸是脸闭环的地盘）。
   - **响应就地标定**：`RESP_INV` 是**按脸**标的；实测身体对同一份增量的响应可以差 **8 倍**
     （DSCF1772：脸的彩度掉 32、身体只掉 3.8）⇒ 拿固定矩阵推会"推不动"。
     所以先在身体区各 +`SKIN_GAP_PROBE` 的 C/M/Y 跑一遍，量出**这张图这个区域**的 3×3 再反算。
   - 迭代到三个通道的误差都 < `SKIN_GAP_STOP`，或到 `SKIN_GAP_MAXIT`；
     单轮 ≤ `SKIN_GAP_STEP`、累计 ≤ `SKIN_GAP_TOTAL`（超了就是"硬掰"，报告里如实写）。

### 实测（可行性探针 `_debug/_c2_probe.py`，用外面那把独立尺子量的）
| 片 | 生产 dC | 闭环后 dC | 靶 | 脸漂移(L/C/H) | 用了多少密度 |
|---|---|---|---|---|---|
| DSCF1772（极端） | **−32.7** | **+0.10** | +0.9 | +0.25 / −0.44 / +1.11 | 0.232 |
| DSCF1665 | **−22.3** | **+0.90** | +0.9 | +0.14 / −0.21 / −0.14 | 0.300 |
★ 两处**独立**证据：闭环自己的读数（`bm`）与外面那把尺子（在成片上现算掩膜）**在量/作用拆开后**才对上
（不拆的时候差 1~2）。这就是 B 那一课的同一个道理。
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
BODY_W = 0.8              # **兜底值**；真值读 `config.GRADE_SKIN_BODY_W`（"可调参数只在 config"）


# ★★★ 09-29：**「量」的那张掩膜必须只用脸**（`MEAS_FACE_MIN_PX` 是它的下限）。
MEAS_FACE_MIN_PX = 200          # 「量」用脸掩膜时的最小像素（小于它 ⇒ 回退成并集）

# ---- 身体闭环（C2）的兜底常数；真值一律读 `config.SKIN_GAP_*`（"可调参数只在 config"）----
GAP_PROBE = 0.05
GAP_DAMP = 0.70
GAP_STEP = 0.10
GAP_TOTAL = 0.40
GAP_MAXIT = 4
GAP_STOP = 0.6
GAP_SIG_MAX_REL = 0.02
GAP_MIN_PX = 2000
GAP_MIN_MEAS_PX = 200


def _finish(m, sig_max=None):
    """羽化 + **峰值归一**（σ = 掩膜等效边长 × `FEATHER`，照 CN104038704A）。

    ⚠ 峰值归一之后，**谁的面积大 / 谁权重高，谁就顶到 1** ⇒ 下游用 `> 0.5` 取"核心"时，
      选中哪些像素**会跟着权重变**。这正是下面 `_masks` 要把"量"和"作用"分开的原因。

    ★★ 09-29（C2）加 `sig_max`：**身体掩膜的 σ 必须封顶**。
      `FEATHER = 等效边长 / 6` 对"脸"合适，但**身体可以占半张画面** ⇒ σ 到 50~100 px
      ⇒ 修正量糊掉半张图、**还糊进脸里**（实测 DSCF1772 的脸彩度被带偏 −1.9）。
      封顶取**长边的 2%**（1600 上 = 32 px）：够藏住接缝（一般 0.6% 就看不出边），
      又不会把"局部修正"变成"全局修正"。封顶后同一张的脸漂移只有 −0.38。
    """
    if FEATHER <= 0:
        return m
    from scipy.ndimage import gaussian_filter
    side = float(np.sqrt(max(int((m > 0.3).sum()), 1)))
    sg = max(1.0, side * FEATHER)
    if sig_max:
        sg = min(sg, float(sig_max))
    m = gaussian_filter(m, sg)
    mx = float(m.max())
    if mx > 1e-6:
        m = np.clip(m / mx, 0.0, 1.0)          # 峰值归一回 1（羽化会降峰）
    return m


def _from_mk(mk, shape, cfg=C, sig_max=None):
    r"""从一份 `face.masks()` 造**四张**掩膜；不可用 ⇒ 四个 `None`。

    | 返回 | 谁用 | 语义 |
    |---|---|---|
    | `m_apply`  | 脸闭环的**作用** | 脸 1.0 ∪ 身体×`GRADE_SKIN_BODY_W` + 羽化 + 峰值归一（管"接缝"）|
    | `m_meas`   | 脸闭环的**量**   | 只用脸（认不到脸 ⇒ 回退并集） |
    | `bd_apply` | 身体闭环的**作用** | `skin − face_skin` + 限 σ + **乘 `(1−脸)` 在脸处归零** |
    | `bd_meas`  | 身体闭环的**量**   | 硬核心 `(skin>0.5) & ~(face_skin>0.5)`，**不羽化不归一** |

    ★★★ 为什么身体也要拆"量/作用"（09-29 实测）：
      拿羽化+归一的那张当"量"时，闭环读数与**外面那把独立尺子**差 **1~2**
      （1772 量出 +0.9、尺子量出 −1.65）—— 因为"羽化/归一把哪些像素算作身体"被混进了读数。
      换成硬核心之后两边对上（1772 量 +0.10 / 尺子 +0.10；1665 两边都是 +0.90）。
      ⇒ 与 B 那一课同构：**「量」和「作用」永远拆开**。
    """
    fs = mk.get('face_skin')
    if fs is None:
        return None, None, None, None
    face0 = np.clip(np.asarray(fs, np.float64), 0.0, 1.0)
    sk = mk.get('skin')
    _bw = float(getattr(cfg, 'GRADE_SKIN_BODY_W', BODY_W))
    m = face0.copy()
    _skc = None
    if sk is not None and np.shape(sk)[:2] == m.shape[:2]:
        _skc = np.clip(np.asarray(sk, np.float64), 0.0, 1.0)
        m = np.maximum(m, _skc * _bw)
    if tuple(m.shape[:2]) != tuple(shape[:2]):
        return None, None, None, None                # 尺寸对不上 ⇒ 不认（换了渲染尺寸必须现算）
    if int((m > 0.05).sum()) < max(1, int(getattr(cfg, 'FACE_GAIN_MIN_MASK_PX', 2000))):
        return None, None, None, None                # 掩膜太小 ⇒ 量不准，动了也是噪声
    if float(m.max()) <= 0.05:
        return None, None, None, None
    m = _finish(m)
    # ---- 「量」：优先只用脸；脸太小 / 认不到脸 ⇒ 回退成并集 ----
    if float(face0.max()) > 0.05:
        fm = _finish(face0.copy())
        m_meas = fm if int((fm > 0.5).sum()) >= MEAS_FACE_MIN_PX else m
    else:
        m_meas = m
    # ---- 身体那两张 ----
    bd = bm = None
    if _skc is not None:
        bd = _finish(np.clip(_skc - face0, 0.0, 1.0), sig_max) * (1.0 - face0)
        bm = ((_skc > 0.5) & ~(face0 > 0.5)).astype(np.float64)
    return m, m_meas, bd, bm


def _masks(pz, shape, cfg=C):
    """兼容旧调用：返回 `(m_apply, m_meas)`（脸闭环那两张）。新代码请用 `_from_mk`。"""
    return _from_mk((pz or {}).get('masks') or {}, shape, cfg)[:2]


def _mask(pz, shape, cfg=C):
    """兼容旧调用：返回**作用**用的那张（脸 ∪ 身体）。新代码请用 `_masks`。"""
    return _masks(pz, shape, cfg)[0]


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


def _lch(lab):
    """`(L*, a*, b*)` → `[L*, C*, H°]`（报告里给人看的；`None` 原样透传）。"""
    if lab is None:
        return None
    L, a, b = (float(x) for x in lab)
    return [L, float(np.hypot(a, b)), float(np.degrees(np.arctan2(b, a)) % 360.0)]


def _body_target(face_lab, gap):
    r"""把**脸实测的 Lab**按 `gap = (dL, dC, dH)` 平移成**身体的靶**（Lab 绝对）。

    `gap` 的语义 = **脸 − 身体**（`targets.skin_gap_target` 给的、大师量出来的）
    ⇒ 身体靶 = (L\*−dL, C\*−dC, H°−dH)。

    ★ 为什么靶要**挂在脸的实测值上**、而不是写死一组数：
      脸闭环已经把脸钉到它的靶上了 ⇒ 身体的靶必须是"**这张图的脸**再差出大师那个差"，
      否则脸偏一点、身体就成了绝对量的搬运（B 那一轮已经在"量/作用"上栽过一次同类坑）。
    """
    L, a, b = (float(x) for x in face_lab)
    Cf = float(np.hypot(a, b))
    Hf = float(np.degrees(np.arctan2(b, a)) % 360.0)
    dL, dC, dH = (float(x) for x in gap)
    Cb = max(Cf - dC, 0.0)
    Hb = np.radians((Hf - dH) % 360.0)
    return np.array([L - dL, Cb * np.cos(Hb), Cb * np.sin(Hb)], np.float64)


def _jac(pl, fld0, m3, m_meas, probe):
    r"""在**给定区域**（`m_meas`）**就地**标定响应 ⇒ `(伪逆, J)`；量不到 ⇒ `(None, None)`。

    `J[:, k]` = 「C/M/Y 里第 k 个通道 +1.0 密度」⇒「该区域 (L\*, a\*, b\*) 的中位变化」。
    `fld0` = **已经叠了脸那一层**的负片场（身体是在脸之后、同一段密度上继续加的）。

    ★ 为什么要就地标：`RESP_INV` 是**按脸**标的；实测身体对同一份增量的响应可以差 **8 倍**
      （DSCF1772：脸的彩度掉 32、身体只掉 3.8）⇒ 拿固定矩阵推会"推不动"。
    """
    base = face_lab(pl.process(fld0, inject='cmy_film'), m_meas)
    if base is None:
        return None, None
    base = np.asarray(base, np.float64)
    cols = []
    for k in range(3):
        d = np.zeros(3, np.float64)
        d[k] = float(probe)
        lab = face_lab(pl.process(fld0 + d[None, None, :] * m3, inject='cmy_film'), m_meas)
        if lab is None:
            return None, None
        cols.append((np.asarray(lab, np.float64) - base) / float(probe))
    J = np.stack(cols, axis=1)
    try:
        return np.linalg.pinv(J), J
    except Exception:                                          # noqa: BLE001
        return None, None


def apply(pl, lin, pz, target_L=None, target_a=None, target_b=None, cfg=C, gap_target=None):
    r"""**跑到成片 → 量脸 → 反算 C/M/Y 增量 → 迭代到 ΔE 达标 → 出图。**

    `pl`：`SimulationPipeline`（**调用方负责建 + 保证路径**）
    `target_L / target_a / target_b`：**脸**的靶（Lab 绝对）。
      · **全空 / `target_L<=0`** ⇒ 不动，**逐位同旧行为**
      · 只给 `target_L` ⇒ 只按亮度做（老的"单值 delta"行为，兼容）
      · 三个都给 ⇒ **分通道迭代**（推荐）
    `gap_target`：**身体**闭环的靶 `(dL, dC, dH)`，语义 **脸 − 身体**
      （调用方从 `targets.skin_gap_target(name, scene)` 取）。
      · `None` / `SKIN_GAP_ENABLE=False` ⇒ **不做第二段，逐位同旧行为**
    `pz`：旧来源的脸掩膜（在**解码后那张很暗的图**上算的）。
      ★ `config.FACE_GAIN_MASK_SRC='baseline'`（默认）时**不直接用它** ——
        `apply` 会在**引擎基线**上自己重算一份（见 §五），拿不到才回退到 `pz`。

    @returns {(ndarray, dict)} 出图 + 报告
      （脸的 `iters` / `delta` / `lab_before` / `lab_after` / `de00`；
        身体的 `body_*` 一整套；`mask_src` = 掩膜到底是哪来的）
    """
    cmy = pl.process(lin, collect='cmy_film')            # ① 负片 CMY 密度
    out = pl.process(cmy, inject='cmy_film')             # ② 基线成片
    info = dict(applied=False, iters=0, delta=[0.0, 0.0, 0.0],
                lab_before=None, lab_after=None, de00=None, target=[target_L, target_a, target_b],
                body_applied=False, body_iters=0, body_delta=[0.0, 0.0, 0.0], body_note=None,
                any_applied=False)
    if not target_L or target_L <= 0:
        info['note'] = '没有脸的靶 ⇒ 不动（身体闭环也没有可挂的脸）'
        return out, info

    # ===== 掩膜来源：★ 09-29（C2）默认换到「引擎基线」上算 =====
    #  为什么：在**很暗的解码图**（L*≈24）上，mediapipe 会把**白裙子 / 游乐设施**当身体皮肤
    #  （实测 +54% / +89%）、还会漏掉真胳膊 —— 而脸增益正拿这张掩膜决定"给哪里加密度"。
    #  基线（引擎出图、还没加脸密度）≈ 成片亮度 ⇒ 分割认得出 ⇒ 掩膜可信（体表像素差 <1%）。
    #  拿不到 ⇒ **回退老来源**，不崩。
    src = str(getattr(cfg, 'FACE_GAIN_MASK_SRC', 'decoded') or 'decoded').strip().lower()
    pz_use = pz
    if src == 'baseline':
        _p2 = None
        try:
            from . import face as _face
            _p2 = _face.parse(np.ascontiguousarray(np.clip(out, 0.0, 1.0)))
        except Exception as _e:                                # noqa: BLE001
            _p2 = None
            src = 'decoded(回退：基线解析失败 %s)' % str(_e)[:50]
        if _p2 is not None and ((_p2.get('masks') or {}).get('face_skin') is not None):
            pz_use = _p2
        elif _p2 is not None:
            src = 'decoded(回退：基线上分不出皮肤)'
    info['mask_src'] = src

    _fs = ((pz_use or {}).get('masks') or {}).get('face_skin')
    n_face = (int((np.clip(np.asarray(_fs, np.float64), 0.0, 1.0) > 0.5).sum())
              if _fs is not None else 0)
    info['face_px'] = n_face                  # ★ 真脸多大（1629 那种"靠身体皮肤撑"的一眼能看出来）
    _sig_max = float(getattr(cfg, 'SKIN_GAP_SIG_MAX_REL', GAP_SIG_MAX_REL)) * float(max(out.shape[:2]))
    m, m_meas, bd_apply, bd_meas = _from_mk((pz_use or {}).get('masks') or {},
                                            out.shape, cfg, _sig_max)
    if m is None:
        info['note'] = ('掩膜不可用 ⇒ 不动（真脸 %d px / 掩膜下限 %s px / 或尺寸对不上）'
                        % (n_face, getattr(cfg, 'FACE_GAIN_MIN_MASK_PX', 2000)))
        return out, info
    info['mask_px'] = int((m > 0.05).sum())    # 作用掩膜多大（含身体皮肤那半张）
    # ★★★ 09-29：「量」用 `m_meas`（优先**只用脸**；认不到脸才回退成并集）。
    #   有靶的是脸 ⇒ 闭环必须盯脸；否则调大 `GRADE_SKIN_BODY_W` 会**把脸带偏**（见 `_masks` 注释）。
    info['meas_masked'] = 'face' if m_meas is not m else 'union(回退：脸太小/认不到)'
    m3 = m[..., None]
    lab0 = face_lab(out, m_meas)
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
        new_lab = face_lab(out, m_meas)
        if new_lab is None:
            break
        cur_lab = new_lab
        de = _de00(cur_lab, tgt)
        info['iters'] = it
        if abs(step).max() < 1e-4:
            break
    info.update(applied=bool(info['iters'] > 0), delta=[float(x) for x in delta],
                lab_after=list(cur_lab), de00=de, de_metric=_DE_METRIC[0])

    # ==================================================================
    # 第二段：**身体闭环**（09-29 C2）—— 靶 = 脸（实测）+ 大师的差
    #   ① 作用范围 = `skin − face_skin`，羽化 σ 封顶 + **乘 `(1−脸掩膜)` 在脸处归零**
    #      ⇒ **脸一个像素都不动**（脸是上一段闭环的地盘）。
    #   ② 「量」用**硬核心**（不羽化不归一）—— 与外面那把独立尺子同构；混用会让读数差 1~2（§五）。
    #   ③ 响应**就地标定**（`_jac`）：身体对同一份增量的响应可以和脸差 8 倍。
    #   ④ 单轮 ≤ `SKIN_GAP_STEP`、累计 ≤ `SKIN_GAP_TOTAL`（超了 = "硬掰"，报告里如实写）。
    # ==================================================================
    if not bool(getattr(cfg, 'SKIN_GAP_ENABLE', False)):
        info['body_note'] = '身体闭环关着（SKIN_GAP_ENABLE=False）'
    elif not gap_target:
        info['body_note'] = '这条预设没挂 skin_gap 靶 ⇒ 不做'
    elif bd_apply is None or bd_meas is None:
        info['body_note'] = '拿不到身体掩膜（分割没给 skin 那张 / 尺寸对不上）'
    else:
        _bpx = int((bd_meas > 0.5).sum())
        _apx = int((bd_apply > 0.05).sum())
        info['body_px'] = _bpx
        info['body_apply_px'] = _apx
        _minm = int(getattr(cfg, 'SKIN_GAP_MIN_MEAS_PX', GAP_MIN_MEAS_PX))
        _mina = int(getattr(cfg, 'SKIN_GAP_MIN_PX', GAP_MIN_PX))
        if _bpx < _minm or _apx < _mina:
            info['body_note'] = ('身体太小 ⇒ 不动（量 %d px < %d ｜ 作用 %d px < %d）'
                                 % (_bpx, _minm, _apx, _mina))
        else:
            tgt_b = _body_target(cur_lab, gap_target)
            fld0 = cmy + delta[None, None, :] * m3      # 脸那一层已经叠进去了
            m3b = bd_apply[..., None]
            J_inv, J = _jac(pl, fld0, m3b, bd_meas,
                            float(getattr(cfg, 'SKIN_GAP_PROBE', GAP_PROBE)))
            b0 = face_lab(out, bd_meas) if J_inv is not None else None
            if J_inv is None or b0 is None:
                info['body_note'] = '身体区量不到 ⇒ 不动'
            else:
                info['body_J'] = [[float(v) for v in row] for row in J]
                info['body_before'] = list(b0)
                info['body_before_lch'] = _lch(b0)
                info['body_target'] = [float(x) for x in tgt_b]
                info['body_target_lch'] = _lch(tgt_b)
                _damp = float(getattr(cfg, 'SKIN_GAP_DAMP', GAP_DAMP))
                _stepL = float(getattr(cfg, 'SKIN_GAP_STEP', GAP_STEP))
                _totL = float(getattr(cfg, 'SKIN_GAP_TOTAL', GAP_TOTAL))
                _maxit = int(getattr(cfg, 'SKIN_GAP_MAXIT', GAP_MAXIT))
                _stop = float(getattr(cfg, 'SKIN_GAP_STOP', GAP_STOP))
                db = np.zeros(3, np.float64)
                cur_b = np.asarray(b0, np.float64)
                o_last, capped = None, False
                for bit in range(1, _maxit + 1):
                    err_b = tgt_b - cur_b
                    if float(np.abs(err_b).max()) < _stop:
                        break
                    stb = np.clip(J_inv @ err_b * _damp, -_stepL, _stepL)
                    cand = np.clip(db + stb, -_totL, _totL)
                    if float(np.abs(cand - (db + stb)).max()) > 1e-9:
                        capped = True                     # 撞到累计上限 = "硬掰"
                    o = pl.process(fld0 + cand[None, None, :] * m3b, inject='cmy_film')
                    lb = face_lab(o, bd_meas)
                    if lb is None:
                        break
                    db, cur_b, o_last = cand, np.asarray(lb, np.float64), o
                    info['body_iters'] = bit
                    if float(np.abs(stb).max()) < 1e-4:
                        break
                if o_last is not None:
                    out = o_last                           # ★ 出图 = 叠了**两段**的那张
                    fl = face_lab(out, m_meas)
                    if fl is not None:
                        info['body_face_drift'] = [round(float(x - y), 3) for x, y in
                                                   zip(_lch(fl), _lch(cur_lab))]
                info.update(body_applied=bool(info['body_iters'] > 0),
                            body_delta=[float(x) for x in db],
                            body_delta_absmax=float(np.abs(db).max()),
                            body_after=(list(cur_b) if info['body_iters'] else None),
                            body_after_lch=(_lch(cur_b) if info['body_iters'] else None),
                            body_err=[float(x) for x in (tgt_b - cur_b)],
                            body_capped=bool(capped))
                if not info['body_iters']:
                    # ★ 起点就在靶附近 ⇒ 第二段不该动（"本来就对的张不能被改坏"这条就靠它）
                    info['body_note'] = ('身体本来就在靶附近 ⇒ 第二段没动（起点最大误差 %.2f）'
                                         % float(np.abs(tgt_b - cur_b).max()))
    # ★★ `applied` 只表示**脸那一段**（老语义，别改，外面有判据在用它）。
    #   但**身体那一段也会单独动**（脸本来就在靶上、身体偏了 ⇒ 脸 0 轮、身体照样动）
    #   ⇒ 再给一个 `any_applied` = 两段里有任一段动过。
    #   ⚠ `presets`/`selftest` 里"这张到底动没动"必须看 `any_applied`，只看 `applied` 会漏。
    info['any_applied'] = bool(info['applied'] or info['body_applied'])
    return out, info
