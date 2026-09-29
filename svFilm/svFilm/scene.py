# -*- coding: utf-8 -*-
r"""**场景判据** —— 「这张图是什么场景」。

## 为什么要有它
SV 09-26 提的：「可不可以分场景，比如分**过曝**场景、分**明暗对比强烈**的场景，
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
   （SV 原话：「人识别就不要用那个性能开销太高的…现在**只需要一个人物整体位置**来辅助判断光位」）。
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

VERSION = 4                          # ★ 换判据就要 +1（缓存键带它）
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


def _light_position(d, L, person, cfg):
    """**光位**：正逆光 / 侧逆光 / 顺平光。

    ★★★ 判据来自同行的实现（技能 §91.2），**不是**"脸比整张中位暗"：
      ① **最亮那一块在不在主体身上** —— 逆光的**因果**是"光源在主体背后" ⇒
         **最亮的东西是背景 / 天空，不是主体**（顺光时最亮的多半就是主体自己）；
      ② **最亮那块比主体亮多少** —— 背景明显亮于主体 ⇒ 光从背后来；
      ③ **亮区重心相对主体重心的水平偏移** —— 偏中轴 ⇒ 正逆光；偏一侧 ⇒ 侧逆光
         （专业上按光源与镜头光轴夹角分：170~180° 是正逆光、120~150° 是侧逆光）。
    ⚠ 一律**用主体 vs 它身后的背景**来比，**不用整张中位** —— 逆光片是"两头重"的分布，
      整张中位落在两峰之间的谷里，最没有信息量。
    ⚠ **主体太小 / 没有人掩膜 ⇒ 返回 `None`（弃权）**，不许硬给"顺平光"。
    @returns (标签 or None, raw 字典)
    """
    H, W = L.shape
    raw = dict(bg_sub=None, above_sub=None, hi_cy=None, hi_cx=None, hi_gap=None,
               hi_on_sub=None, clip_bg_pct=None, person_pct=None)
    if person is None:
        return None, raw
    per = np.asarray(person, np.float64)
    if tuple(per.shape[:2]) != (H, W):
        return None, raw
    sub = per > 0.5
    bak = ~sub
    raw['person_pct'] = float(sub.mean() * 100.0)
    if int(sub.sum()) < int(getattr(cfg, 'SCENE_BACK_MIN_SUB_PX', 800)) or int(bak.sum()) < 500:
        return None, raw                    # 主体太小 / 没背景 ⇒ 这一轴无从判（**弃权**）

    raw['bg_sub'] = float(np.median(L[bak]) - np.median(L[sub]))      # 诊断用（见 config 注释）

    ys, xs = np.nonzero(sub)
    Hm, Wm = max(H - 1, 1), max(W - 1, 1)
    subc_x = float(np.median(xs)) / Wm

    # ★★ 主体正上方那条带（**保留作诊断** —— 它只对"光在正后方、主体居中"成立；
    #   实测：主体在画面右侧、光从左上来的侧逆光，正上方是**树冠**（暗的），这条会判负）。
    band_h = max(8, int(0.6 * (ys.max() - ys.min() + 1)))
    band = np.zeros_like(sub)
    band[max(0, ys.min() - band_h):max(ys.min(), band_h), xs.min():xs.max() + 1] = True
    band &= bak
    raw['above_sub'] = (float(np.median(L[band]) - np.median(L[sub]))
                        if int(band.sum()) >= 200 else raw['bg_sub'])

    # ★★★ 主判据：**画面最亮那一块 (P95+) 在不在主体身上、比主体亮多少**
    hi = L >= float(np.percentile(L, 95.0))
    if int(hi.sum()) < 50:
        return None, raw
    yy, xx = np.nonzero(hi)
    raw['hi_cy'] = float(np.median(yy)) / Hm
    raw['hi_cx'] = float(np.median(xx)) / Wm
    raw['hi_gap'] = float(np.median(L[hi]) - np.median(L[sub]))
    # ★ 「最亮那块有多少落在主体身上」—— 比"外接框"稳：
    #   外接框会被**画面里的第二个人**撑满整幅（实测 DSCF1231 左下角还有个人），
    #   于是"亮区在框内"会误成立。这个比例只跟像素走，跟几个人无关。
    raw['hi_on_sub'] = float((hi & sub).sum()) / max(int(hi.sum()), 1)

    # ④ 「糊死的是哪一块」：全通道贴白的像素里，落在**背景**上的占比。
    #   为什么要有它：负片在逆光下**让天空爆是正常的**（技能 §94.3 的专业共识），
    #   把**主体/皮肤**爆掉才是问题 ⇒ 光看"糊死占比"缺了位置信息，必须看爆在哪。
    rgb = np.clip(np.asarray(d, np.float64), 0.0, 1.0)
    if rgb.ndim == 3:
        cl = rgb.min(axis=-1) >= 254.0 / 255.0
        n_cl = int(cl.sum())
        if n_cl > 0:
            raw['clip_bg_pct'] = float((cl & bak).sum()) / n_cl * 100.0

    # ---- 判决 ----
    # ★ 主判据 = **最亮那块基本不在主体身上** ＋ **最亮那块比主体亮得多**；
    #   次判据 = 亮区重心相对**主体重心**的水平偏移（分正 / 侧）。
    if raw['hi_on_sub'] > float(getattr(cfg, 'SCENE_BACK_HI_ON_SUB', 0.15)):
        return '顺平光', raw                       # 最亮的就是主体自己 ⇒ 光打在主体上
    if raw['hi_gap'] < float(getattr(cfg, 'SCENE_BACK_HI_GAP', 18.0)):
        return '顺平光', raw
    if abs(raw['hi_cx'] - subc_x) <= float(getattr(cfg, 'SCENE_BACK_CX_TOL', 0.18)):
        return '正逆光', raw
    return '侧逆光', raw


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
