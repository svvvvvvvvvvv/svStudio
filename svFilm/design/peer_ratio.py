# -*- coding: utf-8 -*-
"""逐片 peer（关系比优先）—— 曝光锚 = peer 组整幅 p50 的中位。

口径（`设计真源.md` §3/§5、`LR设计.md` §3）：
  · **关系比**（曝光不变量）：`person.L / bg.L`、`skin.L / bg.L`；
  · **场景结构**：sky/env/green/blue 份额；**构图**：person.share、脸位置、画幅方向；
  · 绝对 L*/p50/contrast/clip/光强 **不进距离**；连续光向量只诊断；
  · 目标 = 最近 k 张单帧 peer 的整幅 p50 中位；IQR 记账；
  · 卡内没有同场景同反差的 peer ⇒ 借最近比值 peer（低置信），不硬套。

本模块只找 peer + 出 target，不渲图、不改引擎。
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional

import numpy as np

from . import peer as _peer

TONE = _peer.TONE
TONE_FALLBACK = _peer.TONE_FALLBACK

K = 4
W = dict(ratio=4.0, ps=3.0, fx=1.0, fy=0.5, framing=1.0,
         sky=1.5, env=1.0, green=1.0, blue=1.0, ar=0.5)
SCALE = dict(sky=0.30, env=0.50, green=0.30, blue=0.20, ar=0.50)
MAX_RATIO = 0.70          # |log ratio| 超过它就不当 peer（约 2 倍）
CONF_HI = (0.20, 8.0)
CONF_MID = (0.45, 15.0)


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def framing(ps: Optional[float]) -> Optional[int]:
    if ps is None:
        return None
    return 0 if ps < 0.06 else 1 if ps < 0.14 else 2 if ps < 0.30 else 3


def ratio_of(rec: dict):
    """(person/bg, skin/bg)；分母为 0/缺测 ⇒ None。曝光不变量。"""
    per = _num((rec.get('person') or {}).get('L'))
    bg = _num((rec.get('bg') or {}).get('L'))
    sk = _num((rec.get('skin') or {}).get('L'))
    pb = (per / bg) if per and bg else None
    fb = (sk / bg) if sk and bg else None
    return pb, fb


def features(rec: dict) -> dict:
    face = rec.get('face') or {}
    meta = rec.get('meta') or {}
    w, h = (meta.get('orig') or meta.get('det') or [1080, 720])[:2]
    box = face.get('box') or []
    fx = fy = fh = None
    if len(box) == 4 and h:
        cx, cy, bw, bh = box
        fx = (cx + bw / 2.0) / max(w, 1)
        fy = (cy + bh / 2.0) / max(h, 1)
        fh = bh / max(h, 1)
    ps = _num((rec.get('person') or {}).get('share'))
    pb, fb = ratio_of(rec)
    hb = rec.get('hue_bands') or []

    def sh(i):
        try:
            return float(hb[i].get('share') or 0.0)
        except Exception:                                  # noqa: BLE001
            return 0.0

    return dict(ps=ps, fx=fx, fy=fy, fh=fh, pb=pb, fb=fb,
                sky=_num(rec.get('sky_share_frac')),
                env=_num(rec.get('env_share')),
                green=sh(1) + sh(2), blue=sh(3),
                ar=(w / h) if h else None)


def distance(a: dict, b: dict, detail: bool = False):
    parts = {}
    vals = []
    for k in ('pb', 'fb'):
        x, y = a.get(k), b.get(k)
        if x and y and x > 0 and y > 0:
            vals.append(abs(math.log(x / y)))
    parts['ratio'] = (sum(vals) / len(vals)) if vals else None
    s = W['ratio'] * parts['ratio'] if parts['ratio'] is not None else 0.0
    for k, w in (('ps', 'ps'), ('fx', 'fx'), ('fy', 'fy')):
        x, y = a.get(k), b.get(k)
        if x is not None and y is not None:
            parts[k] = abs(x - y)
            s += W[w] * parts[k]
    fa, fb_ = framing(a.get('ps')), framing(b.get('ps'))
    parts['framing'] = 1.0 if (fa is not None and fb_ is not None and fa != fb_) else 0.0
    s += W['framing'] * parts['framing']
    for k in ('sky', 'env', 'green', 'blue'):
        x, y = a.get(k), b.get(k)
        if x is not None and y is not None:
            parts[k] = min(1.0, abs(x - y) / SCALE[k])
            s += W[k] * parts[k]
    x, y = a.get('ar'), b.get('ar')
    if x and y:
        parts['ar'] = min(1.0, abs(x - y) / SCALE['ar'])
        s += W['ar'] * parts['ar']
    return (s, parts) if detail else s


def _post_level(fname: str) -> bool:
    """`01.jpg`/`02.jpg` 这种整帖图（拼图或重出）；单帧是 `001_01_c00.jpg`。"""
    base = os.path.basename(str(fname))
    return len(base) <= 7 and base[:2].isdigit() and base.lower().endswith('.jpg')


def card_rows(card: str, tone_path: str = TONE, singles_only: bool = True) -> List[dict]:
    if not os.path.isfile(tone_path) and os.path.isfile(TONE_FALLBACK):
        tone_path = TONE_FALLBACK
    d = json.load(open(tone_path, encoding='utf-8'))
    rows = [r for r in (d.get('per_image') or [])
            if r.get('card') == card and r.get('role') == 'master' and not r.get('err')]
    if singles_only:
        keep = [r for r in rows if not _post_level(r.get('file') or '')]
        if len(keep) >= 5:
            rows = keep
    return rows


def peers_for(rec: dict, rows: List[dict], k: int = K,
              max_ratio: float = MAX_RATIO) -> List[dict]:
    a = features(rec)
    scored = []
    for m in rows:
        b = features(m)
        d, parts = distance(a, b, detail=True)
        scored.append((d, parts, m))
    scored.sort(key=lambda x: x[0])
    out = []
    for d, parts, m in scored[:k]:
        rd = parts.get('ratio')
        if rd is not None and rd > max_ratio:
            continue
        out.append(dict(distance=d, ratio_d=rd, parts=parts, rec=m, borrowed=False))
    if not out and scored:
        # 2A：卡里没有同场景同反差的 peer ⇒ 借最近的一张，标低置信
        d, parts, m = scored[0]
        out = [dict(distance=d, ratio_d=parts.get('ratio'), parts=parts,
                    rec=m, borrowed=True)]
    return out


def targets(peers: List[dict]) -> dict:
    """peer 组 → LR 用的关系目标 + 曝光 target（整幅 p50 中位）。"""
    if not peers:
        return {}
    base = _peer.targets(peers)
    if not base:
        return {}
    p50 = [float((p['rec'].get('img') or {}).get('p50')) for p in peers
           if (p['rec'].get('img') or {}).get('p50') is not None]
    if not p50:
        return {}
    iqr = float(np.percentile(p50, 75) - np.percentile(p50, 25)) if len(p50) >= 3 else None
    rd = [p.get('ratio_d') for p in peers if p.get('ratio_d') is not None]
    rdm = float(np.mean(rd)) if rd else None
    borrowed = any(p.get('borrowed') for p in peers)
    conf = '低'
    if rdm is not None and iqr is not None and not borrowed:
        if rdm <= CONF_HI[0] and iqr <= CONF_HI[1]:
            conf = '高'
        elif rdm <= CONF_MID[0] and iqr <= CONF_MID[1]:
            conf = '中'
    tgt = round(float(np.median(p50)), 1)
    base.update(
        peer_ids=[os.path.basename(str(p['rec'].get('file') or '')) for p in peers],
        peer_p50=[round(x, 1) for x in p50],
        target_p50=tgt,
        p50_iqr=round(iqr, 1) if iqr is not None else None,
        ratio_dist=round(rdm, 3) if rdm is not None else None,
        confidence=conf,
        peer_n=len(peers),
        source=('borrowed' if borrowed else 'peer'),
        target_source=('borrowed' if borrowed else 'peer'),
    )
    base['exposure'] = dict(base.get('exposure') or {}, p50=tgt, target_p50=tgt,
                            source=base['source'])
    return base


def build(recs: Dict[str, dict], card: str, k: int = K,
          tone_path: str = TONE, log=print) -> Dict[str, dict]:
    """{stem: 测量} + 卡名 → {stem: peer/target}。不渲图。"""
    rows = card_rows(card, tone_path=tone_path)
    if not rows:
        log('  卡 %s 没有 per-photo 测量 ⇒ peer 跳过' % card)
        return {}
    out = {}
    miss = 0
    for stem, rec in recs.items():
        ps = peers_for(rec, rows, k=k)
        if not ps:
            out[stem] = dict(peer_ids=[], peer_p50=[], target_p50=None,
                             p50_iqr=None, ratio_dist=None, confidence='低',
                             peer_n=0, source='none', target_source='none',
                             why='卡内没有可用 peer')
            miss += 1
            continue
        t = targets(ps)
        if not t:
            out[stem] = dict(peer_ids=[], target_p50=None, confidence='低',
                             peer_n=0, source='none', target_source='none',
                             why='peer 没有 p50')
            miss += 1
            continue
        out[stem] = t
    if miss:
        log('  peer：%d/%d 张没有可用 target（已标 none）' % (miss, len(recs)))
    return out
