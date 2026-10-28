# -*- coding: utf-8 -*-
"""质感参数：**不能靠测量对齐**，只能靠「器材事实给方向 + 引擎这条链上换算」。

手册 §四 把边界写死了，这里逐条落成代码：
  1. 空间族**没有跨链等价**：真实胶片是"记录进去的"，引擎是"后期叠上去的" ⇒ "对上去"没有定义。
  2. 物理/器材事实**只给方向，不给值**（柔光镜档位 / 卷抗晕 / 画幅 / 逆光）。
  3. **必须全长边判**（2048 上高频已被缩图抹掉）⇒ 2048 不许用颗粒绝对值。
  4. 它会**串到颜色**（halation 带色）⇒ 改它要同时看肤色与中性轴。
  5. `print_render.glare` **永不可复现**（numba RNG，同参两渲 max|Δ|=22 码）⇒ 取差前必须关。
  6. 量法铁律：**不要用 `grain.active=False` 当基线**（blur 只加在 active=True 分支里）。

所以质感参数的"求解"= 三件事：
    A. **换算**：像素单位的键按参考长边缩放（REF = 6264）
    B. **规格**：颗粒粗细按卷的公开规格填（数据表 → 参数），不是试
    C. **档位**：柔光/光晕/染料云只有少数离散档，人定

用法：
    from design.texture import plan_texture, warn, Style
    p = plan_texture(stock, paper, long_side=1080, style=Style())
    print(warn(p))
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 手册 §四：像素键缩放参考长边
REF_LONG_SIDE = 6264

# 手册 §9.15：换卷时（`apply_stocks_specifics=True`）会被卷表覆写的键
STOCK_SPECIFIC_KEYS = ('halation.strength', 'halation.sigma_r_um', 'halation.sigma_g_um',
                       'halation.sigma_b_um', 'couplers.gamma_samelayer_rgb',
                       'couplers.gamma_interlayer_b_to_rg')

# 像素单位的键（必须随出图长边缩放）
PIXEL_KEYS = ('grain.blur', 'grain.blur_dye_clouds_um', 'scan_lens_blur', 'scan_unsharp_sigma',
              'camera.lens_blur_um', 'enlarger.lens_blur')

# 规格类（跟卷走，但**卷 profile 里没有**，要我们按数据表填）
SPEC_KEYS = ('grain.rms_granularity',)

# 永不可复现（不进任何"取差"对比）
NONREPRO = ('print_render.glare',)

GRAIN_SPEC_FILE = os.path.join(os.path.dirname(__file__), 'data', 'grain_specs.json')


@dataclass
class Style:
    """质感的风格档（离散，人定；不是求解出来的）。

    档位含义照器材：柔光镜档位 / 卷抗晕强弱 / 是否逆光。
    """
    diffusion: str = 'off'          # off / 1_8 / 1_4 / 1_2 / 1
    halation_gain: float = 1.0      # 相对卷表的倍率（1.0 = 完全按卷的规格）
    dye_clouds_um: float = 3.0      # 染料云尺度（µm）
    grain_gain: float = 1.0         # 相对数据表规格的倍率（1.0 = 按规格）
    format_mm: float = 36.0         # 画幅长边（mm）—— 36 / 54(645) / 70(6×7)


@dataclass
class TexturePlan:
    params: Dict[str, object] = field(default_factory=dict)
    provenance: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def __str__(self):
        lines = []
        for k in sorted(self.params):
            lines.append('  %-32s %-22s ← %s' % (k, self.params[k], self.provenance.get(k, '?')))
        return '\n'.join(lines)


def load_grain_specs() -> Dict[str, dict]:
    """卷的公开颗粒规格表（我们维护，必须带出处）。文件不存在 = 空表。"""
    if not os.path.isfile(GRAIN_SPEC_FILE):
        return {}
    try:
        return json.load(open(GRAIN_SPEC_FILE, encoding='utf-8'))
    except Exception:
        return {}


# ---------------------------------------------------------------- A 换算

def px_scale(long_side: int) -> float:
    """像素键缩放：引擎按 6264 参考长边定义，出图小 ⇒ 按比例缩。"""
    return float(long_side) / float(REF_LONG_SIDE)


def scale_px(value: float, long_side: int) -> float:
    return value * px_scale(long_side)


# ---------------------------------------------------------------- 主入口

def plan_texture(stock: str, paper: str, long_side: int = 1080,
                 style: Optional[Style] = None) -> TexturePlan:
    """给一个卷+纸，产出质感参数与**出处**。没有出处的一律不写。"""
    st = style or Style()
    plan = TexturePlan()

    # 1) 颗粒粗细：按卷的公开规格（数据表），不是试
    specs = load_grain_specs()
    spec = specs.get(stock)
    if spec and spec.get('rms_granularity') is not None:
        rms = [float(v) * st.grain_gain for v in spec['rms_granularity']]
        plan.params['grain.rms_granularity'] = tuple(rms)
        plan.provenance['grain.rms_granularity'] = '规格：%s' % spec.get('source', '数据表')
    else:
        plan.warnings.append(
            'grain.rms_granularity 无规格：%s 不在 %s 里 —— 必须查数据表补，不许猜'
            % (stock, os.path.basename(GRAIN_SPEC_FILE)))

    # 2) 染料云：风格档（µm），与尺寸无关（物理长度）
    plan.params['grain.blur_dye_clouds_um'] = st.dye_clouds_um
    plan.provenance['grain.blur_dye_clouds_um'] = '风格档 dye_clouds_um'

    # 3) halation：由卷的 antihalation 决定（三档），这里只给倍率与提醒
    plan.params['halation.gain'] = st.halation_gain
    plan.provenance['halation.gain'] = '风格档（相对卷表倍率）'
    plan.warnings.append(
        'halation 的绝对值由卷表在三档 antihalation 上重写；'
        '本链 `PRESET_APPLY_STOCK_SPECIFICS = False` ⇒ **不会自动应用**，'
        '要"按卷的完整性格"必须显式打开或手动同步')

    # 4) 画幅：影响三条空间族 σ_px（36→70 时 ×0.514，手册已验）
    plan.params['film_format_mm'] = st.format_mm
    plan.provenance['film_format_mm'] = '风格档（画幅长边 mm）'
    if abs(st.format_mm - 36.0) > 1e-6:
        plan.params['_sigma_px_ratio'] = round(36.0 / st.format_mm, 4)
        plan.provenance['_sigma_px_ratio'] = '手册 §四：36→70 时 σ_px ×0.514'

    # 5) 柔光：离散档位（BPM），不是连续量
    plan.params['diffusion'] = st.diffusion
    plan.provenance['diffusion'] = '风格档（柔光镜档位）'

    # 6) 像素键提醒：本图长边下各键的理论缩放
    plan.params['_px_scale'] = round(px_scale(long_side), 5)
    plan.provenance['_px_scale'] = '像素键缩放 = 长边/%d' % REF_LONG_SIDE

    # 7) 禁用项
    plan.params['print_render.glare'] = 'off'
    plan.provenance['print_render.glare'] = '不可复现（numba RNG）⇒ 不参与任何取差'
    return plan


def warn(plan: TexturePlan, long_side: Optional[int] = None) -> str:
    """把该说的警告一次说清。"""
    w = list(plan.warnings)
    if long_side is not None and long_side < 2048:
        w.append('长边 %d < 2048：**不许用颗粒绝对值**（高频已被缩图抹掉），只能当方向'
                 % long_side)
    w.append('量法铁律：不要用 `grain.active=False` 当颗粒基线'
             '（blur 只加在 active=True 分支里，差值 = 一道全图糊 + 颗粒）')
    w.append('改 halation / 柔光会**串到颜色**（halation 带色）⇒ 要同时看肤色与中性轴')
    return '\n'.join('  ! ' + x for x in w)


def check(plan: TexturePlan) -> List[str]:
    """硬错误（不是提醒）：有出处才许写值。"""
    errs = []
    for k, v in plan.params.items():
        if k.startswith('_'):
            continue
        if k not in plan.provenance:
            errs.append('参数 %s=%s 没有出处' % (k, v))
    return errs
