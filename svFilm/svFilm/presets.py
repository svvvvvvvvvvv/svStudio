# -*- coding: utf-8 -*-
r"""读 public GUI 导出的预设 JSON，直接跑 spektrafilm 渲染。

## 为什么有这个模块

svFilm 原来只有 5 条「真卷」：卷+纸 = `spektra.STOCK_MAP` 定的，性格 = 几根 `SPEK_*` 常量。
2026-09-23 定案：**删掉那 5 条，改用 public GUI 那 9 条大师预设**。
那 9 条每一条是一份**完整的参数快照**（112 个字段），不是"卷 + 几根旋钮"
⇒ 需要一个能「照读快照」的渲染入口，就是这里。

## 为什么自己写映射、不去 import `spektrafilm_gui.params_mapper`

* 那个包是 **GPLv3**，本仓库是 MIT —— 不想把 GPL 代码搬进本仓库；
* 它的 `persistence` 还连带依赖 `qtpy`（svFilm 的运行环境没有 Qt）。

这里按预设 JSON 的字段名自己写一遍。**字段名是 schema 的事实**，不是谁的创作。

## ★★★ 两条踩过的硬约定

1. **`apply_stocks_specifics` 必须 `False`。**
   True 时 `params_builder._apply_halation_preset` 会按卷的「抗晕层」标签
   （`(use, antihalation)`）把 `halation_strength` 重写成 0.015 ——
   **我们调好的「红光晕 40」会被静默抹掉**（实测：True 时 0.40 → 0.015）。
   这一条对 public GUI 也是坑：它的 flag 是**有状态的**（首帧 True，跑过全分辨率缓存后变 False），
   所以同一张图「预览」和「导出」可能吃到**不同的光晕强度**。

2. **喂场景线性、出显示域** —— 与 `spektra.render()` 的契约完全一致，这样它俩可以互换。
   （`io.output_color_space='sRGB'` + `output_cctf_encoding=True`，与 vendor 的出厂默认一致。）

## 组合方式

`pipeline.py` 的真卷分支现在变成了「预设分支」：
stock 里带 `preset=` 就走这里，否则落回 `spektra.render()`。
L1 影调 + 空间层**继续让位**（预设自带 H&D 曲线、颗粒、柔光、halation、输出锐化）。
"""

from __future__ import annotations

import dataclasses          # ★ 09-29：0.3.4 的 `PrintCurvesMorphParams` 是 frozen ⇒ 要 replace
import json
import os
import sys
import threading

import numpy as np

from . import config as C

# 预设按名字缓存：每条只建一次。
# ⚠⚠ 09-26 纠正一句原来写错的话：这里原来写着「建完不再改 ⇒ 多线程下只读，安全」——
#   **不对**。`render()` 每次都会**临时改写**这个对象上的几个字段（印相曝光 / 测光开关 / 相纸）
#   再还原，所以它是**共享可变的**。同预设并发时必须串行 ⇒ 见下面的 `_LOCKS`。
_PARAMS: dict[str, object] = {}
_LOCK = threading.Lock()
# 渲染用的**按预设名**的一把锁（粒度 = 预设，不同预设仍可并行）
_LOCKS: dict = {}
# ★ 10-10：`_post_scan` 的**诊断变量** —— 色度模糊那一层"没生效"时在这里留证。
#   为什么需要：它是**减弱**型效果，失效时画面不报警、直方图也不报警 ⇒ 只能靠这里。
#   值 = None（正常）｜ 字符串（失败原因）。`selftest` 与事后排查都读它。
_POST_SCAN_ERROR: str | None = None

# ★ 10-11：**白端门控警告的进程内去重表**（见 `_check_white_gate`）。
#   同一预设名**只警告一次** ⇒ "看得见但不刷屏"。只在 `clear_cache()` 里清空。
#
# ⚠⚠⚠ **两个坑都踩过，一起记在这里：**
#   ① **不许用 `_LOCK` 保护它** —— `_params_for()` 是 `with _LOCK:` **持锁**调 `_apply()` 的，
#      而 `_check_white_gate()` 就长在 `_apply()` 里 ⇒ 在那里取 `_LOCK`（**非重入**）
#      = **当场自死锁**。实测症状：进程 CPU 100%、`_params_for` **一行都不返回**，
#      而且它**一直握着 `_LOCK`** ⇒ 同进程后续任何 thread 一起被堵死。
#   ② **不许拿 `set.add()` 的返回值当"占位成功"** —— 我第一版写的是
#      `if _WARNED.add(k) is not SENTINEL: return`，理由是"CPython 里 add 是原子的"。
#      **错了：`set.add()` 永远返回 `None`**（不是 `self`，也不是任何哨兵）
#      ⇒ 那个条件**恒为真** ⇒ 函数在 print 之前就返回。
#   ③ **拿到哨兵之后必须用 `!=` 比，不能用 `is not`** —— 我第二版改成
#      `dict.setdefault(k, SENTINEL) is not SENTINEL`：`setdefault` 对**已有键**
#      返回的是**上次存进去的那个对象**，而键是 `str(name)` **每次新建的字符串**
#      ⇒ 新旧字符串**不是同一个对象** ⇒ `is not` **恒为真** ⇒ 又变回"每次都喊"。
#      ⇒ 结论：**哨兵 + 值比较（`!=`）**才同时满足"可判重"与"可原子占位"。
#   ★ 这两个坑的共同点：**条件恒真 ⇒ 看着在跑、其实静默失效** —— 与本项目
#     "静默失效 6 类"同一族。两次都是靠 `verify_all.py` 的**计数断言**抓出来的
#     （不是靠读代码），这也是"警告必须配一条计数测试"的理由。
_WARN_CLAIM = object()          # 哨兵值（只当"已喊过"的占位用；去重判据是 `in`）
_WHITE_GATE_WARNED: dict = {}   # ★ 是 **dict** 不是 set（理由见上面 ①②③）


# ---------------------------------------------------------------------------
# ★★★ 10-10：**overrides 与"基线层"的合并** —— 一个坑了两个人的静默死键
# ---------------------------------------------------------------------------
# 背景：`print_render.density_curves_morph.active` 的 `True` **只存在于
#   `targets.data()['_scene_engine']['*']`**（基线层），**不在预设 JSON 里**；
#   而引擎 schema 的默认是 `active=False`（`params_schema.py:163-165`）。
#   `pipeline` 走 `targets.scene_engine()` ⇒ 它会**先铺 `"*"` 层**再叠轴覆盖 ⇒ `active` 在。
#   ⚠ 但**直接调** `presets.render(overrides={'…density_curves_morph.gamma_factor': 1.6})`
#     的人（脚本 / 试验）会**丢掉整条基线** ⇒ `active` 缺席 ⇒ **morph 整根死掉**，
#     而且输出与"不带该 override"**在噪声内相同**（实测 max|Δ| 3.22e-2 vs 引擎噪声 3.20e-2）
#     ⇒ 看起来像"这根旋钮没用"，实际是"基线没铺"。
#   ⇒ 这里做两件事：① 提供 `_with_base_overrides()` 供调用方铺基线；
#     ② `render()` 里加一条**不改变行为**的警告，命中这个模式就喊一声。
def _base_overrides(cfg, stock=None):
    """取 `targets._scene_engine` 的**基线层**（`"*"` + 可选 `_stock_engine[stock]`）。

    ★ 为什么需要它：`targets.scene_engine()` 是把 **`"*"` 基线 ＋ 场景轴覆盖** 合并后的结果
      （见 `targets.py:218-221`）。试验脚本若**只**把"要动的那一根"塞进 `overrides`，
      就等于把基线整个丢掉 —— morph 的 `active` 就是这么丢的。
      ⇒ 正确写法：`ov = presets._base_overrides(C); ov.update({一根旋钮: 值})`。
    ⚠ 只读 targets，**不碰任何预设 / 不写文件**。取不到（import 失败等）⇒ 返回 `{}`（不崩）。
    """
    try:
        from . import targets as _T
        _se = (_T.load().get('_scene_engine') or {})
    except Exception:                                      # noqa: BLE001
        return {}
    out = {}
    _b = _se.get('*')
    if isinstance(_b, dict):
        out.update(_b)
    if stock:
        _st = (_se.get('_stock_engine') or {}).get(stock)
        if isinstance(_st, dict):
            out.update(_st)
    return out


def _warn_if_morph_dead(overrides):
    """★ 开发期守卫：`overrides` 出现 `…density_curves_morph.*` 却**没带 `active`** ⇒ 喊一声。

    **只警告、不修改行为**（不自动补 `active` —— 那会悄悄改画面，违反"不许改画面"纪律）。
    为什么值得加：这个模式**已经坑了两个人**，而且失效时**没有任何东西会报错**
    （输出落在引擎自身噪声内，像"这根旋钮没用"）。与本项目 `agx_particle_*`、
    `print_render.density_curve_gamma`、`_DECODE_SIG_KEYS` 是同一类"接线断了没人喊"。
    """
    if not overrides:
        return
    _has_morph = False
    _has_active = False
    for _k in overrides:
        _s = str(_k)
        if 'print_render.density_curves_morph' not in _s:
            continue
        _has_morph = True
        if _s.endswith('.active') or _s == 'print_render.density_curves_morph':
            _has_active = True
    if _has_morph and not _has_active:
        print('[svFilm 警告] overrides 里给了 `print_render.density_curves_morph.*` '
              '却**没有带 `active`** ⇒ **morph 很可能是死的**（引擎默认 active=False，'
              '而 True 只写在 `targets._scene_engine["*"]` 基线层里，直接调 render 不会铺它）。\n'
              '  ⇒ 正确写法：`ov = presets._base_overrides(cfg); ov.update({...})`。', file=sys.stderr)


def _check_white_gate(name, wc, bc, white_level):
    r"""★ 10-11：**白端门控警告** —— 预设把 `scan_white_correction` 关掉时喊一声。

    ## 它警告的是什么（`color_reference.py:139-145`，零渲染语义证明）
    `white_correction` / `black_correction` **不是普通开关，而是两根 level 键的门控**：

        TT  两根都活 ｜ TF  white 活 / black **死** ｜ FT  white **死** / black 活 ｜ FF 两根都 **死**

    `:139-142` 是**直接赋值覆盖**局部变量（`white_level = self._y_white` 等），
    所以被覆盖那一支的 level 键**一个读点都没有**（零渲染扫值已证：扫 0.90→1.30，(m,q) 不动）。
    ⇒ `white_correction=False` 时 `scanner.white_level` 写多少都一样 —— **死键**。

    ## 为什么值得报警（后果是**结构性**的，不是幅度问题）
    · 白锚从「固定常数 `W_s`」退化成「链自身白参考 `y_white`」⇒ `m` 从 1.2688 掉到 1.0017；
    · ★ **「三通道都高」的显示码上界**从 236~237 掉到 **226~227** ⇒ `min(RGB)≥235` **恒为 0**
      （`min235` 0.86 / 6.25 / 15.94 → **0**）—— 这是**值域天花板**，与分辨率无关；
    · 整张暗 ~3 L\*、`L95` −3.5~−4.0、颊 `L*` −2.9~−4.0；
    · ★★ 而 `scanner.white_level` **正是唯一能抬「高光天花板低 6 L\*」的那根键**
      （1.02→1.18 时 L\*60 +4.55，且 1.12 时 clip 仍 0.0000%）—— **它在这一支里是死的**
      ⇒ 于是「调 white_level 没反应」会被误判成「这根键没用」，
        真相是「**这个卷把整段白端校正关了**」。（静默失效第 7 类的实例。）

    ## 纪律
    ★ **只打警告，不碰任何值、不改 `p`、不改任何返回值。** 把这几条预设置 `True`
      属于**改画面**（选项 B）⇒ 必须用户拍板，不在本函数的权限内。
    ★ 同名**只警告一次**（进程内去重）⇒ 不刷屏。
    ★★ **不取任何锁** —— 只用一次 `dict` 查询 + 一次赋值。
       理由：本函数被 `_apply()` 调用，而 `_apply()` 的调用方 `_params_for()` 正**持着 `_LOCK`**；
       `_LOCK` 是**非重入**的 ⇒ 在这里取它 = **自死锁**（且会把整个进程一起堵住）。见上面那段。
    """
    if wc and bc:
        return
    _key = str(name)
    # ★★ 这里我连踩三次，全部记下来 —— **三次都是"条件写反/恒真恒假"**，
    #    而且**光读代码三次都看不出来**，每次都是 `verify_all.py` 的**计数断言**抓出来的。
    #    ① `if _WARNED.add(k) is not SENTINEL: return`
    #       —— **`set.add()` 永远返回 `None`** ⇒ 恒真 ⇒ **一次都没喊**（静默失效）。
    #    ② `if _WARNED.setdefault(k, SENTINEL) != SENTINEL: return`
    #       —— 拿到哨兵 = **本次是新键 = 该继续喊**，我却判成 return ⇒ 语义写反。
    #    ③ `if _WARNED.pop(k, SENTINEL) is not SENTINEL: return`
    #       —— `pop` **会把条目删掉**；删了当然"下次又是新键"⇒ 每次都喊。
    #    ⇒ 最终用**最笨、最不容易写反**的写法：**先看、再写**（`in` + 赋值）。
    #      `in` 与 `[k]=v` 都是**单次原子调用**（GIL 下）⇒ 不需要锁。
    #      多线程下最坏结果是**重复喊一次**（日志重复，可接受），**绝不会漏报**。
    #    ⚠ 我一度还在这里留了一句 `SVFILM_WGATE_DEBUG` 的调试 `pop`，
    #      它自己把去重表吃掉了 ⇒ 调试代码本身成了 bug 来源。**已整体删除。**
    if _key in _WHITE_GATE_WARNED:
        return
    _WHITE_GATE_WARNED[_key] = _WARN_CLAIM
    if not wc and bc:
        _dead, _alive, _how = 'scanner.white_level', 'scanner.black_level', 'FT（只开黑）'
    elif wc and not bc:
        _dead, _alive, _how = 'scanner.black_level', 'scanner.white_level', 'TF（只开白）'
    else:
        _dead, _alive, _how = ('scanner.white_level / black_level', '（无）', 'FF（两根都关）')
    print(
        '[presets] ⚠ 预设 %s 的 scan_white_correction=%s / scan_black_correction=%s ⇒ %s\n'
        '          scanner.white_level / black_level 是死键（白端将消失、高光天花板无法抬升）。\n'
        '          组合 = %s ⇒ ★死键: %s ｜ 还活着的: %s（本预设写的是 scan_white_level=%s）。\n'
        '          为何"结构性"：白锚退化 ⇒ "三通道都高"的显示码上界 237→227 ⇒ min(RGB)>=235 恒为 0，\n'
        '                        整张暗 ~3 L*、L95 −3.5~−4.0；而 white_level 是唯一能抬高光天花板的键。\n'
        '          若这是有意为之请在预设里写明；否则置 True（⚠ 置 True 会改那一条的出图 ⇒ 要用户拍板）。'
        % (_key, wc, bc, _how, _how, _dead, _alive, white_level), file=sys.stderr)


def _lock_of(name):
    with _LOCK:
        lk = _LOCKS.get(name)
        if lk is None:
            # ★ 10-08：换成 **RLock** —— `render_copy()` 需要在持有同一把锁的同时调
            #   `render()`（`render` 内部会再取一次同名锁）；用普通 `Lock` 会**自死锁**。
            lk = _LOCKS[name] = threading.RLock()
    return lk

_SUFFIX = '.json'
# 这几个 NOT 字段名在 JSON 里没有、但 svFilm 要自己定（引擎的加速开关）
_SETTINGS_FROM_CFG = ('use_enlarger_lut', 'use_scanner_lut', 'lut_resolution', 'use_fast_stats')


def _pkg_dir():
    return os.path.dirname(os.path.abspath(__file__))


def preset_dir():
    r"""预设目录（第一个存在的胜出）：

    1. 环境变量 `SVFILM_PRESET_DIR`
    2. `<本包>/data/presets`     ← 随仓库带（正常走这条）
    3. public GUI 当前装的那份（`%LOCALAPPDATA%\napari\napari\presets`）—— 方便"那边改了这边就能试"

    找不到 ⇒ 报清楚该怎么办，别冒一个莫名其妙的 FileNotFoundError。
    """
    home = os.path.expanduser('~')
    cands = [
        os.environ.get('SVFILM_PRESET_DIR'),
        os.path.join(_pkg_dir(), 'data', 'presets'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'napari', 'napari', 'presets'),
        os.path.join(home, 'AppData', 'Local', 'napari', 'napari', 'presets'),
    ]
    for c in cands:
        if c and os.path.isdir(c):
            return c
    raise RuntimeError(
        '找不到预设目录。三种办法任选一个：\n'
        '  ① 设环境变量 SVFILM_PRESET_DIR=<放 .json 的目录>\n'
        '  ② 把预设 json 放到 %s\n'
        '  ③ 装了 public GUI（它会写到 %%LOCALAPPDATA%%\\napari\\napari\\presets）'
        % os.path.join(_pkg_dir(), 'data', 'presets'))


# 一条预设 = 一个「胶片风格」。键 = JSON 文件名。
# ⚠⚠ 名字**只写「胶卷 + 风格」**，不带作者名字（本仓库是公开的）。
#     风格词（薄荷/清风/空气感…）沿用 public 那边的叫法，方便对上。
# ⚠ 没在这里写说明的预设**不让上界面**（见 `names()` 下面的断言）——
#   宁可当场炸，也不要出现「界面上一个没名字的选项」。
_DESC = {
    'GOLD200暖橙': ('GOLD200 · 暖橙', '黄橙侧肤色（颊 h≈55）+ 暖光源夜景；GOLD200 民用负片，链条待她冲扫 lab 证实'),
    'Pro400H马卡龙': ('Pro400H · 马卡龙', '马卡龙色系，柔和小清新'),
    'Portra400淡雅': ('Portra400 · 淡雅', '淡雅、低饱和、通透'),
    'Ultramax400沉褐': ('Ultramax400 · 沉褐', '柯达全能400：深黑、亮部到白、暖褐；鹿井那条靶（唯一一条用全能400的）'),
    'Pro400H清风': ('Pro400H · 清风', '清风感，冷调淡雅'),
    'C200过曝': ('C200 · 过曝', '过曝通透、明快'),
    'Portra400空气感': ('Portra400 · 空气感', '空气感、留白多'),
    'Ektar100浓彩': ('Ektar100 · 浓彩', '浓彩，饱和度最高的一条'),
    'C200青蓝': ('C200 · 青蓝', '青蓝调'),
    'C200透明': ('C200 · 透明', '透明感'),
}


def label_of(name):
    """(中文名, 一句话人话说明)。汇报时用它，别甩英文代号。"""
    return _DESC.get(str(name), (str(name), ''))


def _files():
    d = preset_dir()
    return sorted(f for f in os.listdir(d) if f.endswith(_SUFFIX))


def names():
    """所有可用预设（文件名去掉 .json，保持原样不转小写 —— 里面有中文和间隔号）。"""
    return [f[:-len(_SUFFIX)] for f in _files()]


def has(name):
    return str(name) + _SUFFIX in _files() if name else False


def path_of(name):
    p = os.path.join(preset_dir(), str(name) + _SUFFIX)
    if not os.path.isfile(p):
        raise KeyError('没有这个预设: %s（可选：%s）' % (name, ', '.join(names())))
    return p


def load_raw(name):
    """原样读回预设 JSON（只读用途：给前端看字段、给自检比大小）。"""
    with open(path_of(name), 'r', encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# JSON -> RuntimePhotoParams
# ---------------------------------------------------------------------------

def _profile_swap(profile, order):
    """按 GUI 的 `special.*_channel_swap` 重排通道密度（[0,1,2] = 不动）。"""
    if tuple(order) == (0, 1, 2):
        return profile
    profile.data.channel_density = profile.data.channel_density[:, list(order)]
    return profile


def _diffusion(dst, src, prefix):
    """相机端 / 放像端的柔光块，JSON 里字段名带前缀，字段结构一样。"""
    dst.active = bool(src[prefix + '_active'])
    dst.filter_family = src[prefix + '_family']
    dst.strength = float(src[prefix + '_strength'])
    dst.spatial_scale = float(src[prefix + '_spatial_scale'])
    dst.halo_warmth = float(src[prefix + '_halo_warmth'])
    dst.core_intensity = float(src[prefix + '_core_intensity'])
    dst.core_size = float(src[prefix + '_core_size'])
    dst.halo_intensity = float(src[prefix + '_halo_intensity'])
    dst.halo_size = float(src[prefix + '_halo_size'])
    dst.bloom_intensity = float(src[prefix + '_bloom_intensity'])
    dst.bloom_size = float(src[prefix + '_bloom_size'])


def _apply(p, d, cfg, name=None):
    """把预设 JSON 的字段逐条灌进 params。字段名与 `params_schema` 的对应关系写死在下面。

    `name` = 预设名（**只**用于诊断信息，见 `_check_white_gate`）。
    ⚠ 它**不参与任何赋值**，缺省 `None` ⇒ 老调用点行为完全不变。
    """
    ii = d['input_image']
    gr = d['grain']
    pf = d['preflashing']
    ha = d['halation']
    co = d['couplers']
    gl = d['glare']
    sp = d['special']
    si = d['simulation']

    # ---- 卷 profile 的通道重排（GUI 的高级开关，绝大多数预设是 [0,1,2]）----
    p.film = _profile_swap(p.film, sp['film_channel_swap'])
    p.print = _profile_swap(p.print, sp['print_channel_swap'])

    # ---- special：**胶片端**密度曲线 gamma（下限 0.01，与 GUI 一致）----
    p.film_render.density_curve_gamma = max(float(sp['film_gamma_factor']), 0.01)
    # ★★★ 10-10 代码清理：**删掉 `p.print_render.density_curve_gamma = ...`**。
    #   它**没有任何消费者**：0.3.4 的 `PrintRenderingParams` 只声明了两个字段
    #   （`glare` / `density_curves_morph`，见 `params_schema.py:160-165`）——
    #   `density_curve_gamma` **根本不在里面**（存在的是 `FilmRenderingParams.density_curve_gamma`，
    #   那是另一回事、是活的，就是上面这一行）。
    #   `RuntimePhotoParams` 是普通 dataclass（无 `__slots__`）⇒ 这一行**不报错**，
    #   只会给 `print_render` 挂一个**没人读**的新属性 ⇒ 预设里的 `print_gamma_factor`
    #   **从未生效过**（10 条预设的值 0.93~1.07 全部落空）—— 与 `agx_particle_*` 同一个坑。
    #   ⚠ 这不是"删掉一个功能"，而是**删掉一个假的接线**：画面逐位不变（已实测）。
    #   ⚠ 若将来要真的让 `print_gamma_factor` 生效，得先确认 0.3.4 里**印相端密度曲线 gamma
    #     该走哪个字段**（当前 schema 里没有任何入口）—— 别把这一行加回来。
    #   ★ 判据依据：`config.py` 的死键清单（`print_render.density_curve_gamma` 那一行）。

    # ---- camera ----
    p.camera.lens_blur_um = float(si['camera_lens_blur_um'])
    p.camera.exposure_compensation_ev = float(si['exposure_compensation_ev'])
    p.camera.auto_exposure = bool(si['auto_exposure'])
    p.camera.auto_exposure_method = si['auto_exposure_method']
    p.camera.film_format_mm = float(si['film_format_mm'])
    p.camera.filter_uv = tuple(ii['filter_uv'])
    p.camera.filter_ir = tuple(ii['filter_ir'])
    _diffusion(p.camera.diffusion_filter, si, 'camera_diffusion_filter')

    # ---- io ----
    p.io.upscale_factor = float(ii['upscale_factor'])
    p.io.crop = bool(ii['crop'])
    p.io.crop_center = tuple(ii['crop_center'])
    p.io.crop_size = tuple(ii['crop_size'])
    p.io.input_color_space = ii['input_color_space']
    p.io.input_cctf_decoding = bool(ii['apply_cctf_decoding'])
    p.io.output_color_space = si['output_color_space']
    p.io.output_cctf_encoding = True
    p.io.scan_film = bool(si['scan_film'])

    # ---- enlarger ----
    p.enlarger.illuminant = si['print_illuminant']
    p.enlarger.print_exposure = float(si['print_exposure'])
    p.enlarger.print_exposure_compensation = bool(si['print_exposure_compensation'])
    p.enlarger.y_filter_shift = float(si['print_y_filter_shift'])
    # ★ 10-08：**统一加一点品红**（底座补偿）。为什么不去动 `neutral`、为什么是 +8、
    #   以及它**对预闪不生效**这件事，全部写在 `config.PRESET_FILTER_M_TRIM` 上面那段。
    #   ⚠ 它是**全局**的（10 条预设都吃），而每条预设自己的 `shift` 身份仍然保留。
    p.enlarger.m_filter_shift = float(si['print_m_filter_shift']) + float(
        getattr(cfg, 'PRESET_FILTER_M_TRIM', 0.0))
    p.enlarger.preflash_exposure = float(pf['exposure'])
    p.enlarger.preflash_y_filter_shift = float(pf['y_filter_shift'])
    p.enlarger.preflash_m_filter_shift = float(pf['m_filter_shift'])
    _diffusion(p.enlarger.diffusion_filter, si, 'diffusion_filter')

    # ---- scanner ----
    p.scanner.lens_blur = float(si['scan_lens_blur'])
    _wc = bool(si['scan_white_correction'])
    _bc = bool(si['scan_black_correction'])
    # ★ 10-11：**在解析之前**先看一眼门控。它是**只写 stderr** 的纯观察者
    #   （不读也不写 `p`、不参与任何返回路径）⇒ 对渲染逐字段无影响（已用参数级
    #   全字段 SHA256 指纹对全 10 条预设实测：改前 == 改后）。详见 `_check_white_gate`。
    #   ⚠ 放在 `p.scanner.* =` 赋值之前，是为了让"警告"与"解析结果"绑定在同一处、
    #     不会出现"赋值改了但警告没跟上"的漂移。
    _check_white_gate(name, _wc, _bc, si['scan_white_level'])
    p.scanner.white_correction = _wc
    p.scanner.black_correction = _bc
    p.scanner.unsharp_mask = tuple(si['scan_unsharp_mask'])
    black = float(si['scan_black_level'])
    white = float(si['scan_white_level'])
    if white <= black:
        # color_reference.py 里 m = .../(white-black) ⇒ 相等会除以零
        white = black + 0.01
    p.scanner.black_level = black
    p.scanner.white_level = white

    # ---- halation（⚠ 这两个字段 GUI 里是 ×100 的，这里要除回去）----
    h = p.film_render.halation
    h.active = bool(ha['active'])
    h.scatter_amount = float(ha['scatter_amount'])
    h.scatter_spatial_scale = float(ha['scatter_spatial_scale'])
    h.halation_amount = float(ha['halation_amount'])
    h.halation_spatial_scale = float(ha['halation_spatial_scale'])
    h.boost_ev = float(ha['boost_ev'])
    h.protect_ev = float(ha['protect_ev'])
    h.boost_range = float(ha['boost_range'])
    h.scatter_core_um = tuple(ha['scatter_core_um'])
    h.scatter_tail_um = tuple(ha['scatter_tail_um'])
    h.scatter_tail_weight = tuple(float(v) / 100.0 for v in ha['scatter_tail_weight'])
    h.halation_strength = tuple(float(v) / 100.0 for v in ha['halation_strength'])
    h.halation_first_sigma_um = tuple(ha['halation_first_sigma_um'])
    h.halation_n_bounces = int(ha['halation_n_bounces'])
    h.halation_bounce_decay = float(ha['halation_bounce_decay'])
    h.halation_renormalize = bool(ha['halation_renormalize'])

    # ---- grain ----
    # ⚠⚠⚠ 09-30 修一个**静默了整轮**的 bug：这几个字段名**必须跟 vendor 版本对得上**。
    #    **0.3.2 = `agx_particle_*`；0.3.4 去掉了 `agx_` 前缀 ⇒ 一律写 `particle_*`。**
    #    `GrainParams` 是普通 dataclass、**没有 `__slots__`** ⇒ 名字写错**不报错**，
    #    只会静默多出几个**没人读**的属性 ⇒ **颗粒参数一点没生效、画面照旧**。
    #    ★ 我们就是这样：`particle_area_um2` 我们写 2.5，vendor `grain.py` 实读的却是
    #      schema 默认 **0.2** ⇒ 整轮"调颗粒到靶"全是空的（实测铁证见 `selftest.t_presets`）。
    #    ⚠ 换 vendor 版本时**这里必须跟着换**，否则颗粒静默失效。
    #    `selftest.t_presets` 现在**验 vendor 真正读的那个名字**，并断言带 `agx_` 的死属性
    #      **不该存在**（防回退）。
    g = p.film_render.grain
    if float(gr['particle_area_um2']) <= 0:
        # 粒子面积为 0 物理上无意义（grain.py 里会除以零）⇒ 等同关掉
        g.active = False
    else:
        g.active = bool(gr['active'])
        g.sublayers_active = bool(gr['sublayers_active'])
        g.particle_area_um2 = float(gr['particle_area_um2'])
        g.particle_scale = tuple(gr['particle_scale'])
        g.particle_scale_layers = tuple(gr['particle_scale_layers'])
        g.density_min = tuple(gr['density_min'])
        g.uniformity = tuple(gr['uniformity'])
        g.blur = float(gr['blur'])
        g.blur_dye_clouds_um = float(gr['blur_dye_clouds_um'])
        g.micro_structure = tuple(gr['micro_structure'])

    # ---- couplers ----
    c = p.film_render.dir_couplers
    c.active = bool(co['active'])
    c.amount = float(co['amount'])
    c.inhibition_samelayer = float(co['inhibition_samelayer'])
    c.inhibition_interlayer = float(co['inhibition_interlayer'])
    c.gamma_samelayer_rgb = tuple(co['gamma_samelayer_rgb'])
    c.gamma_interlayer_r_to_gb = tuple(co['gamma_interlayer_r_to_gb'])
    c.gamma_interlayer_g_to_rb = tuple(co['gamma_interlayer_g_to_rb'])
    c.gamma_interlayer_b_to_rg = tuple(co['gamma_interlayer_b_to_rg'])
    c.diffusion_size_um = float(co['diffusion_size_um'])

    # ---- glare（JSON 里是印相端那一份）----
    p.print_render.glare.active = bool(gl['active'])
    p.print_render.glare.percent = float(gl['percent'])
    p.print_render.glare.roughness = float(gl['roughness'])
    p.print_render.glare.blur = float(gl['blur'])

    # ---- settings：预设 JSON 里**没有**这几项（是引擎的加速开关）⇒ 由 svFilm 的 config 定 ----
    p.settings.rgb_to_raw_method = ii['spectral_upsampling_method']
    p.settings.apply_hanatos2025_adaptation_window = bool(ii['apply_hanatos2025_adaptation_window'])
    p.settings.apply_hanatos2025_adaptation_surface = bool(ii['apply_hanatos2025_adaptation_surface'])
    p.settings.spectral_gaussian_blur = float(ii['spectral_gaussian_blur'])
    p.settings.preview_max_size = int(d['display']['preview_max_size'])
    p.settings.preview_mode = False        # 要的是成片，不是预览
    # ⚠ 配平基准必须钉死：两版 vendor 的「中性滤片数据库」查出来的不是同一对数
    #   （读 DB 的顺序是 `c_filter, m_filter, y_filter` ⇒ **别按 y/m 猜**：
    #    0.3.2 ⇒ m 50.713 / y 51.423（本预设在这版标定）；0.3.4 ⇒ m 48.154 / y 50.735；
    #    schema 默认 ⇒ y 55 / m 65）。
    #   预设是在 0.3.2 上标定的 ⇒ 关掉 DB、填 0.3.2 的实测值，做到版本无关。
    if bool(getattr(cfg, 'PRESET_NEUTRAL_FROM_DB', False)):
        p.settings.neutral_print_filters_from_database = True
    else:
        p.settings.neutral_print_filters_from_database = False
        p.enlarger.y_filter_neutral = float(getattr(cfg, 'PRESET_NEUTRAL_Y', 51.423))
        p.enlarger.m_filter_neutral = float(getattr(cfg, 'PRESET_NEUTRAL_M', 50.713))
    p.settings.use_enlarger_lut = bool(getattr(cfg, 'SPEK_USE_LUT', True))
    p.settings.use_scanner_lut = bool(getattr(cfg, 'SPEK_USE_LUT', True))
    p.settings.use_fast_stats = bool(getattr(cfg, 'SPEK_FAST_STATS', False))
    p.settings.lut_resolution = 17

    # ★★ 10-08：**显式钉死"印相中灰配平"** —— 它是整张落点的命门。
    #   两键都为 True（默认）时，印相曝光按 18.4% 中灰归一化，`print_exposure` 才是有效的
    #   落点旋钮；同时 `camera.exposure_compensation_ev` 的**净亮度效果被它抵销**
    #   （`filming.py:129` 把它喂进中灰参考、`printing.py:112` 返回 `factor_midgray_comp`）。
    #   原来这里**一个字都没写**，全靠 vendor 的 schema 默认值 ⇒ 换版本若默认翻转，
    #   整张亮度会**静默位移一档**，而报告里看不出原因。⇒ 显式钉。
    p.enlarger.print_exposure_compensation = True
    p.enlarger.normalize_print_exposure = True

    p.debug.lut_mode = False               # lut_mode 会把空间效果全关掉 —— 那是给烘 LUT 用的
    p.debug.deactivate_spatial_effects = False
    p.debug.deactivate_stochastic_effects = False


_PARAMS_SIG = [None]          # 上次建缓存用的 config 指纹（变了就整体作废）
_PARAMS_FSIG = {}             # 预设名 -> 该预设文件的指纹（mtime_ns, size）


def _file_sig(name):
    r"""★ 10-09：**预设文件本身的指纹**（`os.stat` 的 mtime_ns + size）。

    为什么必须有：老版本的缓存键是 `(预设名, config 指纹)`—— **不含预设文件内容**。
    后果（10-09 实测咬到）：**在一次会话里改了预设 JSON，程序察觉不到**，用的还是旧参数，
    必须重启进程才生效 ⇒ 又一次"改了没反应"，而且它**会静默污染所有测量**
    （我按相纸 A 量了一套数、又按相纸 B 量，两次结果一模一样，就是这个原因）。
    """
    try:
        st = os.stat(path_of(name))
        return (st.st_mtime_ns, st.st_size)
    except Exception:                                  # noqa: BLE001
        return None


def _params_for(name, cfg):
    r"""按 **(预设名, config 指纹)** 缓存。

    ★★ 10-08 修两个洞：
      ① 老版本键里**只有 `name`** ⇒ 第一次调用者的 config 会被**永久污染**：
         `_apply()` 里 `PRESET_NEUTRAL_FROM_DB` / `PRESET_FILTER_M_TRIM` / `SPEK_*` 都是
         cfg 决定的，换个 cfg 再跑（A/B 试验、或将来前端按请求切）**静默无效** ——
         而且 `targets.scene_engine` 会用默认 cfg 来建同一批对象，污染源不止一处。
      ② `clear_cache()` 原本**全包零调用点**（docstring 让调，没人调）⇒ 常驻服务里改这些
         开关**必须重启引擎**。现在指纹一变**自动作废**；`clear_cache()` 保留作手动口子。

    ⚠ `_apply()` 之后对象仍会被 `_render_locked` **临时改写**（见 `_LOCKS` 那段注释），
      所以"建完不再改 ⇒ 多线程只读"这句只对 `_apply` 之后、`render` 之外成立。
    """
    _sig = None
    if hasattr(cfg, 'key_signature'):
        try:
            _sig = cfg.key_signature(cfg)
        except Exception:                              # noqa: BLE001
            _sig = None
    with _LOCK:
        if _PARAMS_SIG[0] != _sig:
            _PARAMS.clear()                            # config 变了 ⇒ 老参数对象全部作废
            _PARAMS_FSIG.clear()
            _PARAMS_SIG[0] = _sig
        # ★ 10-09：**预设文件变了也要作废**（见 `_file_sig` 的注释）
        _fs = _file_sig(name)
        if _PARAMS_FSIG.get(name) != _fs:
            _PARAMS.pop(name, None)
            _PARAMS_FSIG[name] = _fs
        p = _PARAMS.get(name)
        if p is not None:
            return p
        d = load_raw(name)
        spektra = __import__(__name__.rsplit('.', 1)[0] + '.spektra', fromlist=['x'])
        init_params, _simulate = spektra._sf()
        p = init_params(film_profile=d['simulation']['film_stock'],
                        print_profile=d['simulation']['print_paper'])
        _apply(p, d, cfg, name)
        _PARAMS[name] = p
        return p


def clear_cache():
    """手动作废参数缓存。现在**通常不需要**（`_params_for` 会按 config 指纹自动作废），
    留作显式口子：改了引擎级开关、又想在同进程里立刻看到效果时调它。

    ★ 10-11：**它故意不去动 `_WHITE_GATE_WARNED`。**
      去重表按设计是**进程级**的（任务书原话「同一预设名只警告一次（进程内去重）」）
      ⇒ 常驻服务里反复切卷 / 反复作废缓存，那条警告**也只该出现一次**。
      我第一版让它跟着清空 ⇒ 实测"清缓存后再连调 3 次 = 又喊 3 次"，
      **在常驻服务里就是刷屏**。⇒ 去重表**只在进程重启时归零**。
      （开发期想再看到它 ⇒ 重启进程；或临时 `presets._WHITE_GATE_WARNED.clear()`。）
    """
    with _LOCK:
        _PARAMS.clear()
        _PARAMS_FSIG.clear()
        _PARAMS_SIG[0] = None


def digested(name, cfg=C, apply_specifics=None):
    r"""按预设建 params **并消化**，返回真正进管线的那一份。

    给自检用。为什么不能拿 `_params_for()` 的结果去验：那是**消化之前**的，
    有些字段（配平基准、密度曲线、以及 `apply_stocks_specifics=True` 时的
    `halation_strength`）是**在 digest 里**被改的 ⇒ 验错对象就会"假绿"。
    `apply_specifics=None` ⇒ 取 `config.PRESET_APPLY_STOCK_SPECIFICS`。
    """
    p = _params_for(name, cfg)        # 先调它：让 spektra._sf() 把 spektrafilm 加载好（有来源守卫）
    from spektrafilm.runtime.params_builder import digest_params
    if apply_specifics is None:
        apply_specifics = bool(getattr(cfg, 'PRESET_APPLY_STOCK_SPECIFICS', False))
    return digest_params(p, apply_stocks_specifics=bool(apply_specifics))


# ---------------------------------------------------------------------------
# 渲染入口：与 spektra.render() 同契约
# ---------------------------------------------------------------------------

def paper_of(name):
    """这一条预设用的相纸（= 卷表里那一份；前端"相纸"下拉默认它）。"""
    return load_raw(name)['simulation']['print_paper']


def film_of(name):
    return load_raw(name)['simulation']['film_stock']


def pe_of(name):
    """预设自己的印相曝光（落点）。"""
    return float(load_raw(name)['simulation']['print_exposure'])


# ===========================================================================
# ★★★ 10-11：**以人为本的测光入口**（`subject`）
# ---------------------------------------------------------------------------
# 【为什么有它】用户 10-11 要求"找理论支撑"，三条合起来指向同一条路：
#   1. **Zone System 的分工**：曝光定暗部、显影/印相定高光 —— 两个独立决策，不许一个旋钮修两端。
#   2. **项目已验证的反振荡原理**（AGENTS 第 12 条）：唯一不振荡的自适应是 spektrafilm 的
#      `auto print exposure` —— **补偿式、可逆、不改目标**。
#   3. **★ 第一原理**：**任何"读图像统计量 → 反馈到全局参数"的规则必然振荡**（没有不动点）。
#      引擎的锚是**写死的 `0.184`**（`filming.py:126`）⇒ 它不读图 ⇒ 它不振荡。
#      ⇒ **要改"人好不好看"，只能改「看哪里」（测光方式，输入侧），不能改「看多少」（曝光量，输出侧）。**
#   实测佐证：`subject` 测光 37 张复验 ⇒ 逆光组脸 **+17.6**、**变差 0 张**；
#     而"全局提亮"那条 52 张实测 ⇒ 中位没变、>75 的从 2 张炸到 14 张。
#
# ⚠ **弃权是安全侧**：mediapipe 拿不到 / 没检出人 ⇒ **自动退回普通 `render()`**，不硬判。
# ⚠ 掩膜口径：`person.py` 的 `1 − 背景类`（粗掩膜，**只服务测光，不做人脸识别**）。
# ===========================================================================
def render_metered(lin, name, cfg=C, print_exposure=None, print_profile=None,
                   overrides=None, person_mask=None):
    r"""与 `render()` 同签名，但**测光看人**（`camera.auto_exposure_method="subject"`）。

    `lin` —— 场景线性（与 `render()` 一样）。
    `overrides` —— 若**没有**显式给 `camera.auto_exposure_method`，本函数会把它设成 `"subject"`。
    `person_mask` —— 可选，外部已算好的掩膜（省一次前向）。
    **拿不到人 / 装不上 shim ⇒ 自动退回普通 `render()`**（弃权）。
    """
    from . import exposure as _EX
    ov = dict(overrides or {})
    if not _EX.install():
        return render(lin, name, cfg, print_exposure, print_profile, overrides)
    m = person_mask
    if m is None:
        try:
            from . import color as _col, person as _P
            disp = np.clip(_col.l2s(np.clip(np.asarray(lin, np.float64), 0.0, None)), 0.0, 1.0)
            m = _P.person(disp)
        except Exception:                                                     # noqa: BLE001
            m = None
    if m is None:
        return render(lin, name, cfg, print_exposure, print_profile, overrides)
    # ★ 10-11 修：**必须直接赋值，不能 setdefault** ——
    #   `overrides` 惯例是「整份 base + 叠加」，而 `_scene_engine["*"]` 里
    #   **本来就带着 `camera.auto_exposure_method = 'matrix'`** ⇒ `setdefault` 静默无效
    #   （实测：subject 与 matrix 输出几乎逐位相同，脸 L* 差 +0.0）。
    ov['camera.auto_exposure_method'] = 'subject'
    _EX.set_mask(m)
    try:
        return render(lin, name, cfg, print_exposure, print_profile, ov)
    finally:
        _EX.clear()


def render(lin, name, cfg=C, print_exposure=None, print_profile=None, overrides=None):
    r"""喂**场景线性**，出**显示域**（与 `spektra.render()` 同契约，可互换）。

    `print_exposure`：不传 = 用「预设自带的 pe × `SPEK_PE_SHIFT`」（与真卷那条路的结构一致）；
      显式传 = 直接覆盖（给二分找落点用）。
    `print_profile`：不传 = 用预设配套的那张纸；传了 = 覆盖（相纸下拉）。
    `overrides`：{「字段路径」: 值}，例 `{'enlarger.print_y_filter_shift': 13.0,
      'film_render.grain.blur': 0.5}`。跑完**一定还原**（取值不对当场报错，不静默）。
      ★★★ 09-26 加这个参数 = **「按场景分参数」的地基**：
      同一个预设名在不同场景要用不同数值时，不能去改 `_PARAMS` 里那份共享对象
      （两个场景并发就互相踩），而是**每张图临时覆盖一次字段、跑完还原**。
      ⚠ 现在还没有"场景判据"去产出这份 dict —— 那是下一步；这里先把**通道**打通、并用自检钉住。

    ⚠ 这里**不做** `digest_params(apply_stocks_specifics=True)` —— 那会把预设里的
      `halation_strength` 冲回卷的出厂值（我们调的「光晕 40」就这么没的）。
      `simulate()` 内部还会再 digest 一次，这一次用的是它自己的默认值；
      所以下面先把 `apply_stocks_specifics` 想关掉的效果**写死在 params 上**，
      并在 `selftest.t_presets` 里钉着不放。

    ★★★ 10-10：**`overrides` 是"整份替换"，不是"叠加"** ⇒ 别只传一根！
      它是 `targets.scene_engine()` 的产出，而那份产出 = **`"*"` 基线层 ＋ 场景轴覆盖** 合并后的
      结果。只筛出"我要动的那一根"再传进来 = **把基线整个丢掉**。
      实例（已实测、坑过两个人）：`print_render.density_curves_morph.active` 的 `True`
      **只在 `targets._scene_engine["*"]` 里**（引擎 schema 默认 `False`，预设 JSON 里也没有）
      ⇒ 只传 `...morph.gamma_factor` 会让 **morph 整根死掉**，输出与"不带 override"在噪声内相同
      （max|Δ| 3.22e-2 vs 引擎噪声 3.20e-2），看着像"这根旋钮没用"。
      ✅ 正确写法：`ov = presets._base_overrides(cfg); ov.update({一根旋钮: 值})`。
      ⚠ 命中这个模式时下面只**警告**，**不自动补 `active`**（自动补 = 悄悄改画面）。
    """
    p = _params_for(name, cfg)
    _warn_if_morph_dead(overrides)

    # ★★★ 09-26：**按预设名加锁**。下面这一整段是「临时改写共享的 `p` → 跑 → 还原」，
    #   而常驻服务是多线程的（`ThreadingHTTPServer`）⇒ 同一预设的两个请求并发时，
    #   一个的改写会被另一个的还原抹掉（现在写的值恰好相同所以侥幸没事，
    #   但只要 `overrides` 一上就立刻变成真竞态）。
    #   ★ 粒度为**每个预设一把锁**：不同预设仍可并行，只有同名预设串行（本来就该串行，它们共用一个对象）。
    # ★★★ 10-11：**测光看人**（`subject`）—— 用户 10-11「以人为本的曝光」的落地。
    #   为什么在这里、为什么是这个形式（见 `config.PERSON_METERING` 的长注释）：
    #     · 要改"人好不好看"，**只能改「看哪里」（测光方式，输入侧）**，
    #       不能改「看多少」（曝光量，输出侧）—— 后者是"读统计量→改全局参数"的闭环，**必然振荡**。
    #     · **注入用副本**（`dict(overrides)`），**不改调用方的字典**。
    #     · **弃权是安全侧**：装不上 shim / 没检出人 ⇒ 走原路径，一个字不变。
    _ov, _pm = _person_metering(lin, overrides, cfg)
    try:
        with _lock_of(name):
            return _post_scan(
            _render_locked(p, name, lin, cfg, print_exposure, print_profile, _ov), cfg)
    finally:
        if _pm is not None:
            try:
                from . import exposure as _EX
                _EX.clear()
            except Exception:                                                # noqa: BLE001
                pass


def _person_metering(lin, overrides, cfg):
    r"""算 person 掩膜并把测光方式改成 `subject`；**任何一步不成立 ⇒ 原样返回**（弃权）。

    @returns `(overrides_to_use, mask_or_None)`。
    `mask is None` ⇒ 调用方**必须**清理（本函数的契约是"返回了掩膜就要 clear"）。
    """
    # ★★★ 10-11：**中灰锚必须无条件装上**（它与"测光看人"是两件独立的事）。
    #   锚落在 `exposure.install()` 的 wrapper 里 ⇒ 若把 `install()` 留在下面
    #   `PERSON_METERING` 那个 if **之后**，**关掉"测光看人"会让锚静默消失**
    #   （而锚现在是整条链的曝光主人）⇒ 提到最前面，无条件装。
    #   ⚠ 失败**不许静默**：装不上就退化成"锚不生效"（= 回到修复前），必须留痕迹。
    _EX = None
    try:
        from . import exposure as _EX
        if not _EX.install():
            import sys as _sys
            print('[svFilm 警告] presets._person_metering: `exposure.install()` 失败 ⇒ '
                  '**中灰锚未生效**（本张走的是"硬锚 0.184"的旧行为）', file=_sys.stderr)
            _EX = None
    except Exception:                                                    # noqa: BLE001
        _EX = None
    if not bool(getattr(cfg, 'PERSON_METERING', False)):
        return overrides, None
    if not isinstance(overrides, dict):
        return overrides, None                       # 不碰 None/别的东西
    try:
        if _EX is None:
            return overrides, None
        import numpy as _np
        from . import color as _col, person as _P
        _lin = _np.clip(_np.asarray(lin, _np.float64), 0.0, None)
        _disp = _np.clip(_col.l2s(_lin), 0.0, 1.0)
        _m = _P.person(_disp)
        if _m is None:
            return overrides, None
        # ★★★ 10-11 修：**掩膜占比守卫**（物理依据）。
        #   实测（合成图纯白/灰阶）`person.person()` 会输出**占比 1.000 的整幅掩膜**
        #   ⇒ 那会让"测光看人"退化成"按整幅测光"，而**结果看起来还挺合理** ⇒ 静默出错。
        #   （症状：自检 ② 暗部过冲 / ④ 中调抬升 由 3 红变 5 红。）
        #   守卫区间 **[0.005, 0.70]**（按 m>0.5 算）：人不可能占满整幅（那是分割失败）、
        #   也不可能一个都没有（空掩膜）。**超出即弃权**，并留 stderr 痕迹（不静默）。
        # ⚠ 口径：person.person() 返回的是 **0~1 的浮点置信度**，不是布尔掩膜 ⇒
        #   必须用**阈值 0.5** 算占比（mean(m>0) 在浮点掩膜上恒等于 1.000 —— 我踩过）。
        #   实测分离度：合成图（没人）**0.000** ｜ 真实照片 **0.241~0.485**。
        _cov = float(_np.mean(_np.asarray(_m) > 0.5))
        if not (0.005 <= _cov <= 0.70):
            import sys as _sys
            print('[svFilm 警告] presets._person_metering: 人掩膜占比 %.3f 不合理 '
                  '⇒ **弃权，改走原测光路径**（本次不做"测光看人"）' % _cov, file=_sys.stderr)
            return overrides, None
        _ov = dict(overrides)
        _ov['camera.auto_exposure_method'] = 'subject'
        _EX.set_mask(_m)
        return _ov, _m
    except Exception:                                                    # noqa: BLE001
        return overrides, None


def _post_scan(out, cfg):
    r"""★ 10-09：**扫描段后处理**（在颜色层之前、引擎之后）。

    目前只有一件：`scanfx.chroma_blur` —— **扫描色度模糊**。
    为什么放在这里（而不是让调用方自己做）：**保证所有调用方一致** ——
    尺子库实测（747 张大师，统一口径）「色度/亮度锐度比」：大师 **0.60**、我们 **0.99**
    ⇒ 真实扫描链的色度分辨率天然低于亮度，**这一层属于"扫描"，不属于调用方**。
    默认关（`CHROMA_BLUR_ENABLE=False`）⇒ 对既有行为**逐位无影响**。
    ★ 10-10 清理：原来这里是**裸 `except Exception: return out`** ⇒ 任何异常（缺 scipy、
      配置写错、内存不足）都会让这一层**静默不生效**，而"没有色度模糊"在画面上**看不出来**
      （它是个**减弱**型效果，缩略图/直方图都不报警）⇒ 与项目里"接线断了没人喊"那一类同源。
      **修法：仍然吞掉异常（绝不抛 —— 常驻服务是批处理的，抛出去会打断整批），
      但至少留一行可查的痕迹**：stderr 一行 warning + 模块级诊断变量 `_POST_SCAN_ERROR`。
      ⚠ **不许改成抛异常**：`_post_scan` 在 `render()` 的主路径上，抛出去 = 整批出图中断。
    """
    global _POST_SCAN_ERROR
    _POST_SCAN_ERROR = None
    scanfx = None
    try:
        from . import scanfx
        out = scanfx.chroma_blur(out, cfg)
    except Exception as e:                                                 # noqa: BLE001
        # 吞掉，但**留下痕迹**（stderr 一行 + 模块级诊断变量，供 selftest / 事后排查读）
        _POST_SCAN_ERROR = '%s: %s' % (type(e).__name__, e)
        print('[svFilm 警告] presets._post_scan 的 scanfx.chroma_blur 抛异常 ⇒ '
              '**色度模糊这一层静默不生效**：%s' % _POST_SCAN_ERROR, file=sys.stderr)
    # ⚠ `scanfx.chroma_blur` **自己**也会吞异常（缺 scipy ⇒ 原样返回、不抛）⇒
    #   上面那个 except **抓不到它**，这里把它的诊断变量并进来，否则"缺 scipy"依然无声。
    _cc = str(getattr(scanfx, '_CHROMA_LAST_ERROR', None) or '') if scanfx else ''
    if _cc:
        _POST_SCAN_ERROR = _cc
        print('[svFilm 警告] scanfx.chroma_blur 跳过（**没有报错，但也没生效**）：%s'
              % _cc, file=sys.stderr)
    return out


# ---------------------------------------------------------------------------
# ★★★ 10-11：**像素键统一缩放**（`film_render.grain.blur` · `scanner.lens_blur`）
# ---------------------------------------------------------------------------
# 「像素键」= σ 的单位是**绝对像素**、且作用在**整幅图**上的那几根。它们的物理量本该
# 固定在**片/纸平面（µm）**，写成绝对像素就变成"同一个数、图越小糊得越狠"
# ⇒ **预览与交付是两个器件**。⇒ 必须随渲染尺寸缩放。
#
# ⚠ 这不是新机制：`grain.blur` 从 10-10 起就是这么做的（原 `_scale_grain_blur`）。
#   10-11 把 `scanner.lens_blur` 并进来，**合并成一张声明式表**——
#   理由：两根键的**失效模式、基准、守卫、还原方式一模一样**，写成两套必然慢慢长歪
#   （项目里"两处写值只有一处算数"已经栽过：`grain.blur` 的 JSON 0.65 vs targets 3.0）。
#   ★ 合并后 `grain.blur` 的**数值行为逐位不变**（同一个式子、同一个基准、同一个早退条件）。
#
# ⚠ 每一根各自带**基准长边所在的 cfg 名**（该值为 0/负 ⇒ 单独关掉这一根，回到"绝对像素"）。
#   两根现在都是 **6264 = 交付口径** ⇒ 交付尺寸下因子**恰为 1.0**。
# ⚠ `f == 1.0` 时**早退**：不写回、不追还原记录、连 `x * 1.0` 都不做
#   ⇒ 交给 `simulate()` 的 params **逐字段不变** ⇒ 交付物不变。
#   ★ 实测（10-11，`e1_bitident_proof.py`）：6264 下 `f = 1.000000000`、**还原记录 0 条**、
#     **钩子前后 params 的 SHA256 指纹相同**，且与"完全没有这根钩子的旧代码"指纹相同。
#   ⚠⚠ **不要用"跑两遍比像素"来验这件事** —— 引擎**本身**在 6264 上不可逐位复现
#     （10-11 实测：同一份代码两个进程 ⇒ `max|Δ| = 0.100`）；根因是
#     `print_render.glare.active=True` 走的 `glare.py → fast_stats.fast_lognormal`
#     是 `@njit` 里用 **numba 的 `np.random.randn()`**，**不受 `np.random.seed()` 约束**。
#     详见 `config.LENS_BLUR_REF_LONG_SIDE` 与本轮报告 §风险。
_PX_SCALED_KEYS = (
    # (运行时字段路径, 基准长边的 cfg 名, 门控 —— 返回 False 表示"这根现在不参与")
    ('film_render.grain.blur', 'GRAIN_BLUR_REF_LONG_SIDE',
     lambda p: bool(getattr(p.film_render.grain, 'active', False))),
    ('scanner.lens_blur', 'LENS_BLUR_REF_LONG_SIDE', None),
)


def _scale_px_keys(p, lin, cfg, _ovs):
    r"""把「绝对像素」的 σ 键按 `实际长边 ÷ 基准长边` 缩放 ⇒ **预览 = 交付物**。

    ## `grain.blur`（10-10 落地，本次只搬进表里，行为不变）
    vendor `grain.py:104-106` 的 `layer_particle_model` 返回输入密度的**无偏采样**
    ⇒ 模糊它 = **模糊整张画面**（名字叫"颗粒模糊"，实际是"整幅柔度"）。
    实测（10-10，`_full_scan.py`，两侧缩到 SIDE=900 同口径）：
      全长边 6264：`A1_ldr50 49.67 / C1_psd_slope −3.006 / C4_grain 0.485`
      2048 @ blur≈1.0：`A1 50.02 / C1 −2.993 / C4 0.500` ⇒ **逐项对上**
      且 `3.0 ÷ 1.0 = 3.0` ≈ 尺寸比 `6264 ÷ 2048 = 3.06` ⇒ 确认**随尺寸线性缩放**。

    ## `scanner.lens_blur`（★ 10-11 新增）
    vendor `scanning.py:124` 直接 `apply_gaussian_blur(rgb, self._scanner.lens_blur)` ——
    **无 µm 换算**（`ScanningStage` 拿不到 `pixel_size_um`）。@1000 线/像高的 MTF：
      2048 ⇒ **0.618**（= 5.62 µm） ｜ 6264 ⇒ **0.950**（= 1.84 µm ≈ f/5.6 衍射 1.34 µm）
    ⇒ **数值 0.32 不动（交付上它是对的），错的是"像素"这个单位**。详见
    `config.LENS_BLUR_REF_LONG_SIDE` 上面那段（含"µm 与画面宽度比例在数值上是同一个式子"的证明）。

    ## 契约
    * 基准尺寸下 `f = 长边/基准 ≡ 1.0` ⇒ **早退**，管线拿到的对象与不装这个钩子**逐位相同**。
    * `p` 是**跨调用共享的缓存对象** ⇒ 只改值、把旧值记进 `_ovs`，由 `_render_locked` 的
      `finally` **逆序还原**。
    * 取值/走路径失败一律**跳过这一根**（不抛）—— 钩子在 `render()` 主路径上，
      抛出去 = 整批出图中断；且这些键都是**减弱型**效果，失效时画面不报警。
    """
    try:
        h, w = np.asarray(lin).shape[:2]
        long_side = float(max(int(h), int(w)))
    except Exception:                                                    # noqa: BLE001
        return
    if long_side <= 0.0:
        return
    for _dotted, _ref_attr, _gate in _PX_SCALED_KEYS:
        _ref = float(getattr(cfg, _ref_attr, 0.0) or 0.0)
        if _ref <= 0.0:
            continue                                   # 这一根关着 ⇒ 回到"绝对像素"旧行为
        try:
            _holder, _attr = _walk(p, _dotted)
        except Exception:                                                # noqa: BLE001
            continue
        if not hasattr(_holder, _attr):
            continue
        if _gate is not None:
            try:
                if not _gate(p):
                    continue                           # （grain 关着 ⇒ 不碰）
            except Exception:                                            # noqa: BLE001
                continue
        _f = long_side / _ref
        if abs(_f - 1.0) < 1e-9:
            continue                                   # ★ 基准尺寸 ⇒ 逐位不变（不写、不乘）
        _ovs.append((_holder, _attr, getattr(_holder, _attr)))
        setattr(_holder, _attr, float(getattr(_holder, _attr)) * _f)


def _halation_kernel_shape(p, cfg, _ovs):
    r"""★★★ 10-11：把 halation 的**绿层核**收窄到红层的 `cfg.HALATION_G_SIGMA_RATIO` 倍。

    ## 一句话
    物理事实是 **近处橙、远处红**（绿层只在返光最强处被推动）；三通道共用 σ 的线性模型
    **表达不出**它。把 G 的核做窄 = 那个阈值非线性**在线性模型里的最佳替身**。

    ## 为什么是"钩子"而不是改数据（三条都实测过，见 `config.HALATION_G_SIGMA_RATIO`）
    * **预设 JSON 那两行是活的**：生产走 `digest_params(apply_stocks_specifics=False)`
      （`config.PRESET_APPLY_STOCK_SPECIFICS = False`），`_apply_halation_preset` **不被调用**
      —— 实测把 σ 改成 `(65,29,65)` 过一遍 digest **仍然是 (65,29,65)**。
      ⇒ `config.py` 死键清单里"`halation_first_sigma_um` 每次 digest 被重写"**只对
      `apply_stocks_specifics=True` 成立**，对生产路径**不成立**。
    * **vendored `_HALATION_PRESETS`（`params_builder.py:233-240`）改不动** ——
      同一条理由：那条路在本项目里跑不到 ⇒ **改了没反应**。
    * ⇒ 落点只能在 svFilm 侧。**且只能有一处**：JSON 与钩子各写一遍 = 两个真相
      （项目已栽过 `grain.blur` 的 JSON 0.65 vs targets 3.0）⇒ 值**只在 config**，
      钩子**派生**出来。

    ## 契约
    * `ratio` 不在 (0,1) 内 ⇒ **不动**（回到三通道同 σ 的旧行为，逐位不变）。
    * 已经等于 `σ_R × ratio`（1e-9 内）⇒ **早退**（幂等，且不产生浮点扰动）。
    * `halation.active=False` ⇒ 不碰。
    * 只改 G；**R / B 与 `halation_strength` 一个字都不动**（后者是物理基线，禁止动）。
    """
    ratio = float(getattr(cfg, 'HALATION_G_SIGMA_RATIO', 0.0) or 0.0)
    if not (0.0 < ratio < 1.0):
        return
    try:
        h = p.film_render.halation
    except Exception:                                                    # noqa: BLE001
        return
    if not bool(getattr(h, 'active', False)):
        return
    sig = getattr(h, 'halation_first_sigma_um', None)
    if sig is None:
        return
    try:
        r, g, b = (float(v) for v in tuple(sig))
    except Exception:                                                    # noqa: BLE001
        return
    want = r * ratio
    if abs(g - want) < 1e-9:
        return                                       # 已经是对的 ⇒ 逐位不变
    _ovs.append((h, 'halation_first_sigma_um', tuple(sig)))
    h.halation_first_sigma_um = (r, want, b)


def _render_locked(p, name, lin, cfg, print_exposure, print_profile, overrides):
    _p0 = p.enlarger.print_exposure
    _pp0 = p.print
    _ovs = []                       # [(对象, 属性名, 原值)] —— 还原用
    try:
        if print_profile:
            spektra = __import__(__name__.rsplit('.', 1)[0] + '.spektra', fromlist=['x'])
            init_params, _sim = spektra._sf()
            _d = load_raw(name)
            p.print = init_params(film_profile=_d['simulation']['film_stock'],
                                  print_profile=str(print_profile)).print

        if print_exposure is not None:
            p.enlarger.print_exposure = float(print_exposure)
        else:
            _sh = 1.0
            if bool(getattr(cfg, 'PRESET_PE_SHIFT_FROM_SPEK', True)):
                _sh = float(getattr(cfg, 'SPEK_PE_SHIFT', 1.0) or 1.0)
            p.enlarger.print_exposure = pe_of(name) * _sh
        # ★★ **保留引擎自己那套测光**（`auto_exposure` / `normalize_print_exposure`）——
        #   曝光 / 反差归引擎，引擎之前一个像素不动。
        #   实测：逼着关掉，中位会比验收版低 **5.1** 个 L*（64.4 vs 69.3），亮部也低（88.6 vs 89.8）；
        #   恢复预设原样 ⇒ 11.7/69.5/89.8，**三个数全中**。

        # ★ 场景覆盖放**最后**（能盖住上面两项）。字段路径写错 **当场报错**，不静默吞掉。
        # ★★ 09-29（0.3.4）：**frozen dataclass 要整体替换，不能 setattr**。
        #   实例：`print_render.density_curves_morph`（`PrintCurvesMorphParams`，`@dataclass(frozen=True)`）
        #   ⇒ 直接 `setattr` 会 `FrozenInstanceError`。这里改成 `dataclasses.replace` 造新对象装回父对象。
        for _dotted, _val in (dict(overrides) if overrides else {}).items():
            _parts = [s for s in str(_dotted).split('.') if s]
            if not _parts:
                raise KeyError('overrides 的字段路径是空的')
            _holder = p
            for _k in _parts[:-1]:
                if not hasattr(_holder, _k):
                    raise KeyError('overrides 的字段路径走不通: %s（在 %r 处断了）'
                                   % (_dotted, _k))
                _holder = getattr(_holder, _k)
            _attr = _parts[-1]
            if not hasattr(_holder, _attr):
                raise KeyError('overrides 里这个字段不存在: %s（预设 %s）' % (_dotted, name))
            if _is_frozen_dc(_holder):
                _new = dataclasses.replace(_holder, **{_attr: _val})
                _pobj, _pkey, _orig = _walk_holder(p, _parts[:-1])
                _ovs.append((_pobj, _pkey, _orig))          # 还原用
                setattr(_pobj, _pkey, _new)
            else:
                _ovs.append((_holder, _attr, getattr(_holder, _attr)))
                setattr(_holder, _attr, _val)

        # ★★ 10-10：grain.blur ——「绝对像素」→「随渲染尺寸缩放」（预览 = 交付物）。
        # ★★★ 10-11：**同一张表**里又加了 `scanner.lens_blur`（同样是绝对像素）。
        #   必须放 overrides **之后**（场景覆盖可能改过它们）；旧值记进 _ovs 供 finally 还原。
        _scale_px_keys(p, lin, cfg, _ovs)

        # ★★★ 10-11：halation 的**绿层核**收窄（物理：近橙远红）。
        #   同样必须在 overrides 之后 —— `targets._scene_engine["*"]` 里带着
        #   `film_render.halation.halation_strength` 的轴覆盖，钩子要读的是**最终**那份 σ。
        _halation_kernel_shape(p, cfg, _ovs)

        out = _simulate_once(p, np.clip(np.asarray(lin, np.float64), 0.0, None),
                             bool(getattr(cfg, 'PRESET_APPLY_STOCK_SPECIFICS', False)))
    finally:
        # ★ 10-10：**逆序**还原。原因：同一个字段可能被记**两次**（先 overrides 记一次、
        #   `_scale_px_keys` 再记一次）⇒ 顺序还原会留最后一次的值、把共享的 `p` 污染掉。
        #   逆序 ⇒ 回到最先记的那个（= `_apply` 建对象时的原值）。字段都不同时与顺序等价。
        for _obj, _attr, _old in reversed(_ovs):   # 覆盖先还，再还上面那两项
            setattr(_obj, _attr, _old)
        p.enlarger.print_exposure = _p0
        p.print = _pp0
    return np.clip(np.asarray(out, np.float64), 0.0, 1.0)


def _is_frozen_dc(obj):
    """是不是 `@dataclass(frozen=True)` —— 那种不能 `setattr`，只能整体替换。"""
    try:
        return bool(dataclasses.is_dataclass(obj) and obj.__dataclass_params__.frozen)
    except Exception:                                          # noqa: BLE001
        return False


def _walk_holder(root, parts):
    """按【父段】走到持有者，返回 `(父对象, 键, 持有者)` —— 供"整体替换"用。

    例：`root.print_render` 的 parts 是 `['print_render']` ⇒ 返回 `(root, 'print_render', root.print_render)`。
    """
    if not parts:
        raise KeyError('要整体替换的东西不能是根对象本身')
    obj = root
    for k in parts[:-1]:
        if not hasattr(obj, k):
            raise KeyError('overrides 的字段路径走不通: %s（在 %r 处断了）' % ('.'.join(parts), k))
        obj = getattr(obj, k)
    return obj, parts[-1], getattr(obj, parts[-1])


def _walk(root, dotted):
    """把 `'enlarger.print_y_filter_shift'` 解成 (倒数第二层的对象, 最后的属性名)。"""
    parts = [s for s in str(dotted).split('.') if s]
    if not parts:
        raise KeyError('overrides 的字段路径是空的')
    obj = root
    for k in parts[:-1]:
        if not hasattr(obj, k):
            raise KeyError('overrides 的字段路径走不通: %s（在 %r 处断了）' % (dotted, k))
        obj = getattr(obj, k)
    return obj, parts[-1]


_SIM = [None]
_SIM_LOCK = threading.Lock()          # ★ 10-08：`_SIM[0]` 的初始化原先**无锁**（裸双检）


def _simulate_once(p, lin, apply_specifics=False):
    """惰性拿 simulate，并**显式指定** `apply_stocks_specifics`。

    为什么不能吃默认：`simulate()` 内部会 `digest_params(params)`，而
    `digest_params` 的默认是 `apply_stocks_specifics=True` ⇒ `_apply_halation_preset`
    会按卷的抗晕层标签重写 `halation_strength`（把预设里的「光晕 40」冲回 0.015）。
    包一层，只改这一个开关；值由 `config.PRESET_APPLY_STOCK_SPECIFICS` 给（默认 False）。
    """
    if _SIM[0] is None:
        with _SIM_LOCK:                     # ★ 10-08：初始化挪进锁里（双检仍在锁内）
            if _SIM[0] is None:
                spektra = __import__(__name__.rsplit('.', 1)[0] + '.spektra', fromlist=['x'])
                _init_params, _simulate = spektra._sf()
                from spektrafilm.runtime.params_builder import (clone_runtime_params,
                                                               digest_params)

                def _run(image, params, _specifics, **kw):
                    # ★★★ 09-28 修一个**静默 bug**（"我们调的光晕 40 从来没生效"的真根因）：
                    #   这里 digest 了一次（用 `_specifics=False` ✓），但**忘了告诉 `simulate` 别再 digest**
                    #   ⇒ `simulate` 的 `digest_params_first` 默认 **True** ⇒ 它内部**又 digest 一次**
                    #   （用 `apply_stocks_specifics=True`）⇒ `_apply_halation_preset` 把
                    #   `halation_strength` 从我们设的 **0.4 重写回卷的出厂值 0.08**（实测确认）。
                    #   vendor 的 docstring 明写：「If you already have digested parameters or want to
                    #   digest them yourself, set `digest_params_first=False`」—— 我们正是"自己 digest"。
                    #   ⇒ 补上 `digest_params_first=False`。
                    # ★★★ 10-11 修（P3）：**digest 之前先克隆**。
                    #   为什么：`digest_params()` 会**就地改写**传入的对象 ——
                    #   而 `params` 是**跨调用共享的缓存对象** ⇒ 一旦它改了字段（实测
                    #   `preview_mode=True` 时会把 grain.active/area/couplers.diffusion/
                    #   scanner.unsharp **就地清零**），**同进程内之后每张图就静默无颗粒/无锐化/无扩散**。
                    #   （子任务当场撞到：候选表第 1 张图 8 条臂全正常、第 2 张图 8 条臂逐个分位数完全相同。）
                    #   本项目现在不触发（`preview_mode` 写死 False），**但这是埋着的雷**。
                    #   `params_builder` 已有现成的 `clone_runtime_params` ⇒ 克隆后再 digest。
                    #   ⚠ 对现有路径**逐位不变**：克隆后 digest 的结果与原地 digest 相同。
                    return _simulate(image,
                                     digest_params(clone_runtime_params(params),
                                                   apply_stocks_specifics=bool(_specifics)),
                                     digest_params_first=False, **kw)
                _SIM[0] = _run
    return _SIM[0](lin, p, bool(apply_specifics), print_timings=False)


def render_copy(lin, name, cfg=C, **kw):
    """调试用：每次都从 JSON 重新建 params（不走缓存），排除"缓存里是旧值"。

    ★ 10-08：整段放进**同一把按预设的锁**（`_lock_of` 已改成 RLock，可重入）。
      老写法只 `pop` 不入锁 ⇒ 并发时同一预设会存在**两份 params**：一份挂在被 pop 掉的
      旧对象上继续跑、一份是新对象 ⇒ 画面"偶尔不一样"，而自检全绿。"""
    with _lock_of(name):
        with _LOCK:
            _PARAMS.pop(name, None)
        return render(lin, name, cfg, **kw)
