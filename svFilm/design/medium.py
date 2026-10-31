# -*- coding: utf-8 -*-
"""每个目录自己的**介质**（2026-10-31：删掉"预设"这个概念）。

介质 = 一份完整的引擎参数块（卷 / 纸 / WB / amount / 化学 / 颗粒…），
放在 `analysis/corpus/kurai/media/<卡名>.json`，**一个目录一份**。

规矩：
  · 没有"预设列表"，没有"选一个 preset"，不按介质反查 —— 目录即介质。
  · 读不到就抛（不猜、不按语料兜底）。目录没介质 = 还没定义，不是"用默认"。
  · `presets/*.json` 只剩**模板**用途（生成新目录的介质块），不再作为可选项。

用法：
    from design.medium import medium_of, save
    block = medium_of(card)          # → 完整参数块（dict），直接喂 presets.render
"""
from __future__ import annotations

import json
import os
from typing import Optional

_ROOT = r'E:\svStudio\svFilm\analysis\corpus\kurai\media'
# 只作"生成新介质块"的模板（不是可选预设）
_TEMPLATE = r'E:\svStudio\svFilm\analysis\corpus\kurai\medium_base\鹿井.json'

# ★ 扫描端点是**引擎常量**（设计 引擎设计 §4.2：0.98 / 0.01），不跟卡的分布走。
SCAN_WHITE_LEVEL = 0.98
SCAN_BLACK_LEVEL = 0.01


def path_of(card_name: str) -> str:
    return os.path.join(_ROOT, '%s.json' % card_name)


def load(card_name: str) -> Optional[dict]:
    p = path_of(card_name)
    if not os.path.isfile(p):
        return None
    return json.load(open(p, encoding='utf-8'))


def medium_of(card) -> dict:
    """这张卡的介质块。没有就**抛**——目录没介质是"未定义"，不是"用默认"。"""
    name = getattr(card, 'name', str(card))
    b = load(name)
    if b is None:
        raise KeyError('目录没有介质：%s（应该在 %s）' % (name, path_of(name)))
    return b


def template() -> dict:
    return json.load(open(_TEMPLATE, encoding='utf-8'))


def save(card_name: str, block: dict, note: str = '') -> str:
    os.makedirs(_ROOT, exist_ok=True)
    p = path_of(card_name)
    if note:
        block = dict(block, _note_medium=note)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(block, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def from_preset(card_name: str, preset_name: str, note: str = '') -> dict:
    """把某个 preset 的**内容**抄成这个目录的介质（一次性迁移用）。

    抄完就是目录自己的了：以后改它，不会影响别的目录，也不再有"共享预设"。
    """
    src = os.path.join(r'E:\svStudio\svFilm\svFilm\data\presets', preset_name, 'preset.json')
    block = json.load(open(src, encoding='utf-8'))
    save(card_name, block, note=note or ('迁移自 preset「%s」' % preset_name))
    return block


# ---------------------------------------------------------------- 目录级目标（只换算）

_CARDS = r'E:\svStudio\svFilm\analysis\corpus\kurai\cards_v5.json'
_TONE = r'E:\svStudio\svFilm\analysis\corpus\kurai\card_tone.json'


def _y_of_l(L: float) -> float:
    """CIE L* → 相对亮度 Y。"""
    return ((L + 16.0) / 116.0) ** 3 if L > 8.0 else L / 903.3


def _code_of_y(y: float) -> float:
    """线性亮度 Y → **sRGB 编码码值**（0~1）。

    ★ 2026-10-31 修（公式错误）：引擎里 `scanner.white_level / black_level` 是
      **sRGB 编码值**，内部再用 `_remove_sRGB_cctf()` 线性化
      （`runtime/services/color_reference.py:27`）。我们之前直接写线性 Y，
      会被二次线性化：0.9227 → 0.833，白端整体压暗约 3.5 L*。
    """
    if y <= 0.0031308:
        return 12.92 * y
    return 1.055 * (y ** (1.0 / 2.4)) - 0.055


def _cc(L: float, a: float, b: float):
    """(L*,a*,b*) → (蓝/黄 CC, 红/绿 CC)。

    ⚠ 这是**色度比**的近似：把 b* 反解成 Z/Y 的亮度比，再以 1 CC = 0.01 密度换算。
      柯达 CC 的严格定义是**滤片密度**（作用在相纸光谱灵敏度上），不是 XYZ 的 Z/Y 比；
      两者只在一阶上一致。所以它是"CC 当量"，不是"定义式"。
    """
    import math
    fY = (L + 16.0) / 116.0
    Z = (fY - b / 200.0) ** 3
    X = (fY + a / 500.0) ** 3
    Y = fY ** 3
    return (math.log10(Z / Y) * 100.0, math.log10(X / Y) * 100.0)


def targets_from_card(card_name: str) -> dict:
    """卡的**目录级目标**：打印端 + 色偏 CC + 形状带。

    全部是定义式换算（L*→Y→D、Lab→CC），不渲染、不拟合：
      · scanner.white_level = Y(p95 上沿)；black_level = Y(p5 下沿)
      · p50 的目标密度 D = −log10(Y(p50 中点))
      · 暗部/高光的色偏 → 蓝黄、红绿 CC 当量
      · 形状：toe / shoulder / contrast_win 带（引擎侧对应 print morph 的
        fast / slow / global gamma，方向：带越宽 ⇒ gamma 越小）
    """
    cards = {c['name']: c for c in json.load(open(_CARDS, encoding='utf-8'))['cards']}
    c = cards.get(card_name)
    if not c:
        raise KeyError('没有这张卡：%s' % card_name)
    pt = c.get('points') or {}

    def _point(key, fallback=None):
        """点值优先；没有点值就返回 None（不退回带中点/带边）。"""
        v = pt.get(key)
        if isinstance(v, (int, float)):
            return float(v)
        return fallback

    out = dict(
        p50_target_D=None,
        shape={k: _point(k, None) for k in ('toe', 'shoulder', 'contrast_win', 'skew')},
        warm_cool_b=_point('warm_cool_b', None),
        face=dict(C=_point('face_C', None), h=_point('face_h', None),
                  L=_point('face_L', None)),
    )
    v50 = _point('p50', None)
    if v50 is not None:
        out['p50_target_D'] = round(-__import__('math').log10(_y_of_l(v50)), 3)
    tone = json.load(open(_TONE, encoding='utf-8')).get(card_name) if os.path.isfile(_TONE) else None
    if tone:
        for z in ('shadow', 'highlight', 'neutral'):
            t = tone.get(z) or {}
            lab = t.get('lab')
            if lab:
                dby, drg = _cc(*lab)
                out[z] = dict(lab=lab, cc_blue_yellow=round(dby, 1),
                              cc_red_green=round(drg, 1))
    return out


def apply_targets(card_name: str) -> str:
    """把目录级目标写进它的介质。

    · 扫描端点 → **引擎常量** `0.98 / 0.01`（不跟卡的 p95/p5 走；卡只做参考）
    · **整幅色偏 → `simulation.print_y_filter_shift / print_m_filter_shift`**
      （放大机 CC 滤片位移，单位 = Kodak CC；1 CC = 0.01 密度）
      为什么用中性轴而不是暗部：滤片是**整幅**的，暗部青蓝来自分色，不是白平衡。
    · 其余（p50 点值密度、形状点值、暗部/高光 CC）→ `_target`，留给批次级求解
    """
    tg = targets_from_card(card_name)
    block = load(card_name)
    if block is None:
        raise KeyError('目录没有介质：%s（先 from_preset 迁移一份）' % card_name)
    sim = dict(block.get('simulation') or {})
    # ★ 柔光的**统一策略**（2026-10-31 人工定，不是推导量）：
    #   关掉放大端柔光，只保留相机端 1/4 档黑柔（Black Pro-Mist）。
    #   为什么关放大端：两层柔光叠起来等于"印相时又加一层扩散"，会让高光发虚两次；
    #   而柔光镜在拍摄端才是物理上真实存在的那一层（印相端柔光是冲洗工艺选择）。
    sim['diffusion_filter_active'] = False
    sim['camera_diffusion_filter_active'] = True
    sim['camera_diffusion_filter_family'] = 'black_pro_mist'
    sim['camera_diffusion_filter_strength'] = 0.25
    # ★ 2026-10-31：扫描端点是引擎常量，不是卡的统计量（拿卡的 p95/p5 当端点 =
    #   把场景当介质；卡只用于 _target 里的点值参考）。
    sim['scan_white_level'] = SCAN_WHITE_LEVEL
    sim['scan_black_level'] = SCAN_BLACK_LEVEL
    neu = tg.get('neutral') or {}
    lab = neu.get('lab')
    if lab:
        by, rg = _cc(*lab)          # by: 正=偏蓝；rg: 正=偏红
        # 符号：黄滤片吸收蓝 ⇒ 要更蓝 = 减黄（shift 取 −CC）；
        #       品红吸收绿 ⇒ 要更红 = 加品红（shift 取 +CC）。
        # ⚠ 这是按"吸收互补色"推的符号约定，第一次出片要人眼确认一次。
        sim['print_y_filter_shift'] = round(-by, 1)
        sim['print_m_filter_shift'] = round(rg, 1)
        tg['filter_shift'] = dict(cc_blue_yellow=round(by, 1), cc_red_green=round(rg, 1),
                                  y=sim['print_y_filter_shift'],
                                  m=sim['print_m_filter_shift'])
    block['simulation'] = sim
    block.pop('scanner', None)          # 旧写法留下的空壳，删掉免得再被误认
    # ★ 形状：只用于**选纸**（toe/shoulder 分位 ↔ 纸的 σ 分位）。
    #   直接换算成 morph 的 γ 会得出 1.2~4.2（全撞边界）——L* 间距与 logE 层宽
    #   不可通约，必须靠前向模型（渲探针）才能解。在解出来之前，morph 保持 1.0。
    tg['morph'] = 'identity（未解：需前向模型；形状已用于选纸）'
    block['_target'] = tg
    return save(card_name, block,
                note='扫描端点/放大机滤片写进 simulation.*；其余目标见 _target')
