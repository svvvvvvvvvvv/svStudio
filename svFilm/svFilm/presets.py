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

# ★★★ **上一次「按脸反解印相曝光」的决策留痕**（`render_person_pe` 写、`pipeline` 读进报告）。
#   它**不影响像素**，只是把"这张到底动没动、动了多少"暴露给调用方 / 自检（否则两遍印相一次都没动
#   也看不出来）。形状：`dict(back, L0, target, pe0, pe1, acted, why)` 或 `None`（没走这条路）。
_LAST_PERSON_PE: dict | None = None
# ★★ **上一遍印相真正写进 params 的 `print_exposure`**（`_render_locked` 写，`render_person_pe` 读）：
#   别处重算（`pe_of()·SPEK_PE_SHIFT`）看不见 overrides / 归一后的实际值 ⇒ 报告与画面会两张皮。
_LAST_RENDER_PE: float | None = None

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
# `…print_render.chemistry.active` 等基线键在 `targets._scene_engine["*"]`（基线层）、不在预设
# JSON 里 ⇒ 只传"要动的那一根"会**丢掉整条基线** ⇒ **整块 chemistry 回默认**（frozen dataclass
# 是整体替换），而输出与"不带 override"在噪声内相同（看起来像"这根旋钮没用"）⇒ 见 `_base_overrides()`。
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
    '滨田预设': ('滨田预设',
              '照滨田正和的作品立靶（22 张）；6×7 画幅、略偏暖；10-17 立，整批判分 18/22'),
    'xhs预设': ('xhs预设',
             '照小红书「不詹锅」的作品立靶（50 张，水印压脸的 5 张已弃）；Minolta Capios 75 + CP200 的取向，'
             '卷用 kodak_gold_200 代（盘上无 ColorPlus 200）；10-18 立，整批判分 15/22'),
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


def _chemistry(dst, src):
    r"""把预设的 `film_chemistry` / `print_chemistry` 灌进 frozen dataclass（缺省 = 不动）。

    字段 = dev 的 `FilmChemistryParams` / `PrintChemistryParams`（`utils/morph_curves.py`）：
    6 个 γ 逐层相乘；`active=False` ⇒ 直接回拟合曲线；`developer_exhaustion` 走 Gumbel 混合。
    """
    if not src:
        return dst
    upd = {}
    for k in ('active', 'gamma_factor', 'gamma_factor_fast', 'gamma_factor_slow',
              'gamma_factor_red', 'gamma_factor_green', 'gamma_factor_blue',
              'developer_exhaustion'):
        if k in src:
            upd[k] = bool(src[k]) if k == 'active' else float(src[k])
    if 'development_time' in src:
        upd['development_time'] = (None if src['development_time'] is None
                                   else float(src['development_time']))
    return dataclasses.replace(dst, **upd) if upd else dst


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

    # ---- 胶片端化学：dev 原生 `film_chemistry`（frozen；identity = γ 全 1.0）----
    p.film_render.chemistry = _chemistry(p.film_render.chemistry, d.get('film_chemistry'))


    # ---- camera ----
    p.camera.lens_blur_um = float(si['camera_lens_blur_um'])
    p.camera.exposure_compensation_ev = float(si['exposure_compensation_ev'])
    p.camera.auto_exposure = bool(si['auto_exposure'])
    p.camera.auto_exposure_method = si['auto_exposure_method']
    p.camera.film_format_mm = float(si['film_format_mm'])
    # dev：相机取景滤镜 = 滤镜库名字（`model/color_filters.py`；`"none"` = 不加）
    p.camera.color_filter = str(ii.get('color_filter', 'none'))

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

    # ---- 印相端化学：dev 原生 `print_chemistry`（frozen；γ 逐层相乘）----
    #   机制（`utils/morph_curves.py:255-283`）：按子层感光度 μ 分 fast/mid/slow，
    #   `μ'=μ/g`、`σ'=max(σ/g,0.05)` ⇒ `D'(x)=D(g·x)` ⇒ **g↑ ⇒ 反差↑**；
    #   `g_mid = g_slow`（实际只有 2 段）。逐通道 γ 是**改中性轴黄绿**的刀（绕 L*≈54 旋转）。
    p.print_render.chemistry = _chemistry(p.print_render.chemistry, d.get('print_chemistry'))

    # ---- 输出色域压缩的"压顶"膝（扫描段）—— **可选键**：缺省 ⇒ 吃引擎 schema 默认 (0.7, 1.0, 2.2)
    #   机制（`utils/gamut_compression.py:297-304`，ACES RGC 的 Reinhard knee）：
    #     `threshold` 越低 ⇒ 高光压得越狠、越容易挤成一坨贴顶的白（clip% 上升、p95 反而低）；
    #     `threshold` 越高 ⇒ 白端越高、跨度越大。实测（2 张，中位 p95 / clip%）：
    #       0.70 → 90.4 / 0.06% ｜ 0.75 → 91.8 ｜ 0.80 → 93.4 / 0.16% ｜ 0.85 → 95.0 ｜ 0.90 → 96.6
    #   ⇒ 它是**白端唯一有效的单根**（+21 L*/单位）。⚠ 改它 = 改"什么形状" ⇒ 目标性（要拍板）。
    #   ⚠ 传 `"off"` = 关掉压顶（超白会被显示端裁掉，clip% 飙到 8% ⇒ 只给诊断用）。
    #   ★ 同组还有**彩度膝** `knee`（`(threshold, limit, power)`，默认 (0.0,1.0,6.0)）：
    #     它是"按 `C/C_max(L,h)` 选择性"压彩度 ⇒ **幂越低咬得越早**。实测（11 张整批）：
    #     幂 6→3 ⇒ 22 条落带 10→**11**（`脸C*` 从压顶 26.59 拉回带内）；
    #     幂 6→10 ⇒ 9（反向过头）；⚠ 它对 `A4 中/亮` 与 `脸C*` 是**反向拉扯**（实测）。
    _lcv = si.get('output_lightness_compression')
    if _lcv is not None:
        _lc = None if str(_lcv) == 'off' else tuple(float(v) for v in _lcv)
        try:
            p.io.output_gamut_compress.lightness_compression = _lc
        except dataclasses.FrozenInstanceError:
            p.io.output_gamut_compress = dataclasses.replace(
                p.io.output_gamut_compress, lightness_compression=_lc)
    _kn = si.get('output_gamut_knee')
    if _kn is not None:
        try:
            p.io.output_gamut_compress.knee = tuple(float(v) for v in _kn)
        except dataclasses.FrozenInstanceError:
            p.io.output_gamut_compress = dataclasses.replace(
                p.io.output_gamut_compress, knee=tuple(float(v) for v in _kn))

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

    # ---- grain：dev 原生 RMS 粒度高（48 µm 孔径、净密度 1.0）----
    #   `rms_granularity` 是**规格**（千分之一密度）：引擎按 profile 的实际子层曲线反解
    #   最粗子层面积（`grain.py:_coarsest_area_from_curves`）⇒ 与出图尺寸无关。
    #   `blur` / `mult_usm_sigma` 是**像素**单位（随出图尺寸变）；本预设 blur 0.65 +
    #   mult_usm_amount 0（不叠锐化）—— 这是**我们的值**，不是 schema 默认（0.8 / 1.5）。
    g = p.film_render.grain
    g.active = bool(gr['active'])
    g.rms_granularity = tuple(float(v) for v in gr['rms_granularity'])
    g.particle_scale_sublayers = tuple(float(v) for v in gr['particle_scale_sublayers'])
    g.density_min = tuple(float(v) for v in gr['density_min'])
    g.uniformity = tuple(float(v) for v in gr['uniformity'])
    g.blur = float(gr['blur'])
    g.mult_usm_sigma = float(gr.get('mult_usm_sigma', 0.7))
    g.mult_usm_amount = float(gr.get('mult_usm_amount', 0.0))
    g.blur_dye_clouds_um = float(gr.get('blur_dye_clouds_um', 2.0))
    g.micro_sublayers = int(gr.get('micro_sublayers', 1))
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
    # dev：Langmuir 饱和（供体 `g(D)=D(K+D_ref)/(K+D)`，`K=k·d_max`、`D_ref=d_max/2`）。
    #   K=1.0 = 引擎默认（低密度端更陡、高密度端滚降）；K=∞ = 纯线性。
    #   ★ 本预设**重标在 K=1.0**（`amount` 同批重扫），不再用 ∞ 复刻旧线性响应。
    c.langmuir_donor_k_rgb = tuple(
        float(v) for v in co.get('langmuir_donor_k_rgb', (1.0, 1.0, 1.0)))
    c.langmuir_receiver_k_rgb = tuple(
        float(v) for v in co.get('langmuir_receiver_k_rgb', (1.0, 1.0, 1.0)))
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
    # ★★ 配平基准 = **引擎数据库**（按 纸 / 放大机光源 / 卷 三维取）。
    #   dev 起**没有硬编码回退**：中性值只能按 (纸, 光源, 卷) 查 DB —— "拿别人的中性值配自己的底片" 是错的。
    p.settings.neutral_print_filters_from_database = True
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

    # ---- 「以人为本」的动态印相曝光（**可选键**：缺省 ⇒ 一个字段都不碰）----
    #   机制：这 6 根**不是引擎字段**（两遍印相是 svFilm 自己的事），所以不灌进任何 dataclass，
    #   而是挂成 `params.settings.person_pe` 这个运行期 dict（`SettingsParams` 无 `__slots__`），
    #   由 `render_person_pe` 读。落点必须**按预设**给：`config.PERSON_PE_*` 是**全局**的
    #   （别的调用方还在读它）⇒ 给一条风格改落点 = 连带改掉所有风格的落点。
    #   ★ 只在键存在时才建这个 dict ⇒ 缺省 = 属性不存在 = `render_person_pe` 逐位回到 `cfg.*`。
    #   ★ 在这里定类型（float/bool/二元组）：JSON 是弱类型的，读的那头不该再猜一遍。
    _ppe = {}
    for _k, _cast in (('person_pe_dynamic', bool), ('person_pe_target_l', float),
                      ('person_pe_slope', float), ('person_pe_max_dp', float),
                      ('person_pe_back_gain', float)):
        if _k in si:
            _ppe[_k] = _cast(si[_k])
    if 'person_pe_band' in si:
        # `false` = **关掉合理窗**（与 `render_person_pe(band=False)` 同一语义），其余按二元组收
        _b = si['person_pe_band']
        _ppe['person_pe_band'] = False if _b is False else tuple(float(v) for v in _b)
    if _ppe:
        p.settings.person_pe = _ppe


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
      场景轴覆盖；只传"要动的那一根" = **丢了整条基线** ⇒ `print_render.chemistry` 的覆盖
      只落在孤立值上（输出与不带 override 在噪声内相同）。
      ✅ `ov = presets._base_overrides(cfg, stock=预设名)`。"""
    p = _params_for(name, cfg)

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
        if _m is None and _mf is None:
            return overrides, None
        _ov = dict(overrides)
        _ov['camera.auto_exposure_method'] = 'subject'
        # ★★★ 判定顺序：**①一级的脸 → ②第二级找脸兜底 → ③人档 → 弃权**。
        #   ★ ①成事 ⇒ ②一次都不跑 ⇒ **其余图逐位不变**。
        _use, _reg, _ref = None, 'person', None

        # ---- ① 一级：分割模型的 `FACE` 类（"脸上的皮肤"）----
        #   **有脸就用脸**（二值 `>0.5` 的占比落在 `[EXPOSURE_FACE_MIN_COVER, …_MAX_COVER]`）：
        #   `r2(颊 ~ 脸)` 二值 **0.890** / 软 **0.310** ⇒ 用二值；两档共用 `ev' = (1-w)·ev + b`。
        #   ★ 口径必须是**二值 >0.5**（见 config 注记：软口径 r2 只有 0.310）。
        if bool(getattr(cfg, 'EXPOSURE_METER_FACE', False)) and _mf is not None:
            _fcov = float(_np.mean(_np.asarray(_mf) > 0.5))
            if (float(getattr(cfg, 'EXPOSURE_FACE_MIN_COVER', 0.005)) <= _fcov
                    <= float(getattr(cfg, 'EXPOSURE_FACE_MAX_COVER', 0.50))):
                _use, _reg = (_np.asarray(_mf, _np.float64) > 0.5).astype(_np.float32), 'face'

        # ---- ② ★ 第二级：找脸兜底（只在①没成时跑）----
        #   用**专门的人脸检测器**回答"脸在哪" → 按框裁块 → **同一个分割模型**再认一次皮肤
        #   ⇒ 口径与①同源（所以锚 `w/b` 不用重标）。`_ref` = 裁块面积，交给 `exposure.set_mask`
        #   当覆盖率分母 —— 小脸本来只占画面千分之几，拿整幅当分母必然被 `_MIN_MASK_COVER` 误判。
        if _use is None and bool(getattr(cfg, 'FACE_FALLBACK', False)):
            try:
                _fb = _P.face_fallback(_disp)
            except Exception:                                                # noqa: BLE001
                _fb = None
            if _fb is not None:
                _use, _reg, _ref = _fb[0], 'face', _fb[1]
                print('[svFilm] presets._person_metering: 一级没拿到脸档 ⇒ **第二级（找脸兜底）接手**，'
                      '覆盖率分母改用检测框面积 %d px' % _ref, file=sys.stderr)

        # ---- ③ 人档（脸两级都没成、但"人在哪"合理）----
        # ★★★ **掩膜占比守卫 [0.005, 0.70]**（按 `m>0.5` 算 —— 它返回的是 **0~1 浮点置信度**、
        #   不是布尔掩膜）：整幅掩膜 = 分割失败 ⇒ 会让"测光看人"退化成"按整幅测光"却看着合理
        #   ⇒ **超出即弃权**并留 stderr 痕迹。
        if _use is None:
            if _m is None:
                return overrides, None
            _cov = float(_np.mean(_np.asarray(_m) > 0.5))
            if not (0.005 <= _cov <= 0.70):
                print('[svFilm 警告] presets._person_metering: 人掩膜占比 %.3f 不合理 '
                      '⇒ **弃权，改走原测光路径**（本次不做"测光看人"）' % _cov, file=sys.stderr)
                return overrides, None
            _use, _reg = _m, 'person'

        _EX.set_mask(_use, _reg, cover_ref=_ref)
        return _ov, _use
    except Exception:                                                    # noqa: BLE001
        return overrides, None


# ★★★ `print_exposure` 的**两个写法**（运行时字段路径 / 预设 JSON 里的段名）—— 都是"印相曝光"。
#   `render_person_pe` **独占**这个字段：`_render_locked` 是"先设 `print_exposure`、后应用 overrides"
#   ⇒ overrides 里再写一次就把第二遍印相**顶掉**，而 `_LAST_PERSON_PE` 照旧报 `acted=True`
#   （静默无效 + 报告说谎）⇒ 检出即抛，**不许默默忽略**。
_PE_OVERRIDE_KEYS = ('enlarger.print_exposure', 'simulation.print_exposure')


def _pe_override_clash(overrides):
    r"""`overrides` 里有没有"印相曝光"这个键（有 ⇒ 返回命中的键名，没有 ⇒ None）。"""
    if not isinstance(overrides, dict):
        return None
    for _k in overrides:
        if str(_k).strip() in _PE_OVERRIDE_KEYS:
            return str(_k).strip()
    return None


def _validate_override_paths(p, name, overrides):
    r"""**跑之前**把 `overrides` 的每个键路径在 params 上走一遍；非法 ⇒ 立刻 `KeyError`（带键名）。

    ★ 为什么前置：`_render_locked` 里那句校验要**等到真正应用 overrides** 才执行 ⇒ 错键在
      "第一遍印相"里才炸，错误从渲染机制深处冒出来，调用方看不出是**自己给错了键**。
    ★ 只读：一个字段都不写（真正应用 + 逆序还原仍归 `_render_locked`）。"""
    if not isinstance(overrides, dict):
        return
    for _dotted in overrides:
        _parts = [s for s in str(_dotted).split('.') if s]
        if not _parts:
            raise KeyError('overrides 的字段路径是空的')
        _holder = p
        for _k in _parts[:-1]:
            if not hasattr(_holder, _k):
                raise KeyError('overrides 的字段路径走不通: %s（在 %r 处断了）'
                               % (_dotted, _k))
            _holder = getattr(_holder, _k)
        if not hasattr(_holder, _parts[-1]):
            raise KeyError('overrides 里这个字段不存在: %s（预设 %s）' % (_dotted, name))


def _print_pe_error(stage, exc):
    r"""`render_person_pe` 出错时的**一行 stderr**（`_LAST_PERSON_PE` 里的 `why` 只有程序读）；
    ★ 失败**不许静默** —— 静默的话现场只看到"第二遍没生效"，查不到任何线索。"""
    try:
        print('[svFilm] presets.render_person_pe: **%s失败** ⇒ 本张「按脸反解印相曝光」未完成：'
              '%s: %s' % (stage, type(exc).__name__, str(exc)[:200]), file=sys.stderr)
    except Exception:                                                    # noqa: BLE001
        pass


def render_person_pe(lin, name, cfg=C, print_profile=None, overrides=None,
                     target_L=None, band=None, back=None):
    r"""★★ **按脸反解印相曝光**（两遍印相 = 暗房的"试条"）—— 以人为本的**落点**。

    先把这张按预设的 `print_exposure` 正常印一遍，**量成片里那张脸有多亮**（用与测光**同一块**
    分割 `FACE` 掩膜）；脸掉出**合理窗** ⇒ 按实测斜率反解一个新的 `print_exposure`，**再印一遍**。
    脸量不到（弃权）⇒ 原样返回第一遍（= 退回普通 `render()`）。

    ★★ 为什么必须"印一遍量一遍"：**负片测光那一层对成片的脸亮度几乎无影响** —— 实测 14 张，
    脸的测光结果与成片脸 L* 只有 `r2 = 0.17`；而**输入**脸亮度更预测不了成片脸 L*（`r2 = 0.08`，
    因为印相端的中灰归一化把负片层的改动吃掉了）⇒ 脸最后落在哪，**零渲染推不出来**。
    能搬动脸的开关只有 `print_exposure`（实测 `d(脸L*)/d(pe) = −79`）。
    ★ 出处：暗房彩色分析仪（用标准负片试印 → 换片后把探头压在**脸颊**上读数）；
    专利 CN109918993B「使人脸区域亮度值趋于一个曝光目标值」；CN112584089B「人脸亮度误差阈值」。
    ★ 它是**单张一次求解**（没有跨帧反馈）⇒ 不存在"读统计量反馈全局参数"那种振荡。

    @param target_L {float|None} 目标 L*（None = 该预设的可选键 `person_pe_target_l`，
        再缺省 = `cfg.PERSON_PE_TARGET_L`），口径 = 测光掩膜。
        ★★ **显式给的**（形参 / 预设键）= **把合理窗的下沿抬到它**（`lo = max(lo, target)`、
        `hi = max(hi, lo)`）⇒ 顺光时目标就是它，逆光仍沿窗推到亮端。
        **只有 `cfg` 的缺省值不算显式**（不碰窗 ⇒ 没显式键的预设逐位保持原行为）。
    @param band {tuple|None|False} 合理窗；`False` = **关掉窗、逐图拉到 target**；
        `None` = 该预设的可选键 `person_pe_band`，再缺省 = `cfg.PERSON_PE_BAND`；`(lo, hi)` = 给定窗
    @param back {float|None} ★★★ **光位 → 力度**（0~1 = 逆光程度，取 `scene.light_evidence`
        的 `deg_back`）。脸管"差多少"，**光位只管"能使多大劲"**：
        · **只有脸真的暗（`L < 窗下沿`）才动手，且只提不压**（`print_exposure` 是全局的）
        · 逆光 ⇒ 提亮目标上移到窗的**亮端**、单张上限放大（背景反正要曝，脸不能暗）
        · 顺光（`back≈0`）⇒ **逐位回到旧行为**（目标 = 下沿、上限 = `MAX_DP`）
    ★★ 六个旋钮（目标 / 带 / 斜率 / 上限 / 逆光增益 / 总开关）都有**两条来源**，优先级 =
        **形参 > 本预设的可选键（`_apply` 收的 `simulation.person_pe_*`）> `cfg.PERSON_PE_*`**。
        预设那层是给"**每条风格自己的落点**"用的（不同大师的脸本来就落在不同的 L*）；
        `cfg` 那层是全局缺省，**别在这里改**（其它调用方还在读它）。
    @returns {ndarray} 显示域成片（与 `render()` 同契约）
    @raises ValueError `overrides` 里带了 `enlarger.print_exposure` / `simulation.print_exposure`
        —— 那会顶掉第二遍印相（静默无效），要指定落点请用形参 / 预设键
    @raises KeyError `overrides` 的键路径在 params 上走不通（**跑之前**就抛，报出键名与断点）
    @raises Exception 第一遍 / 第二遍印相内部的异常 —— **记账 + stderr 一行 + 抛出**，不许吞
    """
    global _LAST_PERSON_PE, _LAST_RENDER_PE
    _LAST_PERSON_PE = None                                 # ★ 先清账（没走这条路 = None）
    _LAST_RENDER_PE = None                                 # ★ 同上（第一遍印过之后才有值）
    _clash = _pe_override_clash(overrides)
    if _clash is not None:
        raise ValueError(
            'overrides 里带了 %r ⇒ 会**禁用本函数的动态印相曝光**：第二遍印相算出的 '
            '`print_exposure` 会被它顶掉，而 `_LAST_PERSON_PE` 仍报 acted=True（静默无效）。'
            '要指定落点，请走**形参 / 预设键**（`target_L=` / `band=` / `back=`，或预设里的 '
            '`person_pe_target_l` / `person_pe_band`）；要自己钉死某一遍的印相曝光，请改用 '
            '`presets.render(..., print_exposure=…)`。' % _clash)
    # ★★ **跑之前**先把 overrides 的键路径验一遍（错键立刻抛并指出键名）
    _p = _params_for(name, cfg)
    _validate_override_paths(_p, name, overrides)
    from . import color as _col, exposure as _EX
    _lin = np.clip(np.asarray(lin, np.float64), 0.0, None)
    _stage = ['第一遍印相']            # 出错时打哪一句（下面**只**有一个 except，别重复喊）
    try:
        # ★★★ **第一遍印相也在 try 里**：它一炸，`_LAST_PERSON_PE` 原来是一片空白
        #   （连"为什么没走两遍"都记不下来），调用方只看到一个从渲染机制深处冒出来的异常。
        out = render(lin, name, cfg, print_profile=print_profile, overrides=overrides)
        # ★★ 本预设给的那一份（`_apply` 收的**可选键**）：缺省 ⇒ 空 dict ⇒ 逐位回落 `cfg.*`。
        #    ⚠ 挂在 `params.settings.person_pe` 上，不是 dataclass 字段（见 `_apply` 那段）。
        _ppe = getattr(getattr(_p, 'settings', None), 'person_pe', None) or {}
        if not bool(_ppe.get('person_pe_dynamic', getattr(cfg, 'PERSON_PE_DYNAMIC', False))):
            return out
        _ov, _m = _person_metering(_lin, {}, cfg)
        if _m is None:
            _LAST_PERSON_PE = dict(back=None, why='no_face', acted=False)
            return out                                     # 脸量不到 ⇒ 不动（安全侧）
        H, W = out.shape[:2]
        _mm = np.asarray(_m, np.float64)
        if _mm.shape[:2] != (H, W):
            import cv2
            _mm = cv2.resize(_mm, (W, H), interpolation=cv2.INTER_AREA)
        _sel = _mm > 0.5
        if int(_sel.sum()) < 30:
            _LAST_PERSON_PE = dict(back=None, why='mask_too_small', acted=False)
            return out
        _lab = _col.to_lab(np.clip(np.asarray(out, np.float64), 0.0, 1.0))
        _L = float(np.median(_lab[..., 0][_sel]))

        # ★★★ **光位 → 力度**：`back` = 逆光程度（0~1）。逆光时**提亮目标上移**（窗内也往亮端推）
        #   —— 用户令「**脸不能暗 暗就不好看**」+「**逆光时 背景肯定会有些曝 人也得多提亮**」。
        #   ⚠ 顺光 `back≈0` ⇒ `_up == 下沿` ⇒ 窗内的脸 **一个像素不动**（老行为逐位保留）。
        _bk = 0.0 if back is None else max(0.0, min(1.0, float(back)))
        # ★★★ **显式目标 vs 缺省目标**（必须分清，否则 `target_L` 是死键）：
        #   · **显式** = 形参 `target_L` 或本预设的可选键 `person_pe_target_l` ⇒ 语义 = **把窗口下沿抬到它**
        #   · **缺省** = `cfg.PERSON_PE_TARGET_L` ⇒ 只当"没有窗时的落点"，**不参与**抬下沿
        #     （否则每条风格都会被一个全局数顶掉自己带的窗 ⇒ 滨田的 band 立刻失效）。
        _tgt_e = target_L if target_L is not None else _ppe.get('person_pe_target_l')
        _tgt = float(_tgt_e if _tgt_e is not None
                     else getattr(cfg, 'PERSON_PE_TARGET_L', 66.96))
        if band is not None:                               # 形参最强（`False` = 关窗，见下）
            _band = band
        elif 'person_pe_band' in _ppe:                     # ★ 预设给的（缺省时**不在这个 dict 里**）
            _band = _ppe['person_pe_band']
        else:
            _band = getattr(cfg, 'PERSON_PE_BAND', (53.5, 76.0))
        if _band is False:
            _band = None                                   # 「关掉窗」= 逐图直接拉到 `_tgt`
        if _band is not None:
            _lo, _hi = float(_band[0]), float(_band[1])
            # ★★ **显式目标 = 把窗口下沿抬到它**（上沿跟着抬，保证 `lo <= hi`）：
            #    只抬下沿 ⇒ 顺光（`_bk≈0`）的目标就是它，逆光仍沿窗推到亮端 ⇒ 两种光位都不丢。
            if _tgt_e is not None:
                _lo = max(_lo, float(_tgt_e))
                _hi = max(_hi, _lo)
            # ★★★ **光位 → 目标**（`_bk` = 逆光程度）：顺光 ⇒ 只保下限（脸别再暗下去）；
            #     逆光 ⇒ 目标推到**亮端**（用户令「**逆光时 背景肯定会有些曝 人也得多提亮**」）。
            _up = _lo + (_hi - _lo) * _bk
            if _L >= _up:
                _LAST_PERSON_PE = dict(back=_bk, L0=round(_L, 2), band=[_lo, _hi],
                                       target=round(_up, 2), acted=False,
                                       why='bright_enough')
                return out                                 # ★★ **够亮 ⇒ 一个像素不动**
                #   —— 只做「脸不能暗」，所以**只提不压**（`print_exposure` 是**全局**的）：
                #     把"偏亮的脸"压回去 = 整张一起压暗（实测 DSCF1744 顺光片被压 −8.8 L*，
                #     起因就是掩膜把亮处当成脸）⇒ 压的收益小、风险大 ⇒ 不做。
            _tgt = _up
        _d = _tgt - _L
        #   ★ 符号：`print_exposure ↑ ⇒ 印得暗 ⇒ 脸 L* ↓`，实测 `d(脸L*)/d(pe) = −79`
        #   （1665：pe 0.620→0.579 实测脸 +3.19 L*）⇒ 要 ΔL*，就得 `Δpe = −ΔL*/79`。
        _sl = abs(float(_ppe.get('person_pe_slope',
                                 getattr(cfg, 'PERSON_PE_SLOPE', 79.0)) or 79.0))
        _mx = abs(float(_ppe.get('person_pe_max_dp',
                                 getattr(cfg, 'PERSON_PE_MAX_DP', 0.20)) or 0.0))
        # ★ 逆光敢使劲：单张上限随逆光程度放大
        _mx *= 1.0 + float(_ppe.get('person_pe_back_gain',
                                    getattr(cfg, 'PERSON_PE_BACK_GAIN', 1.0)) or 0.0) * _bk
        _dpe = max(-_mx, min(_mx, -_d / _sl))
        # ★★★ **只提不压 —— 由代码保证，不靠调用方自觉**：`_dpe > 0` = "脸比目标亮 ⇒ 想压"，
        #   而 `print_exposure` 是**整张一起动**的（压脸 = 压整幅，实测 DSCF1744 被压 −8.8 L*）。
        #   ⚠ 上面那道"窗内够亮就早退"只兜得住 `band` 那条路；`band=False`（关窗直拉目标）
        #     没有窗兜着 ⇒ 实测 `band=False + target_L=40` 把脸 69.5 → 56.74（方向正好反了）。
        if _dpe > 0.0:
            _LAST_PERSON_PE = dict(back=_bk, L0=round(_L, 2), target=round(_tgt, 2),
                                   dpe_asked=round(_dpe, 4), acted=False,
                                   why='positive_only')
            return out
        if abs(_dpe) < 1e-4:
            _LAST_PERSON_PE = dict(back=_bk, L0=round(_L, 2), target=round(_tgt, 2),
                                   acted=False, why='delta_tiny')
            return out
        # ★★ `_pe0` 取**第一遍真正用的那个 pe**（`_render_locked` 回传的 `_LAST_RENDER_PE`）：
        #    在这里重算 `pe_of()·SPEK_PE_SHIFT` 看不见 overrides / 归一后的实际值 ⇒ 报告与画面两张皮。
        _pe0 = _LAST_RENDER_PE
        if _pe0 is None:                                   # 兜底（正常路径走不到：第一遍刚印过）
            _sh = 1.0
            if bool(getattr(cfg, 'PRESET_PE_SHIFT_FROM_SPEK', True)):
                _sh = float(getattr(cfg, 'SPEK_PE_SHIFT', 1.0) or 1.0)
            _pe0 = pe_of(name) * _sh
        _pe0 = float(_pe0)
        _pe1 = _pe0 + _dpe
        _LAST_PERSON_PE = dict(back=_bk, L0=round(_L, 2), target=round(_tgt, 2),
                               pe0=round(_pe0, 4), pe1=round(_pe1, 4),
                               acted=True, why='applied')
        import sys as _sys
        print('[svFilm] presets.render_person_pe: 脸 L* %.1f ⇒ 目标 %.1f ⇒ 印相曝光 %.4f → %.4f'
              '（第二遍印）' % (_L, _tgt, _pe0, _pe1), file=_sys.stderr)
        _stage[0] = '第二遍印相'
        return render(_lin, name, cfg, print_exposure=_pe1, print_profile=print_profile,
                      overrides=overrides)
    except Exception as _e:                                              # noqa: BLE001
        # ★★★ **异常不许吞**：原来这里 `return out`（静默回第一遍，只在 `_LAST` 里留一个
        #   `why='error:…'`、stderr 一行都没有）⇒ 现场只看到"第二遍没生效"，查不到任何线索。
        _LAST_PERSON_PE = dict(back=None, acted=False,
                               why='error: %s: %s' % (type(_e).__name__, str(_e)[:160]))
        _print_pe_error(_stage[0], _e)
        raise
    finally:
        try:
            _EX.clear()
        except Exception:                                                # noqa: BLE001
            pass


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
    global _LAST_RENDER_PE
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

        # ★★ **记下这一遍真正写进 params 的印相曝光**（在 overrides 之后、离屏之前）：
        #    形参 `print_exposure` 可能被 overrides 顶掉 ⇒ 只有 params 上这个是引擎实际读的
        #    （`printing.py:54` 那句 `raw *= self._enlarger.print_exposure`）。
        _LAST_RENDER_PE = float(p.enlarger.print_exposure)
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
