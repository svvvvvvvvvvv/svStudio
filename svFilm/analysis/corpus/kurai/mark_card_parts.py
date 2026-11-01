# -*- coding: utf-8 -*-
"""重标大师卡目录：给每张卡补 **部位目标**（Basic 四根的卡内依据）。

为什么单独标
------------
`LR设计.md` §2.1 写得很清楚：Basic 四根（Highlights/Shadows/Whites/Blacks）
要对齐卡，卡里得先有「头发暗部/中位、白衫/高光落点、皮肤暗部/中暗部」；
v6 的卡只有整幅分位和脸中位，没有这三个部位，所以 Basic 只能给 0。

本脚本用**同一把尺子**（`design.measure_all`，1080 交付域）重测大师片，
按卡聚合三个部位的中位与 IQR，写进 `cards_v7.json` 的 `parts` 字段：

```
"parts": {
  "hair":  {"L_p50": .., "L_p75": .., "L_p90": .., "n": .., "band": {...}},
  "white": {"L_p50": .., "L_p95": .., "n": .., "band": {...}},   # 人身上的白衫
  "skin":  {"L_p10": .., "L_p25": .., "L_p50": .., "n": .., "band": {...}}
}
```

- 只加 `parts`，**不动 v6 已有的 points/bands/wb/hue_bands**；
- 每张卡的部位值取跨图片中位，`band` 取 **min–max**（这张卡自己照片的
  观测包络）。卡自己的照片一定落在包络里 ⇒ 不会被 Basic 反过头去"修"；
  只有新片跑到包络外，才移到最近的边。用 p10–p90 也会把卡里 10% 的照片
  判成出界，用 IQR 更糟（25%），所以这里用完整包络；
- 某部位有效图片 < `MIN_IMAGES` ⇒ 该部位不写（解法侧跳过并记账，不猜）。

用法：
    & $py -X utf8 analysis/corpus/kurai/mark_card_parts.py
    & $py -X utf8 analysis/corpus/kurai/mark_card_parts.py --refresh --jobs 8
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Optional

_CORPUS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_CORPUS)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from design.measure_all import measure_one, measure_sha  # noqa: E402

TONE = os.path.join(_CORPUS, 'photos_tone_v04.json')
CARDS6 = os.path.join(_CORPUS, 'cards_v6.json')
OUT = os.path.join(_CORPUS, 'cards_v7.json')
CACHE = os.path.join(_CORPUS, 'photos_parts_v05.json')

MIN_IMAGES = 3          # 少于 3 张有效 ⇒ 该部位不标（与 peer_n<3 回退同一原则）
PARTS = ('hair', 'white', 'skin')
# 每个部位保留的字段（其余只用来算 n）
KEEP = dict(
    hair=('L_p10', 'L_p25', 'L_p50', 'L_p75', 'L_p90', 'C'),
    white=('L_p50', 'L_p90', 'L_p95', 'L_p99', 'C', 'share_person'),
    skin=('L_p10', 'L_p25', 'L_p50', 'L_p90', 'dark_share40'),
)


def _med(xs: List[float]) -> Optional[float]:
    v = sorted(float(x) for x in xs if isinstance(x, (int, float)))
    return float(st.median(v)) if v else None


def _worker(path: str) -> dict:
    """进程池单张：只留部位，不把整条测量搬回来。"""
    r = measure_one(path)
    return dict(file=path, err=r.get('err'), parts=r.get('parts'))


def _load_cache() -> Dict[str, dict]:
    if not os.path.isfile(CACHE):
        return {}
    try:
        d = json.load(open(CACHE, encoding='utf-8'))
    except Exception:                                      # noqa: BLE001
        return {}
    if (d.get('_meta') or {}).get('sha') != measure_sha():
        return {}                                           # 尺子变了 ⇒ 作废
    return {r['file']: r for r in (d.get('per_image') or []) if r.get('file')}


def measure_masters(paths: List[str], jobs: int, refresh: bool) -> Dict[str, dict]:
    cache = {} if refresh else _load_cache()
    todo = [p for p in paths if p not in cache]
    if cache:
        print('缓存命中 %d 张（%s）' % (len(cache), os.path.basename(CACHE)))
    if todo:
        jobs = max(1, min(int(jobs), 16, len(todo)))
        print('重测 %d 张大师片（%d 路，尺子 %s）' % (len(todo), jobs, measure_sha()))
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(_worker, p): p for p in todo}
            done = 0
            for fu in as_completed(futs):
                p = futs[fu]
                try:
                    r = fu.result()
                except Exception as e:                      # noqa: BLE001
                    r = dict(file=p, err='%s: %s' % (type(e).__name__, str(e)[:120]))
                cache[p] = r
                done += 1
                if done % 50 == 0 or done == len(todo):
                    print('  %d/%d' % (done, len(todo)))
        doc = dict(_meta=dict(sha=measure_sha(), jobs=jobs, n=len(cache),
                              ruler='design.measure_all @ 1080 delivery',
                              note='只存部位（hair/white/skin），供 mark_card_parts 聚合'),
                   per_image=[cache[p] for p in sorted(cache)])
        tmp = CACHE + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE)
        print('写出 %s' % CACHE)
    return cache


def aggregate(rows: List[dict]) -> dict:
    """一个卡目录的逐图部位 → 卡级中位 + min–max 观测包络。"""
    out: Dict[str, dict] = {}
    for part in PARTS:
        keep = KEEP[part]
        vals: Dict[str, List[float]] = {k: [] for k in keep}
        n_px, n_img = [], 0
        for r in rows:
            node = (r.get('parts') or {}).get(part) or {}
            if not node:
                continue
            if node.get('L_p50') is None:
                continue
            n_img += 1
            if isinstance(node.get('n'), (int, float)):
                n_px.append(float(node['n']))
            for k in keep:
                v = node.get(k)
                if isinstance(v, (int, float)):
                    vals[k].append(float(v))
        if n_img < MIN_IMAGES:
            continue
        points, bands = {}, {}
        for k in keep:
            v = sorted(float(x) for x in vals[k] if isinstance(x, (int, float)))
            if not v:
                continue
            points[k] = round(float(st.median(v)), 2)
            # band = 卡内 min–max（观测包络）：卡自己的照片一定在里面，
            # Basic 不会反过头去"修"属于这张卡的照片；新片出界才移到最近的边。
            bands[k] = [round(v[0], 2), round(v[-1], 2)]
        if 'L_p50' not in points:
            continue
        out[part] = dict(points=points, band=bands, n=n_img,
                         n_px=int(_med(n_px) or 0))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--refresh', action='store_true', help='忽略缓存，重测')
    ap.add_argument('--tone', default=TONE)
    a = ap.parse_args(argv)

    tone = json.load(open(a.tone, encoding='utf-8'))
    rows = [r for r in (tone.get('per_image') or [])
            if r.get('role') == 'master' and not r.get('err') and r.get('file')]
    if not rows:
        print('没有大师片测量：%s' % a.tone)
        return 2
    by_card: Dict[str, List[dict]] = {}
    for r in rows:
        by_card.setdefault(r.get('card') or '', []).append(r)
    paths = sorted({r['file'] for r in rows})
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        print('缺 %d 个大师文件（例：%s）⇒ 停' % (len(missing), missing[0]))
        return 2

    cache = measure_masters(paths, a.jobs, a.refresh)
    cards6 = json.load(open(CARDS6, encoding='utf-8'))
    n_marked = {p: 0 for p in PARTS}
    for card in cards6.get('cards') or []:
        rs = by_card.get(card['name']) or []
        parts = aggregate([cache.get(r['file']) or {} for r in rs])
        card['parts'] = parts or None
        for p in parts:
            n_marked[p] += 1
    cards6['_meta'] = dict(cards6.get('_meta') or {})
    cards6['_meta'].update(
        parts_ruler='design.measure_all @ 1080 delivery',
        parts_sha=measure_sha(),
        parts_min_images=MIN_IMAGES,
        parts_note='parts = 头发/白衫/皮肤暗部；band = 卡内 min–max 观测包络；'
                   '只加字段，v6 原字段未改',
        n_cards_with_parts=n_marked)
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(cards6, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print('\n写出 %s：%d 张卡（hair %d / white %d / skin %d）'
          % (OUT, len(cards6['cards']), n_marked['hair'], n_marked['white'],
             n_marked['skin']))
    for c in cards6['cards'][:3]:
        p = c.get('parts') or {}
        print('  %-28s hair %s white %s skin %s' % (
            c['name'],
            (p.get('hair') or {}).get('points', {}).get('L_p50'),
            (p.get('white') or {}).get('points', {}).get('L_p50'),
            (p.get('skin') or {}).get('points', {}).get('L_p25')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
