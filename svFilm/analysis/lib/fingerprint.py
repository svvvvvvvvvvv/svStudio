# -*- coding: utf-8 -*-
r"""_lib / fingerprint.py —— 指纹（参数级 SHA256 + 引擎解析层读数）。

来源 = `颜色体检_1013\m9_fingerprint.py` + `B2p重解_1013\b12_fingerprint.py`
       + `系统重标_1013\s0_base.py`（场景/按卷两层）。

## 为什么要指纹（`AGENTS` §一.6）
```
★ **跨进程比像素是坏的** => 证明"预设没被动过"只能用**参数级全字段 SHA256**。
★ `.pth` 坑：site-packages 里有 `__editable__.spektrafilm-0.3.4.pth` 指向**另一份副本**
  （`E:\WorkBuddy\摄影助手\_tools\spektrafilm\src`）。
  => **在 `import spektra` / `import svFilm` 之前**必须先 `bootstrap_engine()`：
     顶部 `sys.path.insert(0, r'E:\svStudio\svFilm\_tools\spektrafilm\src')` 与 `r'E:\svStudio\svFilm'`。
```

## 主函数（签名）
```
bootstrap_engine(verbose=False)          -> list     ★ 先调它，再 import svFilm / spektra
flatten(o, pre='')                       -> dict     ★ 必须吃 dataclass（`_params_for` 返回 dataclass 树）
file_sha256(path)                        -> str      文件级 sha256
param_flat(name, cfg=None)               -> dict      引擎解析后的全字段（叶子路径 -> 值）
param_digest(name, cfg=None)             -> dict      {sha256, sha16, nfields, keys}
all_param_digests(cfg=None)              -> dict      {预设名: digest}（10 条）
scene_stock_layers()                     -> dict      targets.json 的 **两层**：`_scene_engine` / `_stock_engine`
scene_engine_resolved(stock, scene=None) -> dict      引擎实际解析出来的 targets（9 键，零渲染）
engine_readings(name=None, paths=None)   -> dict      解析层读数（chemistry γ / amount / …）
diff(a_flat, b_flat)                     -> list      逐键差 [(k, a, b)]
preset_dir() / targets_path()            -> str
```

## 实测
```
★ 字段数按**当次快照**（`param_digest()` 返回实测 nfields；dev 6cd00c8 + 三份预设 = 255）。
★ **别把某个数写死**：换引擎/换预设都会改字段集。
```
"""
from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import sys

_PHOTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PHOTO not in sys.path:
    sys.path.insert(0, _PHOTO)
import _lib                                                                  # noqa: E402
import _lib.jsonsafe as _J                                                    # noqa: E402

PRESET_DIR = os.path.join(_lib.REPO, 'svFilm', 'data', 'presets')
TARGETS = os.path.join(_lib.REPO, 'svFilm', 'data', 'targets.json')
NAMES10 = ('鹿井预设', '滨田预设', 'xhs预设')   # ★ 历史名（曾是"十条"）；现在是**生产三条**
# ★ 名字沿用只为不破坏调用方；`--all` 用它，默认走 `_lib.chain_stocks()`（= 有链映射的预设）
# 解析层要读的运行时属性路径（`overrides` 用的就是这套路径）
KEY_PATHS = ('film_render.chemistry.gamma_factor',
             'print_render.chemistry.gamma_factor_red',
             'film_render.dir_couplers.amount',
             'film_render.dir_couplers.inhibition_interlayer',
             'film_render.dir_couplers.langmuir_donor_k_rgb',
             'io.output_gamut_compress.lightness_compression',
             'simulation.print_exposure', 'scanner.white_level', 'scanner.black_level',
             'print_render.chemistry.active')


def bootstrap_engine(verbose=False):
    r"""★ 必须在 `import svFilm` / `import spektra` **之前**调（`.pth` 坑）。@returns {list}"""
    return _lib.bootstrap_engine(verbose=verbose)


def preset_dir(cfg=None):
    r"""当前生效的预设目录（可被 `config.PRESET_DIR` 覆盖）。@returns {str}"""
    if cfg is not None:
        return os.path.join(getattr(cfg, 'PRESET_DIR', PRESET_DIR) or PRESET_DIR, '')
    return PRESET_DIR


def targets_path():
    r"""`targets.json` 的路径。@returns {str}"""
    return TARGETS


# ---------------------------------------------------------------- 拍平 / 摘要

def flatten(o, pre=''):
    r"""任意嵌套 -> 叶子字典。

    ★ **必须吃 dataclass**（`presets._params_for` 返回的是 dataclass 树，不是 dict）——
      只写 dict 分支会把整棵树压成 1 个叶子，指纹全假（`b12_fingerprint.py` 的教训）。
    @returns {dict} {'film_render.chemistry.gamma_factor': 1.0, ...}
    """
    out = {}
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        o = dataclasses.asdict(o)
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(flatten(v, '%s.%s' % (pre, k) if pre else str(k)))
    elif isinstance(o, (list, tuple)):
        for i, v in enumerate(o):
            out.update(flatten(v, '%s[%d]' % (pre, i)))
    elif hasattr(o, '__dict__') and not isinstance(o, type):
        for k, v in vars(o).items():
            out.update(flatten(v, '%s.%s' % (pre, k) if pre else str(k)))
    else:
        out[pre] = o
    return out


def digest_of(obj, exclude_private=True):
    r"""任意对象 -> 全字段 SHA256（键按字典序，值用 `repr` 保类型）。

    @returns {dict} {sha256, sha16, nfields, keys}
    """
    fl = flatten(obj)
    keys = sorted(k for k in fl if not (exclude_private and k.startswith('_')))
    blob = json.dumps([(k, repr(fl[k])) for k in keys], ensure_ascii=False).encode('utf-8')
    h = hashlib.sha256(blob).hexdigest()
    return dict(sha256=h, sha16=h[:16], nfields=len(keys), keys=keys)


def file_sha256(path):
    r"""文件级 SHA256（旧名字；**实现已合并到 `jsonsafe.file_sha256`**）。@returns {str}"""
    _lib.deprecation('fingerprint.file_sha256', 'jsonsafe.file_sha256')
    return _J.file_sha256(path)


def _layout_of(path):
    r"""`jsonsafe.probe()` 的**视图**（键集与旧 `layout()` 完全相同）。

    ★ 为什么不直接返 `probe()`：`param_digest()['layout']` 的键集被 `_selftest.txt`
      与专题报告引用过；加键会悄悄改下游。这里只做投影，**不新增字段**。
    """
    p = _J.probe(path)
    s = p['text']
    return dict(bytes=p['bytes'], crlf=p['crlf'],
                lf_only=(not p['crlf']) and ('\n' in s),
                bom=p['bom'], trailing_nl=p['trailing_nl'],
                lines=p['lines'], sha256=p['sha256'])


def layout(path):
    r"""文件排版探测（旧名字；**实现已合并到 `jsonsafe.probe`**）。@returns {dict}"""
    _lib.deprecation('fingerprint.layout', 'jsonsafe.probe')
    return _layout_of(path)


def _engine():
    bootstrap_engine()
    from svFilm import config as C, presets
    return C, presets


# ---------------------------------------------------------------- 预设

def param_flat(name, cfg=None):
    r"""引擎解析后的预设全字段（`presets._params_for`）。@returns {dict}"""
    C, presets = _engine()
    C = cfg or C
    return flatten(presets._params_for(name, C))


def param_digest(name, cfg=None, exclude_private=True):
    r"""★ **参数级全字段 SHA256**（238 / 240 字段，见模块头）。

    @param name {str} 预设名
    @param cfg {module|None} 引擎 config（None = 默认）
    @returns {dict} {name, sha256, sha16, nfields, keys, file_sha256, layout}
    """
    C, presets = _engine()
    C = cfg or C
    d = digest_of(presets._params_for(name, C), exclude_private)
    p = os.path.join(preset_dir(C), '%s.json' % name)
    d['name'] = name
    if os.path.isfile(p):
        d['file_sha256'] = _J.file_sha256(p)
        d['layout'] = _layout_of(p)
    return d


def all_param_digests(names=NAMES10, cfg=None):
    r"""10 条预设一次算完。@returns {dict} {预设名: digest}"""
    return {n: param_digest(n, cfg) for n in names}


def diff(a_flat, b_flat):
    r"""逐键差（只列不同的）。@returns {list} [(key, a, b)]"""
    ks = sorted(set(a_flat) | set(b_flat))
    return [(k, a_flat.get(k), b_flat.get(k)) for k in ks if a_flat.get(k) != b_flat.get(k)]


# ---------------------------------------------------------------- 两层 / 解析层

def scene_stock_layers(path=None):
    r"""`targets.json` 的**两层**（`AGENTS` §一.8 的层序：`_scene_engine["*"]` -> `_stock_engine[预设]`）。

    @returns {dict} {scene_engine: {键: 值}, stock_engine: {预设: {键: 值}},
                     sha16_scene, sha16_stock, n_scene, n_stock}
    """
    p = path or TARGETS
    d = json.load(io.open(p, encoding='utf-8'))
    se = {k: v for k, v in (d.get('_scene_engine') or {}).items() if not k.startswith('_note')}
    st = {k: v for k, v in (d.get('_stock_engine') or {}).items() if not k.startswith('_note')}
    return dict(scene_engine=se, stock_engine=st,
                sha16_scene=digest_of(se)['sha16'], n_scene=len(se),
                sha16_stock=digest_of(st)['sha16'], n_stock=len(st),
                file_sha256=_J.file_sha256(p), layout=_layout_of(p))


def scene_engine_resolved(stock, scene=None, cfg=None):
    r"""引擎**实际解析出来**的 targets（零渲染）。

    `targets.scene_engine(scene, stock=, cfg=)`；`scene={}` 也合法（得到 9 个键）。
    @returns {dict} 属性路径 -> 值
    """
    bootstrap_engine()
    from svFilm import config as C, targets
    C = cfg or C
    return dict(targets.scene_engine(scene or {}, stock=stock, cfg=C))


def _get_path(obj, dotted):
    cur = obj
    for part in dotted.split('.'):
        if cur is None:
            return None
        cur = getattr(cur, part, None) if not isinstance(cur, dict) else cur.get(part)
    return cur


def engine_readings(name=None, paths=KEY_PATHS, cfg=None):
    r"""引擎解析层读数（生产现值）。

    @param name {str|None} 预设名；None = 三条链的预设
    @param paths {tuple} 运行时属性路径（与 `overrides` 同命名）
    @returns {dict} {预设名: {路径: 值}}
    """
    C, presets = _engine()
    C = cfg or C
    names = [name] if isinstance(name, str) else (
        list(name) if name else [s for _c, s in _lib.chain_stocks()])
    # ★★ 这些键有**两层**，必须叠对
    #   · `presets._params_for(name)` 只给【预设文件 + 轴层】（= 轴层兜底）
    #   · 而 10-12 起有 4 个键**下沉到 `targets.json` 的 `_stock_engine[预设名]`**：
    #     `density_curves_morph.gamma_factor` / `gamma_factor_slow` /
    #     `lightness_compression` / `grain.blur`
    #   ⇒ ★ 只读前者会对这 4 个键**报错值**（实测：lc 报 (0.7,1.0,2.2) 兜底，真值 [0.8,1.0,5.0]）
    #   ⇒ 正确做法：**先取生产覆盖 `targets.scene_engine({}, name, cfg)`，缺的键回落预设**
    # ★ 修（10-18）：原来这里写死 `E:\svStudio\svFilm` 且 `except Exception: _T = None`
    #   （导入失败与"键读错"混在一起，`_OV` 那句还是个 `if False` 的死赋值）——
    #   现在只吞 ImportError，并把失败原因打到 stderr。
    try:
        bootstrap_engine()
        from svFilm import targets as _T
    except ImportError as _e:
        _T = None
        import sys as _s
        print('[fingerprint 警告] `svFilm.targets` 导入失败 ⇒ 解析层读数只能读预设/轴层：%s'
              % _e, file=_s.stderr)
    out = {}
    for n in names:
        pr = presets._params_for(n, C)
        layer = {}
        if _T is not None:
            try:
                layer = _T.scene_engine({}, n, C) or {}
            except Exception as _e:                            # noqa: BLE001
                layer = {}
                import sys as _s
                print('[fingerprint 警告] `targets.scene_engine(%r)` 失败 ⇒ 该预设按轴层读：%s'
                      % (n, _e), file=_s.stderr)
        vals = {}
        for p in paths:
            if p in layer:
                vals[p] = layer[p]                         # ★ 生产覆盖优先
            else:
                vals[p] = _get_path(pr, p)
        out[n] = vals
    return out


def main(argv=None):
    r"""命令行自检：打印 10 条预设指纹 + 两层 + 解析层读数。@returns {int}"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true', help='10 条全打（默认只打三条链）')
    a = ap.parse_args(argv)
    names = NAMES10 if a.all else tuple(s for _c, s in _lib.chain_stocks())
    print('【参数级全字段 SHA256】（引擎解析后；`presets._params_for`）')
    for n in names:
        d = param_digest(n)
        print('  %-24s nfields=%-4d sha=%s  文件 %s  %s'
              % (n, d['nfields'], d['sha16'], d['file_sha256'][:16],
                 'CRLF' if d.get('layout', {}).get('crlf') else 'LF'))
    print('')
    print('【targets 两层】')
    L = scene_stock_layers()
    print('  _scene_engine  键 %d  sha=%s' % (L['n_scene'], L['sha16_scene']))
    print('  _stock_engine  预设 %d 个  sha=%s' % (L['n_stock'], L['sha16_stock']))
    print('')
    print('【引擎解析层读数】')
    for n, r in engine_readings().items():
        print('  %s' % n)
        for k, v in r.items():
            print('      %-46s %s' % (k, v))
    return 0


if __name__ == '__main__':
    sys.exit(main())
