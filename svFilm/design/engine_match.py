# -*- coding: utf-8 -*-
"""卡 → 引擎参数（θ = f(卡)）。没有语料，没有大师预设。

两件事分开：

  ① 介质（卷/纸/画幅）：**每批按卡匹配**算出来
       候选介质 × 本批代表图 → 渲 → 量不变量 → 与卡的带比距离 → 最小者胜出
       本文件的 `nearest_medium()` 就是最后那一步（渲由 batch 侧负责）。

  ② 质感（颗粒/染料云/光晕/柔光）：**不能匹配**
       1080 网络图测不到卡级质感（实测：卡内 IQR ≈ 卡间 IQR）
       ⇒ 用引擎默认档，**记账"未对齐"**，不许假装是从卡算的。

跑：
  & $py -X utf8 -m design.engine_match --card "004_鹿井小渔_n6ab1e755" --medium '{"stock":"kodak_portra_400","paper":"kodak_portra_endura","format_mm":35}'
  & $py -X utf8 -m design.engine_match --audit
"""
from __future__ import annotations

import argparse
import json
from typing import Dict, List, Optional

from .texture import Style, plan_texture, to_overrides, warn

CARDS = r'E:\ds\photo\_corpus\kurai\full\cards_v3.json'

# 质感：不能从卡算，只能用引擎默认档（并记账）
TEXTURE_DEFAULT = dict(diffusion='off', grain_gain=1.0, dye_clouds_um=3.0, halation_gain=1.0)

# 匹配用的不变量（卡上有、引擎渲出来也能量）
MATCH_KEYS = ('contrast', 'toe', 'shoulder', 'face_C', 'face_h', 'warm_cool_b')


def _band_mid(b):
    return None if not b else 0.5 * (float(b[0]) + float(b[1]))


def _band_w(b):
    return None if not b else max(abs(float(b[1]) - float(b[0])), 1e-6)


def nearest_medium(card_bands: dict, table: List[dict]) -> Optional[dict]:
    """table: [{stock, paper, format_mm, bands:{contrast:[lo,hi], ...}}, ...]

    距离 = Σ |库中位 − 卡中位| / 卡带宽（带越大越宽容）；只看两边都有的键。
    """
    best, best_d = None, float('inf')
    for cand in table:
        cb = cand.get('bands') or {}
        ds, n = 0.0, 0
        for k in MATCH_KEYS:
            if k not in cb or not card_bands.get(k):
                continue
            d = abs(_band_mid(cb[k]) - _band_mid(card_bands[k]))
            ds += d / _band_w(card_bands[k])
            n += 1
        if n < 3:
            continue
        d = ds / n
        if d < best_d:
            best, best_d = cand, d
    if best is None:
        return None
    out = dict(best)
    out['distance'] = round(best_d, 4)
    return out


def plan_for_card(card: dict, medium: dict, long_side: int = 1080) -> dict:
    """medium 由 `nearest_medium()` 或人拍板给出；texture 用默认并记账。"""
    tx = dict(TEXTURE_DEFAULT)
    tx.update({k: v for k, v in (card.get('texture') or {}).items() if v is not None})
    st = Style(diffusion=str(tx['diffusion']), grain_gain=float(tx['grain_gain']),
               dye_clouds_um=float(tx['dye_clouds_um']), halation_gain=float(tx['halation_gain']),
               format_mm=float(medium.get('format_mm') or 35.0))
    tp = plan_texture(str(medium['stock']), str(medium['paper']), long_side, st)
    ov = to_overrides(tp, long_side)
    rms = tp.params.get('grain.rms_granularity')
    if rms is not None:
        ov['film_render.grain.rms_granularity'] = rms
        tp.provenance['film_render.grain.rms_granularity'] = \
            tp.provenance.get('grain.rms_granularity', '数据表')

    ws = list(tp.warnings)
    ws.append('质感未与卡对齐（1080 测不到卡级质感）⇒ 用的是引擎默认档：%s' % tx)
    return dict(card=card.get('name'), medium=medium, texture=tx,
                long_side=long_side, overrides=ov, provenance=tp.provenance, warnings=ws)


def _cards() -> List[dict]:
    return json.load(open(CARDS, encoding='utf-8')).get('cards') or []


def audit() -> int:
    cards = _cards()
    print('卡 %d 张（介质每批匹配；质感不能匹配，用引擎默认）' % len(cards))
    has = [c for c in cards if c.get('medium')]
    print('  已带 medium 的卡：%d 张（有就优先用，没有就走匹配）' % len(has))
    print('  下一步：渲候选介质 × 1–2 张代表图 → 填一张 table → `nearest_medium()` 选最近')
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--card', default='')
    ap.add_argument('--stock', default='', help='卷，如 kodak_portra_400')
    ap.add_argument('--paper', default='', help='纸，如 kodak_portra_endura')
    ap.add_argument('--fmt', type=float, default=35.0, help='画幅长边 mm')
    ap.add_argument('--table', default='', help='候选介质签名表 JSON（nearest_medium 用）')
    ap.add_argument('--audit', action='store_true')
    ap.add_argument('--long', type=int, default=1080)
    ap.add_argument('--json', default='')
    a = ap.parse_args(argv)

    if a.audit or not a.card:
        return audit()

    card = next((c for c in _cards() if c.get('name') == a.card), None)
    if card is None:
        raise SystemExit('没有这张卡：%s' % a.card)

    medium = card.get('medium')
    if a.stock and a.paper:
        medium = dict(stock=a.stock, paper=a.paper, format_mm=a.fmt)
    elif a.table and not medium:
        table = json.load(open(a.table, encoding='utf-8')).get('candidates') or []
        medium = nearest_medium(card.get('bands') or {}, table)
        if medium:
            print('匹配到介质 %s（距离 %.3f）' % (medium.get('stock'), medium['distance']))
    if not medium:
        raise SystemExit('这张卡没有 medium，也没有 --medium / --table；'
                         '介质必须每批按卡匹配（见 引擎设计 §3）')

    p = plan_for_card(card, medium, a.long)
    print('卡 %s ｜ 介质 %s' % (p['card'], p['medium']))
    print('覆盖（传到 presets.render(overrides=…)）：')
    for k in sorted(p['overrides']):
        print('  %-42s %-22s ← %s' % (k, p['overrides'][k], p['provenance'].get(k, '?')))
    for w in p['warnings']:
        print('  ! ' + w)
    if a.json:
        json.dump(p, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print('写出 %s' % a.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
