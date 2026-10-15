# -*- coding: utf-8 -*-
r"""**按预设分组的靶** —— 「胶片用了谁的，影调和颜色就依据谁」（现在只有一条 `鹿井预设`，
靶 = 鹿井屿渔 26 张，**逐图 `[P25,P75]`**）。

★ `targets.json` 每条键 = 一条预设的靶，数值全部**实测**（不由本文件产生）。
★ `span` 由 `scorecard.py` 读作**线索** ⇒ **不作验收**；⚠ **跨内容靶不存在**。"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_P = os.path.join(_HERE, 'data', 'targets.json')
_CACHE = {}
_SIG = None            # 上次读盘时的 (st_mtime_ns, st_size) —— 用来判「文件变没变」


def load():
    """读 `targets.json`（**带文件指纹的缓存**）。

    ★★ 为什么必须有指纹：原先是「**进程内永久缓存**」⇒ **同一进程里改 `targets.json` 不生效**
      —— 正是本文件最怕的「明明改了却没反应」，且**没有任何东西会报错**。
      ⇒ 每次 `stat`，`(mtime_ns, size)` 变了才重读（`mtime` 秒精度会漏判）。
    ⚠ **保留 dict 身份**（`clear()` + `update()`）；文件缺失 / 读到半截 ⇒ **当场抛**。"""
    global _SIG
    st = os.stat(_P)
    _sig = (st.st_mtime_ns, st.st_size)
    if _sig != _SIG:
        with open(_P, encoding='utf-8') as f:
            _new = json.load(f)
        _CACHE.clear()
        _CACHE.update(_new)
        _SIG = _sig
    return _CACHE


def for_stock(name, scene=None):
    """取某条预设的靶；没有专属靶的预设落回 `_default`（靶只剩 `span`，`scorecard` 读）。"""
    d = load()
    t = dict(d.get('_default') or {})
    t.update(d.get(name) or {})
    t['stock'] = name
    t['own'] = bool(d.get(name))

    t['scene'] = (scene or {}).get('key')
    return t


def scene_engine(scene, stock=None, cfg=None):
    r"""**按场景改【引擎参数】** —— 与靶（`for_stock`）并列，但喂给的地方不同。

    靶只能喂**后期层**、**够不到引擎**（柔光 / 颗粒 / 光晕都在引擎里）；本函数补这个通道。
    结构（与靶的 `_scene` 同一套语义 —— 同一个 `scene.token()`、同一套轴序）：
        `"_scene_engine": {"span=大": {"film_render.halation.halation_strength": [80, 24, 0]}}`
      值是**引擎参数的点路径**；`"<轴>=*"` = 任意值都命中；轴序固定，后者盖前者。

    ★★ **三种写法**：标量 / 列表 = 绝对值 · `{"add": Δ}` = 在**前面所有层算出的值**上加 Δ ·
      `{"mul": k}` = **乘性系数**（乘预设基线；只有传 `stock` 才解析得动）。
    ★ **任何「读图像统计量 → 反馈全局参数」的规则必然振荡**（没有不动点）。"""
    out = {}
    if not scene:
        scene = {}
    d = load()
    ov = d.get('_scene_engine') or {}
    # ★★★★ 三层合并，**越具体越靠后**：① `_scene_engine["*"]` → ② `_stock_engine[预设]`
    #   （按预设基值；**当前空**）→ ③ 按场景轴覆盖。⚠ 顺序不能反（会静默盖掉预设基值）。
    # ---- ★★ `{"by": <程度量>, "delta": Δ}` —— **连续调制**（替代"档位"驱动动作）----
    #   语义 = **在基线之上再加 Δ × 程度量**（0~1）：`{"add": Δ}` 是**阶跃**，判据一改同一组片
    #   就**整档跳**。★ 耐判据换代 · **弃权 = 安全侧**（拿不到 ⇒ 乘 0）。
    def _deg(_name):
        try:
            return float(((scene.get('raw') or {}).get('light') or {}).get(_name) or 0.0)
        except Exception:                                  # noqa: BLE001
            return 0.0

    _tok, _axes, _add, _bykeys = None, None, {}, set()
    try:
        from . import scene as _S
        _tok, _axes = _S.token, _S.AXES
        # ★ 光型（`lighttype`）是**最后、最高**的一层：它不是 `AXES` 的一轴 ⇒ 否则
        #   `"lighttype=逆光"` 这类键**永远查不到**（静默死键）。
        _lighttype_key = True
    except Exception:                                      # noqa: BLE001
        _tok, _axes = (lambda a, v: str(v)), tuple(scene.keys())
        _lighttype_key = False

    def _acc(_d, _v):
        r"""累加 Δ：**标量配标量、列表配列表**（形状不一致 ⇒ **当场抛**）—— 光晕是每通道一个值。"""
        if _v is None:
            return _d                      # 第一次累加（`_d` 的形状就是基准形状）
        if _d is None:
            return _v
        if isinstance(_d, list) or isinstance(_v, list):
            if not (isinstance(_d, list) and isinstance(_v, list) and len(_d) == len(_v)):
                raise TypeError('{"add"/"by"} 的 Δ 与已累加的 Δ 形状不一致：%r vs %r'
                                % (_d, _v))
            return [a + b for a, b in zip(_d, _v)]
        return float(_d) + float(_v)

    def _absorb(_blk):
        """吃掉一层：`{"add":Δ}` / `{"by":deg,"delta":Δ}` ⇒ 累进 `_add`；其余 ⇒ 直接覆盖。"""
        for _kk, _vv in _blk.items():
            if not isinstance(_vv, dict):
                out[_kk] = _vv
            elif len(_vv) == 1 and 'add' in _vv:
                _add[_kk] = _acc(_vv['add'], _add.get(_kk))
            elif len(_vv) == 2 and 'by' in _vv and 'delta' in _vv:
                _dd = _vv['delta']
                if isinstance(_dd, list):
                    _dd = [float(x) * _deg(_vv['by']) for x in _dd]
                else:
                    _dd = float(_dd) * _deg(_vv['by'])
                _add[_kk] = _acc(_dd, _add.get(_kk))
                _bykeys.add(_kk)
            else:
                out[_kk] = _vv

    _base = ov.get('*')
    if isinstance(_base, dict):
        _absorb(_base)

    _st = (d.get('_stock_engine') or {}).get(stock) if stock else None
    if isinstance(_st, dict):
        _absorb(_st)

    #   ★★ **可叠加的 `{"add": Δ}` 约定**：`out.update(blk)` 是**后匹配的轴赢、不是叠加**，而 `back`
    #     会盖掉 `span=平` —— 两个因子**独立且相加**。
    for _ax in _axes:
        _v = scene.get(_ax)
        if _v is None:
            continue
        for _k in ('%s=%s' % (_ax, _tok(_ax, _v)), '%s=*' % _ax):
            _blk = ov.get(_k)
            if isinstance(_blk, dict):
                _absorb(_blk)

    if _lighttype_key:
        _ltv = (scene.get('lighttype') or {}).get('type')
        if _ltv:
            for _k in ('lighttype=%s' % _ltv, 'lighttype=*'):
                _blk = ov.get(_k)
                if isinstance(_blk, dict):
                    _absorb(_blk)
    # ---- 累加：`{"add"}` 的基准必须已在前面的层里；`{"by"}` 可落到**预设基线** ----
    if _add:
        _P = None
        for _kk, _d in _add.items():
            _cur = out.get(_kk)
            if _cur is None and _kk in _bykeys and stock:
                # ★ `{"by": …}` 调的多半是**预设里的那个基线**，它不在 `out` 里 ⇒ 必须能取到；
                #   ⚠ 路径走不通 ⇒ **当场抛**。
                from . import presets as _PR
                if _P is None:
                    if cfg is None:
                        from . import config as cfg
                    _P = _PR._params_for(stock, cfg)
                _cur = getattr(*_PR._walk(_P, _kk))
            if isinstance(_cur, (int, float)) and not isinstance(_cur, bool):
                if not isinstance(_d, (int, float)) or isinstance(_d, bool):
                    raise TypeError('`%s` 的基准是数、Δ 却是 %r（形状要一致）' % (_kk, _d))
                out[_kk] = float(_cur) + float(_d)
            elif isinstance(_cur, (list, tuple)):
                # ★ 光晕这类是**每通道一个值** ⇒ 逐通道加
                if not (isinstance(_d, list) and len(_d) == len(_cur)):
                    raise TypeError('`%s` 的基准长 %d、Δ 却是 %r（要逐通道给）'
                                    % (_kk, len(_cur), _d))
                out[_kk] = [float(x) + float(y) for x, y in zip(_cur, _d)]
            # 基准不是数值/列表 ⇒ 该键此前没人设过（写错了）⇒ **忽略**（老契约）。
    if stock and any(isinstance(v, dict) and 'mul' in v for v in out.values()):
        from . import presets as _PR
        _p = None
        for _k, _v in list(out.items()):
            if not (isinstance(_v, dict) and 'mul' in _v):
                continue
            if _p is None:
                # ★ `_params_for(name, cfg)` **没有默认值** ⇒ `cfg is None` 时直接传会 `TypeError`
                #   （还会被上层 `except` 吞成空覆盖）⇒ 回落模块 config。
                if cfg is None:
                    from . import config as cfg
                _p = _PR._params_for(stock, cfg)
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
# ★★★ **靶的缓存键**（给 pipeline 的渲染缓存用）
# ---------------------------------------------------------------------------
def cache_key(stock, scene=None):
    """把"这条预设 + 这套场景覆盖之后的靶"压成可哈希的键（缓存 key 原来**不含靶** ⇒ 改了
    `targets.json` 仍命中旧缓存 = "拧了没反应"）。"""
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
