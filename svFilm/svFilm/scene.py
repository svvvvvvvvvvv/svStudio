# -*- coding: utf-8 -*-
r"""**场景判据** —— 「这张图是什么场景」。

## 为什么要有它
09-26 提的：「可不可以分场景，比如分**过曝**场景、分**明暗对比强烈**的场景，
然后采取不同的数值参数？」—— 「按场景分参数」这件事有三件地基，这是**第一件**（判据）；
另两件是「参数表分档」与「缓存键带场景」。

## 判据的选取原则（09-26 踩出来的，别再犯）
① **只收「能量出来、且跟『参数该不该变』直接相关」的量。**
   **不按色相带那种「能算但不知道是什么」的量分类**。
② **只用从"H×W 像素"上能量出来的量**（不依赖 EXIF、不依赖机型）⇒ 同一份代码在
   RAW / 机内 JPG / 别人的图上一样跑。
③ ★★★ **分类之前先问：「这个参数本来就该按场景变吗？」**
   §86.5 已经量过：我们跟大师的三条差（太艳 / 中调太黄 / 脸偏暗）**跨所有分组一致**
   ⇒ 那三根旋钮**分场景没有收益**。所以这个模块存在的意义**不是**"给每根旋钮都套一层场景"，
   而是① 把「哪些轴真的有区别」变成**能量出来的问题**；② 给真要变的那些提供通道。

## 四根轴（09-29 晚口径）

| 轴 | 怎么量 | 分档 | 跟哪根参数有关 |
|---|---|---|---|
| `exp`  曝光 | 整张中位 L50（显示域）| 暗 / 正常 / 亮 | 引擎的落点 / 反差的按场景覆盖 |
| `span` 光比 | 跨度 L95−L5（显示域）| 平 / 正常 / 大 | 同上 |
| `back` 光位 | **主体 vs 它身后的背景** ＋ **最亮区的位置** | 正逆光 / 侧逆光 / 顺平光 | 光晕、柔光、反差该不该放手 |
| `overwhite` 源头过曝 | **线性域**里**超过白点**的像素占比 | 是 / 否 | 唯一一条"必须在引擎里动作"的轴 |

★★ 09-29（新分支 `drop-tone-and-skin` 之后）：`back`（光位）**加回来了**，但**判据重建过** ——
   原来它靠 `face.py` 的人掩膜（含脸），现在**只用 `person.py` 那个 ~0.3 s 的粗"人在哪"**
   （只要「人物整体位置」这一件事来辅助判断光位）。
   ⇒ **不跑人脸检测**、不判景别、不判脸可见度（那三样随认人整套删了）。
⚠ 检不出人 / 主体太小 ⇒ `back` 给 `None`（写进 key 是 `-`）—— **弃权是合法输出**，
   不许硬给一个"顺平光"然后让下游按它改参数。

★★★ **`overwhite` 为什么要单列一轴（09-26 实测）**：我们那 52 张里 **6 张（11.5%）有 6~13% 的
像素在线性域就**超过白点**（`lin.max ≥ 1.0`：5.9 / 5.9 / 6.7 / 8.0 / 10.3 / 12.9%），
其余片子是 **0.0%** —— 中间没有过渡，信号很干净。
**"源头有多少东西超过白"只在线性域还看得到**（显示域早被引擎重渲染压过了）。
按铁律「要改整体亮暗回引擎那一步（线性域、有高光余量）」⇒ 这一轴的**动作位**在引擎。

⚠⚠⚠ **判据踩过的坑（第一版是错的，别改回去）**：第一版写的是
「**三通道都 ≥0.995** 才算爆」，依据是"解码后 L95 > 95 的有 10/52"。
**那个依据是错的**：显示域的 99.8 只是 sRGB 编码把 **1.0 以上一律压到 255** 的假象 ——
实测那 4 张线性最大值只有 **1.261**、三通道同时贴顶的像素是 **0.000%**，
按那个判据这一轴**永远不会响**（= 死轴）。
⇒ 正确判据 = **`lin` 的通道最大值 ≥ 1.0**（"已经超过白点"），不是"三通道同时到顶"。

★ 阈值全在 `config.SCENE_*`（可调参数只在 config），**标定依据**见
  `E:/Debug_svStudio/_debug/场景分布_0926.json`（我们自己 52 张的解码后分布）。
★ `VERSION` **必须进缓存键** —— 判据一改就是另一套参数，缓存得失效。
"""
from __future__ import annotations

import numpy as np

from . import color
from . import config as C

VERSION = 7                          # ★ 换判据就要 +1（缓存键带它）
#   6（10-09）：光位判据的「贴边 ⇒ 弃权」守卫改成**单向**（原先对 `E_bg`/`E_tb` 误用 `abs`，
#              把"方向相反、证据明确"的片子当"贴边"弃权）。实测弃权率 50%→33~40%（鹿井）、71%→49%（我们）。
#   7（10-09）：`SCENE_BACK_MARGIN` 0.35 → **0.15**（标定，SV 定）⇒ 弃权再降到 **19%/17%/24%**。
AXES = ('exp', 'span', 'back', 'overwhite')

_EXP_ORDER = ('暗', '正常', '亮')
_SPAN_ORDER = ('平', '正常', '大')


def _b(names, edges, v):
    """按升序阈值 `edges` 把 `v` 分到 `names`（比 `edges` 多一档）。"""
    i = 0
    for e in edges:
        if v >= e:
            i += 1
    return names[i]


def token(axis, v):
    """把某一轴的值变成**写进 `_scene` 键里的那个词**。

    ★★★ 09-26 为什么要它：`overwhite` 在**代码里是 bool**，但人写覆盖时想写的是「过曝」这种词。
      若两处各写各的，就会出现 `{"overwhite=过曝": …}` 写在文档里、代码却去找
      `"overwhite=True"` 的**静默不命中**（自检第一次跑就抓到了这个）。
      ⇒ **只有这一个函数负责词形**，键与文档都用它。
    """
    if v is None:
        return '-'
    if axis == 'back':
        return str(v)                     # 正逆光 / 侧逆光 / 顺平光（本身就是词，直接用）
    if axis == 'overwhite':
        return '过曝' if v else '正常'
    return str(v)


def _block_z(L, n):
    r"""**分块亮度场** —— `z(i,j) = 块中位 L − 整张中位 L`（**全相对量**）。

    ★★★ 为什么用相对量（v3 的立身之本）：绝对阈值**换一个域就错档**（我们解码图的整张中位 L*≈21，
    而成片/大师原片 ≈61）⇒ 同一套阈值判不了两种图。减掉整张中位以后，**跨域可比**。
    """
    h, w = L.shape
    g = float(np.median(L))
    z = np.zeros((n, n), np.float64)
    for i in range(n):
        for j in range(n):
            s = L[i * h // n:(i + 1) * h // n, j * w // n:(j + 1) * w // n]
            if s.size:
                z[i, j] = float(np.median(s)) - g
    return z


def _light_position(d, L, person, cfg):
    r"""**光位** —— 正逆光 / 侧逆光 / 侧光 / 面光顺平光；判不动 ⇒ `None`（**弃权**）。

    ## 出处 = 09-27 的 **v3**（`_debug/lightpos_v3.py`，过了四关：幻影/目检/扰动/反例）

    ```
    z(块)  = 块中位 L − 整张中位 L           # 相对量 ⇒ 免疫"我们的解码图整体偏暗"
    E_lr   = mean(z[右列]) − mean(z[左列])    # 左右亮暗差
    E_tb   = mean(z[下行]) − mean(z[上行])    # 上下亮暗差（负 = 上亮）
    E_span = max(z) − min(z)                  # 块级跨度
    E_bg   = median(L[背景]) − median(L[主体])
    spike  = z(最高块) − z(第二高块)           # ★ 区分「**光源**」与「**渐变**」
    ```

    ## 判决（**条件触发**，不投票 —— 光位本身决定哪种证据会出现）
    1. `E_bg ≥ TB` **或** `E_tb ≤ −TS` **或** 有过曝块落在主体之外 **或** 有突出光源（`spike` 够大）
       ⇒ **逆光族**：亮区重心偏主体中轴 ⇒ **正逆光**；偏一侧 ⇒ **侧逆光**；
    2. 否则 `|E_lr| ≥ TS` ⇒ **侧光**；
    3. 都弱 ⇒ **面光 / 顺平光**。

    ## 弃权 = 五条兜底（**弃权是合法输出**，下游按"没判"处理，什么都不改）
    `no_subject` · `extreme_luma`（整张太暗/太亮）· `mostly_blown`（整张过曝太多）
    · `conflict`（背景亮 **且** 左右差都大 —— 两条证据打架）· `marginal`（量落在阈值 ±N% 内）

    ## ⚠⚠ 三条 v3 用血换来的教训（别改回去）
    1. **「最亮块在主体之外」几乎没有区分力** —— 任何真实照片的最亮块几乎总在主体外
       （v3 第三次修实测：加上这条后 8 张大师片里 **6 张变成"正逆光"**，连 `E_lr=+54.4`
       这种极强左右差都被判正逆光）⇒ 正确的区分是「**光源 vs 渐变**」，即 `spike`。
    2. **「贴边 ⇒ 弃权」是必须的** —— 只要"算不算"依赖一个会动的阈值，贴边的图就必然翻。
       配套口径：**`None` ↔ 某一档不算翻**（扰动关就按这个判"翻不翻"）。
    3. **只有"所有证据都弱"才兜底**，不许"有一条贴边就整张弃权"（那会把 1726 这种
       `E_tb=−54.8` 已经非常确定的片子判掉 —— 15 张里 6 张 None，40%）。
    ⚠ 输入是 `person.py` 那个 **~0.3 s 的粗"人在哪"**（**不跑人脸检测**）；
      拿不到 / 主体太小 ⇒ 直接弃权，**不许硬给"顺平光"**。
    @returns (标签 or None, raw 字典)
    """
    H, W = L.shape
    raw = dict(why=None, E_lr=None, E_tb=None, E_span=None, E_bg=None, spike=None,
               z_span=None, clip_pct=None, clip_blk_off=None, hot_blk_off=None,
               has_spike_src=False, hi_cx=None, sub_cx=None, cx_diff=None,
               person_pct=None, tb=None, ts=None)
    tb = float(getattr(cfg, 'SCENE_BACK_TB', 15.0))
    ts = float(getattr(cfg, 'SCENE_BACK_TS', 20.0))
    mg = float(getattr(cfg, 'SCENE_BACK_MARGIN', 0.25))
    n = max(int(getattr(cfg, 'SCENE_BACK_NBLK', 3) or 3), 2)
    raw['tb'], raw['ts'] = tb, ts

    # ---------------- ★ 兜底 0：连"主体 / 背景"都没有 ⇒ 无从判 ----------------
    if person is None:
        raw['why'] = 'no_person(没人在哪)'
        return None, raw
    per = np.asarray(person, np.float64)
    if tuple(per.shape[:2]) != (H, W):
        raw['why'] = 'person_shape_mismatch'
        return None, raw
    sub = per > 0.5
    bak = ~sub
    raw['person_pct'] = float(sub.mean() * 100.0)
    if (int(sub.sum()) < int(getattr(cfg, 'SCENE_BACK_MIN_SUB_PX', 500))
            or int(bak.sum()) < 500):
        raw['why'] = 'no_subject'
        return None, raw

    # ---------------- ★ 兜底 1：图本身极端（几乎全白 / 几乎全黑）⇒ 判不动 ----------------
    _l50 = float(np.median(L))
    if (_l50 < float(getattr(cfg, 'SCENE_BACK_EXTREME_LO', 8.0))
            or _l50 > float(getattr(cfg, 'SCENE_BACK_EXTREME_HI', 92.0))):
        raw['why'] = 'extreme_luma(%.0f)' % _l50
        return None, raw
    rgb = np.clip(np.asarray(d, np.float64), 0.0, 1.0)
    cl = (rgb.max(axis=2) >= 254.0 / 255.0) if rgb.ndim == 3 else np.zeros((H, W), bool)
    raw['clip_pct'] = 100.0 * float(cl.mean())
    if raw['clip_pct'] > float(getattr(cfg, 'SCENE_BACK_BLOWN_ALL', 30.0)):
        raw['why'] = 'mostly_blown(%.1f%%)' % raw['clip_pct']
        return None, raw

    # ---------------- 分块亮度场 + 三个方向量 ----------------
    z = _block_z(L, n)
    raw['E_lr'] = float(np.mean(z[:, -1]) - np.mean(z[:, 0]))
    raw['E_tb'] = float(np.mean(z[-1]) - np.mean(z[0]))
    raw['E_span'] = float(z.max() - z.min())
    zf = np.sort(z.ravel())[::-1]
    span_z = float(zf[0] - zf[-1])
    spike = float(zf[0] - zf[1]) if zf.size > 1 else 0.0
    raw['spike'], raw['z_span'] = round(spike, 1), round(span_z, 1)

    # ---- 过曝块：有多少块「过曝了、而且那块不是主体」 ----
    cz = 0.0
    if cl.any():
        off = 0
        _cp = float(getattr(cfg, 'SCENE_BACK_CLIP_PCT', 1.0))
        for i in range(n):
            for j in range(n):
                sb = cl[i * H // n:(i + 1) * H // n, j * W // n:(j + 1) * W // n]
                sp = sub[i * H // n:(i + 1) * H // n, j * W // n:(j + 1) * W // n]
                if sb.size and sb.mean() * 100.0 >= _cp:
                    off += 1 if (sp.size and sp.mean() < 0.5) else 0
        cz = float(off) / float(n * n)
    raw['clip_blk_off'] = cz

    # ---- ★★ 光源 vs 渐变：只取「最高的那一块」，看它落在不在主体外 ----
    hot = z >= zf[0] - float(getattr(cfg, 'SCENE_BACK_HOT_REL', 0.10)) * max(span_z, 1e-6)
    hz = 0.0
    if hot.any():
        off = 0
        for i in range(n):
            for j in range(n):
                if not hot[i, j]:
                    continue
                sp = sub[i * H // n:(i + 1) * H // n, j * W // n:(j + 1) * W // n]
                if sp.size and sp.mean() < 0.5:
                    off += 1
        hz = float(off) / float(n * n)
    raw['hot_blk_off'] = hz
    raw['has_spike_src'] = bool(hz > 0.0 and spike >=
                                float(getattr(cfg, 'SCENE_BACK_SPIKE_REL', 0.30)) * max(span_z, 1e-6))

    # ---- 主体 vs 它身后的**背景**（不用整张中位 —— 逆光片是"两头重"，中位落在谷里没信息量）----
    raw['E_bg'] = float(np.median(L[bak]) - np.median(L[sub]))
    eb = raw['E_bg']

    # ---------------- ★ 兜底 2：证据互相矛盾 ⇒ 不猜 ----------------
    if (eb >= tb) and abs(raw['E_lr']) >= ts:
        raw['why'] = 'conflict: 背景亮 且 左右差也大'
        return None, raw

    # ---------------- ★ 兜底 3：**每条证据各自过「贴边 ⇒ 弃权」** ----------------
    #   ★★★ 09-29 修（扰动关实测出来的）：v3 只在**最后统一**查贴边，于是
    #     `E_tb` / `E_bg` **刚越过阈值**就直接进了"逆光族" —— 阈值一动就翻
    #     （实测 1526：`E_tb=−14.1` 对 `TS=20`；×0.7 时 `TS=14` ⇒ 越阈 ⇒ 判侧逆光 ⇒ **翻**）。
    #   ⇒ 正确做法：**证据要"明确超过带宽"才算数**（`> t×(1+mg)`）；
    #     落在带内的量**直接弃权** —— "不猜"本来就该是合法输出。
    #   ⚠ 只有"所有证据都弱 / 都贴边"才弃权：有**明确强证据**照判
    #     （1726 的 `E_tb=−54.8`、1231/1665 的过曝块，都不受影响）。
    _hard = (eb >= tb * (1 + mg)
             or raw['E_tb'] <= -ts * (1 + mg)
             or cz > float(getattr(cfg, 'SCENE_BACK_CLIP_OFF_MIN', 0.05))
             or (raw['has_spike_src']
                 and raw['E_span'] >= float(getattr(cfg, 'SCENE_BACK_SPAN_MIN', 20.0))))
    if not _hard:
        # ★★★ 10-09 修（**单向证据必须单向守卫**）：
        #   上面 `_hard` 那三条**本来就是单向**的 —— `E_bg` 只有**正**支持"光在主体背后"、
        #   `E_tb` 只有**负**支持逆光；但这里原先一律 `abs(...)` ⇒ **把单向证据当双向**，
        #   于是一大批"方向相反、其实证据很明确"的片子被当成"贴边"弃权。
        #   实测（鹿井 514 + 我们 45 = 559 张）：**74 例正 `E_tb` + 64 例负 `E_bg` 是误弃权**
        #   （`E_bg` 负 = 背景比主体暗 = 顺光/面光的**反证**，却被算成"贴边不敢判"）。
        #   ⇒ 改成单向：只有 **`E_lr`（左↔右）** 才是真正双向的量。
        #   ⚠ 带宽 `mg` 不变（它是 09-29 为"阈值一动就翻"加的），**只是不再用错方向**。
        _marg = ((tb * (1 - mg) <= eb <= tb * (1 + mg))                    # 只有正的 E_bg
                 or (-ts * (1 + mg) <= raw['E_tb'] <= -ts * (1 - mg))      # 只有负的 E_tb
                 or (ts * (1 - mg) <= abs(raw['E_lr']) <= ts * (1 + mg)))  # E_lr 双向
        if _marg:
            raw['why'] = ('marginal: E_bg=%.1f E_tb=%.1f E_lr=%.1f 贴 %.0f/%.0f'
                          % (eb, raw['E_tb'], raw['E_lr'], tb, ts))
            return None, raw

    if _hard:
        # 亮区偏"主体中轴"还是偏一侧 ⇒ 正逆光 / 侧逆光
        if cl.any():
            _yy, _xx = np.nonzero(cl)
            hi_cx = float(np.median(_xx)) / max(W - 1, 1)
        else:
            hi_cx = float(int(np.argmax(z[0] + z[1] + z[2])) + 0.5) / n
        _ys, _xs = np.nonzero(sub)
        sub_cx = float(np.median(_xs)) / max(W - 1, 1)
        raw['hi_cx'], raw['sub_cx'] = round(hi_cx, 3), round(sub_cx, 3)
        raw['cx_diff'] = round(abs(hi_cx - sub_cx), 3)
        _tol = float(getattr(cfg, 'SCENE_BACK_CX_TOL', 0.10))
        return ('正逆光' if abs(hi_cx - sub_cx) <= _tol else '侧逆光'), raw

    if abs(raw['E_lr']) >= ts:
        return '侧光', raw
    return '面光/顺平光', raw


# ---------------------------------------------------------------------------
# ★★ 10-09 新增：「多线索 → 程度量 → 分级」光位（技能 §192 的 ①~④）—— ⚠ **尚未接执行**
#
#   ⚠ 旧 `_light_position` **原样保留**（`back` 轴行为一字不变）；这里是**并列的第二条实现**，
#     结果进 `raw['light']` / `out['back2']`，**默认关**（`config.SCENE_LIGHT_EVIDENCE=False`）
#     ⇒ 不进缓存键、不影响下游取参。**接执行时必做三件**：
#       ① `scene.VERSION` +1（缓存键带它）② 复跑 `selftest` ③ 先出"新旧标签逐张对照"给 SV 目检。
#
#   为什么要推倒重来（§192 的调研结论，四条系统性差异）：
#     · 工业界「逆光检测」是相机里做了 30 年的成熟功能，答案高度一致：
#       **分块 + 块间关系 + 多线索 + 程度量 + 降级** —— 没有一家靠"单判据 + 弃权"。
#     · 我们 v3：分块太粗（3×3=9 块 vs 64 区）· 线索太少（无复核）· 只有二值没有程度 ·
#       判不出只能弃权（没有降级路径）。
#     · 我们一直没用**最可靠的那条证据**：**脸上的明暗形状**（人像布光实践）。
#     · `person.py` 一次前向里**已经白送"脸皮肤"掩膜**（边际成本 0）⇒ 这条证据是白捡的。
# ---------------------------------------------------------------------------

LIGHT_LABELS = ('正逆光', '侧逆光', '侧光', '面光/顺平光')

# ★★ 10-09：**给"动作"用的连续程度量**（名字进 `raw['light']`，`targets.scene_engine` 的
#   `{"by": <名字>, "delta": Δ}` 按它插值）。
#   ⚠⚠ **离散分级（`back`）与这两条是两回事**：分级只描述"光位"，而**动作**该按量取 ——
#      §194 体检的结论是"拿离散分组驱动影调"解释力几乎全来自 `E_bg` 同义反复、且是阶跃
#      ⇒ 动作改用连续量（0~1），既没有阶跃，也不会因判据换代而整批跳。
DEGREES = ('deg_back', 'deg_side', 'deg_front', 'deg_source', 'deg_reflect')


def _ramp(v, hi, lo=0.0):
    r"""把量 `v` 线性映射到 0~1（`lo` 处 0、`hi` 处 1，两端夹住）。

    ★★ 为什么**不用阈值**：阈值是"是/否"（EP1158353 那一代），过了阈就饱和；
      我们要的是**程度**（**EP0570968** 的连续量 `g`、**CN110971841B** 的"逆光程度"）。
      ⇒ 阈值只当**归一化的锚**（取 `hi = 2T` ⇒ 阈值处 ≈ 0.5）。
    """
    hi, lo = float(hi), float(lo)
    if hi <= lo:
        return 0.0
    return float(min(max((float(v) - lo) / (hi - lo), 0.0), 1.0))


def _soft_or(pairs):
    r"""软或（noisy-OR）：`1 − Π(1 − wᵢ·eᵢ)` —— **多条弱线索能累加**。

    出处：**Lalonde ICCV09**「多**弱**线索合并 + 先验」；也对应 **US 7,010,160 B1** 的
    "先按亮度判、**再用第二条独立证据复核**"。
    ⚠ 与 v3「条件触发」的区别：v3 是**任一条强**才进逆光族（弱证据全部浪费）；
      软或让"三条都中等"也算 —— **这正是降弃权要的**（弃权只留给"所有线索都弱"）。
    """
    p = 1.0
    for w, e in pairs:
        p *= (1.0 - max(0.0, min(1.0, float(w) * float(e))))
    return 1.0 - p


def _block_fields(L, n):
    r"""n×n 块级亮度场 + **块间关系**（相邻块梯度的一致性）。

    ★ 提分辨率：`SCENE_BACK_NBLK2 = 8` ⇒ **64 区**（对齐 **EP1158353 A2** 的 64 区分块）；
      v3 是 3×3 = 9 块。
    ★★ **块间关系**（**EP2849431 B1** 的要点：「用**相邻块**的亮度关系判断，
      **不是绝对阈值**」）：`coh` = |相邻块差的均值| ÷ 均绝对值 ∈ [0,1]
      —— 高 ⇒ 左右（上下）**真的有一致的亮暗趋势**；低 ⇒ 只是噪声/局部纹理。
    ★ 块值仍用 v3 的**相对量** `z = 块中位 L − 整张中位 L`（跨域可比，别改回绝对值）。
    """
    h, w = L.shape
    g = float(np.median(L))
    z = np.zeros((n, n), np.float64)
    for i in range(n):
        for j in range(n):
            s = L[i * h // n:(i + 1) * h // n, j * w // n:(j + 1) * w // n]
            if s.size:
                z[i, j] = float(np.median(s)) - g
    zf = np.sort(z.ravel())[::-1]

    def _coh(dv):
        a = float(np.abs(dv).mean())
        return float(abs(dv.mean()) / a) if a > 1e-6 else 0.0

    # ★★ **块间关系**（EP2849431）的更强形式：对**块列均值 / 块行均值**做最小二乘直线，
    #    取"首→末的预测差"当**趋势量**。比 `mean(最右列) − mean(最左列)` 稳健得多 ——
    #    后者只要**一列**亮（一幅亮墙、一块招牌）就翻，那量到的是**内容**不是**光向**。
    #    （10-09 自检发现：`面光→侧光` 的 27 张翻转里，多数是"某一侧有亮物体"。）
    ci = np.arange(n, dtype=np.float64) - (n - 1) / 2.0
    den = float((ci ** 2).sum()) or 1.0
    E_lr_t = float(((z.mean(axis=0) * ci).sum() / den) * (n - 1))
    E_tb_t = float(((z.mean(axis=1) * ci).sum() / den) * (n - 1))

    return dict(z=z,
                E_lr=float(np.mean(z[:, -1]) - np.mean(z[:, 0])),
                E_tb=float(np.mean(z[-1]) - np.mean(z[0])),
                E_lr_t=E_lr_t, E_tb_t=E_tb_t,
                E_span=float(zf[0] - zf[-1]),
                spike=float(zf[0] - zf[1]) if zf.size > 1 else 0.0,
                coh_x=_coh(np.diff(z, axis=1)),
                coh_y=_coh(np.diff(z, axis=0)))


def _blk_off(mask, sub, n, thresh_pct, max_row=None):
    """`mask` 里"达到 `thresh_pct` 的块"中，落在**主体之外**的块占比。

    ★ `max_row`（块行号）**以下**的块不算 —— 逆光的意思是"光源在**主体背后**"，
      而**画面底部的一片过曝（亮地面 / 水面反光 / 白斑马线）不是光源**。
      ★★ 这是 10-09 我自己抽查翻转样例时抓出来的**假阳性来源**：
      005_02（白箭头路面）被 cz 推成"正逆光"、125_02（亮水面）被推成"正逆光"，
      而 125 的**脸比背景亮 36 格**（正光）—— 两条自相矛盾。
    """
    H, W = mask.shape
    off = tot = 0
    for i in range(n):
        if max_row is not None and i > max_row:
            continue
        for j in range(n):
            sb = mask[i * H // n:(i + 1) * H // n, j * W // n:(j + 1) * W // n]
            sp = sub[i * H // n:(i + 1) * H // n, j * W // n:(j + 1) * W // n]
            if sb.size and sb.mean() * 100.0 >= thresh_pct:
                tot += 1
                if sp.size and sp.mean() < 0.5:
                    off += 1
    return (float(off) / float(n * n)), tot


def _sub_bottom_row(sub, n):
    """主体**最低**像素落在第几个块行（块行号从 0 起）。拿不到 ⇒ `None`。"""
    rr = np.nonzero(sub.any(axis=1))[0]
    if rr.size == 0:
        return None
    H = sub.shape[0]
    return min(int(rr.max()) * n // H, n - 1)


def _face_evidence(L, sub, face, cfg):
    r"""**脸上的受光** —— 人像布光实践里最可靠的证据（自有出处，见 §192.1 末行）。

    与「主体 vs 背景」的区别：头肩掩膜**混了头发/衣服**，而脸是**同一种材质、连续表面**
    ⇒ 脸上的明暗差是**光方向**的直接读数（不是"身上平均亮度"那种混合量）。

    量三样（都用**掩膜内中位**，抗噪）：
      · `E_bg`  = 背景中位 − **脸**中位（**正的越大 ⇒ 脸被背景压 ⇒ 逆光**）
      · `E_lr`  = 脸（按掩膜自身外接框左右分半）右半中位 − 左半中位（**侧光/侧逆光的读数**）
      · `E_tb`  = 脸下半中位 − 上半中位（**负 ⇒ 上亮 ⇒ 光从上方/背后**）
    ⚠ 脸太小 / 掩膜为空 ⇒ `ok=False`（该线索**缺席**，合成器跳过它 —— 这就是 **US8035727** 的
      "证据不够就**降级**、而不是弃权"）。
    """
    out = dict(ok=False, n_px=0, E_bg=None, E_lr=None, E_tb=None, E_span=None)
    if face is None:
        return out
    m = np.asarray(face, np.float64)
    if m.ndim != 2 or tuple(m.shape[:2]) != tuple(L.shape[:2]):
        return out
    fm = m > 0.5
    n_px = int(fm.sum())
    out['n_px'] = n_px
    if n_px < int(getattr(cfg, 'SCENE_LIGHT_FACE_MIN_PX', 300)):
        return out
    if n_px < float(getattr(cfg, 'SCENE_LIGHT_FACE_MIN_REL', 0.03)) * max(int(sub.sum()), 1):
        return out
    ys, xs = np.nonzero(fm)
    xm = int((int(xs.min()) + int(xs.max()) + 1) // 2)
    ym = int((int(ys.min()) + int(ys.max()) + 1) // 2)
    yy, xx = np.mgrid[0:L.shape[0], 0:L.shape[1]]
    Lf = float(np.median(L[fm]))
    out['E_bg'] = float(np.median(L[~sub]) - Lf)
    for key, half in (('E_lr', xx >= xm), ('E_tb', yy >= ym)):
        a, b = fm & half, fm & (~half)
        if int(a.sum()) >= 30 and int(b.sum()) >= 30:
            out[key] = float(np.median(L[a]) - np.median(L[b]))
    vs = L[fm]
    out['E_span'] = float(np.percentile(vs, 95) - np.percentile(vs, 5))
    out['ok'] = True
    return out


def light_evidence(d, L, person=None, face=None, cfg=C):
    r"""多线索光位证据 → **程度量** → 分级。**不改 `back`、不进缓存键**（供对照与后续接执行）。

    ## 与 v3（`_light_position`）的四点不同
    1. **程度量而不是二值**（EP0570968 的 `g`）：`deg_back` / `deg_side` ∈ [0,1]。
    2. **软或合并**而不是条件触发（Lalonde ICCV09）：多条中等证据能累加。
    3. **多一条最可靠的线索**：**脸上的受光**（`_face_evidence`）。
    4. **有降级路径**（US8035727）：人掩膜不可用 ⇒ 用**中心区**当主体（TI US2008/0110226 口径），
       **不是直接弃权**；脸掩膜太小 ⇒ 只跳过那条线索。
       ⇒ **弃权只留给"画面有大结构、但所有线索彼此不支持某一边"**。

    @returns {dict} `label` / `deg_back` / `deg_side` / `conf` / `degrade` / `ev`（原始证据）/ `why`
    """
    H, W = L.shape
    tb = float(getattr(cfg, 'SCENE_BACK_TB', 15.0))
    ts = float(getattr(cfg, 'SCENE_BACK_TS', 20.0))
    n2 = max(int(getattr(cfg, 'SCENE_BACK_NBLK2', 8) or 8), 2)
    floor = float(getattr(cfg, 'SCENE_LIGHT_CONF_FLOOR', 0.35))
    grade = float(getattr(cfg, 'SCENE_LIGHT_GRADE', 0.50))
    t_off = float(getattr(cfg, 'SCENE_BACK_CLIP_OFF_MIN', 0.05))
    ev = {}
    out = dict(label=None, why=None, conf=0.0, deg_back=0.0, deg_side=0.0,
               degrade=None, ev=ev, H=H, W=W)

    # ---- ★ 主体：优先「人在哪」；拿不到 ⇒ **降级**用中心区（不是弃权）----
    sub = None
    if person is not None:
        per = np.asarray(person, np.float64)
        if per.ndim == 2 and tuple(per.shape[:2]) == (H, W):
            s = per > 0.5
            if (int(s.sum()) >= int(getattr(cfg, 'SCENE_BACK_MIN_SUB_PX', 500))
                    and int((~s).sum()) >= 500):
                sub = s
    if sub is None:
        sub = np.zeros((H, W), bool)
        sub[H // 3:2 * H // 3, W // 3:2 * W // 3] = True
        out['degrade'] = 'center_subject(人在哪不可用 ⇒ 中心区当主体)'
    bak = ~sub
    ev['person_pct'] = round(float(sub.mean() * 100.0), 2)

    # ---- 图本身极端 ⇒ 真的判不动（这两条保留：不是"证据弱"，是"没有证据"）----
    _l50 = float(np.median(L))
    ev['L50'] = round(_l50, 1)
    if (_l50 < float(getattr(cfg, 'SCENE_BACK_EXTREME_LO', 8.0))
            or _l50 > float(getattr(cfg, 'SCENE_BACK_EXTREME_HI', 92.0))):
        out['why'] = 'extreme_luma(%.0f)' % _l50
        return out
    rgb = np.clip(np.asarray(d, np.float64), 0.0, 1.0)
    cl = (rgb.max(axis=2) >= 254.0 / 255.0) if rgb.ndim == 3 else np.zeros((H, W), bool)
    ev['clip_pct'] = round(100.0 * float(cl.mean()), 3)
    if ev['clip_pct'] > float(getattr(cfg, 'SCENE_BACK_BLOWN_ALL', 30.0)):
        out['why'] = 'mostly_blown(%.1f%%)' % ev['clip_pct']
        return out

    # ---- ① 明暗结构（提分辨率 + 块间关系：边条 + **趋势**两种口径都留）----
    bf = _block_fields(L, n2)
    e_lr, e_tb, e_span, spike = bf['E_lr'], bf['E_tb'], bf['E_span'], bf['spike']
    ev.update(E_lr=round(e_lr, 1), E_tb=round(e_tb, 1), E_span=round(e_span, 1),
              spike=round(spike, 1), coh_x=round(bf['coh_x'], 2), coh_y=round(bf['coh_y'], 2),
              E_lr_t=round(bf['E_lr_t'], 1), E_tb_t=round(bf['E_tb_t'], 1), nblk=n2)
    ev['E_bg'] = round(float(np.median(L[bak]) - np.median(L[sub])), 1)

    # ---- ② 过曝区在主体之外（**底部不算**：亮地面/水面不是光源）+ 突出光源 ----
    _bot = _sub_bottom_row(sub, n2)
    ev['sub_bot_blk'] = _bot
    cz, _n_clip = _blk_off(cl, sub, n2, float(getattr(cfg, 'SCENE_BACK_CLIP_PCT', 3.0)),
                           max_row=_bot)
    hz, _n_hot = _blk_off(bf['z'] >= bf['z'].max()
                          - float(getattr(cfg, 'SCENE_BACK_HOT_REL', 0.10)) * max(e_span, 1e-6),
                          sub, n2, 0.0, max_row=_bot)
    ev['clip_blk_off'] = round(cz, 3)
    ev['hot_blk_off'] = round(hz, 3)
    has_src = bool(hz > 0.0 and spike >= float(getattr(cfg, 'SCENE_BACK_SPIKE_REL', 0.30))
                   * max(e_span, 1e-6))
    ev['has_spike_src'] = has_src

    # ---- ③ ★ 脸上的受光（新线索；缺 ⇒ 跳过，不是弃权）----
    fe = _face_evidence(L, sub, face, cfg)
    ev['face'] = dict(ok=fe['ok'], n_px=fe['n_px'],
                      **{k: (None if fe[k] is None else round(fe[k], 1))
                         for k in ('E_bg', 'E_lr', 'E_tb', 'E_span')})

    # ---- ④ 合成器：每条线索 → 0~1 程度，软或合并 ----
    def _face(key, w=1.0, sign=1.0):
        v = fe.get(key)
        return (w, _ramp(sign * v, 2.0 * tb)) if (fe['ok'] and v is not None) else (0.0, 0.0)

    # ★★★ 10-09 自检修的第 4 个缺陷（最要紧的一条）：**因果证据必须是必要条件**。
    #   逆光的**定义**就是"光在主体背后" ⇒ 它必然表现为「**主体（尤其是脸）比它身后暗**」。
    #   原先六条线索一律软或 ⇒ "上亮下暗"或"过曝区在主体外"**单独**就能顶出逆光族，
    #   结果实测：新判逆光族的 158 张里 **87 张（55%）的脸其实比背景亮**（自相矛盾）。
    #   ⇒ 现在：`deg_back = 因果 × 支持`。
    #     · 因果（必需）= 主体比背景暗 **或** 脸比背景暗（两者都可用时软或）；
    #     · 支持（强化）= 上亮下暗 / 过曝区在主体外 / 突出光源 / 脸上上亮下暗。
    # ★★★ 10-09 自检修的第 6 个缺陷：**主/客体掩膜会污染因果证据**。
    #   `E_bg = 背景中位 − 主体中位`，"主体"里**混着头发和衣服**（常常很暗）⇒ 一个
    #   "深色衣服 + 亮脸 + 中亮背景"的人会让 `E_bg` 变正，于是被判逆光 —— 但**根本没逆光**。
    #   实测：新判逆光族的 98 张里仍有 **42 张（43%）的脸其实比背景亮**。
    #   ⇒ 有**脸皮肤**掩膜时，以**脸**为主证据（脸是同一材质、连续表面，最干净的"主体"读数，
    #     也是人像布光实践里最可靠的证据），掩膜版的 `E_bg` 只当**弱支持**（权重 0.35）。
    if fe['ok'] and fe.get('E_bg') is not None:
        causal = _soft_or([
            (1.0, _ramp(fe['E_bg'], 2.0 * tb)),                       # ★ 脸比背景暗（主）
            (float(getattr(cfg, 'SCENE_LIGHT_EBG_W_FACE', 0.35)),
             _ramp(ev['E_bg'], 2.0 * tb)),                            # 主体比背景暗（弱支持）
        ])
    else:
        causal = _ramp(ev['E_bg'], 2.0 * tb)
    support = _soft_or([
        (1.0, _ramp(-e_tb, 2.0 * ts)),               # 上亮下暗
        (1.0, _ramp(cz, 2.0 * t_off)),               # 过曝区在主体之外（**底部已排除**）
        (0.6, 1.0 if has_src else 0.0),              # 突出光源（v3 教训：单独不够 ⇒ 权重低）
        _face('E_tb', 0.7, -1.0),                    # ★ 脸上「上亮下暗」
    ])
    #   ★★ 形式：`deg_back = causal × (1 + 0.35 × support)`（上限 1）。
    #      · `causal = 0` ⇒ `deg_back = 0`（**因果必需**，这次修的重点）；
    #      · `causal = 0.5`（= `E_bg` 正好在标定阈值 `T` 上）⇒ 0.5~0.68 ⇒ 够「逆光族」的门
    #        （**与 v3「E_bg ≥ T ⇒ 逆光族」口径对齐**）；
    #      · `support` 只能**放大**、不能**制造**。
    deg_back = min(1.0, causal * (1.0 + 0.35 * support))
    ev['causal'], ev['support'] = round(causal, 3), round(support, 3)
    deg_side = _soft_or([
        (1.0, _ramp(abs(bf['E_lr_t']), 2.0 * ts)),   # ★ **趋势量**（不是边条）：抗"某一侧有亮物体"
        _face('E_lr', 0.7, +1.0),                    # ★ 脸上左右明暗差
    ])
    deg_front = _soft_or([                            # "明确顺光/面光"的**正面证据**
        (1.0, _ramp(-ev['E_bg'], 2.0 * tb)),          # 背景比主体暗 ⇒ 光从相机方向来
        _face('E_bg', 0.7, -1.0),                     # 脸比背景亮
    ])
    out['deg_back'], out['deg_side'] = round(deg_back, 3), round(deg_side, 3)
    out['conf'] = round(max(deg_back, deg_side, deg_front), 3)
    out['deg_front'] = round(deg_front, 3)

    # ---- ⑤ ★★ 两条**给"动作"用的连续量**（不是光位分类；不参与上面的分级）----
    #   · `deg_source` = **画面里有"独立强光源"的程度** ⇒ 光晕 / 柔光 该不该给。
    #     ★ 两个分量都是本工程**已标定**的口径，不新拍阈值：
    #       ① `spike / E_span` = **「光源 vs 渐变」**（v3 的立身之本，本身就是相对量 ⇒ 跨域可比）；
    #       ② 显示域过曝面积占比（TU Delft NAO 的"≥2% 像素被 clip 即开门"那一档）。
    #     ⚠⚠ **实测（本批素材）：这条通道基本不动** —— `spike/span` 到得了阈值的 **0%**、
    #       `has_src` **0%**、显示域过曝 我们 **0%** / 鹿井 10%（中位 0.10%）。
    #       ⇒ **通道建好，数值等有"强光源"的素材再标定**（别拿 0 覆盖率的信号去定幅度）。
    #   · `deg_reflect` = **反射光 / 底光程度**（画面下亮）——
    #     人民日报对「脚光」的定义原文就是「**如水面的反光**」；实测鹿井 **19%** ≥0.5、我们 9%。
    #     ⚠ 目前**只输出、不驱动任何参数**（还没有可靠的动作对应）。
    _src_rel = spike / max(e_span, 1e-6)
    out['deg_source'] = round(_soft_or([
        (1.0, _ramp(_src_rel, 2.0 * float(getattr(cfg, 'SCENE_BACK_SPIKE_REL', 0.30)))),
        (0.5, _ramp(ev['clip_pct'], float(getattr(cfg, 'SCENE_LIGHT_SRC_CLIP_HI', 3.0)))),
    ]), 3)
    out['deg_reflect'] = round(_ramp(e_tb, 2.0 * ts), 3)
    ev['src_rel'] = round(_src_rel, 3)

    # ---- 判决：三个程度量**取最大**（不是"逆光优先"）+ 只在"全弱"时看结构 ----
    #   ★★ 10-09 自检修的第二个缺陷：原先只比较 `deg_back` 与 `deg_side`，
    #      **从不用正面证据否决** ⇒ 125_02 那种"脸比背景亮 36 格"（明显正光）也会被判正逆光。
    #      现在三量取 argmax；逆光族还要**压过**正面证据。
    #   ★★ 10-09 自检修的第 5 个缺陷（**逻辑漏洞**）：原先第一道门写的是
    #      `best >= max(floor, grade)`，而 `floor(0.35) < grade(0.5)` ⇒
    #      **落在 [0.35, 0.50) 的片子直接被推进"弃权"**（既不该判、也不该弃——白丢）。
    #      ⇒ 改成单一门槛 `floor`：**弃权只留给「best < floor 且画面本来有大结构」**。
    struct_min = float(getattr(cfg, 'SCENE_LIGHT_STRUCT_MIN', 12.0))
    best = max(deg_back, deg_side, deg_front)
    if best >= floor:
        if best == deg_back and deg_back >= grade:
            # 亮区偏主体中轴 ⇒ 正逆光；偏一侧 ⇒ 侧逆光（沿用 v3 口径）
            if cl.any():
                _yy, _xx = np.nonzero(cl)
                hi_cx = float(np.median(_xx)) / max(W - 1, 1)
            else:
                hi_cx = float(int(np.argmax(bf['z'][0] + bf['z'][1] + bf['z'][2])) + 0.5) / n2
            _ys, _xs = np.nonzero(sub)
            cx = abs(hi_cx - float(np.median(_xs)) / max(W - 1, 1))
            ev['hi_cx'], ev['cx_diff'] = round(hi_cx, 3), round(cx, 3)
            out['label'] = ('正逆光' if cx <= float(getattr(cfg, 'SCENE_BACK_CX_TOL', 0.10))
                            else '侧逆光')
        elif best == deg_side:
            out['label'] = '侧光'
        else:
            out['label'] = '面光/顺平光'
    elif e_span < struct_min:
        # 画面**本来就没有明暗结构** ⇒ "面光/顺平光"是**有信息**的判断（不是弃权）
        out['label'] = '面光/顺平光'
    else:
        out['why'] = ('weak: best=%.2f deg_back=%.2f deg_side=%.2f deg_front=%.2f E_span=%.1f'
                      % (best, deg_back, deg_side, deg_front, e_span))
    return out


def classify(disp, cfg=C, lin=None, person=None, face=None):
    r"""量四根轴（一次算完）。

    `lin`：解码后的**线性**图 —— 只给 `overwhite`（源头过曝）那一轴用。
    `person`：`person.person(disp)` 的粗掩膜（人在哪）—— 只给 `back`（光位）那一轴用。
      **拿不到就传 `None`** ⇒ `back` 弃权（写 `-`），其余三根照常。
    `face`：`person.person_face(disp)` 给的**脸皮肤**掩膜（可选）—— 只给**并列的第二条光位实现**
      （`light_evidence`，见下）用；`back` 轴**不看它**。

    ⚠⚠ **`SCENE_LIGHT_EVIDENCE`（默认 `False`）**：打开后多算一份"多线索 → 程度量"的光位，
      落在 `out['back2']` / `raw['light']` —— **只给对照用，不参与缓存键、不参与下游取参**。
      接执行要三件：① `VERSION` +1 ② 复跑 `selftest` ③ 先出对照给 SV 目检（技能 §192）。

    @returns {dict}
      四根轴 + `key`（缓存用，带 VERSION）+ `raw`（量到的原值，便于自查与事后标定阈值）
      （+ `back2` / `raw['light']`，仅当 `SCENE_LIGHT_EVIDENCE` 打开）
    """
    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    lab = color.to_lab(np.ascontiguousarray(d))
    L = lab[..., 0]
    p5, p50, p95 = (float(np.percentile(L, q)) for q in (5.0, 50.0, 95.0))
    span = p95 - p5
    out = dict(raw=dict(L5=p5, L50=p50, L95=p95, span=span))

    out['exp'] = _b(list(_EXP_ORDER),
                    [float(getattr(cfg, 'SCENE_EXP_DARK', 18.5)),
                     float(getattr(cfg, 'SCENE_EXP_BRIGHT', 25.2))], p50)
    out['span'] = _b(list(_SPAN_ORDER),
                     [float(getattr(cfg, 'SCENE_SPAN_FLAT', 47.0)),
                      float(getattr(cfg, 'SCENE_SPAN_WIDE', 70.0))], span)

    # ---- 光位：主体 vs 它身后的背景（见 `_light_position`）----
    out['back'], _lp = _light_position(d, L, person, cfg)
    out['raw'].update(_lp)

    # ---- ★ 并列的第二条光位实现（多线索 → 程度量；**默认关、未接执行**）----
    #   ⚠ 这里**不影响** `back`、`key`、任何下游取参 —— 纯粹是"同一次调用里顺带算一份"。
    if bool(getattr(cfg, 'SCENE_LIGHT_EVIDENCE', False)):
        try:
            out['raw']['light'] = light_evidence(d, L, person=person, face=face, cfg=cfg)
            out['back2'] = out['raw']['light'].get('label')
        except Exception as _e:                             # noqa: BLE001
            out['raw']['light'] = dict(label=None, why='error: %s' % type(_e).__name__)
            out['back2'] = None

    # ---- 源头过曝：**必须在解码后的线性域判**（显示域那边早被引擎重渲染压过了）----
    ow_pct = None
    if lin is not None:
        a = np.asarray(lin, np.float64)
        lvl = float(getattr(cfg, 'SCENE_OVERWHITE_LEVEL', 1.0))
        if a.ndim == 3 and a.shape[-1] >= 3:
            ow_pct = float((a[..., :3].max(axis=-1) >= lvl).mean() * 100.0)
    out['raw']['overwhite_pct'] = ow_pct
    out['overwhite'] = (None if ow_pct is None
                        else bool(ow_pct >= float(getattr(cfg, 'SCENE_OVERWHITE_PCT', 0.5))))

    out['key'] = 'v%d|%s' % (VERSION, '|'.join(token(k, out.get(k)) for k in AXES))
    return out


def label(scene):
    """给人看的一句话（汇报用，别甩 `key`）。"""
    if not scene:
        return '（没判）'
    bits = [scene.get('exp') or '?', scene.get('span') or '?']
    if scene.get('back'):
        bits.append(scene['back'])
    if scene.get('overwhite'):
        bits.append('源头过曝')
    return ' · '.join(bits)
