# -*- coding: utf-8 -*-
r"""读预设 JSON，直接跑 spektrafilm 渲染（喂**场景线性**、出**显示域**，与 `spektra.render()`
同契约 ⇒ 两者可互换）。每份预设是**完整参数快照**（上百字段），字段映射在这里手写。

★ `apply_stocks_specifics` 必须 `False`：True 时 `_apply_halation_preset` 会按卷的「抗晕层」
  标签把 `halation_strength` 重写成 0.015 ⇒ 预设调好的光晕**静默消失**。
"""

from __future__ import annotations

import dataclasses          # `PrintCurvesMorphParams` 是 frozen ⇒ 只能 replace
import json
import os
import sys
import threading

import numpy as np

from . import config as C

# 预设按名字缓存：每条只建一次；⚠ `render()` 会**临时改写**这个共享对象的字段再还原 ⇒
# 同预设并发必须串行（见 `_LOCKS`）。
_PARAMS: dict[str, object] = {}
_LOCK = threading.Lock()
# 渲染用的**按预设名**的一把锁（粒度 = 预设，不同预设仍可并行）
_LOCKS: dict = {}
# `_post_scan` 的**诊断变量**：减弱型效果失效时画面 / 直方图都不报警，只能靠它留证。
_POST_SCAN_ERROR: str | None = None

# 白端门控警告的**进程内去重表**（见 `_check_white_gate`）：同一预设名只警告一次。
# ⚠ **不许用 `_LOCK` 保护它** —— 本表用在 `_apply()` 里，而 `_params_for()` 是**持锁**调 `_apply()`
#   的 ⇒ 在那里取 `_LOCK`（**非重入**）= **当场自死锁**（连同进程其它线程一起堵死）。
# ⚠ 去重判据必须**值比较**：键是每次新建的 `str(name)`（对象同一性判不出来），而 `set.add()`
#   **永远返回 `None`** ⇒ 只能"先看、再写"（`in` + 赋值，各是单次原子调用）。
_WARN_CLAIM = object()          # 哨兵值（只当"已喊过"的占位用；去重判据是 `in`）
_WHITE_GATE_WARNED: dict = {}   # ★ 是 **dict** 不是 set（理由见上面那段）


# ---------------------------------------------------------------------------
# **overrides 与"基线层"** —— 一个静默死键
# ---------------------------------------------------------------------------
# `…density_curves_morph.active=True` **只在 `targets._scene_engine["*"]`**（基线层）、不在预设
# JSON 里（schema 默认 `False`）⇒ 只传"要动的那一根"会**丢掉整条基线** ⇒ **morph 整根死掉**，
# 而输出与"不带 override"在噪声内相同（看起来像"这根旋钮没用"）⇒ 见 `_base_overrides()`。
def _base_overrides(cfg, stock=None):
    r"""取**基线层**（`_scene_engine["*"]` ＋ `_stock_engine[stock]`），**已解析**。

    ★ 合并规则**只留一份**（委托 `targets.scene_engine`）：曾另抄一份 ⇒ 两处必然漂 —— 曾把
      `_stock_engine` 整层**静默丢掉**、`{"mul"}` 原样返回 ⇒ setattr 炸 `UFuncOutputCastingError`。
    ⚠ `{"mul": k}` = 「乘在**该预设**的基线之上」⇒ **没有 `stock` 就无解**（`stock=None` 当场抛）。
    """
    if not stock:
        raise ValueError(
            'presets._base_overrides() 必须传 `stock=<预设名>`。\n'
            '  ⇒ 10-12 起 `_stock_engine` 已启用：**每条预设的基值不同**'
            '（如 `lightness_compression` 鹿井 [0.8,1.0,2.2] / 增田·滨田 [0.85,1.0,2.2]）\n'
            '  ⇒ 不指定预设就没有"基线"可言，硬返回一份会**静默给错基准**。\n'
            '  ⇒ 只要全局那层：`targets.scene_engine({}, stock=None, cfg=cfg)`。')
    try:
        from . import targets as _T
    except Exception:                                      # noqa: BLE001
        return {}
    out = dict(_T.scene_engine({}, stock=stock, cfg=cfg) or {})
    _unres = sorted(k for k, v in out.items()
                    if isinstance(v, dict) and 'mul' in v)
    if _unres:
        raise ValueError(
            '_base_overrides(stock=%r) 里仍有 %d 个 `{"mul": k}` 未解析：%s\n'
            '  ⇒ 多半是 `targets.scene_engine` 那个解析分支没走到'
            '（它要求 `stock` 非空且能取到该预设的基线）。'
            % (stock, len(_unres), _unres))
    return out


def _warn_if_morph_dead(overrides):
    """★ 开发期守卫：`overrides` 给了 `…density_curves_morph.*` 却**没带 `active`** ⇒ 喊一声
    （**只警告、不改行为** —— 自动补 `active` = 悄悄改画面）。"""
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
              '  ⇒ 正确写法：`ov = presets._base_overrides(cfg, stock=预设名); '
              'ov.update({...})`。', file=sys.stderr)


def _check_white_gate(name, wc, bc, white_level):
    r"""**白端门控警告** —— 预设把 `scan_white_correction` 关掉时喊一声。

    `white_correction` / `black_correction` **是两根 level 键的门控**（`color_reference.py:139-145`）：
    关掉哪一根，那一根对应的 level 键就**一个读点都没有** ⇒ **死键**（扫 0.90→1.30 无反应）。
    ★ 后果**结构性**：白锚退化成链自身白参考 ⇒ 「三通道都高」的显示码上界 237 → **227**
      （`min(RGB)≥235` 恒为 0），而 `white_level` 正是唯一能抬高光天花板的键（1.02→1.18 时
      L\*60 +4.55）—— 它在这一支里是死的。
    ★ 只警告、不碰值；**不取任何锁**（本函数被持锁路径调用，`_LOCK` **非重入** ⇒ 自死锁）。"""
    if wc and bc:
        return
    _key = str(name)
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
            # 用 **RLock**：`render_copy()` 要在持有同一把锁时再调 `render()`（内部会取同名锁）。
            lk = _LOCKS[name] = threading.RLock()
    return lk

_SUFFIX = '.json'
# 这几个 NOT 字段名在 JSON 里没有、但 svFilm 要自己定（引擎的加速开关）
_SETTINGS_FROM_CFG = ('use_enlarger_lut', 'use_scanner_lut', 'lut_resolution', 'use_fast_stats')


def _pkg_dir():
    return os.path.dirname(os.path.abspath(__file__))


def preset_dir():
    r"""预设目录（第一个存在的胜出）：`SVFILM_PRESET_DIR` → `<本包>/data/presets` →
    public GUI 装的那份（`%LOCALAPPDATA%\\napari\\napari\\presets`，方便"那边改了这边就能试"）。"""
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


# 一条预设 = 一个「胶片风格」，键 = JSON 文件名；没在这里写说明的**不让上界面**（宁可当场炸）。
_DESC = {
    '鹿井预设': ('鹿井预设',
              '照鹿井屿渔的作品立靶（26 张，大师逐图 [P25,P75]）；10-15 从零重立'),
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
    """把预设 JSON 的字段逐条灌进 params（对应关系写死在下面）。`name` **只**用于诊断
    （见 `_check_white_gate`），不参与任何赋值。"""
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

    # ---- special：**胶片端**密度曲线 gamma（下限 0.01）----
    # 标量或**逐通道 3 元组**都接受（只放宽类型、不改语义；底层本就把标量铺成 `[g,g,g]`）。
    _fg = sp['film_gamma_factor']
    if isinstance(_fg, (list, tuple)):
        p.film_render.density_curve_gamma = tuple(max(float(_v), 0.01) for _v in _fg)
    else:
        p.film_render.density_curve_gamma = max(float(_fg), 0.01)
    # ⚠ **不要加回** `p.print_render.density_curve_gamma = ...`：它**没有任何消费者**（0.3.4 的
    #   `PrintRenderingParams` 只有 `glare` / `density_curves_morph`）；`RuntimePhotoParams` 无
    #   `__slots__` ⇒ 写了不报错、只挂一个**没人读**的属性 ⇒ 预设的 `print_gamma_factor` 从未生效。

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
    # ★ **统一加一点品红**（印相底座补偿）；剂量与"对预闪不生效"见 `config.PRESET_FILTER_M_TRIM`。
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
    # ⚠⚠ 字段名**必须跟 vendor 版本对得上**：**0.3.2 = `agx_particle_*`、0.3.4 去掉 `agx_` 前缀
    #   ⇒ 一律写 `particle_*`**。`GrainParams` 无 `__slots__` ⇒ 写错**不报错**，只静默多出几个
    #   **没人读**的属性 ⇒ **颗粒参数一点没生效**（写 2.5，vendor 实读 schema 默认 0.2）。
    g = p.film_render.grain
    # ⚠ 下面这 3 个键是**死键**：`sublayers_active=False` 时引擎一个都不读（`grain.py:179` 的分支），
    # 已搬进 `_archive/B7_dead_keys_grain.json`。缺省时**不写**（保持引擎 schema 默认），
    # 否则从 JSON 删掉这些键会让 `_apply` 当场 KeyError。
    if float(gr['particle_area_um2']) <= 0:
        # 粒子面积为 0 物理上无意义（grain.py 里会除以零）⇒ 等同关掉
        g.active = False
    else:
        g.active = bool(gr['active'])
        g.sublayers_active = bool(gr['sublayers_active'])
        g.particle_area_um2 = float(gr['particle_area_um2'])
        g.particle_scale = tuple(gr['particle_scale'])
        if 'particle_scale_layers' in gr:
            g.particle_scale_layers = tuple(gr['particle_scale_layers'])
        g.density_min = tuple(gr['density_min'])
        g.uniformity = tuple(gr['uniformity'])
        g.blur = float(gr['blur'])
        if 'blur_dye_clouds_um' in gr:
            g.blur_dye_clouds_um = float(gr['blur_dye_clouds_um'])
        if 'micro_structure' in gr:
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
    # ⚠ 配平基准必须钉死：两版 vendor 的中性滤片 DB 查出来的**不是同一对数**（读序 `c,m,y`）；
    #   预设标定在 0.3.2 ⇒ 关掉 DB、填它的实测值，做到**版本无关**。
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

    # ★★ **显式钉死"印相中灰配平"**：两键 True 时印相曝光按 18.4% 中灰归一化、`print_exposure`
    #   才是有效落点旋钮，且 `camera.exposure_compensation_ev` 的净亮度效果被它抵销
    #   （`filming.py:129` / `printing.py:112`）⇒ 靠 vendor 默认的话，换版本会**静默位移一档**。
    p.enlarger.print_exposure_compensation = True
    p.enlarger.normalize_print_exposure = True

    p.debug.lut_mode = False               # lut_mode 会把空间效果全关掉 —— 那是给烘 LUT 用的
    p.debug.deactivate_spatial_effects = False
    p.debug.deactivate_stochastic_effects = False


_PARAMS_SIG = [None]          # 上次建缓存用的 config 指纹（变了就整体作废）
_PARAMS_FSIG = {}             # 预设名 -> 该预设文件的指纹（mtime_ns, size）


def _file_sig(name):
    r"""**预设文件本身的指纹**（`os.stat` 的 mtime_ns + size）：缓存键不含它 ⇒ 一次会话里改了
    预设 JSON **程序察觉不到**（必须重启进程），而且它会**静默污染所有测量**。"""
    try:
        st = os.stat(path_of(name))
        return (st.st_mtime_ns, st.st_size)
    except Exception:                                  # noqa: BLE001
        return None


def _params_for(name, cfg):
    r"""按 **(预设名, config 指纹, 预设文件指纹)** 缓存。

    ★★ 必须带 config 指纹，否则第一次调用者的 cfg 会被**永久污染**（换 cfg 再跑**静默无效**）。"""
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
    """手动作废参数缓存（**通常不需要** —— `_params_for` 会按指纹自动作废），留作显式口子。

    ★ **故意不动 `_WHITE_GATE_WARNED`**：去重表是**进程级**的，跟着清空 = 常驻服务里刷屏。"""
    with _LOCK:
        _PARAMS.clear()
        _PARAMS_FSIG.clear()
        _PARAMS_SIG[0] = None


def digested(name, cfg=C, apply_specifics=None):
    r"""按预设建 params **并消化**，返回真正进管线的那一份（给自检用）：`_params_for()` 是**消化
    之前**的（配平基准 / 密度曲线 / `halation_strength` 在 digest 里才改）⇒ 验错对象就"假绿"。"""
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
# **以人为本的测光入口**（`subject`）
# ---------------------------------------------------------------------------
# **只能改「看哪里」（测光方式，输入侧），不能改「看多少」（曝光量，输出侧）** —— 后者是
# "读统计量 → 改全局参数"的闭环，**必然振荡**（引擎的锚是写死的 `0.184`，不读图 ⇒ 不振荡）。
# ⚠ 弃权是安全侧：没检出人 ⇒ 退回普通 `render()`。
# ===========================================================================
def render_metered(lin, name, cfg=C, print_exposure=None, print_profile=None,
                   overrides=None, person_mask=None):
    r"""与 `render()` 同签名，但**测光看人**（`camera.auto_exposure_method="subject"`）：
    **拿不到人 / 装不上 shim ⇒ 自动退回普通 `render()`**（弃权是安全侧）。"""
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
    # ★ **必须直接赋值，不能 setdefault**：`_scene_engine["*"]` 里本来就带着
    #   `camera.auto_exposure_method`。
    ov['camera.auto_exposure_method'] = 'subject'
    _EX.set_mask(m)
    try:
        return render(lin, name, cfg, print_exposure, print_profile, ov)
    finally:
        _EX.clear()


def render(lin, name, cfg=C, print_exposure=None, print_profile=None, overrides=None):
    r"""喂**场景线性**，出**显示域**（与 `spektra.render()` 同契约，可互换）。

    `print_exposure` 不传 = 「预设的 pe × `SPEK_PE_SHIFT`」；`overrides` = {字段路径: 值}，
    **跑完一定还原**、取值不对**当场报错**（不静默）。
    ★★ **`overrides` 是"整份替换"、不是"叠加"** ⇒ **别只传一根**：它的产出 = `"*"` 基线层 ＋
      场景轴覆盖；只传"要动的那一根" = **丢了整条基线** ⇒ `…density_curves_morph` **整根死掉**
      （输出与不带 override 在噪声内相同）。✅ `ov = presets._base_overrides(cfg, stock=预设名)`。
      ⚠ 命中时只**警告**、不自动补 `active`（自动补 = 悄悄改画面）。"""
    p = _params_for(name, cfg)
    _warn_if_morph_dead(overrides)

    # ★★★ **按预设名加锁**：「临时改写共享的 `p` → 跑 → 还原」在多线程服务下必须串行；
    #   ★ 测光看人用**副本** `dict(overrides)` 注入（不改调用方的字典）。
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
    @returns `(overrides_to_use, mask_or_None)`；返回了掩膜 ⇒ 调用方**必须** clear。"""
    # ★★★ **中灰锚必须无条件装上** —— 关掉"测光看人"不该让锚静默消失（锚是整条链的曝光主人）；
    #   装不上也要留痕迹（不许静默）。
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
        _m, _mf = None, None
        try:
            _m, _mf = _P.person_face(_disp)
        except Exception:                                                    # noqa: BLE001
            _m, _mf = _P.person(_disp), None
        if _m is None:
            return overrides, None
        # ★★★ **掩膜占比守卫 [0.005, 0.70]**（按 `m>0.5` 算 —— 它返回的是 **0~1 浮点置信度**、
        #   不是布尔掩膜）：整幅掩膜 = 分割失败 ⇒ 会让"测光看人"退化成"按整幅测光"却看着合理
        #   ⇒ **超出即弃权**并留 stderr 痕迹。
        _cov = float(_np.mean(_np.asarray(_m) > 0.5))
        if not (0.005 <= _cov <= 0.70):
            import sys as _sys
            print('[svFilm 警告] presets._person_metering: 人掩膜占比 %.3f 不合理 '
                  '⇒ **弃权，改走原测光路径**（本次不做"测光看人"）' % _cov, file=_sys.stderr)
            return overrides, None
        _ov = dict(overrides)
        _ov['camera.auto_exposure_method'] = 'subject'
        # ★★★ **有脸就用脸**（二值 `>0.5` 的占比落在 `[EXPOSURE_FACE_MIN_COVER, …_MAX_COVER]`）：
        #   `r2(颊 ~ 脸)` 二值 **0.890** / 软 **0.310** ⇒ 用二值；两档共用 `ev' = (1-w)·ev + b`。
        _use, _reg = _m, 'person'
        if bool(getattr(cfg, 'EXPOSURE_METER_FACE', False)) and _mf is not None:
            _fcov = float(_np.mean(_np.asarray(_mf) > 0.5))
            if (float(getattr(cfg, 'EXPOSURE_FACE_MIN_COVER', 0.005)) <= _fcov
                    <= float(getattr(cfg, 'EXPOSURE_FACE_MAX_COVER', 0.50))):
                # ★ 口径必须是**二值 >0.5**（见 config 注记：软口径 r2 只有 0.310）
                _use, _reg = (_np.asarray(_mf, _np.float64) > 0.5).astype(_np.float32), 'face'
        _EX.set_mask(_use, _reg)
        return _ov, _use
    except Exception:                                                    # noqa: BLE001
        return overrides, None


def _post_scan(out, cfg):
    r"""**扫描段后处理**：目前只有 `scanfx.chroma_blur`（**扫描色度模糊** —— 属于"扫描"不属于调用方）。

    ★ 异常口径：**吞掉但留痕迹**（stderr + `_POST_SCAN_ERROR`）—— 绝不抛（常驻服务是批处理的）。"""
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
# **像素键统一缩放**（`film_render.grain.blur` · `scanner.lens_blur`）
# ---------------------------------------------------------------------------
# 「像素键」= σ 用**绝对像素**、作用在**整幅图**上 ⇒ 图越小糊得越狠 ⇒ **预览与交付是两个器件**。
# ⚠ `f == 1.0` 时**早退**（不写回、不乘）⇒ 参数逐字段不变；**验"逐位不变"要用参数级 SHA256
#   指纹**（`print_render.glare` 走 numba RNG，不可复现）。
_PX_SCALED_KEYS = (
    # (运行时字段路径, 基准长边的 cfg 名, 门控 —— 返回 False 表示"这根现在不参与")
    ('film_render.grain.blur', 'GRAIN_BLUR_REF_LONG_SIDE',
     lambda p: bool(getattr(p.film_render.grain, 'active', False))),
    ('scanner.lens_blur', 'LENS_BLUR_REF_LONG_SIDE', None),
)


def _scale_px_keys(p, lin, cfg, _ovs):
    r"""把「绝对像素」的 σ 键按 `实际长边 ÷ 基准长边` 缩放 ⇒ **预览 = 交付物**。

    ⚠ `grain.blur` 模糊的是**整张画面**（`grain.py:104-106` 返回输入密度的无偏采样 ⇒ 是"整幅
      柔度"）；`scanner.lens_blur` 在 vendor 里**无 µm 换算**（`scanning.py:124`）⇒ 同一个数在
      两条路径上是**两个器件**。
    ★ 契约：基准尺寸早退；`p` 共享 ⇒ 旧值记进 `_ovs` 由 finally **逆序还原**；失败**跳过不抛**。"""
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
    r"""把 halation 的**绿层核**收窄成红层的 `cfg.HALATION_G_SIGMA_RATIO` 倍：物理是**近橙远红**，
    而 `diffusion.py:72-75` 三通道共用 σ ⇒ 模型表达不出它。

    ⚠ 值**只在 config、钩子派生**（两处写值 = 两个真相）；只改 G，**R/B 与 `halation_strength`
      一个字都不动**（后者是物理基线）。"""
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

        # ★ 场景覆盖放**最后**；★★ frozen dataclass（`PrintCurvesMorphParams`）只能
        #   `dataclasses.replace` 整体替换，setattr 会 `FrozenInstanceError`。
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

        # ★★ 像素键缩放（见 `_PX_SCALED_KEYS`）必须在 overrides **之后**（场景覆盖可能改过它们）。
        _scale_px_keys(p, lin, cfg, _ovs)

        # ★★ halation 绿层核收窄，同样在 overrides 之后 —— 要读**最终**那份 σ。
        _halation_kernel_shape(p, cfg, _ovs)

        out = _simulate_once(p, np.clip(np.asarray(lin, np.float64), 0.0, None),
                             bool(getattr(cfg, 'PRESET_APPLY_STOCK_SPECIFICS', False)))
    finally:
        # ★ **逆序**还原：同一字段可能被记两次（overrides / 钩子）⇒ 顺序还原会留最后一次的值。
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
    """按【父段】走到持有者，返回 `(父对象, 键, 持有者)` —— 供"整体替换"用。"""
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
_SIM_LOCK = threading.Lock()          # `_SIM[0]` 的初始化原先**无锁**（裸双检）


def _simulate_once(p, lin, apply_specifics=False):
    """惰性拿 simulate，并**显式指定** `apply_stocks_specifics`（值来自 config）：
    `simulate()` 内部还会再 digest 一次，而它的默认会按卷的抗晕层标签重写 `halation_strength`。"""
    if _SIM[0] is None:
        with _SIM_LOCK:                     # 初始化挪进锁里（双检仍在锁内）
            if _SIM[0] is None:
                spektra = __import__(__name__.rsplit('.', 1)[0] + '.spektra', fromlist=['x'])
                _init_params, _simulate = spektra._sf()
                from spektrafilm.runtime.params_builder import (clone_runtime_params,
                                                               digest_params)

                def _run(image, params, _specifics, **kw):
                    # ★★★ `digest_params_first=False` 必须给：否则 `simulate` 会**再 digest 一次**
                    #   （`apply_stocks_specifics=True`）⇒ `_apply_halation_preset` 把
                    #   `halation_strength` 重写回卷的出厂值。
                    # ★★★ digest 前**先克隆**：`digest_params()` **就地改写**传入对象，而它是**跨调用
                    #   共享的缓存对象** ⇒ 一旦改了字段（`preview_mode=True` 会清零 grain /
                    #   couplers.diffusion / scanner.unsharp）⇒ **之后每张图静默无颗粒/无锐化**。
                    return _simulate(image,
                                     digest_params(clone_runtime_params(params),
                                                   apply_stocks_specifics=bool(_specifics)),
                                     digest_params_first=False, **kw)
                _SIM[0] = _run
    return _SIM[0](lin, p, bool(apply_specifics), print_timings=False)


def render_copy(lin, name, cfg=C, **kw):
    """调试用：每次都从 JSON 重新建 params（不走缓存），排除"缓存里是旧值"。

    ★ 整段进**同一把按预设的锁**：老写法只 `pop` 不入锁 ⇒ 并发时同一预设会存在**两份 params**。"""
    with _lock_of(name):
        with _LOCK:
            _PARAMS.pop(name, None)
        return render(lin, name, cfg, **kw)
