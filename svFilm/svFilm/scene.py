# -*- coding: utf-8 -*-
r"""**场景判据** —— 「这张图是什么场景」。

## 为什么要有它
SV 09-26 提的：「可不可以分场景，比如分**过曝**场景、分**明暗对比强烈**的场景、分**脸部特写**场景，
然后采取不同的数值参数？」—— 「按场景分参数」这件事有三件地基，这是**第一件**（判据）；
另两件是「参数表分档」与「缓存键带场景」。

## 判据的选取原则（09-26 踩出来的，别再犯）
① **只收「能量出来、且跟『参数该不该变』直接相关」的量。**
   **不按色相带那种「能算但不知道是什么」的量分类** —— 那是 L3 混色 / L4 肤色踩过的坑
   （拿色相窗当人脸，实测窗里只有 10.5% 是真皮肤，剩下是墙/木头/黄叶）。
② **只用从"H×W 像素"上能量出来的量**（不依赖 EXIF、不依赖机型）⇒ 同一份代码在
   RAW / 机内 JPG / 别人的图上一样跑。
③ ★★★ **分类之前先问：「这个参数本来就该按场景变吗？」**
   §86.5 已经量过：我们跟大师的三条差（太艳 / 中调太黄 / 脸偏暗）**跨所有分组一致**
   ⇒ 那三根旋钮**分场景没有收益**。所以这个模块存在的意义**不是**"给每根旋钮都套一层场景"，
   而是① 把「哪些轴真的有区别」变成**能量出来的问题**；② 给真要变的那些提供通道。
   ⚠ 相反的例子（**大师自己就是分场景的**）：他的**落点 L50** 从 42.6（顺光）到 71.1（高调）
   —— 那个变化是**内容/场景**带来的，所以它归「曝光风格」这根手选滑杆，不归自动分档。

## 五根轴
| 轴 | 怎么量 | 分档 | 跟哪根参数有关 |
|---|---|---|---|
| `exp`  曝光 | 整张中位 L50 | 暗 / 正常 / 亮 | 影调层的压黑位/压亮部力度 |
| `span` 光比 | 跨度 L95−L5 | 平 / 正常 / 大 | 同上的护栏（大光比先压暗部会压死） |
| `back` 逆光 | 脸中位L − 整张L50 | 是 / 否 | 脸的亮度靶（逆光的脸**本来**就暗） |
| `shot` 景别 | 人占画面面积 % | 特写 / 近景 / 中景 / 远景 | L4 脸修正的作用面 |
| `face` 脸可见度 | 检测器 / 分割 | face / seg / none | 没脸时 L4 整套不该跑 |
| `overwhite` 源头过曝 | **线性域**里**超过白点**的像素占比 | 是 / 否 | 唯一一条"必须在引擎里动作"的轴 |

★★★ **`overwhite` 为什么要单列一轴（09-26 实测）**：我们那 52 张里 **6 张（11.5%）有 6~13% 的
像素在线性域就**超过白点**（`lin.max ≥ 1.0`：5.9 / 5.9 / 6.7 / 8.0 / 10.3 / 12.9%），
其余片子是 **0.0%** —— 中间没有过渡，信号很干净。而现在的护栏（`tone.health`）量的是
**引擎出图之后**的显示域，那时高光已经被重渲染压过，信息早没了；
**"源头有多少东西超过白"只在线性域还看得到。**
按铁律「要改整体亮暗回引擎那一步（线性域、有高光余量）」⇒ 这一轴的**动作位**在引擎。

⚠⚠⚠ **判据踩过的坑（第一版是错的，别改回去）**：第一版写的是
「**三通道都 ≥0.995** 才算爆」，依据是"解码后 L95 > 95 的有 10/52"。
**那个依据是错的**：显示域的 99.8 只是 sRGB 编码把 **1.0 以上一律压到 255** 的假象 ——
实测那 4 张线性最大值只有 **1.261**、三通道同时贴顶的像素是 **0.000%**，
按那个判据这一轴**永远不会响**（= 死轴）。
⇒ 正确判据 = **`lin` 的通道最大值 ≥ 1.0**（"已经超过白点"），不是"三通道同时到顶"。
   （"三通道同时到顶"那条要真成立，得先知道传感器真正的饱和电平；`1.0` 只是白点，不是饱和点。）

★ 阈值全在 `config.SCENE_*`（可调参数只在 config），**标定依据**见
  `E:/Debug_svStudio/_debug/场景分布_0926.json`（我们自己 52 张的解码后分布）。
★ `VERSION` **必须进缓存键** —— 判据一改就是另一套参数，缓存得失效。
"""
from __future__ import annotations

import numpy as np

from . import color
from . import config as C

VERSION = 1
AXES = ('exp', 'span', 'back', 'shot', 'face', 'overwhite')

_EXP_ORDER = ('暗', '正常', '亮')
_SPAN_ORDER = ('平', '正常', '大')
_SHOT_ORDER = ('特写', '近景', '中景', '远景')


def _b(names, edges, v):
    """按升序阈值 `edges` 把 `v` 分到 `names`（比 `edges` 多一档）。"""
    i = 0
    for e in edges:
        if v >= e:
            i += 1
    return names[i]


def token(axis, v):
    """把某一轴的值变成**写进 `_scene` 键里的那个词**。

    ★★★ 09-26 为什么要它：`back`/`blown` 在**代码里是 bool**（`if scene['blown']`），
      但人写覆盖时想写的是「过曝」这种词。若两处各写各的，就会出现
      `{"blown=过曝": …}` 写在文档里、代码却去找 `"blown=True"` 的**静默不命中**
      （自检第一次跑就抓到了这个）。⇒ **只有这一个函数负责词形**，键与文档都用它。
    """
    if v is None:
        return '-'
    if axis == 'back':
        return '逆光' if v else '顺平'
    if axis == 'overwhite':
        return '过曝' if v else '正常'
    return str(v)


def classify(disp, parsed=None, cfg=C, lin=None):
    r"""给**解码后**那张图（`pipeline` 里传的是 `s.disp`）打标签。

    `parsed`：`face.parse(...)` 的结果 —— **必须跟人脸掩膜是同一次**
      （同一张图、同一次调用；见 `pipeline` 里 `parsed=` 的说明）。
    `lin`：**解码后的线性图**（`s.lin`）。只有 `blown` 那一轴要它 ——
      源头过曝只能在**线性域**判（显示域那边已经压过了）。不传 ⇒ `blown=None`（不判）。

    @returns {dict}
      六根轴 + `key`（缓存用，带 VERSION）+ `raw`（量到的原值，便于自查与事后标定阈值）
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

    # ---- 人 / 脸：只认**同一次**解析 ----
    mk = ((parsed or {}).get('masks') or {})
    per = mk.get('person')
    person_pct = float((np.asarray(per) > 0.5).mean() * 100.0) if per is not None else None
    out['raw']['person_pct'] = person_pct
    fs = mk.get('face_skin')
    face_L = face_rel = None
    skin_n = 0
    if fs is not None:
        sel = np.asarray(fs) > 0.5
        skin_n = int(sel.sum())
        if skin_n > 200:
            face_L = float(np.median(L[sel]))
            face_rel = face_L - p50
    out['raw']['face_L'] = face_L
    out['raw']['face_rel'] = face_rel
    out['raw']['skin_n'] = skin_n

    if (parsed or {}).get('face') is not None:
        out['face'] = 'face'
    elif skin_n > 200:
        out['face'] = 'seg'
    else:
        out['face'] = 'none'

    # 逆光 = 脸明显比整张暗（脸本来就该暗）。没有脸 ⇒ None（这一轴无从判断）
    # ⚠⚠ **这一轴不能拿去看"大师的逆光脸是不是更暗"** —— 它是**用脸自己的相对亮度**定义的，
    #   再去看脸亮度就是自证。09-26 实测踩过：按它分组得出"大师逆光脸暗 5~8 格"，
    #   改用**与脸无关**的量（整张 L50 三分位）分组后，大师的脸 L* 只漂 ±2~5（鹿井 r=−0.17）
    #   ⇒ 那是分组方式造成的**假象**，脸的绝对 L* 仍然是**不变量**。
    out['back'] = (None if face_rel is None
                   else bool(face_rel < float(getattr(cfg, 'SCENE_BACK_REL', -3.0))))

    if person_pct is None:
        out['shot'] = None
    else:
        out['shot'] = _b(list(_SHOT_ORDER),
                         [float(getattr(cfg, 'SCENE_SHOT_NEAR', 10.0)),
                          float(getattr(cfg, 'SCENE_SHOT_MED', 20.0)),
                          float(getattr(cfg, 'SCENE_SHOT_CLOSE', 35.0))], person_pct)

    # ---- 源头过曝：只能在**线性域**判（"通道最大值 ≥ 白点" = 已经超过白）----
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
    if scene.get('back') is not None:
        bits.append('逆光' if scene['back'] else '顺平光')
    if scene.get('shot'):
        bits.append(scene['shot'])
    bits.append({'face': '认到脸', 'seg': '只有分割脸皮', 'none': '没有脸'}.get(scene.get('face'), '?'))
    if scene.get('overwhite'):
        bits.append('源头过曝')
    return ' · '.join(bits)
