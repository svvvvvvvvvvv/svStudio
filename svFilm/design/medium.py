# -*- coding: utf-8 -*-
"""每个目录自己的**介质**（2026-11-02：并进目录 JSON）。

介质 = 一份完整的引擎参数块（卷 / 纸 / WB / amount / 化学 / 颗粒…），
存在 `analysis/corpus/kurai/cards/<卡名>.json` 的 `medium` 字段里。
**一个目录一份 JSON**；不再拆 `media/<卡名>.json` + `card_medium.json`。

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

_CARDS_DIR = r'E:\svStudio\svFilm\analysis\corpus\kurai\cards'
# 只作"生成新介质块"的模板（不是可选预设）
_TEMPLATE = r'E:\svStudio\svFilm\analysis\corpus\kurai\medium_base\鹿井.json'

# ★ 扫描端点是**引擎常量**（设计 引擎设计 §4.2：0.98 / 0.01），不跟卡的分布走。
SCAN_WHITE_LEVEL = 0.98
SCAN_BLACK_LEVEL = 0.01


def path_of(card_name: str) -> str:
    return os.path.join(_CARDS_DIR, '%s.json' % card_name)


def _load_doc(card_name: str) -> Optional[dict]:
    p = path_of(card_name)
    if not os.path.isfile(p):
        return None
    return json.load(open(p, encoding='utf-8'))


def load(card_name: str) -> Optional[dict]:
    d = _load_doc(card_name)
    if d is None:
        return None
    b = d.get('medium')
    return b if isinstance(b, dict) else None


def medium_of(card) -> dict:
    """这张卡的介质块。没有就**抛**——目录没介质是"未定义"，不是"用默认"。"""
    if isinstance(card, dict):
        name = card.get('name')
        if isinstance(card.get('medium'), dict):
            return card['medium']
    else:
        name = getattr(card, 'name', None)
    name = name or str(card)
    b = load(name)
    if b is None:
        raise KeyError('目录没有介质：%s（应该在 %s 的 medium 字段）'
                       % (name, path_of(name)))
    return b


def template() -> dict:
    return json.load(open(_TEMPLATE, encoding='utf-8'))


def save(card_name: str, block: dict, note: str = '') -> str:
    """把介质块写回**目录 JSON** 的 `medium` 字段；不动卡的其他字段。"""
    p = path_of(card_name)
    doc = _load_doc(card_name)
    if doc is None:
        raise KeyError('没有目录 JSON：%s' % p)
    if note:
        block = dict(block, _note_medium=note)
    doc['medium'] = block
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def update_card(card_name: str, **fields) -> str:
    """更新目录 JSON 的顶层字段（pe/tone/stock/paper/content…），原子写。"""
    p = path_of(card_name)
    doc = _load_doc(card_name)
    if doc is None:
        raise KeyError('没有目录 JSON：%s' % p)
    doc.update(fields)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


# ---------------------------------------------------------------- 目录级目标（只换算）

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
    """卡的**目录级目标**：打印端 + 色偏 CC。

    全部是定义式换算（Lab→CC），不渲染、不拟合：
      · 暗部/高光/中性的色偏 → 蓝黄、红绿 CC 当量
      · 卡不再提供逐图形状/肤色/冷暖靶（这些走 peer）
    """
    c = _load_doc(card_name)
    if not c:
        raise KeyError('没有这张卡：%s' % card_name)

    out = {}
    tone = c.get('tone') or {}
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
    · 其余（暗部/高光/中性 CC）→ `_target`，留给批次级求解
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
    # ★ 2026-10-31：**关掉引擎的逐图自动曝光**。引擎只做介质；落点归 LR。
    #   开着 subject 测光 = 引擎在做 LR 的活，PE 也不是真正的"固定基线"。
    sim['auto_exposure'] = False
    sim['auto_exposure_method'] = 'center_weighted'
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
