# -*- coding: utf-8 -*-
r"""E:\ds\photo\_lib —— 共享工具库（各专题**直接用/扩它**）。

写新脚本前先问：这东西 `_lib\` 里有没有？有就直接用，别另起一个。

```
# —— 核心六件（抽库时的原件）
render.py       渲染编排    分片 / 并行 / 内存闸门 / 自报应有-实得 / 原子写 / ★ cached_render 四层缓存
measure.py      测量管线    900 域 to_lab / 颊掩膜（SV_CHEEK 三档）/ D1 分档 / 中性轴 5 档 / 色带占比 / clip
verdict.py      判据机器    界表查询(并集+本链) / 分辨力 / 中位检验 / bootstrap / 配对统计
bands.py        ★ 靶带真源   `bands.json` 唯一来源：A 组并集 + 三链本链界 + 中性轴五档 + 三位共识
                            （`verdict` 的 `AGENTS_BOUNDS`/`R1_BAND`/`NEUTRAL_SIG` 全由它派生）
fingerprint.py  指纹        参数级全字段 SHA256 + targets 两层 + 引擎解析层读数
sbs.py          并排图      整幅两联 / 脸 1:1 / 颊 1:1(放大) / index.html
jsonsafe.py     JSON 安全改写  探测排版 / 纯文本插入 / json.loads 硬门 / 快照 / 原子写
# —— 10-15 起的工具
selftest.py     自检        用已有数据跑一遍，证明"能用"（零渲染）
codecheck.py    代码卫生    "只动了注释"的 AST+token 双尺子 · 量注释体量 · EOL 归一
docedit.py      注释改写    按限定名/锚点批量改注释（锚唯一起作用）
mkpreset.py     预设生成    从零生成一条预设（值 = 引擎默认）
notes_index.py  `_note_*` 按字段归位（纪律 §二.3 的工具）
preset_diff.py  两份预设逐字段账
preset_notes.py 预设 JSON 逐键中文说明（`_note_params`；mkpreset 自动附）
# —— 10-17/10-18 起的工具
cache.py        四层缓存    L1 解码 / L2 场景 / L3 渲染 / L4 测量（键的算法都在这里）
sharpness.py    锐度尺子    SQF 式归一 + 真 PSD 斜率（空间族，必须全长边）
label.py        中文标签    画中文必须走这里（PIL 默认字体没有汉字）
```

本文件**故意只放常量与引擎路径引导**（不 import numpy / 引擎）——
这样 `import _lib.jsonsafe` 不会有任何重依赖。

## 引擎路径（.pth 坑）
`spektrafilm` 这个包在 site-packages 里有一条 editable 安装的 `.pth`
（`__editable__.spektrafilm-0.3.4.pth`），它指向的是**另一份副本**：
```
E:\WorkBuddy\摄影助手\_tools\spektrafilm\src
```
=> **不显式改 `sys.path` 就会 import 到那份**。
所以：**在 `import spektra` / `import svFilm` 之前**，先调 `bootstrap_engine()`
（它把 vendored 的 `E:\svStudio\svFilm\_tools\spektrafilm\src` 与 `E:\svStudio\svFilm`
插到 `sys.path` 最前）。
"""
from __future__ import annotations

import os
import sys
import warnings

__all__ = ['render', 'measure', 'verdict', 'fingerprint', 'sbs', 'jsonsafe', 'selftest',
           'codecheck', 'docedit', 'mkpreset', 'notes_index', 'preset_diff', 'preset_notes',
           'sharpness', 'label', 'cache', 'bands']

VERSION = '1.0'
# ★ 测量算法版本：`measure.py` 任何改动都要 bump（`gen_bands` 会拒绝混用两代口径）
MEASURE_VERSION = '1.1'

ROOT = os.path.dirname(os.path.abspath(__file__))          # E:\ds\photo\_lib
PHOTO = os.path.dirname(ROOT)                              # E:\ds\photo

# ---- 引擎坐标（AGENTS §一.8 / 项目坐标）
REPO = r'E:\svStudio\svFilm'                               # import svFilm 的根
VENDOR = r'E:\svStudio\svFilm\_tools\spektrafilm\src'      # vendored spektrafilm（0.3.4）
PY = r'C:/Users/psw99/.workbuddy/binaries/python/envs/spektrafilm/Scripts/python.exe'
PYHOME = r'C:/Users/psw99/.workbuddy/binaries/python/versions/3.13.12'

# ---- 常用语料 / 数据包
FEAT = os.path.join(PHOTO, 'datapack_1011', 'feat.json')
M8 = os.path.join(PHOTO, '颜色体检_1013', '_m8_master_dist.json')
YUNET = os.path.join(REPO, 'svFilm', '_models', 'yunet.onnx')
RAF_DIR = r'D:\照片库\效果测试919'
CAM_DIR = r'D:\照片库\2026-09-19_互勉约拍_深圳港湾生活小区\JPG'
STEMS11 = ['DSCF1150', 'DSCF1231', 'DSCF1289', 'DSCF1307', 'DSCF1422', 'DSCF1526',
           'DSCF1608', 'DSCF1627', 'DSCF1665', 'DSCF1726', 'DSCF1772']

# 三条链：字母 / 中文名 / 预设名（全项目通用叫法）
CHAINS = ('kurai', 'masuda', 'hamada')
MNAME = {'kurai': '鹿井', 'masuda': '増田', 'hamada': '滨田'}
# ★★ 10-15：预设只剩**一条**（旧 10 条已删，分支 feature/kashii-create）
#   ⇒ `masuda` / `hamada` 两条链**在生产里已经没有对应预设**（KeyError 是**故意的**：
#     拿旧链名字去渲染只会得到"另一条链"，而脚本不会报错 ⇒ 静默错口径）。
STOCK = {'kurai': '鹿井预设'}

_engine_done = False
_WARNED = set()          # 已打过的 `deprecation()` 名字（每进程每名字一次）


def stock_of(chain):
    r"""链名 -> 生产预设名；**该链在生产里没有预设** ⇒ 抛 `KeyError`（故意的，见下）。

    ★ 为什么要这个函数：`STOCK['masuda']` 这种写法一旦链表变了就是 `KeyError: 'masuda'`
      —— 而它**恰好会在"遍历 CHAINS 拼名字"的循环里炸**（`fingerprint.main` /
      `selftest.check_fingerprint` 原来就各炸过一次）。遍历场景请用 `chain_stocks()`。
    @param chain {str} `kurai` / `masuda` / `hamada`
    @returns {str} 预设名
    """
    return STOCK[chain]


def chain_stocks(chains=CHAINS):
    r"""遍历用：返回 `[(chain, 预设名), …]`，**跳过生产里没有对应预设的链**。

    ★ `masuda` / `hamada` 两条链在生产里已经没有预设（旧 10 条已删），拿旧链名去渲染
      只会得到"另一条链"且不报错 ⇒ 静默错口径。所以这里**主动跳过**，而不是让调用方炸。
    @returns {list[tuple]} [(chain, 预设名), …]
    """
    return [(c, STOCK[c]) for c in chains if c in STOCK]


def deprecation(old, new):
    r"""旧名字转发时打**一次** `DeprecationWarning`（每进程每名字一次）。

    ★ 为什么保留旧名字：`E:\ds\photo` 下有上百个专题脚本按旧签名 import；
      直接删会打断它们。合并的等价性证据在 `_lib\_合并报告.md`。
    @param old {str} 旧名字（带模块前缀）
    @param new {str} 唯一的实现
    """
    if old in _WARNED:
        return
    _WARNED.add(old)
    msg = '%s 已合并 -> 用 %s（旧名字保留为转发；本警告每进程一次）' % (old, new)
    warnings.warn(msg, DeprecationWarning, stacklevel=3)
    print('[DeprecationWarning] ' + msg, file=sys.stderr)


def bootstrap_engine(verbose=False):
    r"""把引擎两处路径插到 `sys.path` **最前**（必须在 `import spektra` / `svFilm` 之前调）。

    顺序（照 `颜色体检_1013\m1_measure.py` 逐字）：
    ```
    先 insert(VENDOR, 0)   再 insert(REPO, 0)
    => sys.path = [REPO, VENDOR, ...]      # REPO 在前
    ```
    ★ 顺手清掉"已经按 `.pth` 装载的**错副本**"（`sys.modules` 里 `spektrafilm*` 的
      `__file__` 不在 VENDOR 下就丢掉）—— 否则先 `import spektrafilm` 再 `bootstrap`
      会得到"路径对、模块错"的静默错误。

    @returns {list} 实际生效的前两项
    """
    global _engine_done
    for p in (VENDOR, REPO):
        while p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)
    vn = os.path.normcase(os.path.abspath(VENDOR))
    dropped = []
    for name, mod in list(sys.modules.items()):
        if name != 'spektrafilm' and not name.startswith('spektrafilm.'):
            continue
        f = getattr(mod, '__file__', None) or ''
        if f and not os.path.normcase(os.path.abspath(f)).startswith(vn):
            dropped.append(name)
            del sys.modules[name]
    _engine_done = True
    if verbose:
        print('[bootstrap] sys.path[0:2] = %r' % (sys.path[:2],))
        if dropped:
            print('[bootstrap] 丢掉 .pth 装载的错副本：%s' % dropped)
    return sys.path[:2]


def env_for_engine(threads=None):
    r"""子进程跑引擎时要的 env（中文输出 + PYTHONHOME + **每路线程数**）。

    ★ 为什么有 `threads`（10-15 修）：引擎的热点全是 `numba.prange`
      （`fast_gaussian_filter` / `fast_interp_lut` / `fast_stats` / `boost_highlights`），
      numba 默认线程 = **核数**（本机 32）⇒ 起 N 路就是 **N x 32 线程抢 32 核**，
      严重超订。引擎文档早就写过「每进程 `OMP_NUM_THREADS=1`，否则线程互抢」。
    ⇒ 调用方（`render.run_shards`）按路数算好每路该给几线程再传进来。
    ⚠ 四个变量都要设：numba 读 `NUMBA_NUM_THREADS`，OpenMP 读 `OMP_NUM_THREADS`，
      MKL/OpenBLAS 各有自己的名字（`colour`/`numpy` 的线性代数会走它们）。

    @param threads {int|None} 每路线程数；None = 不动（用 numba 默认）
    @returns {dict} 复制自 `os.environ` 并叠加上述键
    """
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    env['PYTHONHOME'] = PYHOME
    if threads:
        t = str(int(threads))
        for k in ('OMP_NUM_THREADS', 'NUMBA_NUM_THREADS', 'MKL_NUM_THREADS',
                  'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
            env[k] = t
    return env
