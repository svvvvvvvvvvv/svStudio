# -*- coding: utf-8 -*-
r"""读 public GUI 导出的预设 JSON，直接跑 spektrafilm 渲染。

## 为什么有这个模块

svFilm 原来只有 5 条「真卷」：卷+纸 = `spektra.STOCK_MAP` 定的，性格 = 几根 `SPEK_*` 常量。
2026-09-23 SV 定案：**删掉那 5 条，改用 public GUI 那 9 条大师预设**。
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

import copy
import json
import os
import threading

import numpy as np

from . import config as C

# 预设按名字缓存：9 条，每条只建一次；建完不再改 ⇒ 多线程下只读，安全。
_PARAMS: dict[str, object] = {}
_LOCK = threading.Lock()

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
    p.enlarger.m_filter_shift = float(si['print_m_filter_shift'])
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
    # ⚠⚠ 字段名必须跟 vendor 版本对得上：**0.3.2 叫 `agx_particle_area_um2` / `agx_particle_scale` /
    #    `agx_particle_scale_layers`**（0.3.4 把 `agx_` 前缀去掉了）。
    #    名字写错**不会报错**（`GrainParams` 是普通 dataclass、没有 `__slots__`），
    #    只会静默多出几个没人读的属性 ⇒ 颗粒参数一点没生效、画面照旧。
    #    `selftest.t_presets` 拿 JSON 的值逐条回读钉着这一条。
    #    ⚠ 换 vendor 版本时**这里必须跟着换**，否则颗粒静默失效。
    g = p.film_render.grain
    if float(gr['particle_area_um2']) <= 0:
        # 粒子面积为 0 物理上无意义（grain.py 里会除以零）⇒ 等同关掉
        g.active = False
    else:
        g.active = bool(gr['active'])
        g.sublayers_active = bool(gr['sublayers_active'])
        g.agx_particle_area_um2 = float(gr['particle_area_um2'])
        g.agx_particle_scale = tuple(gr['particle_scale'])
        g.agx_particle_scale_layers = tuple(gr['particle_scale_layers'])
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

    p.debug.lut_mode = False               # lut_mode 会把空间效果全关掉 —— 那是给烘 LUT 用的
    p.debug.deactivate_spatial_effects = False
    p.debug.deactivate_stochastic_effects = False


def _params_for(name, cfg):
    """按预设名缓存。建完不再改 ⇒ 多线程只读。"""
    with _LOCK:
        p = _PARAMS.get(name)
        if p is not None:
            return p
    d = load_raw(name)
    spektra = __import__(__name__.rsplit('.', 1)[0] + '.spektra', fromlist=['x'])
    init_params, _simulate = spektra._sf()
    p = init_params(film_profile=d['simulation']['film_stock'],
                    print_profile=d['simulation']['print_paper'])
    _apply(p, d, cfg)
    with _LOCK:
        _PARAMS[name] = p
    return p


def clear_cache():
    """改完 config 里的加速开关要调它（否则缓存里那份还是老设置）。"""
    with _LOCK:
        _PARAMS.clear()


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


def render(lin, name, cfg=C, print_exposure=None, print_profile=None):
    r"""喂**场景线性**，出**显示域**（与 `spektra.render()` 同契约，可互换）。

    `print_exposure`：不传 = 用「预设自带的 pe × `SPEK_PE_SHIFT`」（与真卷那条路的结构一致）；
      显式传 = 直接覆盖（给二分找落点用）。
    `print_profile`：不传 = 用预设配套的那张纸；传了 = 覆盖（相纸下拉）。

    ⚠ 这里**不做** `digest_params(apply_stocks_specifics=True)` —— 那会把预设里的
      `halation_strength` 冲回卷的出厂值（我们调的「光晕 40」就这么没的）。
      `simulate()` 内部还会再 digest 一次，这一次用的是它自己的默认值；
      所以下面先把 `apply_stocks_specifics` 想关掉的效果**写死在 params 上**，
      并在 `selftest.t_presets` 里钉着不放。
    """
    p = _params_for(name, cfg)

    _p0 = p.enlarger.print_exposure
    _ae0 = p.camera.auto_exposure
    _np0 = p.enlarger.normalize_print_exposure
    _pp0 = p.print
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
        # ★★ 引擎自己那套测光要不要留 —— **跟着"曝光风格作用在哪一段"走**：
        #   · `TONE_AFTER_ENGINE = True`（当前）：曝光风格作用在**成片**上，引擎之前一个像素
        #     不动 ⇒ **必须保留预设自己的测光**（`auto_exposure` / `normalize_print_exposure`）。
        #     实测：逼着关掉，中位会比验收版低 **5.1** 个 L*（64.4 vs 69.3），
        #     而且亮部也低（88.6 vs 89.8）；恢复预设原样 ⇒ 11.7/69.5/89.8，**三个数全中**。
        #   · `TONE_AFTER_ENGINE = False`（老路）：落点由 `tone` 在引擎**之前**定
        #     ⇒ 引擎那套测光会把它的活抵消掉，**必须关**（跟 `spektra.render` 一个道理）。
        if not bool(getattr(cfg, 'TONE_AFTER_ENGINE', False)):
            p.camera.auto_exposure = False
            p.enlarger.normalize_print_exposure = False

        out = _simulate_once(p, np.clip(np.asarray(lin, np.float64), 0.0, None),
                             bool(getattr(cfg, 'PRESET_APPLY_STOCK_SPECIFICS', False)))
    finally:
        p.enlarger.print_exposure = _p0
        p.camera.auto_exposure = _ae0
        p.enlarger.normalize_print_exposure = _np0
        p.print = _pp0
    return np.clip(np.asarray(out, np.float64), 0.0, 1.0)


_SIM = [None]


def _simulate_once(p, lin, apply_specifics=False):
    """惰性拿 simulate，并**显式指定** `apply_stocks_specifics`。

    为什么不能吃默认：`simulate()` 内部会 `digest_params(params)`，而
    `digest_params` 的默认是 `apply_stocks_specifics=True` ⇒ `_apply_halation_preset`
    会按卷的抗晕层标签重写 `halation_strength`（把预设里的「光晕 40」冲回 0.015）。
    包一层，只改这一个开关；值由 `config.PRESET_APPLY_STOCK_SPECIFICS` 给（默认 False）。
    """
    if _SIM[0] is None:
        spektra = __import__(__name__.rsplit('.', 1)[0] + '.spektra', fromlist=['x'])
        _init_params, _simulate = spektra._sf()
        from spektrafilm.runtime.params_builder import digest_params

        def _run(image, params, _specifics, **kw):
            return _simulate(image,
                             digest_params(params, apply_stocks_specifics=bool(_specifics)),
                             **kw)
        _SIM[0] = _run
    return _SIM[0](lin, p, bool(apply_specifics), print_timings=False)


def render_copy(lin, name, cfg=C, **kw):
    """调试用：每次都从 JSON 重新建 params（不走缓存），排除"缓存里是旧值"。"""
    with _LOCK:
        _PARAMS.pop(name, None)
    return render(lin, name, cfg, **kw)
