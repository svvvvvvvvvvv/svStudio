# -*- coding: utf-8 -*-
"""逐片 peer：在选中的卡里找「同构图 + 同场景/背景」的大师照片。

用途：
  · LR-B 的逐图曲线/曝光/肤色目标 —— **不是整卡中位**；
  · 输出 peer_ids / n / score / IQR，plan 里可复核。

★ 2026-11-01（按技能 §194/§195 纠正）：
  · **光位/光向量不参与 peer 距离，更不驱动影调。**
    证据：§194 四分组不成立（η² 0.245 里 87% 是 E_bg 同义反复；E_lr_t 对落点 0.004）；
    §195 拍板 A：光位不再驱动影调，改连续量 `{"by": degree}`；
    `source_deg` 只驱动光晕/柔光，`deg_reflect` 只输出。
  · 光位只做**诊断字段**（`light_probe` 四分量），不进 `targets/curve/exposure/skin`。
  · peer 只用构图（景别/人占幅/脸位置）；场景/背景结构后续用 card 内容卡补。

口径：
  · 构图：`person.share` 分景别、脸中心 x/y、人占幅连续量；
  · unknown 不参与距离；peer 不足按 framing 放宽，再不够回退卡。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

# v04 = 1080 交付域 + grain 字段；读不到再退回 v03
TONE = r'E:\svStudio\svFilm\analysis\corpus\kurai\photos_tone_v04.json'
TONE_FALLBACK = r'E:\svStudio\svFilm\analysis\corpus\kurai\photos_tone_v03.json'

W = dict(framing=1.5, person_share=3.0, face_x=1.0, face_y=0.5)


def _med(vals):
    v = [float(x) for x in vals if isinstance(x, (int, float))]
    if not v:
        return None
    v.sort()
    return v[len(v) // 2]


def _iqr(vals):
    v = sorted(float(x) for x in vals if isinstance(x, (int, float)))
    if len(v) < 2:
        return None
    lo, hi = v[int(0.25 * (len(v) - 1))], v[int(0.75 * (len(v) - 1))]
    return hi - lo


def comp_of(rec: dict) -> dict:
    """从 measure_all 的一条记录算构图/光位（同一把尺子）。

    景别用 **person.share（人面积/画幅）**，不用脸高：
      脸高受裁切/镜头影响太大；人面积在同一套掩膜下更稳。
      environment <0.06 · full <0.14 · half <0.30 · closeup ≥0.30。
    """
    face = rec.get('face') or {}
    meta = rec.get('meta') or {}
    # face.box 是**原生坐标**；用 orig，不是 det（det 只有 1080）
    w, h = (meta.get('orig') or meta.get('det') or [1080, 720])[:2]
    box = face.get('box') or []
    if len(box) == 4 and h:
        cx, cy, bw, bh = box
        fx = (cx + bw / 2.0) / max(w, 1)
        fy = (cy + bh / 2.0) / max(h, 1)
        fh = bh / max(h, 1)
    else:
        fx = fy = fh = None
    ps = (rec.get('person') or {}).get('share')
    if ps is None:
        framing = 'unknown'
    elif ps < 0.06:
        framing = 'environment'
    elif ps < 0.14:
        framing = 'full'
    elif ps < 0.30:
        framing = 'half'
    else:
        framing = 'closeup'
    sx = None if fx is None else ('left' if fx < 0.38 else
                                  ('right' if fx > 0.62 else 'center'))
    sy = None if fy is None else ('upper' if fy < 0.38 else
                                  ('lower' if fy > 0.62 else 'middle'))
    li = rec.get('light') or {}
    d = li.get('d_lr_img')
    rn = li.get('ratio_near90')
    cl = li.get('clump')
    label, conf = 'unknown', 0.0
    if cl is not None and cl >= 0.53:
        label, conf = 'dappled', 0.55
    elif rn is not None and rn < 0.78 and d is not None and abs(d) < 8:
        label, conf = 'back', 0.5
    elif rn is not None and rn < 0.78 and d is not None and abs(d) >= 8:
        label, conf = 'back_side', 0.45
    elif d is not None and abs(d) >= 8:
        label, conf = 'side', 0.45
    else:
        lv = (rec.get('skin') or {}).get('L')
        pm = (rec.get('img') or {}).get('p50')
        if lv is not None and pm and float(lv) / float(pm) >= 0.95:
            label, conf = 'front', 0.35
    # 人给/VLM 标签优先（`build(labels=...)` 挂上来）
    lab = rec.get('_labels') or {}
    if lab.get('framing'):
        framing = lab['framing']
    if lab.get('light'):
        label, conf = lab['light'], float(lab.get('light_conf') or 1.0)
    return dict(framing=framing, person_share=ps, face_x=fx, face_y=fy,
                face_h_frac=fh, subject_x=sx, subject_y=sy,
                light=label, light_conf=conf)


def _d(a: dict, b: dict) -> float:
    s = 0.0
    if a.get('framing') not in (None, 'unknown') and b.get('framing') not in (None, 'unknown'):
        s += W['framing'] * (0.0 if a['framing'] == b['framing'] else 1.0)
    if a.get('person_share') is not None and b.get('person_share') is not None:
        s += W['person_share'] * abs(float(a['person_share']) - float(b['person_share']))
    for k in ('face_x', 'face_y'):
        if a.get(k) is not None and b.get(k) is not None:
            s += W[k] * abs(float(a[k]) - float(b[k]))
    # ★ 2026-11-01：**光位/光向量不参与 peer 距离**。
    #   技能 §194/§195：四分组不成立、E_lr_t 对落点 η²=0.004、E_bg 是同义反复；
    #   光位不再驱动影调。peer 只按构图/场景；light 只做诊断字段。
    return s


def peers_for(rec: dict, card_rows: List[dict], k: int = 3,
              max_d: float = 2.5) -> List[dict]:
    a = comp_of(rec)
    scored = []
    for m in card_rows:
        b = comp_of(m)
        scored.append((_d(a, b), m, b))
    scored.sort(key=lambda x: x[0])
    return [dict(distance=round(d, 3), rec=m, comp=b)
            for d, m, b in scored[:k] if d <= max_d]


def targets(peers: List[dict]) -> dict:
    """peer 组 → 逐图目标（曲线/关系/肤色）。"""
    if not peers:
        return {}
    rs = [p['rec'] for p in peers]
    shape = {k: _med([(r.get('shape') or {}).get(k) for r in rs])
             for k in ('toe', 'shoulder', 'contrast_win')}
    p95 = _med([(r.get('img') or {}).get('p95') for r in rs])
    if p95 is not None:
        shape['p95'] = p95
    rel = dict(
        face_over_bg=_med([_ratio((r.get('skin') or {}).get('L'),
                                  (r.get('bg') or {}).get('L')) for r in rs]),
        p50=_med([(r.get('img') or {}).get('p50') for r in rs]),
        p95=_med([(r.get('img') or {}).get('p95') for r in rs]),
        contrast=_med([(r.get('img') or {}).get('contrast') for r in rs]),
        warm_cool_b=_med([r.get('warm_cool_b') for r in rs]),
    )
    skin = dict(
        L=_med([(r.get('skin') or {}).get('L') for r in rs]),
        C=_med([(r.get('skin') or {}).get('C') for r in rs]),
        h=_med([(r.get('skin') or {}).get('h') for r in rs]),
        b=_med([(r.get('skin') or {}).get('b') for r in rs]),
        C_over_L=_med([_ratio((r.get('skin') or {}).get('C'),
                              (r.get('skin') or {}).get('L')) for r in rs]),
    )
    return dict(shape=shape, rel=rel, skin=skin,
                n=len(peers),
                distances=[p['distance'] for p in peers],
                iqr=dict(face_h=_iqr([(r.get('face') or {}).get('size') for r in rs]),
                         p95=_iqr([(r.get('img') or {}).get('p95') for r in rs]),
                         skin_C=_iqr([(r.get('skin') or {}).get('C') for r in rs])))


def _ratio(a, b):
    try:
        return float(a) / float(b) if b else None
    except (TypeError, ValueError):
        return None


def build(recs: Dict[str, dict], card: str, tone_path: str = TONE,
          k: int = 3, labels: Optional[Dict[str, dict]] = None,
          log=print) -> Dict[str, dict]:
    """逐图 peer + target。返回 {stem: {peer_ids, peer_n, peer_score, ...}}。

    `labels`：{basename: {'light': 'front|side|...', 'framing': 'half|...'}}；
    有标签就覆盖四分量预填（低置信）。
    """
    if not os.path.isfile(tone_path) and os.path.isfile(TONE_FALLBACK):
        tone_path = TONE_FALLBACK
    if not os.path.isfile(tone_path):
        log('  没有 photos_tone：%s ⇒ peer 跳过' % tone_path)
        return {}
    d = json.load(open(tone_path, encoding='utf-8'))
    rows = [r for r in (d.get('per_image') or [])
            if r.get('card') == card and r.get('role') == 'master'
            and not r.get('err')]
    if labels:
        for i, r in enumerate(rows):
            lb = labels.get(os.path.basename(r.get('file') or ''))
            if lb:
                rows[i] = dict(r, _labels=lb)
    if not rows:
        log('  卡 %s 没有 per-photo 测量 ⇒ peer 跳过' % card)
        return {}
    out = {}
    for stem, rec in recs.items():
        ps = peers_for(rec, rows, k=k)
        if not ps:
            out[stem] = dict(peer_ids=[], peer_n=0,
                             why='没有同构图/同光位 peer')
            continue
        t = targets(ps)
        t['peer_n'] = t.pop('n')
        t.update(peer_ids=[os.path.basename(p['rec'].get('file') or '') for p in ps],
                 peer_score=[p['distance'] for p in ps],
                 light=comp_of(rec).get('light'),
                 framing=comp_of(rec).get('framing'))
        out[stem] = t
    return out
