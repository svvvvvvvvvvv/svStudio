# -*- coding: utf-8 -*-
"""逐片 peer：**只找一张**同光位的 master，拿它的直方图当靶。

设计（设计真源.md §3/§5、LR设计.md §3；2026-11-03 拍板）：
  · peer 的语义是「同一种光线情况」：逆光 / 面光 / 侧逆光 / 团状光，
    由人物亮度对背景亮度、以及光的方向判（peer.comp_of 的标签）；
  · **只找一张**：卡里同光位的 master 中，构图/场景距离最近的那张；
  · 找到后，靶 = 这张 peer 的**直方图**（p5/p25/p50/p75/p95/p99），
    由引擎反解 PE，让这张 RAW 渲出来的直方图跟它「大概对齐」；
  · 找不到同光位的 ⇒ **按本图机内 JPG 自己的直方图**（人优先，不借别人水平）；
  · **不做中位、不做平均**：多张取中位会把不同光位的曝光混在一起。

关系比（person.L / bg.L、skin.L / bg.L）仍是构图/光位匹配与距离的主量，
它与绝对亮度无关，换介质也成立。本模块只找 peer + 出目标，不渲图。
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

FACE_TARGET_L = 70.6      # 大师片 skin.L 中位（746 帧）—— 只作报告/兜底
FACE_MID_TARGET = 60.3    # 大师片 face_mid=(p5+p95)/2 中位（728 帧）—— **控制量**
                          # 脸的中位：不饱和、分布最窄（p10–p90 = 52~69）
PERSON_TARGET_L = 65.0     # 大师片 person.L 中位（746 帧）；没有 peer 时的「人正常曝光」
LIGHT_RATIO_TOL = 0.35     # 人/背景比允许差多少（log）≈ 1.4 倍，超过就不算「同一种光」
# 权重（2026-11-04 语料实测后加的四项）：
#   dr  场景反差 p95−p5   —— 决定「靶做不做得到」（平光配高反差 = 做不出来）
#   fh  脸高占画面比      —— 决定「按脸曝光」对整幅的影响（特写 vs 小人）
#   sk  肤色 L            —— 脸读数可比性（R²=0.34，单项最强）
#   dir 光方向符号        —— 侧光在左/右，脸暗侧不同
W = dict(ratio=4.0, ps=3.0, fx=1.0, fy=0.5, framing=1.0,
         sky=1.5, env=1.0, green=1.0, blue=1.0, ar=0.5,
         dr=2.0, fh=2.0, sk=2.0, dir=1.5,
         pq5=3.0, pq95=3.0)
SCALE_D = dict(dr=0.10, sk=0.10)    # dr/sk 的归一尺度（10 级 = 1 个单位）
SCALE = dict(sky=0.30, env=0.50, green=0.30, blue=0.20, ar=0.50)


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

    img = rec.get('img') or {}
    p5, p95 = _num(img.get('p5')), _num(img.get('p95'))
    dr = (p95 - p5) / 100.0 if (p5 is not None and p95 is not None) else None
    sk = _num((rec.get('skin') or {}).get('L'))
    pq5 = _num(img.get('p5'))
    pq95 = _num(img.get('p95'))
    dlr = _num((rec.get('light') or {}).get('d_lr_img'))
    dir_ = (1 if dlr > 0 else -1 if dlr < 0 else 0) if dlr is not None else None
    return dict(ps=ps, fx=fx, fy=fy, fh=fh, pb=pb, fb=fb,
                dr=dr, sk=sk, dir=dir_, pq5=pq5, pq95=pq95,
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
    for k in ('pq5', 'pq95'):
        x, y = a.get(k), b.get(k)
        if x is not None and y is not None:
            parts[k] = min(2.0, abs(x - y) / 12.0)     # 12 级 = 1 个单位
            s += W[k] * parts[k]
    for k in ('dr', 'sk'):
        x, y = a.get(k), b.get(k)
        if x is not None and y is not None:
            parts[k] = min(2.0, abs(x - y) / SCALE_D[k])
            s += W[k] * parts[k]
    x, y = a.get('dir'), b.get('dir')
    if x and y:
        parts['dir'] = 0.0 if x == y else 1.0
        s += W['dir'] * parts['dir']
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


# ---------------------------------------------------------------- 找 peer：只找一张

def light_of(rec: dict) -> str:
    """光位标签：front / side / back / back_side / dappled / unknown。

    与 peer.comp_of 同一把尺子（人物亮度 vs 背景亮度 + 光的方向 + 团状光），
    不在这里另造一套。
    """
    try:
        return str((_peer.comp_of(rec) or {}).get('light') or 'unknown')
    except Exception:                                      # noqa: BLE001
        return 'unknown'


def pick_peer(rec: dict, rows: List[dict]):
    """→ (peer, 说明)。**只挑一张**：先按光位同类，再按距离最近。

    设计（2026-11-03 拍板）：
      · peer 的语义是「同一种光线情况」（逆光 / 面光 / 侧逆光……），
        用人物亮度对背景亮度、以及光的方向判；peer.comp_of 已经给出标签；
      · 卡里没有同光位的 master ⇒ 返回 None，交给「保人曝光」那条路；
      · **不取多张、不做中位/平均** —— 找了 k 张再平均，等于把不同光位的曝光混在一起。
    """
    lab = light_of(rec)
    pb, _ = ratio_of(rec)
    pool = []
    for m in rows:
        if lab != 'unknown' and light_of(m) != lab:
            continue
        pool.append(m)          # 人/背景比不再当硬门（改权重），只留光位类别
    if not pool:
        have = '、'.join(sorted({light_of(m) for m in rows}))
        return None, ('卡里没有「%s」光位且人/背景亮度相当（%.2f× 以内）的 master'
                      '（卡内光位：%s）' % (lab, 2 ** LIGHT_RATIO_TOL, have))
    a = features(rec)
    best = min(pool, key=lambda m: distance(a, features(m)))
    pb, _ = ratio_of(rec)
    pb_m, _ = ratio_of(best)
    return dict(rec=best, light=lab, same_light=True,
                distance=distance(a, features(best)),
                ratio_d=(abs(math.log(pb / pb_m)) if (pb and pb_m) else None)), ''


def rank_of(rec: dict, L) -> Optional[float]:
    """L 在这张图分布里的百分位（用 p5..p95 插值）。单调映射下它不变。"""
    im = (rec or {}).get('img') or {}
    if L is None:
        return None
    qs = [(5, _num(im.get('p5'))), (10, _num(im.get('p10'))),
          (25, _num(im.get('p25'))), (50, _num(im.get('p50'))),
          (75, _num(im.get('p75'))), (90, _num(im.get('p90'))),
          (95, _num(im.get('p95')))]
    qs = [(p, v) for p, v in qs if v is not None]
    if len(qs) < 3:
        return None
    if L <= qs[0][1]:
        return 0.0
    if L >= qs[-1][1]:
        return 100.0
    for (p0, v0), (p1, v1) in zip(qs, qs[1:]):
        if v0 <= L <= v1:
            if v1 - v0 < 1e-9:
                return float(p1)
            return float(p0 + (p1 - p0) * (L - v0) / (v1 - v0))
    return None


BG_CONST = dict(p10=11.0, L=48.2, p90=85.9, p95=90.2)   # 语料 746 帧的 bg 中位


def _bg_of(rec: dict):
    """背景分布四件套（p10 / 均值 / p90 / p95）；缺测给 None。"""
    bg = (rec or {}).get('bg') or {}
    out = dict(p10=_num(bg.get('L_p10')), L=_num(bg.get('L')),
               p90=_num(bg.get('L_p90')), p95=_num(bg.get('L_p95')))
    return out if all(v is not None for v in out.values()) else None


def _face_mid(rec: dict):
    """脸的中位 = (face_p5 + face_p95)/2；缺测给 None。"""
    p5 = _num((rec or {}).get('face_p5'))
    p95 = _num((rec or {}).get('face_p95'))
    return (p5 + p95) / 2.0 if (p5 is not None and p95 is not None) else None


def _person(rec: dict):
    """→ (人的亮度, 人离这张图高光 p95 的距离)。缺测 ⇒ (None, None)。

    用「距离」而不是百分位：同一张 JPG 上量的距离跨到胶片上更稳
    （两套曲线的形状不同，百分位不可搬）。
    """
    L = _num((rec.get('person') or {}).get('L'))
    if L is None:
        L = _num((rec.get('skin') or {}).get('L'))
    p95 = _num(((rec or {}).get('img') or {}).get('p95'))
    gap = (p95 - L) if (L is not None and p95 is not None) else None
    return L, gap


def _q(rec: dict) -> dict:
    """一张图的直方图（分位）。**目标就是它**，不是某一个数、更不是平均值。"""
    im = (rec or {}).get('img') or {}
    return {k: _num(im.get(k)) for k in ('p5', 'p25', 'p50', 'p75', 'p95', 'p99')}


def _rq(q: dict) -> dict:
    return {k: (round(v, 1) if v is not None else None) for k, v in q.items()}


def target_of(peer: dict, rec: dict) -> dict:
    """一张 peer → 这张的目标：**直方图**（给引擎反解 PE）+ 形状/肤色/关系（给 LR）。"""
    r = peer['rec']
    q = _q(r)
    base = _peer.targets([peer])          # 只有一张 ⇒ 取的就是它自己的值，不是平均
    base.update(
        peer_ids=[os.path.basename(str(r.get('file') or ''))],
        peer_n=1,
        peer_light=peer.get('light'),
        peer_distance=round(float(peer.get('distance') or 0.0), 3),
        ratio_d=(round(float(peer['ratio_d']), 3)
                 if peer.get('ratio_d') is not None else None),
        peer_q=_rq(q),
        peer_rel=(ratio_of(r)[0] or 1.55),      # peer 的 人/背景 亮度比（切边预算用）
        bg_target=_bg_of(r),                    # peer 的背景分布（引擎的对齐目标）
        target_p50=(round(q['p50'], 1) if q.get('p50') is not None else None),
        # 锚：把**这张的人**放到 peer 的人所在的位置（peer 没有人的测量就退大师片中位）
        person_target_L=(_num((r.get('person') or {}).get('L')) or PERSON_TARGET_L),
        # 锚脸：目标 = peer 的皮肤亮度；脸框用**本图**的（从 RAW 里取脸上像素）
        face_target_L=(_num((r.get('skin') or {}).get('L')) or FACE_TARGET_L),
        # 控制量：peer 的脸中位 (p5+p95)/2 —— 不饱和、最稳；不用亮侧 skin.L（会顶肩部）
        face_mid_target=_face_mid(r) or FACE_MID_TARGET,
        face_box=((rec.get('face') or {}).get('box')),
        face_orig=((rec.get('meta') or {}).get('orig')),
        person_gap=(_person(rec)[1] if _person(rec)[1] is not None else 25.0),
        person_own_L=(_person(rec)[0]),
        target_from='peer', source='peer', target_source='peer',
    )
    return base


def constant_target(rec: dict, why: str = '') -> dict:
    """找不到同光位 peer 时的兜底：**常数靶**（语料 face_mid 中位 60.3）。

    2026-11-04 设计：同光位一张都没有 ⇒ 靶用常数，status=constant；
    不再借别人的直方图、不另编"正常亮度"。
    """
    L, g = _person(rec)
    # 常数分布：746 张大师片的中位（引擎对齐整幅直方图用）
    q_const = dict(p5=8.2, p25=21.8, p50=51.8, p75=75.7, p95=91.0)
    return dict(peer_ids=[], peer_n=0, peer_light=None, peer_distance=None,
                ratio_d=None, peer_q=q_const, peer_rel=1.55,
                bg_target=BG_CONST,
                target_p50=q_const['p50'],
                person_target_L=PERSON_TARGET_L,
                face_target_L=FACE_TARGET_L,
                face_mid_target=FACE_MID_TARGET,
                face_box=((rec.get('face') or {}).get('box')),
                face_orig=((rec.get('meta') or {}).get('orig')),
                person_gap=(g if g is not None else 25.0),
                person_own_L=L,
                target_from='constant', source='none', target_source='constant',
                why=why)


own_jpg_target = constant_target       # 旧名兼容（2026-11-04 改名为 constant_target）


def build(recs: Dict[str, dict], card: str, tone_path: str = TONE,
          log=print) -> Dict[str, dict]:
    """{stem: 测量} + 卡名 → {stem: 目标}。不渲图。"""
    rows = card_rows(card, tone_path=tone_path)
    if not rows:
        log('  卡 %s 没有 per-photo 测量 ⇒ peer 跳过' % card)
        return {}
    out: Dict[str, dict] = {}
    n_own = 0
    for stem, rec in recs.items():
        peer, why = pick_peer(rec, rows)
        if peer is None:
            out[stem] = constant_target(rec, why)
            n_own += 1
            log('  %-9s 无同光位 peer（%s）⇒ 按本图机内 JPG 的直方图' % (stem, why))
            continue
        t = target_of(peer, rec)
        out[stem] = t
        log('  %-9s peer %-12s（%s，距离 %.2f）→ p50 %5.1f ｜ p95 %5.1f'
            % (stem, (t['peer_ids'] or ['—'])[0], t.get('peer_light'),
               t.get('peer_distance') or 0.0,
               t.get('target_p50') or -1, t['peer_q'].get('p95') or -1))
    log('  目标来源：同光位 peer %d 张、按本图 JPG %d 张（不做中位/平均）'
        % (len(out) - n_own, n_own))
    return out
