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
    'Portra400薄荷': ('Portra400 · 薄荷', '暖侧逆光 + 青蓝背景，肤色有血色（最厚的一条）'),
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


def _apply(p, d, cfg):
    """把预设 JSON 的字段逐条灌进 params。字段名与 `params_schema` 的对应关系写死在下面。"""
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

    # ---- special：两条密度曲线 gamma（下限 0.01，与 GUI 一致）----
    p.film_render.density_curve_gamma = max(float(sp['film_gamma_factor']), 0.01)
    p.print_render.density_curve_gamma = max(float(sp['print_gamma_factor']), 0.01)

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
    p.scanner.white_correction = bool(si['scan_white_correction'])
    p.scanner.black_correction = bool(si['scan_black_correction'])
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
        _apply(p, d, cfg)
        _PARAMS[name] = p
        return p


def clear_cache():
    """手动作废参数缓存。现在**通常不需要**（`_params_for` 会按 config 指纹自动作废），
    留作显式口子：改了引擎级开关、又想在同进程里立刻看到效果时调它。"""
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
    """
    p = _params_for(name, cfg)

    # ★★★ 09-26：**按预设名加锁**。下面这一整段是「临时改写共享的 `p` → 跑 → 还原」，
    #   而常驻服务是多线程的（`ThreadingHTTPServer`）⇒ 同一预设的两个请求并发时，
    #   一个的改写会被另一个的还原抹掉（现在写的值恰好相同所以侥幸没事，
    #   但只要 `overrides` 一上就立刻变成真竞态）。
    #   ★ 粒度为**每个预设一把锁**：不同预设仍可并行，只有同名预设串行（本来就该串行，它们共用一个对象）。
    with _lock_of(name):
        return _post_scan(
        _render_locked(p, name, lin, cfg, print_exposure, print_profile, overrides), cfg)


def _post_scan(out, cfg):
    r"""★ 10-09：**扫描段后处理**（在颜色层之前、引擎之后）。

    目前只有一件：`scanfx.chroma_blur` —— **扫描色度模糊**。
    为什么放在这里（而不是让调用方自己做）：**保证所有调用方一致** ——
    尺子库实测（747 张大师，统一口径）「色度/亮度锐度比」：大师 **0.60**、我们 **0.99**
    ⇒ 真实扫描链的色度分辨率天然低于亮度，**这一层属于"扫描"，不属于调用方**。
    默认关（`CHROMA_BLUR_ENABLE=False`）⇒ 对既有行为**逐位无影响**。
    """
    try:
        from . import scanfx
        return scanfx.chroma_blur(out, cfg)
    except Exception:                                                      # noqa: BLE001
        return out


def _scale_grain_blur(p, lin, cfg, _ovs):
    r"""★★★ 10-10：把 `film_render.grain.blur` 从「绝对像素」转成「随渲染尺寸缩放」。

    ## 为什么
    `grain.blur` 在 vendor 里是**绝对像素**的高斯 σ，而且它模糊的是**整幅染料密度图**
    （`grain.py:104-106`：`layer_particle_model` 返回输入密度的无偏采样 ⇒ 模糊它 = 模糊整张画面）
    ⇒ 名义上叫"颗粒模糊"，**实际是"整幅画面的柔度"**；单位是绝对像素
    ⇒ **同一个数，图越小糊得越狠**。

    ## 实测（10-10，`_full_scan.py`，两侧都缩到 SIDE=900 同口径）
    全长边 6264：`A1_ldr50 49.67 / C1_psd_slope −3.006 / C4_grain 0.485`
    2048 @ blur≈1.0：`A1 50.02 / C1 −2.993 / C4 0.500` ⇒ **逐项对上**
    ⇒ 且 `3.0 ÷ 1.0 = 3.0` ≈ 尺寸比 `6264 ÷ 2048 = 3.06` ⇒ **确认随尺寸线性缩放**。

    ## 做法
    `blur_eff = blur × (实际长边 ÷ cfg.GRAIN_BLUR_REF_LONG_SIDE)`。
    基准取**交付口径长边**（6264，10-09 夜标 3.0 时用的）⇒ 交付尺寸因子 **1.0**、
    **输出逐位不变**；2048 预览因子 0.327 ⇒ 颗粒与交付物等价 ⇒ **预览 = 交付物**。

    ★ 与 `scanfx.CHROMA_BLUR_W`（画面宽度比例）同思路；这里保留"绝对像素 × 尺寸因子"的
      形式，好处是**不动 targets/预设里既有的值与语义**，且交付物零变化。

    ⚠ `p` 是跨调用**共享的缓存对象** ⇒ 只改值、把旧值记进 `_ovs`，由 `finally` 还原。
    """
    ref = float(getattr(cfg, 'GRAIN_BLUR_REF_LONG_SIDE', 0.0) or 0.0)
    if ref <= 0.0:
        return                                     # 关（0/负）⇒ 回到"绝对像素"旧行为
    try:
        g = p.film_render.grain
    except Exception:                                                    # noqa: BLE001
        return
    if not bool(getattr(g, 'active', False)) or not hasattr(g, 'blur'):
        return                                     # 颗粒关着 ⇒ 不碰
    try:
        h, w = np.asarray(lin).shape[:2]
    except Exception:                                                    # noqa: BLE001
        return
    f = float(max(int(h), int(w))) / ref
    if abs(f - 1.0) < 1e-9:
        return                                     # 交付尺寸 ⇒ 逐位不变
    _ovs.append((g, 'blur', g.blur))
    g.blur = float(g.blur) * f


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
        #   必须放 overrides **之后**（场景覆盖可能改过它）；旧值记进 _ovs 供 finally 还原。
        _scale_grain_blur(p, lin, cfg, _ovs)

        out = _simulate_once(p, np.clip(np.asarray(lin, np.float64), 0.0, None),
                             bool(getattr(cfg, 'PRESET_APPLY_STOCK_SPECIFICS', False)))
    finally:
        # ★ 10-10：**逆序**还原。原因：同一个字段可能被记**两次**（先 overrides 记一次、
        #   `_scale_grain_blur` 再记一次）⇒ 顺序还原会留最后一次的值、把共享的 `p` 污染掉。
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
                from spektrafilm.runtime.params_builder import digest_params

                def _run(image, params, _specifics, **kw):
                    # ★★★ 09-28 修一个**静默 bug**（"我们调的光晕 40 从来没生效"的真根因）：
                    #   这里 digest 了一次（用 `_specifics=False` ✓），但**忘了告诉 `simulate` 别再 digest**
                    #   ⇒ `simulate` 的 `digest_params_first` 默认 **True** ⇒ 它内部**又 digest 一次**
                    #   （用 `apply_stocks_specifics=True`）⇒ `_apply_halation_preset` 把
                    #   `halation_strength` 从我们设的 **0.4 重写回卷的出厂值 0.08**（实测确认）。
                    #   vendor 的 docstring 明写：「If you already have digested parameters or want to
                    #   digest them yourself, set `digest_params_first=False`」—— 我们正是"自己 digest"。
                    #   ⇒ 补上 `digest_params_first=False`。
                    return _simulate(image,
                                     digest_params(params,
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
