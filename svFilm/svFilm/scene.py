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

VERSION = 6                          # ★ 换判据就要 +1（缓存键带它）
#   6（10-09）：光位判据的「贴边 ⇒ 弃权」守卫改成**单向**（原先对 `E_bg`/`E_tb` 误用 `abs`，
#              把"方向相反、证据明确"的片子当"贴边"弃权）。实测弃权率 50%→33~40%（鹿井）、71%→49%（我们）。
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


def classify(disp, cfg=C, lin=None, person=None):
    r"""量四根轴（一次算完）。

    `lin`：解码后的**线性**图 —— 只给 `overwhite`（源头过曝）那一轴用。
    `person`：`person.person(disp)` 的粗掩膜（人在哪）—— 只给 `back`（光位）那一轴用。
      **拿不到就传 `None`** ⇒ `back` 弃权（写 `-`），其余三根照常。

    @returns {dict}
      四根轴 + `key`（缓存用，带 VERSION）+ `raw`（量到的原值，便于自查与事后标定阈值）
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
