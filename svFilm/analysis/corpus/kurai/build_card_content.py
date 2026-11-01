# -*- coding: utf-8 -*-
"""生成 `card_content.json`：64 张目录内容卡。

口径（v0.3）：
  · 一个目录 = 一次拍摄；内容/场景/服装按目录标一次，不按 746 张照片标。
  · 5 张试点用人标（`card_content_manual.json`）；其余先用 `tags_all.json`
    的 background.type/palette 做**低置信草稿**，服装/光位留 unknown。
  · unknown 字段在匹配里不参与分数，不猜。

跑：
    & $py -X utf8 -m analysis.corpus.kurai.build_card_content
或：
    & $py -X utf8 analysis/corpus/kurai/build_card_content.py
"""
from __future__ import annotations

import json
import os
import time

BASE = r'E:\svStudio\svFilm\analysis\corpus\kurai'
CARDS = os.path.join(BASE, 'cards_v5.json')
TAGS = os.path.join(BASE, 'tags_all.json')
MANUAL = os.path.join(BASE, 'card_content_manual.json')
OUT = os.path.join(BASE, 'card_content.json')

_SCENE = {'nature': 'park', 'street': 'street', 'interior': 'interior',
          'studio': 'studio'}


def _tags_by_dir() -> dict:
    out = {}
    for e in json.load(open(TAGS, encoding='utf-8')):
        t = (e.get('tags') or {}).get('light') or {}
        out[e.get('dir')] = {k: (v or {}).get('dominant')
                             for k, v in t.items() if isinstance(v, dict)}
    return out


def _auto(name: str, corpus: str, n: int, tags: dict) -> dict:
    scene = _SCENE.get(tags.get('background.type') or '', 'other')
    pal = tags.get('background.palette') or 'neutral'
    elements = []
    if scene in ('park', 'nature'):
        elements += ['trees', 'foliage']
    elif scene == 'street':
        elements += ['buildings', 'road']
    elif scene == 'interior':
        elements += ['furniture', 'window']
    elif scene == 'studio':
        elements += ['backdrop']
    if pal == 'green' and 'foliage' not in elements:
        elements.append('foliage')
    if pal == 'warm' and scene == 'interior' and 'wood' not in elements:
        elements.append('wood')
    if (tags.get('color_env.value') or '') == 'cool':
        elements.append('sky')
    return dict(card=name, corpus=corpus, n=int(n or 0), scene=scene,
                subject='single',
                clothing=dict(type='unknown', palette='unknown', pattern='unknown',
                              silhouette='unknown', coverage='unknown'),
                palette=pal, elements=elements, light='unknown',
                evidence='自动草稿：只从 tags_all 的 background.type/palette/color_env 映射；'
                         '服装/光位未标，匹配时跳过',
                confidence=0.35, source='auto_tags')


def main() -> int:
    cards = (json.load(open(CARDS, encoding='utf-8')).get('cards') or [])
    tags = _tags_by_dir()
    manual = {}
    if os.path.isfile(MANUAL):
        for c in (json.load(open(MANUAL, encoding='utf-8')).get('cards') or []):
            manual[c['card']] = c
    out = []
    for c in cards:
        name = c.get('name')
        if not name:
            continue
        if name in manual:
            row = dict(manual[name])
            row.setdefault('corpus', c.get('corpus') or '')
            row.setdefault('n', int(c.get('n') or 0))
        else:
            row = _auto(name, c.get('corpus') or '', int(c.get('n') or 0),
                        tags.get(name) or {})
        out.append(row)
    doc = dict(schema='card_content/v0.1', generated=time.strftime('%Y-%m-%d %H:%M:%S'),
               n=len(out), n_manual=sum(1 for r in out if r.get('source') == 'manual'),
               note='目录级内容卡；unknown 不进分数。5 张人标试点，其余自动草稿。',
               cards=out)
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print('卡 %d 张（人标 %d）→ %s' % (len(out), doc['n_manual'], OUT))
    from collections import Counter
    def _top(r, key):
        if r.get(key):
            return r[key]
        vs = r.get('variants') or []
        return (vs[0].get(key) if vs else 'mixed') or 'mixed'
    print('scene', dict(Counter(_top(r, 'scene') for r in out)))
    print('palette', dict(Counter(_top(r, 'palette') for r in out)))
    print('source', dict(Counter(r['source'] for r in out)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
