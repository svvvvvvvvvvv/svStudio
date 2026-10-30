# -*- coding: utf-8 -*-
r"""预设 JSON 的**逐参数中文说明**（`_note_params`）—— 唯一真源。

## 为什么有它（10-19）
预设 JSON 是"完整参数快照"（上百个键），但键名是引擎的英文代号，值的单位/方向/门控
散在 `引擎手册.md` 的十几张表里。要人工 review 一条预设，得同时开三个文件。
⇒ 把**每个键一句话**直接写进预设 JSON 的 `_note_params`：
```
"_note_params": {
  "couplers.amount": "★ 唯一主标量：整张抑制矩阵乘它；调大 = 抑制强、饱和高。",
  ...
}
```
键 = **预设 JSON 里的点路径**（不是运行时属性路径）；值 = 一句话：是什么 / 单位 / 往哪调。
`_note_params` 是**纯说明**：`presets._apply()` 不读，`mkpreset.verify()` 也跳过 `_note*`。

## 口径
- 事实来源 = `E:\svStudio\svFilm\引擎手册.md`（§9 逐参数表）+ `presets.py::_apply()`；
  两边冲突时**以引擎代码为准**（例：`grain.rms_granularity` 调大是**更粗**，手册 §9.1 写反了）。
- 只说"这根旋钮管什么、调大往哪走、单位是什么"，**不写"我们为什么选这个值"**
  —— 那是 `_note_self` 的活。少数带 ★ 的是容易看错的（缩放 / 门控 / 反直觉方向）。
- 不写进 JSON 的键（旧名/死键）也不在这里；`OPTIONAL` 只登记"当前三份预设没有、
  但换一条预设可能出现的键"，防止覆盖检查把它们当错别字。

## 用法
```python
import _lib.preset_notes as PN
PN.check()                          # 三份预设：每个键都有说明？（只读）
PN.install(path, apply=False)       # 纯文本插入/替换 `_note_params`（默认 dry-run）
```
CLI：
```
python -m _lib.preset_notes check
python -m _lib.preset_notes install --apply
python -m _lib.preset_notes table --out <review.md>
```
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

# ---------------------------------------------------------------------------
# 逐键说明（顺序 = 预设 JSON 的块顺序；install 只输出该预设真有的键）
# ---------------------------------------------------------------------------
NOTES = {
    # ---- wb（解码层；svFilm 自己的顶层块，引擎不认）----
    'wb.white_balance': '白平衡模式：custom = 用下面两个数；其它值是固定白点。',
    'wb.temperature': '解码层色温（K）：假定现场光多暖；调高 ⇒ 画面更暖。',
    'wb.tint': '绿-品红倍率：>1 加绿、<1 加品红（1.0 = 不动）。',

    # ---- input_image（解码 / 光谱重建）----
    'input_image.upscale_factor': '先放大再模拟；1.0 = 原尺寸。',
    'input_image.crop': '只裁一块做预览；false = 全图。',
    'input_image.crop_center': '裁切中心（相对坐标 x,y，0~1）。',
    'input_image.crop_size': '裁切大小（x,y 按长边归一，0~1）。',
    'input_image.input_color_space': '输入图的色彩空间（这里是 ProPhoto RGB）。',
    'input_image.apply_cctf_decoding': '是否对输入再做一次 CCTF 反解码；false = 按已线性处理。',
    'input_image.spectral_upsampling_method': 'RGB→光谱重建法：hanatos2025 覆盖全可见轨迹；mallett2019 只认 sRGB、会裁。',
    'input_image.apply_hanatos2025_adaptation_window': 'hanatos2025 的带通适配窗（重建光谱用）。',
    'input_image.apply_hanatos2025_adaptation_surface': 'hanatos2025 的曲面适配多项式。',
    'input_image.spectral_gaussian_blur': '重建光谱的高斯模糊 σ（nm）；0 = 不糊。',
    'input_image.color_filter': '镜头前滤镜（滤镜库名字）；none = 不加。',

    # ---- grain（颗粒）----
    'grain.active': '颗粒总开关；关掉会连那道全图柔化一起消失（不能当颗粒基线）。',
    'grain.rms_granularity': '颗粒粗细（RMS×1000，48µm 孔径）；调大 = 粒子更大更粗；与出图尺寸无关。',
    'grain.particle_scale_sublayers': '三层感光层的粒子面积比（最粗层 = 1）；调大 = 该层更细。',
    'grain.density_min': '最小密度（灰雾）；不是暗端旋钮，同时是 LUT 的 0 参考点。',
    'grain.uniformity': '颗粒均匀度；调低更接近 Selwyn 钟形（更不均）。',
    'grain.blur': '颗粒高斯模糊 σ（像素）；按实际长边 ÷ 6264 缩放。',
    'grain.mult_usm_sigma': '乘性密度锐化的半径（像素）；补 blur 糊掉的锐度。',
    'grain.mult_usm_amount': '乘性密度锐化强度；0 = 关。',
    'grain.blur_dye_clouds_um': '染料云模糊 σ（µm）；高放大才明显。',
    'grain.micro_structure': '[σ 像素, 团簇 nm] 微观团簇；普通尺寸看不到（两道内门）。',
    'grain.micro_sublayers': '颗粒子层细分；只改细分，不改 RMS 规格。',

    # ---- preflashing（预闪）----
    'preflashing.exposure': '预闪曝光（线性加性，代码里不是 EV）；调大 = 整体垫亮、抬黑位。',
    'preflashing.y_filter_shift': '预闪的 Y 滤片偏移（Kodak CC 单位）。',
    'preflashing.m_filter_shift': '预闪的 M 滤片偏移（Kodak CC 单位）。',

    # ---- halation（散射 + 光晕；两个数组在 JSON 里 ×100）----
    'halation.active': '散射 + 光晕开关；关不掉 boost_ev（那根在另一段）。',
    'halation.scatter_amount': '散射强度：1.0 = 全物理；调大更柔、锐度更低。',
    'halation.scatter_spatial_scale': '散射尺寸倍率（芯和尾一起放大）。',
    'halation.halation_amount': '光晕强度倍率；1.0 = 物理默认。',
    'halation.halation_spatial_scale': '光晕尺寸倍率（第一跳 σ）。',
    'halation.boost_ev': '高光额外提亮上限（stop）；protect_ev=4 会把它门死（0 = 不开）。',
    'halation.protect_ev': '中灰以上多少 stop 内不抬（保护范围）。',
    'halation.boost_range': '高光提亮的过渡坡度（0~1）。',
    'halation.scatter_core_um': '散射芯 σ（µm，R/G/B）；调大芯更糊。',
    'halation.scatter_tail_um': '散射长尾的衰减长度（µm）；调大拖尾更长。',
    'halation.scatter_tail_weight': '长尾占散射能量的比例；JSON 里 ×100（78 = 0.78）。',
    'halation.halation_strength': '背反射光晕幅度（0~1）；JSON 里 ×100（1.5 = 0.015）；蓝通道通常≈0。',
    'halation.halation_first_sigma_um': '第一跳光晕 σ（µm）；对应片基厚度 40~80µm。',
    'halation.halation_n_bounces': '光晕叠加几跳（典型 2~3）。',
    'halation.halation_bounce_decay': '每跳幅度衰减比例（物理 0.3~0.7）。',
    'halation.halation_renormalize': '按 1+Σ 归一以保住中灰；关 = 纯加性（阴影也抬）。',

    # ---- couplers（DIR 成色剂抑制）----
    'couplers.active': 'DIR 成色剂抑制总开关。',
    'couplers.amount': '★ 唯一主标量：整张抑制矩阵乘它；调大 = 抑制强、饱和高；Langmuir 下阴影/高光不等比。',
    'couplers.inhibition_samelayer': '同层抑制倍率（管每层反差 / γ）。',
    'couplers.inhibition_interlayer': '层间抑制倍率（管饱和增强）。',
    'couplers.gamma_samelayer_rgb': '同层逐通道抑制 γ（R,G,B）。',
    'couplers.gamma_interlayer_r_to_gb': 'R 层对 G/B 层的抑制量。',
    'couplers.gamma_interlayer_g_to_rb': 'G 层对 R/B 层的抑制量。',
    'couplers.gamma_interlayer_b_to_rg': 'B 层对 R/G 层的抑制量。',
    'couplers.diffusion_size_um': '成色剂扩散 σ（µm，常见 5~20）；调大 = 串扰大、锐度降、影响饱和。',
    'couplers.langmuir_donor_k_rgb': '负片供体饱和 K（默认 1.0）；调大接近线性，调小低密度更陡。',
    'couplers.langmuir_receiver_k_rgb': '正片/反转的受体饱和 K；负片不走这条（1.0 = 默认）。',

    # ---- glare（印相眩光）----
    'glare.active': '印相眩光；⚠ 用随机数，同参两次不可复现。',
    'glare.percent': '眩光比例；调大整体/阴影被抬（起雾）。',
    'glare.roughness': '眩光的空间不均匀度（0~1）。',
    'glare.blur': '眩光斑块 σ（像素）。',

    # ---- 两端化学（同一套 s023 γ morph）----
    'film_chemistry.development_time': '黑白卷的显影时间档；彩色卷忽略。',
    'film_chemistry.active': '胶片曲线 morph 开关。',
    'film_chemistry.gamma_factor': '胶片密度曲线 γ 总倍率；1.0 = 不改，调大更陡、反差高。',
    'film_chemistry.gamma_factor_fast': '快速子层 γ。',
    'film_chemistry.gamma_factor_slow': '中/慢子层 γ（mid = slow）。',
    'film_chemistry.gamma_factor_red': '胶片红通道 γ。',
    'film_chemistry.gamma_factor_green': '胶片绿通道 γ。',
    'film_chemistry.gamma_factor_blue': '胶片蓝通道 γ。',
    'film_chemistry.developer_exhaustion': '显影疲劳 0~1：肩更长、趾更陡（压高光不动中灰）。',
    'print_chemistry.development_time': '黑白纸的显影时间档；彩色纸忽略。',
    'print_chemistry.active': '印相曲线 morph 开关。',
    'print_chemistry.gamma_factor': '印相密度曲线 γ 总倍率；1.0 = 不改。',
    'print_chemistry.gamma_factor_fast': '快速子层 γ。',
    'print_chemistry.gamma_factor_slow': '中/慢子层 γ（mid = slow）。',
    'print_chemistry.gamma_factor_red': '印相红通道 γ；>1 更陡；三通道不等会挪中性轴（不是调色刀）。',
    'print_chemistry.gamma_factor_green': '印相绿通道 γ；>1 更陡。',
    'print_chemistry.gamma_factor_blue': '印相蓝通道 γ；>1 更陡。',
    'print_chemistry.developer_exhaustion': '显影疲劳 0~1：肩更长、趾更陡（压高光不动中灰）。',

    # ---- special（通道重排）----
    'special.film_channel_swap': '负片通道重排；[0,1,2] = 不动。',
    'special.print_channel_swap': '相纸通道重排；[0,1,2] = 不动。',

    # ---- simulation（引擎侧；大部分是相机/放大机/扫描仪）----
    'simulation.film_format_mm': '画幅长边 mm（35/70/36）；调大 = 所有 µm 空间核与颗粒像素面积同比放大（不改景深）。',
    'simulation.camera_lens_blur_um': '镜头光学柔 σ（µm）；0 = 不糊。',
    'simulation.exposure_compensation_ev': '拍摄端曝光补偿（EV）；印相端会自动抵消，净亮度基本不变。',
    'simulation.auto_exposure': '虚拟相机自动曝光（raw 域，中灰 = 1）。',
    'simulation.auto_exposure_method': '测光方式；subject = svFilm 的「测光看人」（装不上退回普通测光）。',
    'simulation.print_illuminant': '印相放大机光源（dev 只给 TH-KG3）。',
    'simulation.print_exposure': '★ 印相曝光（线性倍率）；调大 = 印得更暗（脸 L* 约 −93/单位）；动态 PE 会再解一次。',
    'simulation.output_lightness_compression': '输出明度压缩膝；off = 关（高光不压顶、可能裁切）；缺省 (0.7,1.0,2.2)。',
    'simulation.output_gamut_knee': '输出彩度压缩膝 (threshold, limit, power)；幂越低咬得越早。',
    'simulation.print_exposure_compensation': '印相自动补偿相机曝光补偿；这里钉死 true。',
    'simulation.print_y_filter_shift': 'Y 滤片偏移（Kodak CC）；调大 = 偏冷（黄片吸蓝）。',
    'simulation.print_m_filter_shift': 'M 滤片偏移（Kodak CC）；调大 = 偏绿（品红片吸绿）。',
    'simulation.scan_lens_blur': '扫描镜头模糊 σ（像素）；不随画幅缩放，按实际长边 ÷ 6264 缩放。',
    'simulation.scan_white_correction': '白端校正门控；关掉 white_level 变死键、高光天花板降低。',
    'simulation.scan_black_correction': '黑端校正门控；关掉 black_level 变死键。',
    'simulation.scan_unsharp_mask': '[σ 像素, 强度]；σ 不随出图缩放；只有 σ>0 且强度>0 才跑（0/负 = 关闭）。',
    'simulation.scan_black_level': '黑点目标（先按 sRGB 解码再当线性 Y）；调大 = 黑位抬。',
    'simulation.scan_white_level': '白点目标；调大 = 白端更亮。',
    'simulation.output_color_space': '输出色彩空间（成片 sRGB）。',
    'simulation.scan_film': 'false = 看成片；true = 看负片扫描。',
    'simulation.camera_diffusion_filter_active': '相机端柔光开关（黑柔 1/4）。',
    'simulation.camera_diffusion_filter_family': '柔光族；black_pro_mist = 黑柔（偏折更少、晕更紧；代码里不吸光，能量守恒）。',
    'simulation.camera_diffusion_filter_strength': '商业档位：1/8=0.125、1/4=0.25、1/2=0.5；物理 = 偏折光子占比。',
    'simulation.camera_diffusion_filter_spatial_scale': 'PSF 尺寸倍率；和 film_format_mm 改同一件事。',
    'simulation.camera_diffusion_filter_halo_warmth': '外晕暖/内晕冷偏移；0 = 族默认。',
    'simulation.camera_diffusion_filter_core_intensity': '芯权重倍率；1.0 = 族默认（三组权重会归一）。',
    'simulation.camera_diffusion_filter_core_size': '芯半径倍率；1.0 = 族默认。',
    'simulation.camera_diffusion_filter_halo_intensity': '晕权重倍率；1.0 = 族默认。',
    'simulation.camera_diffusion_filter_halo_size': '晕半径倍率；1.0 = 族默认。',
    'simulation.camera_diffusion_filter_bloom_intensity': '泛光权重倍率；1.0 = 族默认。',
    'simulation.camera_diffusion_filter_bloom_size': '泛光半径倍率；1.0 = 族默认。',
    'simulation.diffusion_filter_active': '放大机端柔光开关。',
    'simulation.diffusion_filter_family': '柔光族（与相机端同一套结构）。',
    'simulation.diffusion_filter_strength': '商业档位：1/8=0.125、1/4=0.25、1/2=0.5。',
    'simulation.diffusion_filter_spatial_scale': 'PSF 尺寸倍率。',
    'simulation.diffusion_filter_halo_warmth': '外晕暖/内晕冷偏移；0 = 族默认。',
    'simulation.diffusion_filter_core_intensity': '芯权重倍率；1.0 = 族默认。',
    'simulation.diffusion_filter_core_size': '芯半径倍率；1.0 = 族默认。',
    'simulation.diffusion_filter_halo_intensity': '晕权重倍率；1.0 = 族默认。',
    'simulation.diffusion_filter_halo_size': '晕半径倍率；1.0 = 族默认。',
    'simulation.diffusion_filter_bloom_intensity': '泛光权重倍率；1.0 = 族默认。',
    'simulation.diffusion_filter_bloom_size': '泛光半径倍率；1.0 = 族默认。',
    'simulation.film_stock': '模拟胶卷；构建 params 时选定，overrides 改不了。',
    'simulation.print_paper': '模拟相纸；构建 params 时选定，overrides 改不了。',

    # ---- 「以人为本」的动态印相曝光（svFilm 自己的键，不是引擎字段）----
    'simulation.person_pe_dynamic': '动态印相曝光总开关；缺省 = true。',
    'simulation.person_pe_target_l': '显式目标 L*：无 band 时直接当落点；有 band 时当 mid（P50）用。',
    'simulation.person_pe_band': '脸 L* 接受窗 [P10,P90]：窗内不动；低于下沿提到 P25（逆光往 P50 推），高于上沿可压到 P75。',
    'simulation.person_pe_slope': '脸 L* 对印相曝光的斜率（默认 93）；改它 = 改第二遍推力。',
    'simulation.person_pe_max_dp': '单张印相曝光最大步长（默认 0.25）；逆光会再放大。',
    'simulation.person_pe_back_gain': '逆光时单张步长的放大系数（默认 1.0）。',
    'simulation.person_pe_white_band': '亮中性服装 L50 带；只截「往亮提」的偏好，不截脸的下限。',
    'simulation.person_pe_white_slope': '白服 L* 对印相曝光的斜率（默认 45）；换卷/纸要重测。',
    'simulation.person_pe_cloth_p99_max': '全色服装 p99 L* 天花板；超了不再往亮提（脸下限优先）。',
    'simulation.person_pe_cloth_clip_max': '全色服装 clip%（≥254）上限；超了不再往亮提。',
    'simulation.person_pe_cloth_p1_min': '全色服装 p1 只报（黑衣接近 0 正常）。',
    'simulation.person_pe_lc': 'auto = 脸已在带内时，用输出明度压缩压亮白服（再渲一遍）；off = 不做。',
    'simulation.person_pe_tail': '尾巴目标 [P25,P75]；只在脸出接受窗时用（暗了提向 P25，亮了压向 P75）。',
    'simulation.person_pe_mid': '脸中位 P50；逆光提亮目标的上界（顺光用 P25）。',

    # ---- display / export（交付约定）----
    'display.preview_max_size': '预览长边像素（640）；只影响预览，不影响成片。',
    'export.long_side': '交付长边 1080；当前渲染链不读，供出片/复核对齐。',
    'export.jpeg_quality': '交付 JPEG 质量 84；当前渲染链不读。',
    'export.px_ref_long_side': '像素参数的参考长边 1080（scan USM 的 σ 按它标定）；换尺寸要换算。',
}

# 当前三份预设没有、但换预设可能出现的键（覆盖检查别把它们当错别字）
OPTIONAL = {
    'grain.micro_structure',
    'film_chemistry.development_time', 'film_chemistry.active',
    'film_chemistry.gamma_factor_fast', 'film_chemistry.gamma_factor_slow',
    'film_chemistry.gamma_factor_red', 'film_chemistry.gamma_factor_green',
    'film_chemistry.gamma_factor_blue', 'film_chemistry.developer_exhaustion',
    'print_chemistry.development_time', 'print_chemistry.active',
    'print_chemistry.gamma_factor', 'print_chemistry.gamma_factor_fast',
    'print_chemistry.gamma_factor_slow', 'print_chemistry.developer_exhaustion',
    'simulation.output_gamut_knee',
    'simulation.person_pe_dynamic', 'simulation.person_pe_target_l',
    'simulation.person_pe_slope', 'simulation.person_pe_max_dp',
    'simulation.person_pe_back_gain', 'simulation.person_pe_white_slope',
    'simulation.person_pe_cloth_p1_min',
}

PRESET_DIR = r'E:\svStudio\svFilm\svFilm\data\presets'
PRESETS = ('鹿井预设', '滨田预设', 'xhs预设')


# ---------------------------------------------------------------------------
# 叶子路径
# ---------------------------------------------------------------------------

def leaves(obj, pre=''):
    r"""预设 dict -> 叶子点路径（**跳过 `_note*`**）。"""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).startswith('_note'):
                continue
            out.extend(leaves(v, '%s.%s' % (pre, k) if pre else str(k)))
    else:
        out.append(pre)
    return out


def for_preset(obj):
    r"""按该预设真实存在的叶子，生成有序 `{路径: 说明}`。"""
    have = set(leaves(obj))
    return {k: v for k, v in NOTES.items() if k in have}


# ---------------------------------------------------------------------------
# 纯文本插入 / 替换（不重排 JSON）
# ---------------------------------------------------------------------------

def _block_lines(notes, ind=2):
    r"""把说明 dict 渲染成 JSON 文本行（顶层键的缩进 = ind）。"""
    lines = [' ' * ind + '"_note_params": {']
    items = list(notes.items())
    for i, (k, v) in enumerate(items):
        tail = ',' if i < len(items) - 1 else ''
        lines.append(' ' * (ind + 2) + json.dumps(k, ensure_ascii=False) + ': '
                     + json.dumps(v, ensure_ascii=False) + tail)
    lines.append(' ' * ind + '}')
    return lines


def _span_of_params(text):
    r"""找 `_note_params` 的**值**（`{...}`）在原文里的 [start, end)；没有 -> None。

    只做顶层键：用一次带字符串/转义感知的括号扫描（不靠正则硬吃嵌套）。
    """
    key = '"_note_params"'
    pos = text.find(key)
    if pos < 0:
        return None
    # 键之后必须是冒号（防止 `_note_params_x` 之类误命中）
    i = pos + len(key)
    while i < len(text) and text[i] in ' \t\r\n':
        i += 1
    if i >= len(text) or text[i] != ':':
        return None
    i += 1
    while i < len(text) and text[i] in ' \t\r\n':
        i += 1
    if i >= len(text) or text[i] != '{':
        return None
    start = i
    depth, in_str, esc = 0, False, False
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return start, i + 1
        i += 1
    return None


def install(path, apply=False, snapdir=None):
    r"""把 `_note_params` 写进一份预设（**只做纯文本插入/替换**）。

    @param path {str} 预设 JSON 路径
    @param apply {bool} False = dry-run（只报告，不落盘）
    @param snapdir {str|None} 写前快照目录；None = 预设目录旁的 `_snap`
    @returns {dict} {status, path, notes, added, replaced, delta_bytes, sha_before, sha_after,
                     json_ok, leaves_same, bare_lf, snapshot, new_text?}
    """
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import _lib.jsonsafe as J

    pr = J.probe(path)
    raw = pr['text']
    nl = pr['newline']
    old = J.gate(raw)
    notes = for_preset(old)
    want = set(leaves(old))
    got = set(notes)
    if want != got:
        raise ValueError('%s：说明没覆盖全（缺 %s / 多 %s）'
                         % (os.path.basename(path), sorted(want - got), sorted(got - want)))

    block = _block_lines(notes, ind=(pr['indent'] or 2))
    span = _span_of_params(raw)
    if span is not None:
        # 替换已有块：从键所在行的行首到值的 `}`；块自带顶层缩进，块后的逗号原样保留
        start, end = span
        line_start = raw.rfind('\n', 0, start) + 1
        new = raw[:line_start] + nl.join(block) + raw[end:]
        replaced = True
    else:
        lines = raw.split(nl)
        # 找最后一个 `}` 行（顶层对象的收尾）
        idx = None
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == '}':
                idx = i
                break
        if idx is None:
            return dict(status='FAIL（找不到 JSON 收尾）', path=path, sha_before=pr['sha256'])
        # 前一个非空行补逗号（它原来是最后一个键）
        j = idx - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j < 0:
            return dict(status='FAIL（空对象？）', path=path, sha_before=pr['sha256'])
        if not lines[j].rstrip().endswith(','):
            lines[j] = lines[j].rstrip() + ','
        lines[idx:idx] = block
        new = nl.join(lines)
        replaced = False

    # ---- 硬门：JSON 可解 + 非说明叶子逐位不变 + 覆盖全 ----
    back = J.gate(new)
    a, b = dict(old), dict(back)
    a.pop('_note_params', None)
    b.pop('_note_params', None)
    leaves_same = (a == b)
    bad = J.bare_lf_check(new, pr['crlf'])
    res = dict(status='dry-ok', path=path, notes=len(notes), replaced=replaced,
               leaves_same=leaves_same, delta_bytes=len(new.encode('utf-8')) - pr['bytes'],
               sha_before=pr['sha256'], json_ok=True, bare_lf=bad)
    if bad:
        res['status'] = 'FAIL（裸 LF %d 处）' % len(bad)
        return res
    if not leaves_same:
        res['status'] = 'FAIL（说明之外的字段被改了）'
        return res
    if not apply:
        res['new_text'] = new
        return res
    res['snapshot'] = J.snapshot(path, snapdir)
    J.write_atomic(path, new, newline='')
    after = J.read_raw(path)
    J.gate(after)
    res['status'] = 'applied'
    res['roundtrip_identical'] = (after == new)
    res['sha_after'] = hashlib.sha256(after.encode('utf-8')).hexdigest()
    return res


# ---------------------------------------------------------------------------
# 人工 review 表（三份并排：值 + 说明）
# ---------------------------------------------------------------------------

def _fmt(v):
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def table(paths=None):
    r"""三份预设并排的 Markdown 表（参数 / 三条值 / 说明），给人工 review 用。

    @returns {str} Markdown 文本
    """
    paths = list(paths or [os.path.join(PRESET_DIR, n + '.json') for n in PRESETS])
    names, objs = [], {}
    for p in paths:
        n = os.path.splitext(os.path.basename(p))[0]
        names.append(n)
        objs[n] = json.load(open(p, encoding='utf-8'))
    union = []
    for n in names:
        for k in leaves(objs[n]):
            if k not in union:
                union.append(k)
    order = [k for k in NOTES if k in union] + [k for k in union if k not in NOTES]
    out = ['| 参数 | ' + ' | '.join(names) + ' | 说明 |',
           '|---|' + '---|' * len(names) + '---|']
    for k in order:
        vals = []
        for n in names:
            cur = objs[n]
            ok = True
            for part in k.split('.'):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    ok = False
                    break
            vals.append(_fmt(cur) if ok else '—')
        note = NOTES.get(k, '（缺说明）').replace('\n', ' ')
        out.append('| `%s` | %s | %s |' % (k, ' | '.join(vals), note))
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------------------
# 覆盖检查
# ---------------------------------------------------------------------------

def check(paths=None, verbose=True):
    r"""三份预设：`_note_params` 覆盖每个叶子？说明为空？键名有没有写错？

    @returns {dict} {ok, presets: {name: {leaves, notes, missing, empty, unknown}}, unused, typo}
    """
    paths = list(paths or [os.path.join(PRESET_DIR, n + '.json') for n in PRESETS])
    rep = {}
    all_leaves = set()
    ok = True
    for p in paths:
        name = os.path.splitext(os.path.basename(p))[0]
        obj = json.load(open(p, encoding='utf-8'))
        lv = set(leaves(obj))
        nt = obj.get('_note_params') or {}
        want = for_preset(obj)
        missing = sorted(lv - set(nt))
        empty = sorted(k for k, v in nt.items() if not str(v).strip())
        stale = sorted(k for k in (set(want) & set(nt)) if want[k] != nt[k])
        unknown = sorted(set(nt) - lv)
        all_leaves |= lv
        good = not missing and not empty and not stale
        ok = ok and good
        rep[name] = dict(leaves=len(lv), notes=len(nt), missing=missing,
                         empty=empty, stale=stale, unknown=unknown, ok=good)
        if verbose:
            print('%-8s 键 %3d / 说明 %3d  %s' % (name, len(lv), len(nt), 'OK' if good else 'FAIL'))
            for k in missing:
                print('   ✗ 缺说明: %s' % k)
            for k in empty:
                print('   ✗ 空说明: %s' % k)
            for k in stale:
                print('   ✗ 说明过期（与 NOTES 不一致）: %s' % k)
            for k in unknown:
                print('   · 本预设没有这个键: %s' % k)
    unused = sorted(set(NOTES) - all_leaves - OPTIONAL)
    typo = sorted(set(NOTES) - all_leaves - OPTIONAL)
    if typo:
        ok = False
        if verbose:
            print('✗ NOTES 里有 %d 个路径在三份预设里都不存在（错别字？）：%s'
                  % (len(typo), ', '.join(typo)))
    if verbose:
        print('总覆盖：%d 个叶子｜NOTES=%d 条｜可选=%d 条｜%s'
              % (len(all_leaves), len(NOTES), len(OPTIONAL), 'PASS' if ok else 'FAIL'))
    return dict(ok=ok, presets=rep, unused=unused, typo=typo)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser('_lib.preset_notes')
    ap.add_argument('cmd', choices=('check', 'install', 'table'))
    ap.add_argument('--apply', action='store_true', help='install 真的落盘（默认 dry-run）')
    ap.add_argument('--preset', default='', help='只处理一条（名字，不带 .json）')
    ap.add_argument('--out', default='', help='table 写到这个文件（缺省 = stdout）')
    a = ap.parse_args(argv)
    if a.cmd == 'check':
        return 0 if check()['ok'] else 1
    if a.cmd == 'table':
        txt = table()
        if a.out:
            os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
            with open(a.out, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(txt)
            print('写出 %s（%d 行）' % (a.out, txt.count('\n')))
        else:
            sys.stdout.write(txt)
        return 0
    names = (a.preset,) if a.preset else PRESETS
    rc = 0
    for n in names:
        p = os.path.join(PRESET_DIR, n + '.json')
        r = install(p, apply=a.apply,
                    snapdir=os.path.join(r'%s\_once' % r'E:\ds\photo', 'preset_notes_1019'))
        print('%-8s %-10s 说明 %3d 条  %+d 字节  %s'
              % (n, r['status'], r.get('notes', 0), r.get('delta_bytes', 0),
                 '叶子不变' if r.get('leaves_same') else '⚠ 叶子变了'))
        if r['status'] not in ('dry-ok', 'applied'):
            rc = 1
    if not a.apply:
        print('（dry-run；加 --apply 才落盘）')
    return rc


if __name__ == '__main__':
    sys.exit(main())
