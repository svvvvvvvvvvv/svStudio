# -*- coding: utf-8 -*-
"""目录内容卡：读 / 算 / 排序（v0.3）。

一个目录 = 一次拍摄；内容/场景/服装按目录标一次（64 条），不按 746 张照片标。
匹配 A 只用这里的字段；unknown 一律不参与分数，不猜。

用法：
    from design.card_content import load, rank, batch_from_measures
    hits = rank(batch, topn=10)
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

# 生产默认从每个目录 JSON 的 `content` 字段读；path 参数只留旧文件兼容。

# 未标定前：**等权**；κ/成对比较出来后才允许写权重（纪律 1/3，§10）。
# ★ 2026-11-01：`light` 不参与分数（技能 §194/§195）。
W = dict(scene=1.0, ctype=1.0, cpal=1.0, pal=1.0, elems=1.0,
         subject=1.0)

_REL_TYPE = ({'dress', 'skirt+top'}, {'pants+top', 'shorts+top', 'skirt+top'})
_SCENE_FAMILY = {'street', 'park', 'street_park'}


def _tokens(s) -> set:
    if not s or s == 'unknown':
        return set()
    return {x for x in str(s).replace('/', '+').split('+') if x and x != 'unknown'}


def _cat_d(a, b, groups=()) -> Optional[float]:
    if not a or not b or a == 'unknown' or b == 'unknown':
        return None
    if a == b:
        return 0.0
    for g in groups:
        if a in g and b in g:
            return 0.5
    return 1.0


def _scene_d(a, b) -> Optional[float]:
    if not a or not b or a == 'unknown' or b == 'unknown':
        return None
    if a == b:
        return 0.0
    if a in _SCENE_FAMILY and b in _SCENE_FAMILY:
        return 0.0
    return 1.0


def _type_d(a, b) -> Optional[float]:
    return _cat_d(a, b, _REL_TYPE)


def _pal_d(a, b) -> Optional[float]:
    if not a or not b or a == 'unknown' or b == 'unknown':
        return None
    if a == b:
        return 0.0
    sa, sb = _tokens(a), _tokens(b)
    if not sa or not sb:
        return 1.0
    return 0.0 if sa == sb else (0.5 if sa & sb else 1.0)


def _elements_d(a, b) -> Optional[float]:
    sa = {x for x in (a or []) if x}
    sb = {x for x in (b or []) if x}
    if not sa or not sb:
        return None
    return 1.0 - len(sa & sb) / max(1, len(sa | sb))


def load(path: str = '') -> Dict[str, dict]:
    """name → 内容卡；默认从每个目录 JSON 的 `content` 字段读。"""
    if path:
        try:
            d = json.load(open(path, encoding='utf-8'))
        except Exception:                                  # noqa: BLE001
            return {}
        return {c['card']: c for c in (d.get('cards') or []) if c.get('card')}
    from .matchcard import raw_cards
    out = {}
    for c in raw_cards():
        content = c.get('content')
        if isinstance(content, dict) and content.get('card'):
            out[content['card']] = content
    return out


def normalize_batch(b: dict) -> dict:
    """把 batch_content 统一成 rank() 要的形状。"""
    b = dict(b or {})
    cl = b.get('clothing') or {}
    if not isinstance(cl, dict):
        cl = {'type': str(cl)}
    return dict(
        scene=b.get('scene') or 'unknown',
        subject=b.get('subject') or 'unknown',
        clothing=dict(type=cl.get('type') or 'unknown',
                      palette=cl.get('palette') or 'unknown',
                      pattern=cl.get('pattern') or 'unknown',
                      silhouette=cl.get('silhouette') or 'unknown',
                      coverage=cl.get('coverage') or 'unknown'),
        palette=b.get('palette') or 'unknown',
        elements=list(b.get('elements') or []),
        light=b.get('light') or 'unknown',
        light_note=b.get('light_note') or b.get('light') or 'unknown',
        evidence=b.get('evidence') or '',
        confidence=b.get('confidence'),
        source=b.get('source') or 'manual',
    )


def load_batch(path: str) -> dict:
    """读一个 batch_content JSON：允许 {batch:{...}} 或直接字段。"""
    d = json.load(open(path, encoding='utf-8'))
    return normalize_batch(d.get('batch') or d)


def rank(batch: dict, topn: int = 10, cards: Optional[Dict[str, dict]] = None,
         min_known: float = 2.0, min_confidence: float = 0.6,
         include_auto: bool = False) -> List[dict]:
    """内容距离排序。返回 [{card, score, dims, reason, cost, content}]。

    · `min_known`：至少要有这么多权重才算数；否则这张卡不出候选。
    · 默认**只要人标卡**（source=manual 且 confidence≥0.6）；自动草稿卡
      服装/光位是 unknown，放进来会因为"少比几项"而虚高（实测过）。
      `include_auto=True` 才带草稿卡，仅用于诊断。
    """
    b = normalize_batch(batch)
    cards = load() if cards is None else cards

    def eval_one(c: dict):
        # 主体数量是硬过滤（单人/双人/群像的构图逻辑不同）
        if (b['subject'] not in (None, 'unknown')
                and c.get('subject') not in (None, 'unknown')
                and b['subject'] != c.get('subject')):
            return None
        cc = c.get('clothing') or {}
        dims = dict(
            scene=_scene_d(b['scene'], c.get('scene')),
            ctype=_type_d(b['clothing']['type'], cc.get('type')),
            cpal=_pal_d(b['clothing']['palette'], cc.get('palette')),
            pal=_pal_d(b['palette'], c.get('palette')),
            elems=_elements_d(b['elements'], c.get('elements')),
            subject=_cat_d(b['subject'], c.get('subject')),
        )
        num = den = 0.0
        known = {}
        for k, d in dims.items():
            if d is None:
                continue
            w = W[k]
            num += w * d
            den += w
            known[k] = round(d, 3)
        if den < min_known:
            return None
        reason, cost = [], []
        for k, d in known.items():
            if d <= 0.01:
                reason.append(_why_hit(k, b, c))
            elif d >= 0.99:
                cost.append(_why_miss(k, b, c))
            elif d >= 0.4:
                cost.append(_why_miss(k, b, c, half=True))
        return dict(score=round(num / den, 3), dims=known,
                    reason=[x for x in reason if x],
                    cost=[x for x in cost if x], content=c,
                    confidence=c.get('confidence'),
                    source=c.get('source'))

    hits = []
    for name, c in cards.items():
        if not include_auto:
            if c.get('source') != 'manual':
                continue
            if float(c.get('confidence') or 0.0) < min_confidence:
                continue
        contents = c.get('variants') or [c]
        best = None
        for i, cv in enumerate(contents):
            h = eval_one(cv)
            if h is None:
                continue
            if best is None or h['score'] < best['score']:
                if len(contents) > 1:
                    h['variant'] = cv.get('name') or ('variant %d' % (i + 1))
                best = h
        if best is None:
            continue
        best['card'] = name
        best['mixed'] = bool(c.get('mixed') or len(contents) > 1)
        hits.append(best)
    hits.sort(key=lambda h: h['score'])
    return hits[:topn]


def _why_hit(k: str, b: dict, c: dict) -> str:
    if k == 'scene':
        return '同场景：%s' % c.get('scene')
    if k == 'ctype':
        return '同服装类型：%s' % (c.get('clothing') or {}).get('type')
    if k == 'cpal':
        return '同服装色：%s' % (c.get('clothing') or {}).get('palette')
    if k == 'pal':
        return '同环境色：%s' % c.get('palette')
    if k == 'elems':
        return '同背景元素：%s' % '、'.join(c.get('elements') or [])
    if k == 'subject':
        return '同主体：%s' % c.get('subject')
    return ''


def _why_miss(k: str, b: dict, c: dict, half: bool = False) -> str:
    if k == 'scene':
        return '场景不同：它是 %s，这批是 %s' % (c.get('scene'), b['scene'])
    if k == 'ctype':
        return '服装类型不同：它是 %s，这批是 %s' % (
            (c.get('clothing') or {}).get('type'), b['clothing']['type'])
    if k == 'cpal':
        return '服装色不同：它是 %s，这批是 %s' % (
            (c.get('clothing') or {}).get('palette'), b['clothing']['palette'])
    if k == 'pal':
        return '环境色不同：它是 %s，这批是 %s' % (c.get('palette'), b['palette'])
    if k == 'elems':
        return '背景元素不同：它是 %s' % '、'.join(c.get('elements') or [])
    if k == 'subject':
        return '主体不同：它是 %s，这批是 %s' % (c.get('subject'), b['subject'])
    return ''


# ---------------------------------------------------------------- 自动 batch_content

def _med(vals):
    v = [float(x) for x in vals if isinstance(x, (int, float))]
    if not v:
        return None
    v.sort()
    return v[len(v) // 2]


def batch_from_measures(measures: Dict[str, dict]) -> dict:
    """从机内 JPG 的 measure 记录汇总一个 batch_content（粗分，低置信）。

    ★ 只做 scene/palette/elements；服装/光位留 unknown —— 这两项必须人给或 VLM，
      自动猜会污染匹配。页面/CLI 会提示用 --content 覆盖。
    """
    rows = [r for r in (measures or {}).values() if r]
    if not rows:
        return normalize_batch(dict(scene='unknown', palette='unknown',
                                    elements=[], light='unknown',
                                    source='auto_jpg', confidence=0.0,
                                    evidence='没有可用的机内 JPG 测量'))
    hb = [r.get('hue_bands') or [] for r in rows]

    def share(i):
        vals = []
        for h in hb:
            if i < len(h) and isinstance(h[i], dict):
                vals.append(h[i].get('share'))
        return _med(vals) or 0.0

    # design.measure_all 的 hue_bands 是按 0-60/60-120/... 排列的
    green = share(1) + share(2)
    blue = share(3)
    warm = share(0) + share(5)
    env = _med([r.get('env_share') for r in rows]) or 0.0
    if green >= 0.22:
        scene = 'park'
    elif blue >= 0.28 and env >= 0.70:
        scene = 'seaside'
    elif env <= 0.55:
        scene = 'interior'
    else:
        scene = 'street'
    if green >= 0.22:
        pal = 'green'
    elif blue >= 0.28:
        pal = 'blue'
    elif warm >= 0.30:
        pal = 'warm'
    else:
        pal = 'neutral'
    elements = []
    if scene in ('park', 'seaside'):
        elements += ['trees', 'foliage'] if green >= 0.15 else ['sky']
    if scene == 'seaside':
        elements += ['water', 'sky']
    if scene == 'street':
        elements += ['buildings', 'road']
    if scene == 'interior':
        elements += ['furniture', 'window']
    return normalize_batch(dict(
        scene=scene, subject='single',
        clothing=dict(type='unknown', palette='unknown', pattern='unknown',
                      silhouette='unknown', coverage='unknown'),
        palette=pal, elements=sorted(set(elements)), light='unknown',
        evidence='自动：从 %d 张机内 JPG 的 env_share/hue_bands 粗分；服装/光位未判'
                 % len(rows),
        confidence=0.35, source='auto_jpg'))


def describe(hits: List[dict]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        c = h['content']
        lines.append('%d) %.2f  %s  [%s/%s] conf=%s' % (
            i, h['score'], h['card'], c.get('scene'), c.get('palette'),
            h.get('confidence')))
        if h['reason']:
            lines.append('     ' + ' ｜ '.join(h['reason']))
        if h['cost']:
            lines.append('     代价：' + '；'.join(h['cost']))
    return '\n'.join(lines)
