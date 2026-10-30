# -*- coding: utf-8 -*-
r"""**从零生成一条 svFilm 预设 JSON** —— 值 = 引擎 schema 默认，只选「卷 + 纸」。

## 为什么有它（10-15）
旧 10 条预设已删，`鹿井预设` 要**从 0 重立**（用户拍板「完全从零开始」）。
"从零"必须可核对 ⇒ 本脚本是**唯一**的生成入口：
  ① 字段白名单 = `svFilm/presets.py::_apply()` 真正读的键（**逐行抄下来**，不靠猜）；
  ② 每个键的值 = `spektrafilm.runtime.init_params(film, print)` 的**出厂默认**；
  ③ 只有 `simulation.film_stock` / `simulation.print_paper` 是"选"的（其余一个都不许手改）；
  ④ 自动附 `_note_params`（逐键中文说明；唯一真源 = `_lib/preset_notes.py`）。
⇒ 生成的 JSON 与引擎默认**逐字段比对**（`verify()`），不一致就报出来。

## 死键（**不写进新预设**，理由见 `_DEAD_NOTE`）
`load_raw.*` 整段 · `special.print_gamma_factor` · `couplers.diffusion_tail_um` ·
`display.{use_display_transform,gray_18_canvas,white_padding,output_interpolation}` ·
`simulation.{auto_preview,saving_color_space,saving_cctf_encoding}` ·
`grain.{particle_scale_layers,blur_dye_clouds_um,micro_structure}`（`sublayers_active=False` 时引擎不读）。

## 用法
```python
import _lib.mkpreset as M
obj = M.build('kodak_ultramax_400', 'kodak_portra_endura')   # 值 = 引擎默认
M.verify(obj)                                                # 与默认逐字段比
M.write(r'...\data\presets\鹿井预设.json', obj)
```
CLI：`python -m _lib.mkpreset --film kodak_ultramax_400 --paper kodak_portra_endura --out <path>`
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

# ---------------------------------------------------------------------------
# 字段白名单：JSON 路径 -> (运行时属性路径, 缩放)
#   ★ 逐行抄自 `svFilm/presets.py::_apply()`（9xx 行那一段）——
#     改 `_apply` 必须回来改这里，否则 `verify()` 会把"新键没落地"当成噪音。
# ---------------------------------------------------------------------------
SCALE100 = 100.0        # GUI 里 ×100 的两组（halation 的两个字段）


def _map():
    m = {
        # ---- input_image ----
        'input_image.upscale_factor': ('io.upscale_factor', 1.0),
        'input_image.crop': ('io.crop', 1.0),
        'input_image.crop_center': ('io.crop_center', 1.0),
        'input_image.crop_size': ('io.crop_size', 1.0),
        'input_image.input_color_space': ('io.input_color_space', 1.0),
        'input_image.apply_cctf_decoding': ('io.input_cctf_decoding', 1.0),
        'input_image.spectral_upsampling_method': ('settings.rgb_to_raw_method', 1.0),
        'input_image.apply_hanatos2025_adaptation_window':
            ('settings.apply_hanatos2025_adaptation_window', 1.0),
        'input_image.apply_hanatos2025_adaptation_surface':
            ('settings.apply_hanatos2025_adaptation_surface', 1.0),
        'input_image.spectral_gaussian_blur': ('settings.spectral_gaussian_blur', 1.0),
        'input_image.color_filter': ('camera.color_filter', 1.0),
        # ---- simulation（引擎侧） ----
        'simulation.film_format_mm': ('camera.film_format_mm', 1.0),
        'simulation.camera_lens_blur_um': ('camera.lens_blur_um', 1.0),
        'simulation.exposure_compensation_ev': ('camera.exposure_compensation_ev', 1.0),
        'simulation.auto_exposure': ('camera.auto_exposure', 1.0),
        'simulation.auto_exposure_method': ('camera.auto_exposure_method', 1.0),
        'simulation.print_illuminant': ('enlarger.illuminant', 1.0),
        'simulation.print_exposure': ('enlarger.print_exposure', 1.0),
        'simulation.print_exposure_compensation': ('enlarger.print_exposure_compensation', 1.0),
        'simulation.print_y_filter_shift': ('enlarger.y_filter_shift', 1.0),
        'simulation.print_m_filter_shift': ('enlarger.m_filter_shift', 1.0),
        'simulation.scan_lens_blur': ('scanner.lens_blur', 1.0),
        'simulation.scan_white_correction': ('scanner.white_correction', 1.0),
        'simulation.scan_black_correction': ('scanner.black_correction', 1.0),
        'simulation.scan_unsharp_mask': ('scanner.unsharp_mask', 1.0),
        'simulation.scan_black_level': ('scanner.black_level', 1.0),
        'simulation.scan_white_level': ('scanner.white_level', 1.0),
        'simulation.output_color_space': ('io.output_color_space', 1.0),
        # ★ 10-16 新增：输出色域压缩的"压顶"膝（扫描段，白端唯一有效的单根；+21 L*/单位阈值）
        #   ⚠ `presets._apply` 里是**可选键**（缺省 ⇒ 吃引擎 schema 默认 0.7）
        'simulation.output_lightness_compression': (
            'io.output_gamut_compress.lightness_compression', 1.0),
        # ★ 10-16 新增：同一组的**彩度膝**（`(threshold, limit, power)`；幂↓ 咬得越早）
        'simulation.output_gamut_knee': ('io.output_gamut_compress.knee', 1.0),
        # ★ 印相端化学（dev：`print_render.chemistry`；`active` 默认 True）
        'print_chemistry.development_time': ('print_render.chemistry.development_time', 1.0),
        'print_chemistry.active': ('print_render.chemistry.active', 1.0),
        'print_chemistry.gamma_factor': ('print_render.chemistry.gamma_factor', 1.0),
        'print_chemistry.gamma_factor_fast': (
            'print_render.chemistry.gamma_factor_fast', 1.0),
        'print_chemistry.gamma_factor_slow': (
            'print_render.chemistry.gamma_factor_slow', 1.0),
        'print_chemistry.gamma_factor_red': (
            'print_render.chemistry.gamma_factor_red', 1.0),
        'print_chemistry.gamma_factor_green': (
            'print_render.chemistry.gamma_factor_green', 1.0),
        'print_chemistry.gamma_factor_blue': (
            'print_render.chemistry.gamma_factor_blue', 1.0),
        'print_chemistry.developer_exhaustion': (
            'print_render.chemistry.developer_exhaustion', 1.0),
        'simulation.scan_film': ('io.scan_film', 1.0),
        # ★ 「以人为本」的动态印相曝光（两遍印相）那 6 根的**键位**：它们**不是引擎字段**，
        #   值是 svFilm 自己挂在 `params.settings.person_pe` 上的运行期 dict（`presets._apply`）
        #   ⇒ 没有"引擎默认"可读，`build()` **一根都不写**（缺省 = 逐位回到 `config.PERSON_PE_*`）。
        #   见 `NO_ENGINE_DEFAULT`；登记在这里 = `verify()` 认这几个键是**白名单内的**。
        'simulation.person_pe_dynamic': ('settings.person_pe.person_pe_dynamic', 1.0),
        'simulation.person_pe_target_l': ('settings.person_pe.person_pe_target_l', 1.0),
        'simulation.person_pe_band': ('settings.person_pe.person_pe_band', 1.0),
        'simulation.person_pe_slope': ('settings.person_pe.person_pe_slope', 1.0),
        'simulation.person_pe_max_dp': ('settings.person_pe.person_pe_max_dp', 1.0),
        'simulation.person_pe_back_gain': ('settings.person_pe.person_pe_back_gain', 1.0),
        # ★ 10-18 A1/A1.1：P2 服装约束 —— 同样不是引擎字段
        'simulation.person_pe_white_band': ('settings.person_pe.person_pe_white_band', 1.0),
        'simulation.person_pe_white_slope': ('settings.person_pe.person_pe_white_slope', 1.0),
        'simulation.person_pe_cloth_p99_max': ('settings.person_pe.person_pe_cloth_p99_max', 1.0),
        'simulation.person_pe_cloth_clip_max': ('settings.person_pe.person_pe_cloth_clip_max', 1.0),
        'simulation.person_pe_cloth_p1_min': ('settings.person_pe.person_pe_cloth_p1_min', 1.0),
        'simulation.person_pe_lc': ('settings.person_pe.person_pe_lc', 1.0),
        'simulation.person_pe_tail': ('settings.person_pe.person_pe_tail', 1.0),
        'simulation.person_pe_mid': ('settings.person_pe.person_pe_mid', 1.0),
        # ---- preflashing ----
        'preflashing.exposure': ('enlarger.preflash_exposure', 1.0),
        'preflashing.y_filter_shift': ('enlarger.preflash_y_filter_shift', 1.0),
        'preflashing.m_filter_shift': ('enlarger.preflash_m_filter_shift', 1.0),
        # ---- film_chemistry（dev：`film_render.chemistry`；pipeline init 烘焙进曲线）----
        'film_chemistry.development_time': ('film_render.chemistry.development_time', 1.0),
        'film_chemistry.active': ('film_render.chemistry.active', 1.0),
        'film_chemistry.gamma_factor': ('film_render.chemistry.gamma_factor', 1.0),
        'film_chemistry.gamma_factor_fast': ('film_render.chemistry.gamma_factor_fast', 1.0),
        'film_chemistry.gamma_factor_slow': ('film_render.chemistry.gamma_factor_slow', 1.0),
        'film_chemistry.gamma_factor_red': ('film_render.chemistry.gamma_factor_red', 1.0),
        'film_chemistry.gamma_factor_green': ('film_render.chemistry.gamma_factor_green', 1.0),
        'film_chemistry.gamma_factor_blue': ('film_render.chemistry.gamma_factor_blue', 1.0),
        'film_chemistry.developer_exhaustion': (
            'film_render.chemistry.developer_exhaustion', 1.0),
        # ---- display ----
        'display.preview_max_size': ('settings.preview_max_size', 1.0),
    }
    # ---- 柔光块（两个前缀、字段结构一样；`presets._diffusion()`） ----
    _DIFF = (('active', 'active'), ('family', 'filter_family'), ('strength', 'strength'),
             ('spatial_scale', 'spatial_scale'), ('halo_warmth', 'halo_warmth'),
             ('core_intensity', 'core_intensity'), ('core_size', 'core_size'),
             ('halo_intensity', 'halo_intensity'), ('halo_size', 'halo_size'),
             ('bloom_intensity', 'bloom_intensity'), ('bloom_size', 'bloom_size'))
    for js, rt in (('camera_diffusion_filter', 'camera.diffusion_filter'),
                   ('diffusion_filter', 'enlarger.diffusion_filter')):
        for a, b in _DIFF:
            m['simulation.%s_%s' % (js, a)] = ('%s.%s' % (rt, b), 1.0)
    # ---- 逐块：前缀 + 属性 ----
    for grp, rt in (('grain', 'film_render.grain'),
                    ('halation', 'film_render.halation'),
                    ('couplers', 'film_render.dir_couplers'),
                    ('glare', 'print_render.glare')):
        m['__group__' + grp] = rt
        for attr in DELIVERY_GROUPS[grp]:
            key = '%s.%s' % (grp, attr)
            m[key] = ('%s.%s' % (rt, attr), SCALE100 if key in SCALE100_KEYS else 1.0)
    return m


MAP = None          # 见文件末尾的 `MAP = _map()`（依赖下面的 DELIVERY_GROUPS）
DELIVERY_GROUPS = {
    'grain': ('active', 'rms_granularity', 'density_min', 'uniformity',
              'particle_scale_sublayers', 'blur', 'mult_usm_sigma', 'mult_usm_amount',
              'blur_dye_clouds_um', 'micro_structure', 'micro_sublayers'),
    'halation': ('active', 'scatter_amount', 'scatter_spatial_scale', 'halation_amount',
                 'halation_spatial_scale', 'boost_ev', 'protect_ev', 'boost_range',
                 'scatter_core_um', 'scatter_tail_um', 'scatter_tail_weight',
                 'halation_strength', 'halation_first_sigma_um', 'halation_n_bounces',
                 'halation_bounce_decay', 'halation_renormalize'),
    'couplers': ('active', 'amount', 'inhibition_samelayer', 'inhibition_interlayer',
                 'gamma_samelayer_rgb', 'gamma_interlayer_r_to_gb',
                 'gamma_interlayer_g_to_rb', 'gamma_interlayer_b_to_rg',
                 'langmuir_donor_k_rgb', 'langmuir_receiver_k_rgb',
                 'diffusion_size_um'),
    'glare': ('active', 'percent', 'roughness', 'blur'),
}
SCALE100_KEYS = ('halation.scatter_tail_weight', 'halation.halation_strength')
# ★★ 「以人为本」的动态印相曝光那 8 根：**引擎里没有对应字段**（两遍印相是 svFilm 自己的事，
#   值是挂在 `params.settings.person_pe` 上的运行期 dict，见 `svFilm/presets.py::_apply`）。
#   ⇒ 没有"出厂默认"可比 ⇒ `build()` **一根都不写**（缺省 = 逐位回到 `config.PERSON_PE_*`）、
#     `verify()` 不判它们（类型由 `_apply` 那头收口，写错当场炸，不靠这里兜）。
NO_ENGINE_DEFAULT = ('simulation.person_pe_dynamic', 'simulation.person_pe_target_l',
                     'simulation.person_pe_band', 'simulation.person_pe_slope',
                     'simulation.person_pe_max_dp', 'simulation.person_pe_back_gain',
                     'simulation.person_pe_white_band', 'simulation.person_pe_white_slope',
                     'simulation.person_pe_cloth_p99_max',
                     'simulation.person_pe_cloth_clip_max',
                     'simulation.person_pe_cloth_p1_min',
                     'simulation.person_pe_lc', 'simulation.person_pe_tail',
                     'simulation.person_pe_mid')

# ---------------------------------------------------------------------------
# ★★ 有意偏离引擎默认的键（**每一根都要写清依据**）—— 10-15 用户拍板
#   `scanner.*_correction` 不是"风格值"，是**接线**：False 时对应的 `*_level` 变成**死键**
#   （`color_reference.py:139-145` 直接赋值覆盖 ⇒ 那一支的 level 谁都不读）。
#   实测后果：白锚从固定常数 `W_s` 退化成链自身白参考 ⇒ "三通道都高"的显示码上界 237→227
#   ⇒ `min(RGB)>=235` **恒为 0**、整张暗 ~3 L*、L95 −3.5~−4.0、颊 L* −2.9~−4.0。
PINS = {
    'simulation.scan_white_correction': (True, '白端必须活着（False ⇒ white_level 死键、天花板 −6 L*）'),
    'simulation.scan_black_correction': (True, '黑点同上（False ⇒ black_level 死键）'),
    # ★ 10-19 用户拍板：1.5 在 1080 黑发上把颗粒锐成白点（A/B 829→359 个亮点），直接关。
    'grain.mult_usm_amount': (0.0, '10-19 关：1080 黑发白点（grain.blur 0.8 在 1080 只剩 0.14px，USM 一锐就成点）'),
}
MAP = _map()

_DEAD_NOTE = (
    '死键不写：`load_raw.*` 整段 ｜ `special.print_gamma_factor` ｜ '
    '`couplers.diffusion_tail_um/weight`（用引擎默认 200/0.03）｜ '
    '`display.{use_display_transform,gray_18_canvas,white_padding,output_interpolation}` ｜ '
    '`simulation.{auto_preview,saving_color_space,saving_cctf_encoding}`。\n'
    '★ dev 原生：颗粒 `rms_granularity`（48µm 孔径）· 相机 `color_filter` · '
    '两端 `film_chemistry` / `print_chemistry` · Langmuir 显式 K=1.0。')


# ---------------------------------------------------------------------------
# 引擎侧
# ---------------------------------------------------------------------------

def boot():
    """把仓库与 vendored 引擎挂上 `sys.path`。@returns init_params"""
    import _lib
    sys.path.insert(0, _lib.REPO)
    from svFilm import spektra
    init_params, _sim = spektra._sf()
    return init_params


def _get(obj, dotted):
    cur = obj
    for part in dotted.split('.'):
        cur = getattr(cur, part)
    return cur


def _plain(v):
    """numpy / tuple -> 可 JSON 化的纯量。"""
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return float(v)
    try:                                    # numpy 标量
        import numpy as np
        if isinstance(v, np.generic):
            return _plain(v.item())
    except Exception:                       # noqa: BLE001
        pass
    return v


def build(film_profile, print_profile, note=None):
    r"""生成预设 dict：**值 = 引擎 schema 默认**，只钉 `film_stock` / `print_paper`。

    ⚠ 三个键**必须显式写**（它们不在 dataclass 默认里，或默认对"成片"这条路是错的）：
      · `special.film_channel_swap` / `special.print_channel_swap` = `[0,1,2]`（= 不重排，引擎默认值就是它）
      · `enlarger.print_exposure_compensation` / `normalize_print_exposure` 由 `_apply` 钉死 True
        （见 `presets.py` 10-08 那段）⇒ 这里只把 JSON 侧的值照默认写。
    """
    init_params = boot()
    p = init_params(film_profile=film_profile, print_profile=print_profile)
    out = {}
    if note:
        out['_note_self'] = note
    for grp, rt in ((k[9:], v) for k, v in MAP.items() if k.startswith('__group__')):
        blk = {}
        for attr in DELIVERY_GROUPS[grp]:
            key = '%s.%s' % (grp, attr)
            v = _get(p, '%s.%s' % (rt, attr))
            if key in SCALE100_KEYS:
                v = [float(x) * SCALE100 for x in v]
            blk[attr] = _plain(v)
        out[grp] = blk
    for js, spec in MAP.items():
        if js.startswith('__group__'):      # ★ 分组哨兵的值是字符串（运行时前缀），不是 (路径, 缩放)
            continue
        if js in NO_ENGINE_DEFAULT:         # ★ 无引擎默认可读（见 `NO_ENGINE_DEFAULT`）
            continue
        rt, sc = spec
        head = js.split('.', 1)[0]
        blk = out.setdefault(head, {})
        v = _get(p, rt)
        if isinstance(v, (list, tuple)):
            v = [float(x) * sc for x in v] if sc != 1.0 else list(v)
        elif isinstance(v, float) and sc != 1.0:
            v = float(v) * sc
        blk[js.split('.', 1)[1]] = _plain(v)
    # 通道重排：默认就是"不动"
    out.setdefault('special', {})
    out['special']['film_channel_swap'] = [0, 1, 2]
    out['special']['print_channel_swap'] = [0, 1, 2]
    out['_note_dead_keys'] = _DEAD_NOTE
    out['simulation']['film_stock'] = str(film_profile)
    out['simulation']['print_paper'] = str(print_profile)
    # ---- 有意偏离默认的几根（见 `PINS`）----
    for js, (val, _why) in PINS.items():
        grp, key = js.split('.', 1)
        out.setdefault(grp, {})[key] = val
    # ---- 逐参数中文说明（10-19；唯一真源 = `_lib/preset_notes.py`）----
    #   `_note_params` 是纯说明：`presets._apply()` 不读，`verify()` 跳过 `_note*`。
    from . import preset_notes as _PN
    out['_note_params'] = _PN.for_preset(out)
    return _order(out)


_ORDER = ('_note_self', 'input_image', 'grain', 'preflashing', 'halation', 'couplers',
          'film_chemistry', 'glare', 'print_chemistry', 'special', 'simulation', 'display',
          '_note_dead_keys', '_note_params')


def _order(obj):
    """把顶层键按人读的顺序排（与旧生产预设一致：input_image 打头、simulation 居中）。"""
    out = {k: obj[k] for k in _ORDER if k in obj}
    for k, v in obj.items():
        if k not in out:
            out[k] = v
    return out


def verify(obj, film_profile, print_profile, verbose=True):
    r"""逐字段比对：JSON 里每个键 == 引擎默认（缩放还原）。@returns 不一致清单"""
    init_params = boot()
    p = init_params(film_profile=film_profile, print_profile=print_profile)
    bad = []
    for js, val in _leaves(obj):
        if js.startswith('_note') or js.startswith('simulation.film_stock') \
                or js.startswith('simulation.print_paper'):
            continue
        if js.startswith('special.film_channel_swap') or js.startswith('special.print_channel_swap'):
            if list(val) != [0, 1, 2]:
                bad.append((js, val, '[0,1,2]'))
            continue
        rt = MAP.get(js)
        if rt is None:
            bad.append((js, val, '<不在白名单里>'))
            continue
        if js in NO_ENGINE_DEFAULT:
            continue                        # ★ 无引擎默认可比（值是 svFilm 自己的旋钮）
        want = _plain(_get(p, rt[0]))
        got = list(val) if isinstance(val, list) else val
        if js in SCALE100_KEYS:
            want = [float(x) * SCALE100 for x in want]
        if js in PINS:
            # ★ 有意偏离：只要求 == 钉住的那个值（并在报告里单列，不作"不一致"）
            if _eq(got, PINS[js][0]):
                continue
            bad.append((js, got, PINS[js][0]))
            continue
        if not _eq(got, want):
            bad.append((js, got, want))
    if verbose:
        print('[mkpreset] 与引擎默认不一致的键：%d' % len(bad))
        for js, got, want in bad:
            print('   %-46s 我们=%r  默认=%r' % (js, got, want))
    return bad


def _leaves(o, pre=''):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _leaves(v, '%s.%s' % (pre, k) if pre else k)
    else:
        yield pre, o


def _eq(got, want):
    """逐位比（列表逐元素、bool 严格、字符串严格；None == None）。"""
    if want is None or got is None:
        return want is None and got is None
    if isinstance(want, list):
        return isinstance(got, list) and len(want) == len(got) and \
            all(abs(float(a) - float(b)) < 1e-9 for a, b in zip(want, got))
    if isinstance(want, str) or isinstance(got, str):
        return str(want) == str(got)
    if isinstance(want, bool) or isinstance(got, bool):
        return bool(want) == bool(got)
    return abs(float(want) - float(got)) < 1e-9


def write(path, obj, indent=2, crlf=True, trailing_nl=True):
    """按 `jsonsafe` 的口径落盘（原子写 + 不翻译换行）。"""
    # ★ 10-22 细密档（SV 定）：鹿井预设的颗粒不能被"引擎默认"重新盖回去。
    #   10-22 15:48 改成 blur 2.0 / rms 20，23:21 一次重新生成又回 0.8 / 9，
    #   导致 10-23 的 919 批次烘出来没有颗粒 ⇒ 这里按文件名兜底。
    if os.path.basename(path).startswith('鹿井预设'):
        obj.setdefault('grain', {})
        obj['grain']['blur'] = 2.0
        obj['grain']['rms_granularity'] = [20.0, 20.0, 20.0]
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import _lib.jsonsafe as J
    txt = json.dumps(obj, ensure_ascii=False, indent=indent)
    if crlf:
        txt = txt.replace('\n', '\r\n')
    if trailing_nl:
        txt += '\r\n' if crlf else '\n'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    J.write_atomic(path, txt, newline='')
    pr = J.probe(path)
    print('[mkpreset] 写出 %s  字节=%d indent=%s %s 尾换行=%s'
          % (os.path.basename(path), pr['bytes'], pr['indent'], pr['newline'], pr['trailing_nl']))
    return pr


def main(argv=None):
    ap = argparse.ArgumentParser('_lib.mkpreset')
    ap.add_argument('--film', default='kodak_portra_400')
    ap.add_argument('--paper', default='kodak_portra_endura')
    ap.add_argument('--out', default='')
    ap.add_argument('--note', default='')
    ap.add_argument('--indent', type=int, default=2)
    a = ap.parse_args(argv)
    obj = build(a.film, a.paper, note=(a.note or None))
    verify(obj, a.film, a.paper)
    if a.out:
        write(a.out, obj, indent=a.indent)
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=a.indent)[:3000])
    return 0


if __name__ == '__main__':
    sys.exit(main())
