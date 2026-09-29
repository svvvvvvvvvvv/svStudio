# -*- coding: utf-8 -*-
r"""**按预设分组的靶** —— 「胶片用了谁的，影调和颜色就依据谁」。

## 为什么要有这个文件
09-24 SV 提的：现在三条链的靶来自三个不同的人 ——
选了「Pro400H清风」（= **滨田英明**那条预设）却往**鹿井**的形状收、颜色往**小红书**收，**内部不一致**。
⇒ 把这个文件建起来：**每条预设 → 那位大师自己的靶**。

## 靶是从哪来的（全部实测，`_debug/analysis/stock_targets_0924.json`）
素材：`E:\WorkBuddy\摄影助手\大师作品\` 下对应大师的原片。

| 预设 | 对应大师 | 原片 | 黑位形状 | 亮部形状 | 暗Δa | 暗Δb | 亮Δa | 亮Δb |
|---|---|---|---|---|---|---|---|---|
| **Pro400H清风** | 滨田英明 | 33 张 | **−45.8** | **+15.9** | −2.69 | −0.88 | +2.17 | +1.16 |
| **Portra400薄荷** | 増田彩来 | 120 张 | **−41.1** | **+29.8** | −0.19 | −0.66 | 0.00 | +0.04 |
| （其余 7 条，暂用） | 鹿井+小红书 | 32+80 | −53.1 | +31.8 | −1.2 | +2.0 | −0.4 | −0.5 |

★ 读得出来的两件事：
- **滨田的亮部形状只有 +15.9**（鹿井是 +31.7）⇒ 他**高光收得很紧**，亮部离中间不远。
- **滨田是"暗部青绿、亮部偏暖"**（暗 Δa −2.69 / 亮 Δa +2.17）—— 典型的冷暖分离。
- **増田几乎不做分色**（四个数都在 ±0.2 以内）⇒ 他的片颜色很中性。

## 色相带的增益：只动"两位大师一致"的那些
口径＝「该带的 C 中位 ÷ 整张 C 中位」，**内容归一**。增益 = 靶 ÷ 我们基线 − 1，
基线来自「滨田引擎 + 影调、**关掉分色混色**」的成片。

| 带 | 滨田 | 増田 | 两位一致？ |
|---|---|---|---|
| 0–30 红 | −0.00 | −0.18 | ✗ 不一致 |
| 30–60 橙 | −0.09 | +0.13 | ✗ 不一致 |
| 60–90 黄 | −0.28 | −0.02 | ✗ 不一致 |
| 90–120 黄绿 | −0.25 | −0.27 | ✅ 都负 |
| 120–150 绿 | −0.24 | −0.23 | ✅ 都负 |
| 150–180 青绿 | −0.00 | +0.05 | ✗ 不一致 |
| 210–240 蓝 | −0.23 | −0.17 | ✅ 都负 |
| 240–270 蓝紫 | −0.33 | −0.16 | ✅ 都负 |
| 270–300 紫 | −0.19 | −0.30 | ✅ 都负 |
| 300–330 品红 | −0.25 | −0.32 | ✅ 都负 |
| 330–360 粉红 | −0.29 | −0.30 | ✅ 都负 |

⇒ **只动那 7 个"两位一致"的带**（都是"压低杂色"）。红/橙/黄/青绿两位不一致 ⇒ 保守不动
（不一致的差别更可能来自**内容差**而不是风格差 —— 色相带本身是受"他拍什么"影响的量）。

⚠ 色相带这一层**天生受内容影响**（比影调形状脏），所以这里只取"两位背书的交集"，
  不像影调形状/分色那样直接对着一位大师的绝对值调。
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_P = os.path.join(_HERE, 'data', 'targets.json')
_CACHE = {}


def load():
    if not _CACHE:
        with open(_P, encoding='utf-8') as f:
            _CACHE.update(json.load(f))
    return _CACHE


def for_stock(name, scene=None):
    """取某条预设的靶；没有专属靶的预设落回 `_default`。

    `scene`：`scene.classify(...)` 的结果 —— **这是「按场景分参数」的入口**。
    """
    d = load()
    t = dict(d.get('_default') or {})
    t.update(d.get(name) or {})
    t['stock'] = name
    t['own'] = bool(d.get(name))

    # ★★★ 09-26：**场景覆盖**（「按场景分参数」的入口）
    #   结构：`"_scene": {"<轴>=<值>": {字段: 值, ...}}`，例：`{"overwhite=过曝": {"split_limit": 8.0}}`。
    #   · 轴名 = `scene.AXES`（exp / span / overwhite）；
    #     **值一律用中文词**（`back` 用「逆光/顺平」、`overwhite` 用「过曝/正常」）——
    #     词形只由 `scene.token()` 负责，别在这儿另写一套（会静默不命中）。
    #   · `"<轴>=*"` = 那个轴的任意值都命中（写兜底用）
    #   · 轴序固定，后面的盖前面的
    #   · ⚠ **默认没有 `_scene` 这个键 ⇒ 一个字段都不盖，行为与加它之前逐位相同**
    #   · ⚠ 这里**只做覆盖、不做拟合** —— 数值从哪来是人的决定（见 `scene.py` 顶部那段：
    #     我们跟大师的三条差跨所有分组一致 ⇒ 那几根旋钮分场景没有收益）。
    t['_scene_hits'] = []
    if scene:
        ov = d.get('_scene') or {}
        if ov:
            # ⚠ 键里的"值"必须跟 `scene.token()` 一致（`back`/`blown` 在代码里是 bool，
            #   写覆盖时要用「逆光/顺平」「过曝/正常」这些词）—— 词形只有那一个函数负责。
            try:
                from . import scene as _S
                _tok = _S.token
                _axes = _S.AXES
            except Exception:                                  # noqa: BLE001
                _tok, _axes = (lambda a, v: str(v)), tuple(scene.keys())
            for _ax in _axes:
                _v = scene.get(_ax)
                if _v is None:
                    continue
                for _k in ('%s=%s' % (_ax, _tok(_ax, _v)), '%s=*' % _ax):
                    _blk = ov.get(_k)
                    if isinstance(_blk, dict):
                        t.update(_blk)
                        t['_scene_hits'].append(_k)
    t['scene'] = (scene or {}).get('key')
    return t


# ---------------------------------------------------------------------------
# ★★★ 09-28 新增：**场景 → 引擎参数覆盖**（`_scene_engine`）
# ---------------------------------------------------------------------------
def scene_engine(scene, stock=None, cfg=None):
    r"""**按场景改【引擎参数】** —— 和 `_scene` 并列，但喂给的地方不同。

    为什么要有它（09-28 SV 追问「之前不是做了场景识别影响柔光/颗粒/光晕吗」）：
      调研确实做过（`效果debug\2026-09-27\调研_颗粒与柔光该给多少.md`），
      结论之一：**「光晕只在有强光源时出现 ⇒ 不该固定，该按 `back` 轴挂覆盖」**。
      ⚠ 但**一直没落地** —— 因为 `_scene` 走的是 `for_stock()` ⇒ 返回的是**靶**，
      只能喂**后期层（`grade`）**，**够不到引擎**（柔光/颗粒/光晕都在引擎里）。
      ⇒ 本函数就是补这个通道。

    结构（键语义**与 `_scene` 完全一致** —— 同一个 `scene.token()`、同一套轴序）：
        `"_scene_engine": {"span=大": {"film_render.halation.halation_strength": [80, 24, 0]}}`
      · 值是**引擎参数的点路径**（如 `film_render.halation.halation_strength`）
      · `"<轴>=*"` = 该轴任意值都命中（兜底）
      · 轴序固定（`scene.AXES`），后面的盖前面的

    ★★ **值有两种写法**（`设计_光位到引擎参数的提示与偏移表.md` §1.2）：
        · **标量 / 列表** ⇒ **绝对值**，直接覆盖（如那条全局 morph）。
        · **`{"mul": k}`** ⇒ ★ **乘性系数**，乘在**该预设的基线**上（`k<1` 变亮、`k>1` 变暗，看字段）。
          ⇒ **落地按系数写**：这样 `SPEK_PE_SHIFT` 这类"手动微调旋钮"以及**换预设**时都还跟得上。
          ⚠ 传 `stock` 才解析得动（要读预设基线）；`stock=None` 时 `{"mul":...}` **原样返回** ⇒
            下游 `presets._render_locked` 会当场报"字段路径走不通" ⇒ **故意的，别让它静默**。

    ⚠⚠ **写错路径会当场报错**（`presets._render_locked` 里那个"字段路径走不通"）——
      这是**故意**的：静默不命中才是灾难。
    ★★ 09-29（晚）：上面说的"全局 `"*"`"是**第一层兜底**；**再往下一层**是
      `_stock_engine[<预设名>]`（**按预设**的基值，合并顺序 预设基值 → `"*"` → 按轴覆盖）。
      为什么要有它：三条大师预设的**跨度/色偏本来就分得开**（靶 82.1 / 77.7 / 75.3），
      一份全局值贴不住三条 —— 见 §标定。
    ⚠ **默认没这个键 ⇒ 返回空 dict ⇒ 逐位同旧行为**（机制先建好，数值后面调研）。

    @param scene {dict}  `scene.classify()` 的输出（`None` ⇒ 只吃全局兜底 `"*"`）
    @param stock {str|None}  预设名（解析 `{"mul": k}` 用；`None` ⇒ 不解析）
    @returns {dict} `{引擎参数点路径: 值}`（可直接喂 `presets.render(overrides=...)`）
    """
    out = {}
    if not scene:
        scene = {}
    d = load()
    ov = d.get('_scene_engine') or {}
    # ★★★★ 09-29（晚）：三层合并，**越具体越靠后**（后面盖前面）：
    #   ① `_scene_engine["*"]`   —— **全局默认**（所有预设、所有场景都吃）
    #   ② `_stock_engine[预设]`  —— **按预设的基值**（比全局具体）
    #   ③ `_scene_engine["<轴>=…"]` —— **按场景的覆盖**（最具体，最后叠）
    #   为什么必须加第 ② 层：09-29 晚标定实测 —— 三条大师预设**彼此分得开**
    #     （跨度靶 鹿井 82.1 / 増田 77.7 / 滨田 75.3，色偏方向也不同）
    #   ⇒ **一份全局值贴不住三条**。原「影调层/颜色层」本来就是**按预设**给靶的
    #     （`targets.json` 的 `sh_abs` / `hi_abs`），这里把同一件事补回给引擎。
    #   ⚠ 顺序**不能反**：若把 `"*"` 放在后面，预设基值会被全局值**静默盖掉**
    #     （表现 = "明明写了却不生效"，正是本文件最怕的那类坑）。
    #   ⚠ 默认没有 `_stock_engine` 这个键 ⇒ `out` 为空 ⇒ **行为与加它之前逐位相同**。
    # --- ① 全局 `"*"`（无条件生效的兜底）---
    #   为什么要有：`"<轴>=*"` 只有在**该轴判出了值**时才命中（轴为 None 时整轴跳过）。
    #   而现实里"判不出光位"很常见（画面里没主体/人检不出）⇒ 那些片会一条都不命中。
    #   ⇒ `"*"` 是**无条件**的兜底（所有片都吃），后面的按轴覆盖再叠上去。
    _base = ov.get('*')
    if isinstance(_base, dict):
        out.update(_base)
    # --- ② 按预设的基值 ---
    _st = (d.get('_stock_engine') or {}).get(stock) if stock else None
    if isinstance(_st, dict):
        out.update(_st)
    # --- ③ 按场景轴的覆盖 ---
    try:
        from . import scene as _S
        _tok, _axes = _S.token, _S.AXES
    except Exception:                                      # noqa: BLE001
        _tok, _axes = (lambda a, v: str(v)), tuple(scene.keys())
    for _ax in _axes:
        _v = scene.get(_ax)
        if _v is None:
            continue
        for _k in ('%s=%s' % (_ax, _tok(_ax, _v)), '%s=*' % _ax):
            _blk = ov.get(_k)
            if isinstance(_blk, dict):
                out.update(_blk)
    # ---- ★ 解析 `{"mul": k}`（乘性系数 → 绝对量）----
    if stock and any(isinstance(v, dict) and 'mul' in v for v in out.values()):
        from . import presets as _PR
        _p = None
        for _k, _v in list(out.items()):
            if not (isinstance(_v, dict) and 'mul' in _v):
                continue
            if _p is None:
                _p = _PR._params_for(stock, cfg) if cfg is not None else _PR._params_for(stock)
            _b = getattr(*_PR._walk(_p, _k))       # ⚠ 路径走不通 ⇒ 抛，别吞
            _m = float(_v['mul'])
            if isinstance(_b, (list, tuple)):
                out[_k] = [float(x) * _m for x in _b]
            else:
                out[_k] = float(_b) * _m
    return out


def names():
    return [k for k in load() if not k.startswith('_')]


# ---------------------------------------------------------------------------
# ★★★ 09-29：**靶的缓存键**（给 pipeline 的渲染缓存用）
# ---------------------------------------------------------------------------
def cache_key(stock, scene=None):
    """把"这条预设 + 这套场景覆盖之后的靶"压成一个可哈希的键。

    为什么必须有它：`pipeline` 的渲染缓存 key 原来**不含靶** ⇒
      同一进程里改了 `targets.json`（或 `_scene` 覆盖生效与否变了）⇒ **仍然命中旧缓存** ⇒
      表现就是"拧了没反应"。⇒ 把它塞进 ckey，靶一变缓存立刻作废。
    """
    try:
        t = for_stock(stock, scene)
    except Exception:                                      # noqa: BLE001
        return ('tg-err', stock)
    items = []
    for k in sorted(t.keys()):
        v = t[k]
        if k.startswith('_scene_hits'):
            v = tuple(v or [])
        elif isinstance(v, list):
            v = tuple(v)
        elif isinstance(v, dict):
            v = tuple(sorted(v.items()))
        try:
            hash(v)
        except Exception:                                  # noqa: BLE001
            v = str(v)
        items.append((k, v))
    return tuple(items)
