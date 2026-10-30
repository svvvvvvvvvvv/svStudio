# -*- coding: utf-8 -*-
r"""匹配卡与匹配算法（严格按 `设计真源.md` §48 / §55 / §57 实现）。

一张**匹配卡** = 一套风格，自带三样：介质 · 画像 · 分面标签（+ 样片）。
匹配 = 两条通道合成分数：
  ① 分面标签（定性）：比"情境"像不像 —— 只在**有 κ** 的分面上加权；
     光分面没有 κ ⇒ 只做**硬过滤**（我方 light_probe 判出的类别必须在卡的候选里）。
  ② 数值画像（定量）：p5/p50/p95/contrast/clip/天空/背景跨度，用 64 张卡自己的 IQR 归一。

★ §57.2 的红线（写死在代码里）：**肤色 L/C/h、中性轴 a\*b\*、暖冷 b 一律不进数值匹配**
  —— 它们是"当前基线卡"造出来的，拿它比候选卡 = 自指，越比越像基线。

用法：
    from design.matchcard import cards, batch_profile, rank_top5
    hits = rank_top5(recs)          # recs = ① 的测量 {stem: rec}
    print(shortlist(hits))
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


_CORPUS = r'E:\svStudio\svFilm\analysis\corpus\kurai'
_TAGS = os.path.join(_CORPUS, 'tags_all.json')
_VALID = os.path.join(_CORPUS, 'validation.json')
_IMAGES = os.path.join(_CORPUS, 'images.jsonl')

import sys as _sys
if _CORPUS not in _sys.path:
    _sys.path.insert(0, _CORPUS)

# ---------------------------------------------------------------- 常量与出处

# ★ §57.2：这些维度**永远**不许进数值通道（自指）。删掉它们不是"少比几维"，
#   是因为它们量的不是这批照片，而是基线卡。改这里之前先读 §57.2。
SELFREF_KEYS = ('L', 'C', 'h', 'wc')

# 数值通道允许的维度（§57.1 第二行）。全部是**场景侧/结构**量，与用哪张卡无关。
NUM_KEYS = ('p5', 'p50', 'p95', 'contrast', 'clip', 'sky', 'span')
# 只有**场景侧**这两维能进排序：其余五维是**成片量**，我们的 ① 还没过 LR，
# 拿 ① 的它们比卡的成片画像 = 两种不同的态（老账：拿中间态比成品）⇒ 只报不用。
NUM_KEYS_RANK = ('sky', 'span')
NUM_KEYS_REPORT = ('p5', 'p50', 'p95', 'contrast', 'clip')

# ★ 单维最大贡献 = 3 个 IQR。为什么必须有这条：
#   有些维度（clip 尤其）在 64 张卡里的 IQR 极小（多数卡 0.1~0.4%），
#   我们的 1.1% 会被算成 +18 ~ +37 个 IQR ⇒ **一个近零离散度的维度吃掉全部权重**。
#   这是归一化的病，不是"这批真的差了 30 个 IQR"。截断是稳健距离的常规做法。
MAX_ABS_IQR = 3.0

# 分面通道：只用**有 κ** 的分面（权重 = κ，出处 = validation.json）。
# 光分面（light.*）在 validation.json 里**没有 κ** ⇒ 不进加权，只做硬过滤。
LABEL_FACETS = ('background.type', 'background.palette',
                'clothing.type', 'clothing.palette', 'clothing.pattern',
                'color_env.value', 'composition.framing')

# 我方（这批）分面标签的粗分规则 —— **只用于匹配排序，不作任何判据**。
# 出处：无公开标准，纯工程粗分；因此它只出现在"匹配页的理由"里，不进验收。
_SCENE_RULES = ((0.35, 'greenery'), (0.20, 'street'), (0.10, 'interior'))
_COLOR_RULES = ((-6.0, 'cool'), (6.0, 'warm'))     # 中性轴 b 的符号与幅度
_FRAME_RULES = ((-0.18, 'left'), (0.18, 'center'))


@dataclass
class MatchCard:
    """一套风格。字段定义见 §55。"""
    name: str
    n: int = 0
    # ① 介质
    stock: str = ''
    paper: str = ''
    format_mm: float = 36.0
    texture: Dict[str, object] = field(default_factory=dict)
    # ② 画像
    profile: Dict[str, float] = field(default_factory=dict)
    spread: Dict[str, float] = field(default_factory=dict)
    # ③ 分面标签 + 样片
    tags: Dict[str, str] = field(default_factory=dict)      # 分面 -> dominant
    tag_conf: Dict[str, str] = field(default_factory=dict)
    samples: List[str] = field(default_factory=list)
    thin: bool = False        # 样本不足的卡（不进匹配，只留档）

    def label(self) -> str:
        t = self.tags
        bits = [t.get('light.direction'), t.get('light.quality'),
                t.get('scene.type'), t.get('color_env.value')]
        return '%s（%s）' % (self.name, '/'.join([b for b in bits if b]))


# ---------------------------------------------------------------- 读数据

def _kappa() -> Dict[str, float]:
    """每个分面的评分者一致性。**这就是分面权重的出处**（§57.3）。"""
    out = {}
    try:
        d = json.load(open(_VALID, encoding='utf-8'))
        for row in (d.get('second') or {}).get('rows') or []:
            k = row.get('kappa')
            if row.get('key') and isinstance(k, (int, float)) and k > 0:
                out[row['key']] = float(k)
    except Exception:                                      # noqa: BLE001
        pass
    return out


def _tags_by_dir() -> Dict[str, dict]:
    out = {}
    try:
        for e in json.load(open(_TAGS, encoding='utf-8')):
            t = (e.get('tags') or {}).get('light') or {}
            merged = {}
            for k, v in t.items():
                if isinstance(v, dict) and v.get('dominant'):
                    merged[k] = str(v['dominant'])
            out[e.get('dir')] = dict(tags=merged,
                                     conf={k: (v or {}).get('confidence')
                                           for k, v in t.items() if isinstance(v, dict)},
                                     corpus=e.get('corpus'))
    except Exception:                                      # noqa: BLE001
        pass
    return out


def _samples_by_dir(limit: Optional[int] = None) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    try:
        with open(_IMAGES, encoding='utf-8') as f:
            for line in f:
                d = json.loads(line)
                k = d.get('dir')
                p = d.get('path')
                if k and p and os.path.isfile(p) and (limit is None or len(out.get(k, [])) < limit):
                    out.setdefault(k, []).append(p)
    except Exception:                                      # noqa: BLE001
        pass
    return out


_PRESET_DIR = r'E:\svStudio\svFilm\svFilm\data\presets'


def _medium_by_corpus() -> Dict[str, Tuple[str, str]]:
    """介质从哪来：卡自带（§48.2）。现有的三张 preset 卡给了显式介质；
    64 张大师卡没有配方 ⇒ 用它们的 corpus 对应的默认介质（人可覆盖）。"""
    return {'鹿井': ('kodak_portra_400', 'kodak_portra_endura'),
            '滨田': ('kodak_portra_400', 'kodak_portra_endura'),
            'xhs': ('kodak_ultramax_400', 'kodak_portra_endura')}



_CARDS_V4 = r'E:\svStudio\svFilm\analysis\corpus\kurai\cards_v4.json'
_CARDS_V3 = r'E:\svStudio\svFilm\analysis\corpus\kurai\cards_v3.json'


def _from_v3() -> List['MatchCard']:
    """从 cards_v4.json（没有就退回 v3）装卡：带的**中点**当画像，半宽当 spread。"""
    _path = _CARDS_V4 if os.path.isfile(_CARDS_V4) else _CARDS_V3
    if not os.path.isfile(_path):
        return []
    d = json.load(open(_path, encoding='utf-8'))
    tags = _tags_by_dir()
    med = _medium_by_corpus()
    out: List[MatchCard] = []
    for c in d.get('cards') or []:
        b = c.get('bands') or {}
        prof: Dict[str, float] = {}
        spread: Dict[str, float] = {}
        for key, bname in (('p5', 'p5'), ('p50', 'p50'), ('p95', 'p95'),
                           ('contrast', 'contrast'), ('clip', 'clip'), ('sky', 'sky')):
            band = b.get(bname)
            if isinstance(band, list) and len(band) == 2:
                prof[key] = 0.5 * (band[0] + band[1])
                spread[key] = max((band[1] - band[0]) / 2.0, 1e-6)
        fb = b.get('face_L')
        if isinstance(fb, list):
            prof['L'] = 0.5 * (fb[0] + fb[1])
            spread['L'] = max((fb[1] - fb[0]) / 2.0, 1e-6)
        cb = b.get('face_C')
        if isinstance(cb, list):
            prof['C'] = 0.5 * (cb[0] + cb[1])
        if prof.get('p95') is not None and prof.get('p5') is not None:
            prof['span'] = prof['p95'] - prof['p5']
        tg = tags.get(c['name']) or {}
        # 介质不由卡定（批级预演算）；这里只放**基线**，别当答案
        st_, pp = ('kodak_portra_400', 'kodak_portra_endura')
        out.append(MatchCard(name=c['name'], n=int(c.get('n') or 0), stock=st_, paper=pp,
                             profile=prof, spread=spread,
                             tags=dict(tg.get('tags') or {}),
                             tag_conf=dict(tg.get('conf') or {}),
                             samples=list(c.get('samples') or []),
                             thin=bool(c.get('thin'))))
    return out


def cards() -> List[MatchCard]:
    """全部匹配卡，按 §55 的字段装好。

    优先读 `cards_v3.json`（新口径 v7 的聚合，含 person/near_bg/light 三块）；
    没有才退回 `refcards()`（v2 老口径，只作兜底）。
    """
    v3 = _from_v3()
    if v3:
        # 薄卡不进匹配（§65.4），但仍留在 cards_v3.json 里备查
        return [c for c in v3 if not c.thin]
    from .refcards import refcards as _refs
    tags = _tags_by_dir()
    samples = _samples_by_dir()
    med = _medium_by_corpus()
    out: List[MatchCard] = []
    for r in _refs():
        tg = tags.get(r.name) or {}
        st, pp = med.get((tg.get('corpus') or ''), ('kodak_portra_400',
                                                    'kodak_portra_endura'))
        prof = dict(r.profile)
        # span（背景跨度/影调跨度）= p95 − p5，卡与自己这批用同一个定义
        if prof.get('p95') is not None and prof.get('p5') is not None:
            prof['span'] = float(prof['p95']) - float(prof['p5'])
        out.append(MatchCard(name=r.name, n=r.n, stock=st, paper=pp,
                             profile=prof, spread=dict(r.spread),
                             tags=dict(tg.get('tags') or {}),
                             tag_conf=dict(tg.get('conf') or {}),
                             samples=list(samples.get(r.name) or [])))
    return out


# ---------------------------------------------------------------- 我方这批的画像与粗分标签

def batch_profile(recs: Dict[str, dict]) -> Dict[str, float]:
    """这批的数值画像：**只用场景侧/结构量**（§57.2）。"""
    def med(key, fn):
        v = [fn(r) for r in recs.values() if fn(r) is not None]
        return float(np.median(v)) if v else float('nan')

    img = lambda r: (r.get('img') or {})
    bg = lambda r: (r.get('bg') or {})
    f = {
        'p5': med('p5', lambda r: img(r).get('p5')),
        'p50': med('p50', lambda r: img(r).get('p50')),
        'p95': med('p95', lambda r: img(r).get('p95')),
        'contrast': med('contrast', lambda r: img(r).get('contrast')),
        'clip': med('clip', lambda r: img(r).get('clip_pct')),
        'sky': med('sky', lambda r: r.get('sky_share_frac')),
        # ★ span 与卡**同一个定义**：p95 − p5（10-30 修：以前批侧用 bg.p90−p10，卡侧用 p95−p5，两边不同源）
        'span': med('span', lambda r: (img(r).get('p95') - img(r).get('p5'))
                    if img(r).get('p95') is not None and img(r).get('p5') is not None else None),
    }
    return {k: v for k, v in f.items() if np.isfinite(v)}


def batch_tags(recs: Dict[str, dict], paths: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """这批的分面标签（**粗分**，只给匹配排序用）。

    · 光：用 light_probe 的判定（它是唯一有依据的那个）
    · 场景/色彩/构图：按画面统计粗分，规则写在常量区，**不作判据**
    """
    out: Dict[str, str] = {}
    env = [r.get('env_share') for r in recs.values() if r.get('env_share') is not None]
    if env:
        e = float(np.median(env))
        out['scene.type'] = 'interior' if e < _SCENE_RULES[2][0] else (
            'street' if e < _SCENE_RULES[1][0] else 'greenery')
    try:
        import light_probe as LP
        kinds = []
        for st, r in recs.items():
            p = (paths or {}).get(st) or r.get('_path')
            if p and os.path.isfile(p):
                k, _, _, _ = LP.classify(LP.probe(p))
                if k and k != 'unknown':
                    kinds.append(k)
        if kinds:
            out['light.direction'] = max(set(kinds), key=kinds.count)
    except Exception:                                      # noqa: BLE001
        pass
    wc = [r.get('warm_cool_b') for r in recs.values() if r.get('warm_cool_b') is not None]
    if wc:
        b = float(np.median(wc))
        out['color_env.value'] = 'warm' if b > _COLOR_RULES[1][0] else (
            'cool' if b < _COLOR_RULES[0][0] else 'neutral')
    return out


# ---------------------------------------------------------------- 打分（§57.3~§57.4）

def _num_distance(batch: Dict[str, float], card: MatchCard,
                  scales: Dict[str, float]) -> Tuple[float, Dict[str, float]]:
    d = {}
    for k in NUM_KEYS_RANK:
        b, v = batch.get(k), card.profile.get(k)
        if b is None or v is None or not np.isfinite(v):
            continue
        z = (float(b) - float(v)) / max(scales.get(k, 1.0), 1e-6)
        z = float(np.clip(z, -MAX_ABS_IQR, MAX_ABS_IQR))     # 单维最多算 3 个 IQR
        d[k] = z
    if not d:
        return float('nan'), {}
    return float(np.sqrt(sum(x * x for x in d.values())) / np.sqrt(len(d))), d


def _label_distance(batch: Dict[str, str], card: MatchCard,
                    kap: Dict[str, float]) -> Tuple[Optional[float], List[str], List[str]]:
    """分面距离：命中 0、不命中 1，按 κ 加权；**没有 κ 的分面不参与**。

    光分面**不在这里**：它没有 κ ⇒ 只做硬过滤（见 rank_top5）。
    """
    tot = w = 0.0
    hit, miss = [], []
    # 10-30：kappa 缺失时用**固定权重**（场景/构图/色彩/光位），不再让标签整段失效
    _W = {'scene.type': 1.0, 'composition.framing': 0.8,
          'color_env.value': 0.6, 'light': 1.2}
    for facet in LABEL_FACETS:
        k = kap.get(facet) or _W.get(facet)
        if not k:
            continue
        bv, cv = batch.get(facet), card.tags.get(facet)
        if bv is None or cv is None:
            continue
        tot += k * (0.0 if bv == cv else 1.0)
        w += k
        (hit if bv == cv else miss).append('%s=%s' % (facet.split('.')[0], bv))
    if w <= 0:
        return None, hit, miss
    return tot / w, hit, miss


def _costs(card: MatchCard, batch: Dict[str, float]) -> List[str]:
    """§57.5 代价：这张卡**做不到**的地方（要能落到"换它以后画面会变成什么样"）。"""
    out = []
    cp95 = card.profile.get('p95')
    bp95 = batch.get('p95')
    if cp95 is not None and bp95 is not None and cp95 + 4.0 < bp95:
        out.append('它的亮部定位比这批低 %.0f L*（用它要整体压暗）' % (bp95 - cp95))
    cclip = card.profile.get('clip')
    bclip = batch.get('clip')
    if cclip is not None and bclip is not None and bclip > max(2.0 * cclip, 1.0):
        out.append('这批本身爆白 %.1f%%，它只有 %.2f%% ⇒ 要么压暗要么接受爆' % (bclip, cclip))
    span = batch.get('span')
    if span is not None and span > 55.0:
        out.append('这批背景跨度 %.0f L*，需要宽肩；肩只由卷纸定，LR 不许造' % span)
    return out


def rank_top5(recs: Dict[str, dict], k: int = 5,
              batch: Optional[Dict[str, float]] = None,
              btags: Optional[Dict[str, str]] = None,
              paths: Optional[Dict[str, str]] = None) -> List[dict]:
    """§57.4：合成分数 → 排序 → 前 k 个（每个带理由与代价）。"""
    cards_ = cards()
    batch = batch_profile(recs) if batch is None else batch
    btags = batch_tags(recs, paths) if btags is None else btags
    kap = _kappa()

    # 数值通道的尺度 = 64 张卡自己这一维的 IQR（§57.3）
    scales = {}
    for key in NUM_KEYS:
        vals = [c.profile[key] for c in cards_
                if c.profile.get(key) is not None and np.isfinite(c.profile[key])]
        scales[key] = float(np.percentile(vals, 75) - np.percentile(vals, 25)) \
            if len(vals) > 8 else 1.0
        if not np.isfinite(scales[key]) or scales[key] < 1e-6:
            scales[key] = 1.0

    # 光：硬过滤（没有 κ，不做加权）。我方判出的光位若在卡的标签里没有，降权但不剔除
    blight = btags.get('light.direction')
    hits = []
    for c in cards_:
        nd, ndiff = _num_distance(batch, c, scales)
        ld, lhit, lmiss = _label_distance(btags, c, kap)
        parts, wts = [], []
        if np.isfinite(nd) and ndiff:
            parts.append(nd)
            wts.append(len(ndiff))
        if ld is not None:
            parts.append(ld)
            wts.append(len([1 for f in LABEL_FACETS if btags.get(f) and c.tags.get(f)]))
        if not parts and ld is None:
            continue            # 数值/标签两边都缺才丢；缺一边照常参与
        score = float(np.average(parts, weights=wts))
        reason = []
        if lhit:
            reason.append('像：' + '、'.join(lhit[:3]))
        if lmiss:
            reason.append('不像：' + '、'.join(lmiss[:2]))
        if ndiff:
            top = sorted(ndiff.items(), key=lambda kv: -abs(kv[1]))[:2]
            reason.append('数值偏：' + '、'.join('%s%+.2f IQR' % (kk, vv) for kk, vv in top))
        if blight and c.tags.get('light.direction') and c.tags['light.direction'] != blight:
            score += 0.10          # 光位不同 ⇒ 轻微降权（不是判据，只是排序）
            reason.append('光位是 %s（这批判 %s）' % (c.tags['light.direction'], blight))
        hits.append(dict(card=c, score=round(score, 3), reason=reason,
                         cost=_costs(c, batch), num=round(nd, 3) if np.isfinite(nd) else None,
                         label=None if ld is None else round(ld, 3)))
    hits.sort(key=lambda h: h['score'])
    return hits[:k]


def shortlist(hits: List[dict]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        c = h['card']
        lines.append('%d) %.2f  %s  n=%d  [%s/%s]' % (i, h['score'], c.name, c.n, c.stock, c.paper))
        if h['reason']:
            lines.append('     ' + ' ｜ '.join(h['reason']))
        for x in h['cost']:
            lines.append('     代价：' + x)
    return '\n'.join(lines)
