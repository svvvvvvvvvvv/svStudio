# -*- coding: utf-8 -*-
"""用选定的卡（004_鹿井小渔_n6ab1e755）渲全批 ①（§56 步 5）。

卡的介质 → preset；边界 = **不变量**（p95≤93 / clip<0.5% / p1≥1.0），不是卡的分布（§63.7）。
并行渲（内存预算算路数）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, r'E:\svStudio\svFilm')
sys.path.insert(0, r'E:\ds\photo\_corpus\kurai\full')

import svFilm  # noqa: E402
import svFilm.lrbase  # noqa: E402,F401
import svFilm.presets  # noqa: E402,F401
import svFilm.io  # noqa: E402,F401
assert 'svStudio' in svFilm.__file__, 'svFilm 解析错了：%s' % svFilm.__file__

from design.batch import Batch                      # noqa: E402
from design.card_render import preset_for, safety_for  # noqa: E402
from design.flow import Choice, _Hit, render_card   # noqa: E402
from design.measure import load                     # noqa: E402
from design.matchcard import cards                  # noqa: E402
from design.texture import plan_texture             # noqa: E402

RAF = r'D:\照片库\效果测试919'
OUT = r'E:\ds\photo\_run_1030_card004'
CARD = '004_鹿井小渔_n6ab1e755'


def main() -> int:
    cs = {c.name: c for c in cards()}
    if CARD not in cs:
        print('没有这张卡：%s' % CARD)
        return 2
    card = cs[CARD]
    preset = preset_for(card)
    saf = safety_for(card)
    print('卡    %s' % card.name)
    print('介质  %s + %s → preset %s' % (card.stock, card.paper, preset))
    print('边界  %s（不变量，不是卡的分布）' % saf)
    print('样片  %d 张 ｜ 这套 n=%d' % (len(card.samples), card.n))

    b = Batch.of(RAF, OUT)
    stems = b.stems
    print('批    %d 张：%s' % (len(stems), ' '.join(stems)))

    ch = Choice(hit=_Hit(card, 0.0),
                texture=plan_texture(card.stock, card.paper, long_side=2048))
    tifs = render_card(ch, b.raf_paths(stems), b.sub('engine'), log=print)
    print('\n渲完 %d 张 → %s' % (len(tifs), b.sub('engine')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
