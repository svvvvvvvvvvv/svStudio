# -*- coding: utf-8 -*-
"""参照卡：**大师的一套就是一张卡**（一次拍摄 / 一个笔记）。

为什么要有这一层（2026-10-29 SV 指出"为什么只有三张卡"）：
  · 我把"卡"理解成了引擎里的那 3 个配置 —— 那是**生产卡**，不是全部；
  · 大师作品是**按套**拍的：鹿井 62 套（每套中位 10 张）、xhs 1 套、滨田 1 套；
  · 我们早就按套量过了（`m_鹿井.json` 662 张、`m_xhs` 31、`m_滨田` 19）
    ⇒ 天然就有 **64 张参照卡**。

两类卡的区别（不要混）：
  参照卡（64 张）  只有**画像**（这套片子长什么样），**没有配方**（不知道怎么渲出来）
  生产卡（3 张）   有**配方**（卷/纸/质感档位），**能渲**

所以匹配不是一步，是两步：
  ① 这批该像**哪一套**（在 64 张参照卡里找）
  ② 我们有没有能做出那个像的**生产卡**；没有 ⇒ 该做新卡，方向由那一套的画像给

用法：
    from design.refcards import refcards, profile_batch, rank_refs
    refs = refcards()
    print(len(refs))                       # 64
    tb = profile_batch(recs_of_my_batch)
    for h in rank_refs(tb, refs)[:5]: print(h.name, h.score)
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

CORPUS_DIR = r'E:\svStudio\svFilm\analysis\corpus\kurai'
CORPORA = ('鹿井', 'xhs', '滨田')

# 画像里用到的量（都能从中位/IQR 比，不用绝对靶）
PROFILE_KEYS = ('L', 'C', 'h', 'p50', 'p95', 'p5', 'contrast', 'clip',
                'sky', 'light', 'wc')


@dataclass
class RefCard:
    name: str                    # 套名
    corpus: str
    n: int
    profile: Dict[str, float]    # 中位
    spread: Dict[str, float]     # IQR（p75−p25）
    source: str = ''

    def label(self) -> str:
        return '%s/%s' % (self.corpus, self.name)

    def __str__(self):
        p = self.profile
        return ('%-28s n=%-3d 脸L=%-5.1f 脸C=%-5.1f 脸h=%-5.1f p50=%-5.1f p95=%-5.1f clip=%.2f%%'
                % (self.label(), self.n, p.get('L', 0), p.get('C', 0), p.get('h', 0),
                   p.get('p50', 0), p.get('p95', 0), p.get('clip', 0)))


def _med(xs):
    xs = [x for x in xs if x is not None and np.isfinite(x)]
    return float(np.median(xs)) if xs else float('nan')


def _iqr(xs):
    xs = [x for x in xs if x is not None and np.isfinite(x)]
    if len(xs) < 4:
        return 0.0
    return float(np.percentile(xs, 75) - np.percentile(xs, 25))


def _profile(rows: List[dict]) -> Dict[str, float]:
    def col(fn):
        return [fn(r) for r in rows]

    return dict(
        L=_med(col(lambda r: (r.get('skin') or {}).get('L'))),
        C=_med(col(lambda r: (r.get('skin') or {}).get('C'))),
        h=_med(col(lambda r: (r.get('skin') or {}).get('h'))),
        p50=_med(col(lambda r: (r.get('img') or {}).get('p50'))),
        p95=_med(col(lambda r: (r.get('img') or {}).get('p95'))),
        p5=_med(col(lambda r: (r.get('img') or {}).get('p5'))),
        contrast=_med(col(lambda r: (r.get('img') or {}).get('contrast'))),
        clip=_med(col(lambda r: (r.get('img') or {}).get('clip_pct'))),
        sky=_med(col(lambda r: r.get('sky_share'))),
        light=_med(col(lambda r: r.get('light_ratio'))),
        wc=_med(col(lambda r: r.get('warm_cool_b'))),
    )


def _spread(rows: List[dict]) -> Dict[str, float]:
    return dict(
        L=_iqr([(r.get('skin') or {}).get('L') for r in rows]),
        C=_iqr([(r.get('skin') or {}).get('C') for r in rows]),
        h=_iqr([(r.get('skin') or {}).get('h') for r in rows]),
        p50=_iqr([(r.get('img') or {}).get('p50') for r in rows]),
    )


def refcards(min_n: int = 3) -> List[RefCard]:
    """把每套大师成片做成一张参照卡。套 = 目录（顶层文件算一套）。"""
    out = []
    for corpus in CORPORA:
        path = os.path.join(CORPUS_DIR, 'm_%s.json' % corpus)
        if not os.path.isfile(path):
            continue
        d = json.load(open(path, encoding='utf-8'))
        buckets: Dict[str, List[dict]] = {}
        for r in d.get('per_image') or []:
            f = (r.get('file') or '').replace('\\', '/')
            # ★ 顶层的散图也要能认出来是谁：用**来源语料**当名字，
            #   否则'滨田'与'小红书'两张卡都叫'(顶层)'，撞名、还会被过滤掉
            key = f.split('/')[0] if '/' in f else '%s（整套）' % corpus
            buckets.setdefault(key, []).append(r)
        for name, rows in sorted(buckets.items()):
            if len(rows) < min_n:
                continue
            out.append(RefCard(name=name, corpus=corpus, n=len(rows),
                               profile=_profile(rows), spread=_spread(rows),
                               source=path))
    return out


def profile_batch(recs: Dict[str, dict]) -> Dict[str, float]:
    """我们这一批的画像（同一套量、同一套口径）。"""
    return _profile(list(recs.values()))


def rank_refs(batch: Dict[str, float], refs: List[RefCard],
              keys=('L', 'C', 'h', 'p50', 'p95')) -> List['RefHit']:
    """按**归一化距离**排序：每一维用参照卡之间的离散度做尺度。

    注意：这不是"打靶"。它回答的是"这批最像哪一套"，
    用来找**该往哪个方向走**，不是要求我们做到那一套的数。
    """
    scales = {}
    for k in keys:
        vals = [r.profile.get(k) for r in refs if np.isfinite(r.profile.get(k, np.nan))]
        scales[k] = float(np.percentile(vals, 75) - np.percentile(vals, 25)) if len(vals) > 8 else 1.0
        if not np.isfinite(scales[k]) or scales[k] < 1e-6:
            scales[k] = 1.0
    hits = []
    for r in refs:
        d = {}
        for k in keys:
            b, v = batch.get(k), r.profile.get(k)
            if not (np.isfinite(b) and np.isfinite(v)):
                continue
            d[k] = (b - v) / scales[k]
        score = float(np.sqrt(sum(x * x for x in d.values())))
        hits.append(RefHit(ref=r, score=round(score, 3),
                           diff={k: round(v, 1) for k, v in
                                 ((k, (batch.get(k) or 0) - (r.profile.get(k) or 0)) for k in keys)
                                 if abs(v) > 0.5}))
    hits.sort(key=lambda h: h.score)
    return hits


@dataclass
class RefHit:
    ref: RefCard
    score: float
    diff: Dict[str, float] = field(default_factory=dict)

    def __str__(self):
        why = ' · '.join('%s%+.1f' % (k, v) for k, v in list(self.diff.items())[:3]) or '很接近'
        return '%.2f  %-34s n=%-3d  %s' % (self.score, self.ref.label(), self.ref.n, why)
