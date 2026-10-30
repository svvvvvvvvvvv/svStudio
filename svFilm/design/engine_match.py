# -*- coding: utf-8 -*-
"""卡 → 引擎参数（θ = f(卡)）。**没有语料、没有大师预设。**

规则：
  · 引擎需要的每一个参数，只能来自**这张卡**；
  · 卡没声明 ⇒ 卡**不完整**，拒绝渲染（不退回任何"语料默认"）；
  · 声明值写进卡时就带出处（事实 / 人给 / 库匹配），这里只做换算。

介质怎么来（下一步的算法，见 `引擎设计.md` §7.0）：
  介质库里每个（卷, 纸）组合渲一次测试靶 → 测签名 → 卡的签名与库签名最近的那个胜出。
  这一步是"算出来的"；在库签名建好之前，卡必须显式带 `medium`。

跑：
  & $py -X utf8 -m design.engine_match --card "004_鹿井小渔_n6ab1e755"
  & $py -X utf8 -m design.engine_match --audit        # 看 64 张卡缺什么
"""
from __future__ import annotations

import argparse
import json
from typing import Dict, List, Optional

from .texture import Style, plan_texture, to_overrides, warn

CARDS = r'E:\ds\photo\_corpus\kurai\full\cards_v3.json'

MEDIUM_KEYS = ('stock', 'paper', 'format_mm')
TEXTURE_KEYS = ('diffusion', 'grain_gain', 'dye_clouds_um', 'halation_gain')


def missing_fields(card: dict) -> List[str]:
    miss: List[str] = []
    md = card.get('medium') or {}
    for k in MEDIUM_KEYS:
        if md.get(k) in (None, ''):
            miss.append('medium.%s' % k)
    tx = card.get('texture') or {}
    for k in TEXTURE_KEYS:
        if tx.get(k) in (None, ''):
            miss.append('texture.%s' % k)
    return miss


def style_of(card: dict) -> Style:
    md = card.get('medium') or {}
    tx = card.get('texture') or {}
    return Style(diffusion=str(tx.get('diffusion') or 'off'),
                 grain_gain=float(tx.get('grain_gain') or 1.0),
                 dye_clouds_um=float(tx.get('dye_clouds_um') or 3.0),
                 halation_gain=float(tx.get('halation_gain') or 1.0),
                 format_mm=float(md.get('format_mm') or 35.0))


def plan_for_card(card: dict, long_side: int = 1080) -> dict:
    miss = missing_fields(card)
    base = dict(card=card.get('name'), long_side=long_side,
                missing=miss, incomplete=bool(miss))
    if miss:
        base.update(overrides={}, provenance={},
                    warnings=['卡不完整：缺 %s ⇒ **拒绝渲染**（不退回任何默认）'
                              % '、'.join(miss)])
        return base

    md = card['medium']
    st = style_of(card)
    tp = plan_texture(str(md['stock']), str(md['paper']), long_side, st)
    ov = to_overrides(tp, long_side)
    rms = tp.params.get('grain.rms_granularity')
    if rms is not None:
        ov['film_render.grain.rms_granularity'] = rms
        tp.provenance['film_render.grain.rms_granularity'] = \
            tp.provenance.get('grain.rms_granularity', '数据表')

    base.update(declared=True, medium=md, texture=card.get('texture'),
                overrides=ov, provenance=tp.provenance,
                warnings=list(tp.warnings))
    return base


def _cards() -> List[dict]:
    return json.load(open(CARDS, encoding='utf-8')).get('cards') or []


def audit() -> int:
    cards = _cards()
    bad = [(c.get('name'), missing_fields(c)) for c in cards]
    bad = [(n, m) for n, m in bad if m]
    print('卡 %d 张 ｜ 不完整 %d 张' % (len(cards), len(bad)))
    if bad:
        print('缺得最多的前三张：')
        for n, m in bad[:3]:
            print('  %s：%s' % (n, '、'.join(m)))
        print('结论：介质/质感必须写进卡（或用介质库匹配算出来）才允许渲。')
    return 0 if not bad else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--card', default='')
    ap.add_argument('--audit', action='store_true')
    ap.add_argument('--long', type=int, default=1080)
    ap.add_argument('--json', default='')
    a = ap.parse_args(argv)

    if a.audit or not a.card:
        return audit()

    card = next((c for c in _cards() if c.get('name') == a.card), None)
    if card is None:
        raise SystemExit('没有这张卡：%s' % a.card)

    p = plan_for_card(card, a.long)
    print('卡 %s ｜ 完整=%s' % (p['card'], not p['incomplete']))
    if p['incomplete']:
        print('  缺：%s' % '、'.join(p['missing']))
        for w in p['warnings']:
            print('  ! ' + w)
        return 1
    print('介质 %s' % p['medium'])
    print('质感 %s' % p['texture'])
    print('覆盖（传到 presets.render(overrides=…)）：')
    for k in sorted(p['overrides']):
        print('  %-42s %-22s ← %s' % (k, p['overrides'][k], p['provenance'].get(k, '?')))
    print(warn(plan_texture(str(p['medium']['stock']), str(p['medium']['paper']),
                            a.long, style_of(card)), a.long))
    if a.json:
        json.dump(p, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print('写出 %s' % a.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
