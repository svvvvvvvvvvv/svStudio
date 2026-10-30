# -*- coding: utf-8 -*-
"""卡 → 引擎质感计划（"引擎匹配卡"的唯一合法路径）。

结论先写：
  · 1080 网络图**测不出**卡级质感（实测：卡内 IQR ≈ 卡间 IQR，743 张 / 62 卡）；
  · 所以质感只能走**声明**：卡带 medium / texture 两个字段（事实或人给的），
    没声明就用所属语料默认，并记账"按语料，不是按卡"。

产出：`presets.render(overrides=...)` 能直接吃的运行时属性路径 + 出处。

跑：
  & $py -X utf8 -m design.engine_match --card "004_鹿井小渔_n6ab1e755"
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, Optional

from .texture import Style, plan_texture, to_overrides, warn

CARDS = r'E:\ds\photo\_corpus\kurai\full\cards_v3.json'

CORPUS_MEDIUM = {
    '鹿井': dict(stock='kodak_portra_400', paper='kodak_portra_endura', format_mm=35.0),
    '滨田': dict(stock='kodak_portra_400', paper='kodak_portra_endura', format_mm=70.0),
    'xhs': dict(stock='kodak_gold_200', paper='kodak_portra_endura', format_mm=36.0),
}

TEXTURE_DEFAULT = dict(diffusion='off', grain_gain=1.0, dye_clouds_um=3.0, halation_gain=1.0)


def medium_of(card: dict) -> Dict[str, object]:
    base = dict(CORPUS_MEDIUM.get(card.get('corpus') or '', CORPUS_MEDIUM['鹿井']))
    base.update({k: v for k, v in (card.get('medium') or {}).items() if v is not None})
    return base


def style_of(card: dict) -> Style:
    t = dict(TEXTURE_DEFAULT)
    t.update({k: v for k, v in (card.get('texture') or {}).items() if v is not None})
    return Style(diffusion=str(t['diffusion']), grain_gain=float(t['grain_gain']),
                 dye_clouds_um=float(t['dye_clouds_um']), halation_gain=float(t['halation_gain']),
                 format_mm=float(medium_of(card)['format_mm']))


def plan_for_card(card: dict, long_side: int = 1080) -> dict:
    md = medium_of(card)
    st = style_of(card)
    tp = plan_texture(str(md['stock']), str(md['paper']), long_side, st)
    ov = to_overrides(tp, long_side)

    # `to_overrides` 不含颗粒粗细（它按卷的公开规格算），这里补上运行时路径
    rms = tp.params.get('grain.rms_granularity')
    if rms is not None:
        ov['film_render.grain.rms_granularity'] = rms
        tp.provenance['film_render.grain.rms_granularity'] = (
            tp.provenance.get('grain.rms_granularity', '数据表'))

    declared = bool(card.get('medium') or card.get('texture'))
    warns = list(tp.warnings)
    if not declared:
        warns.append('本卡没有声明 medium / texture ⇒ 用「%s」的语料默认：'
                     '**按语料，不是按卡**' % (card.get('corpus') or '?'))
    warns.append('质感绝对值在 1080 网络图上不可测（卡内 IQR ≈ 卡间 IQR）；'
                 '要按卡对齐必须拿到大师原扫描件')

    return dict(card=card.get('name'), corpus=card.get('corpus'),
                declared=declared, medium=md,
                texture=dict(diffusion=st.diffusion, grain_gain=st.grain_gain,
                             dye_clouds_um=st.dye_clouds_um,
                             halation_gain=st.halation_gain),
                long_side=long_side, overrides=ov,
                provenance=tp.provenance, warnings=warns)


def _load_card(name: str) -> dict:
    d = json.load(open(CARDS, encoding='utf-8'))
    for c in d.get('cards') or []:
        if c.get('name') == name:
            return c
    raise SystemExit('没有这张卡：%s' % name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--card', default='004_鹿井小渔_n6ab1e755')
    ap.add_argument('--long', type=int, default=1080)
    ap.add_argument('--json', default='')
    a = ap.parse_args(argv)

    p = plan_for_card(_load_card(a.card), a.long)
    print('卡 %s ｜ 语料 %s ｜ 声明=%s' % (p['card'], p['corpus'], p['declared']))
    print('介质 %s' % p['medium'])
    print('质感 %s' % p['texture'])
    print('覆盖（传到 presets.render(overrides=…)）：')
    for k in sorted(p['overrides']):
        print('  %-42s %-24s ← %s' % (k, p['overrides'][k], p['provenance'].get(k, '?')))
    print(warn(plan_texture(str(p['medium']['stock']), str(p['medium']['paper']), a.long,
                            style_of(_load_card(a.card))), a.long))
    for w in p['warnings']:
        print('  ! ' + w)
    if a.json:
        json.dump(p, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print('写出 %s' % a.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
