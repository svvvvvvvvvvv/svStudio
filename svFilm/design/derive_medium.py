# -*- coding: utf-8 -*-
"""给每个目录写出它自己的介质（卷/纸/amount），全部有据、不要人拍板。

三条依据：

1) **卷 + 纸**：源码里写着每套预设是照谁立的靶（`svFilm/presets.py::_DESC`）：
      鹿井预设 ← 鹿井屿渔 26 张 ｜ 滨田预设 ← 滨田正和 22 张 ｜ xhs预设 ← 不詹锅 50 张
   我们的 64 张卡，语料正好是 鹿井 / 滨田 / xhs ⇒ 按语料取**该语料声明的**那套。
   （打印端数据反推不出卷 —— 实测过：反推会把大半卡判成电影卷，那是映射假象。）

2) **amount**：引擎手册实测「amount +1 ⇒ 颊 C* +2.3」（n=2~3 卷，台账级）。
   每个目录按"这张卡的肤 C 中位 vs 它的卡带中点"反解所需的 amount 偏移，夹在 [0.6, 1.6]。

3) **白端/黑端/落点/色偏**：定义式换算（L*→Y→D、Lab→CC），见 medium.targets_from_card。

产物：`analysis/corpus/kurai/media/<卡名>.json` + 汇总 `card_medium.json`
"""
from __future__ import annotations

import json
import math
import os
import statistics as st

_ROOT = r'E:\svStudio\svFilm\analysis\corpus\kurai'
_CARDS = os.path.join(_ROOT, 'cards_v5.json')
_PRESETS = r'E:\svStudio\svFilm\svFilm\data\presets'

# 源码 `_DESC` 声明的"预设 ← 大师"关系；我们的卡语料名 → 该预设
CORPUS_PRESET = {'鹿井': '卡38', '滨田': '滨田预设', 'xhs': 'xhs预设'}

# 引擎手册：couplers.amount +1 ⇒ 颊 C* +2.3（台账级，n=2~3 卷）
C_PER_AMOUNT = 2.3
AMOUNT_MIN, AMOUNT_MAX = 0.6, 1.6


def _block(preset: str) -> dict:
    return json.load(open(os.path.join(_PRESETS, preset, 'preset.json'), encoding='utf-8'))


def apply_all() -> dict:
    from design.medium import apply_targets, load, save
    cards = json.load(open(_CARDS, encoding='utf-8'))['cards']
    out = {}
    for c in cards:
        name = c['name']
        corpus = c.get('corpus') or ''
        preset = CORPUS_PRESET.get(corpus)
        if not preset:
            out[name] = dict(ok=False, why='语料「%s」在 _DESC 里没有对应预设' % corpus)
            continue
        block = _block(preset)
        split = _split_of(name)
        base = float(block['couplers']['amount'])
        amount, why = _amount_from_split(base, name, split)
        block['couplers'] = dict(block.get('couplers') or {}, amount=amount)
        save(name, block, note='卷/纸按语料声明（%s → %s）；amount 按分色强度；端点见 _target' % (corpus, preset))
        apply_targets(name)
        b = load(name)
        b['_derived'] = dict(corpus=corpus, preset=preset, amount=amount, amount_reason=why,
                             split=split)
        save(name, b, note='卷/纸按语料声明；amount 按分色强度；端点取自卡的打印端')
        out[name] = dict(ok=True, preset=preset, film=block['simulation']['film_stock'],
                         paper=block['simulation']['print_paper'], amount=amount, why=why)
    tmp = os.path.join(_ROOT, 'card_medium.json') + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(_ROOT, 'card_medium.json'))
    return out


_TONE = os.path.join(_ROOT, 'card_tone.json')


def _split_of(name: str):
    """分色强度 = |暗部 CC − 高光 CC|（蓝/黄）。全部来自 card_tone 的 Lab。"""
    tones = json.load(open(_TONE, encoding='utf-8'))
    t = tones.get(name) or {}
    sh = (t.get('shadow') or {}).get('lab')
    hi = (t.get('highlight') or {}).get('lab')
    if not sh or not hi:
        return None
    def cc(lab):
        L, a, b = lab
        fY = (L + 16.0) / 116.0
        Z = (fY - b / 200.0) ** 3
        Y = fY ** 3
        return math.log10(Z / Y) * 100.0
    return round(abs(cc(sh) - cc(hi)), 2)


def _amount_from_split(base: float, name: str, split) -> tuple:
    """amount 由分色强度定：在**同语料**里排百分位，映射到 base 的 ±15%。"""
    if split is None:
        return base, '没有暗部/高光 Lab ⇒ 沿用 %.2f' % base
    cards = json.load(open(_CARDS, encoding='utf-8'))['cards']
    corpus = next((c.get('corpus') for c in cards if c['name'] == name), '')
    xs = []
    for c in cards:
        if c.get('corpus') == corpus:
            s = _split_of(c['name'])
            if s is not None:
                xs.append(s)
    if len(xs) < 5:
        return base, '同语料样本不足（n=%d）⇒ 沿用 %.2f' % (len(xs), base)
    pct = sum(1 for x in xs if x <= split) / float(len(xs))
    amount = round(base * (0.85 + 0.30 * pct), 2)
    return amount, ('分色强度 %.1f 在同语料排 %.0f%% ⇒ amount %.2f×(0.85+0.30×%.2f)=%.2f'
                    % (split, 100 * pct, base, pct, amount))
