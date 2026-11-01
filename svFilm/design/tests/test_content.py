# -*- coding: utf-8 -*-
"""内容匹配的纯函数单测（不碰图、不碰渲染）。

跑：
    & $py -X utf8 design/tests/test_content.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.card_content import normalize_batch, rank


def _cards():
    return {
        'A_street': dict(card='A_street', source='manual', confidence=0.9,
                         scene='street', subject='single', palette='green',
                         elements=['trees', 'road', 'buildings'],
                         light='soft',
                         clothing=dict(type='skirt+top', palette='white',
                                       pattern='solid', silhouette='flowy',
                                       coverage='short_sleeve')),
        'B_sea': dict(card='B_sea', source='manual', confidence=0.9,
                      scene='seaside', subject='single', palette='blue',
                      elements=['sea', 'sky', 'wall'],
                      light='hard',
                      clothing=dict(type='dress', palette='blue',
                                    pattern='solid', silhouette='fitted',
                                    coverage='sleeveless')),
        'C_auto': dict(card='C_auto', source='auto_tags', confidence=0.35,
                       scene='street', subject='single', palette='green',
                       elements=['trees'], light='unknown',
                       clothing=dict(type='unknown', palette='unknown',
                                     pattern='unknown', silhouette='unknown',
                                     coverage='unknown')),
    }


def test_rank_manual_only():
    b = normalize_batch(dict(scene='street', subject='single',
                             clothing=dict(type='skirt+top', palette='white'),
                             palette='green', elements=['trees', 'road'],
                             light='soft'))
    hits = rank(b, cards=_cards())
    names = [h['card'] for h in hits]
    assert names == ['A_street', 'B_sea'], names
    assert hits[0]['score'] < hits[1]['score']


def test_auto_excluded_by_default():
    b = normalize_batch(dict(scene='street', subject='single'))
    names = [h['card'] for h in rank(b, cards=_cards())]
    assert 'C_auto' not in names, names
    names2 = [h['card'] for h in rank(b, cards=_cards(), include_auto=True)]
    assert 'C_auto' in names2, names2


def test_unknown_not_scored():
    b = normalize_batch(dict(scene='street', subject='single',
                             clothing=dict(type='skirt+top', palette='unknown'),
                             palette='unknown', elements=[], light='unknown'))
    h = [x for x in rank(b, cards=_cards()) if x['card'] == 'A_street'][0]
    # 只有 scene + ctype + subject 参与；不能因为 unknown 多而虚高/虚低
    assert 'cpal' not in h['dims'] and 'pal' not in h['dims']
    assert h['dims']['scene'] == 0.0


def main():
    test_rank_manual_only()
    test_auto_excluded_by_default()
    test_unknown_not_scored()
    print('test_content: ok')


if __name__ == '__main__':
    main()
