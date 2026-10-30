# -*- coding: utf-8 -*-
"""卡 → 介质推断（建卡时算好，不让人选）。

思路：只用量到的卡的带，和作者 profile 里读出来的"介质性格"，做**秩匹配**。

  纸：卡的反差越高 → 纸越硬（窗越小）
  卷：卡的冷暖越暖 → 卷的红峰越长（Pro400H 610 → Portra800 660）
      且卷的官配纸必须等于选中的纸（作者 target_print）

⚠ 这是**低置信推断**（10-19 卷纸研究：留一图排名不稳）。
   **2026-10-30 实测：62 张同一大师的卡 → 推出 36 种不同组合** ⇒ 秩推断被卡级内容噪声淹没，
   不能当生产路径。留作诊断（证明"从带反推介质"这条路不通），
   生产改走**批级预演**：选卡后，用本批 1–2 张代表图渲 N 个候选，量 ①′ 与卡的带比距离。

跑：
  & $py -X utf8 -m design.medium_infer --card "004_鹿井小渔_n6ab1e755"
  & $py -X utf8 -m design.medium_infer --all
"""
from __future__ import annotations

import argparse
import json
from typing import Dict, List, Optional

import numpy as np

from .profile_table import table

CARDS = r'E:\ds\photo\_corpus\kurai\full\cards_v3.json'


def _mid(b):
    return None if not b else 0.5 * (float(b[0]) + float(b[1]))


def _pct(values: List[float], x: float) -> float:
    a = np.asarray([v for v in values if v is not None], dtype=float)
    if len(a) < 3 or x is None:
        return 0.5
    return float((a < x).mean())


def infer(card: dict, all_cards: List[dict]) -> dict:
    t = table()
    papers = sorted([p for p in t['papers'] if p.get('window')], key=lambda p: -p['window'])
    films = [f for f in t['films']
             if f.get('type') == 'negative' and f.get('use') == 'still' and f.get('peaks')]

    conts = [_mid((c.get('bands') or {}).get('contrast')) for c in all_cards]
    warms = [_mid((c.get('bands') or {}).get('warm_cool_b')) for c in all_cards]
    c_rank = _pct(conts, _mid((card.get('bands') or {}).get('contrast')))
    w_rank = _pct(warms, _mid((card.get('bands') or {}).get('warm_cool_b')))

    # 纸：反差高 → 硬纸（窗小）。rank 0 = 最低反差 → 最软纸
    pi = int(round(c_rank * (len(papers) - 1)))
    paper = papers[max(0, min(pi, len(papers) - 1))]

    # 卷：在该纸的官配卷里，按红峰从短到长排，卡的冷暖 rank 决定位置
    cand = [f for f in films if f.get('target_print') == paper['stock']]
    if not cand:
        cand = films
    cand.sort(key=lambda f: f['peaks'].get('R', 0))
    fi = int(round(w_rank * (len(cand) - 1)))
    film = cand[max(0, min(fi, len(cand) - 1))]

    return dict(
        card=card.get('name'),
        medium=dict(stock=film['stock'], paper=paper['stock'], format_mm=35.0),
        evidence=dict(
            card_contrast=round(_mid((card.get('bands') or {}).get('contrast')) or 0, 2),
            contrast_rank=round(c_rank, 3),
            paper_window=paper['window'], paper_n=len(papers), paper_idx=pi,
            card_warm=round(_mid((card.get('bands') or {}).get('warm_cool_b')) or 0, 2),
            warm_rank=round(w_rank, 3),
            film_red_peak=film['peaks'].get('R'), film_n=len(cand), film_idx=fi),
        confidence='low',
        verify='渲一张代表图 → 量 ①′ 与卡的带 → 偏了就挪一档，不搜')


def _cards() -> List[dict]:
    return json.load(open(CARDS, encoding='utf-8')).get('cards') or []


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--card', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--json', default='')
    a = ap.parse_args(argv)

    cards = _cards()
    if a.all:
        out = [infer(c, cards) for c in cards]
        from collections import Counter
        cnt = Counter('%s+%s' % (r['medium']['stock'], r['medium']['paper']) for r in out)
        print('推断 %d 张卡：' % len(out))
        for k, v in cnt.most_common():
            print('  %-60s %d 张' % (k, v))
        if a.json:
            json.dump(out, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print('写出 %s' % a.json)
        return 0

    card = next((c for c in cards if c.get('name') == a.card), None)
    if not card:
        raise SystemExit('没有这张卡：%s' % a.card)
    r = infer(card, cards)
    print('卡 %s' % r['card'])
    print('推断介质 %s + %s' % (r['medium']['stock'], r['medium']['paper']))
    print('依据 %s' % json.dumps(r['evidence'], ensure_ascii=False))
    print('置信 %s ｜ 校验 %s' % (r['confidence'], r['verify']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
