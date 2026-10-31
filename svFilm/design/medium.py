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
_TEMPLATE = r'E:\svStudio\svFilm\svFilm\data\presets\卡38\preset.json'


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
    """CIE L* → 相对亮度。"""
    return ((L + 16.0) / 116.0) ** 3 if L > 8.0 else L / 903.3


def _cc(L: float, a: float, b: float):
    """(L*,a*,b*) → (蓝/黄 CC, 红/绿 CC)。1 CC = 0.01 密度（定义式，不拟合）。"""
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
    b = c.get('bands') or {}
    p5, p50, p95 = b.get('p5'), b.get('p50'), b.get('p95')
    out = dict(
        white_level=round(_y_of_l(p95[1]), 4) if p95 else None,
        black_level=round(_y_of_l(p5[0]), 4) if p5 else None,
        p50_target_D=round(-__import__('math').log10(_y_of_l(0.5 * (p50[0] + p50[1]))), 3)
        if p50 else None,
        shape={k: b.get(k) for k in ('toe', 'shoulder', 'contrast_win', 'skew')},
        warm_cool_b=b.get('warm_cool_b'),
        face=dict(C=b.get('face_C'), h=b.get('face_h'), L=b.get('face_L')),
    )
    tone = json.load(open(_TONE, encoding='utf-8')).get(card_name) if os.path.isfile(_TONE) else None
    if tone:
        for z in ('shadow', 'highlight'):
            t = tone.get(z) or {}
            lab = t.get('lab')
            if lab:
                dby, drg = _cc(*lab)
                out[z] = dict(lab=lab, cc_blue_yellow=round(dby, 1),
                              cc_red_green=round(drg, 1))
    return out


def apply_targets(card_name: str) -> str:
    """把目录级目标写进它的介质：scanner 端点直接落，其余进 `_target` 供批次求解。"""
    tg = targets_from_card(card_name)
    block = load(card_name)
    if block is None:
        raise KeyError('目录没有介质：%s（先 from_preset 迁移一份）' % card_name)
    sc = dict(block.get('scanner') or {})
    if tg.get('white_level') is not None:
        sc['white_level'] = tg['white_level']
    if tg.get('black_level') is not None:
        sc['black_level'] = tg['black_level']
    block['scanner'] = sc
    block['_target'] = tg
    return save(card_name, block, note='scanner 端点取自卡的打印端；其余目标见 _target')
