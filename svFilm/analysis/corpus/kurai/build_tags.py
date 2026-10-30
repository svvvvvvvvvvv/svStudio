# -*- coding: utf-8 -*-
"""重建 tags_all.json（匹配用的分面标签）—— 两遍：先算分位阈值，再打标签。

分面（对齐 matchcard.LABEL_FACETS + light.direction）：
  background.type       env_share 三分位 → interior / street / nature（sky 高 → studio）
  background.palette    同上 → green / warm / neutral
  color_env.value       warm_cool_b → warm / neutral / cool
  composition.framing   face_size 三分位 → environment / half / closeup
  light.direction       gap/dark 双区判据 → back / mid / front

输出：analysis/corpus/kurai/tags_all.json（嵌套结构，加载端 `_tags_by_dir` 认这个）
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

import numpy as np

BASE = r'E:\svStudio\svFilm\analysis\corpus\kurai'
CORPORA = {'鹿井': 'm_鹿井_v4.json', '滨田': 'm_滨田_v4.json', 'xhs': 'm_xhs_v4.json'}


def _med(vals):
    v = [x for x in vals if x is not None]
    return float(np.median(v)) if v else None


def _light(rec):
    bg, sk = rec.get('bg') or {}, rec.get('skin') or {}
    gap = (float(bg['L_p90']) - float(sk['L'])
           if bg.get('L_p90') is not None and sk.get('L') is not None else None)
    dark = rec.get('face_dark_share40')
    if gap is None:
        return 'unknown'
    if gap >= 35 or (dark is not None and dark >= 0.35 and gap >= 12):
        return 'back'
    if dark is not None and dark <= 0.12 and gap <= 12:
        return 'front'
    return 'mid'


def _tag(v, lo, hi, names):
    if v is None:
        return names[1]
    return names[0] if v < lo else (names[2] if v >= hi else names[1])


def main() -> int:
    raw = []
    for corpus, fn in CORPORA.items():
        p = os.path.join(BASE, fn)
        if not os.path.isfile(p):
            continue
        rows = json.load(open(p, encoding='utf-8')).get('per_image') or []
        by = defaultdict(list)
        for r in rows:
            if r.get('file'):
                by[r['file'].split('/')[0]].append(r)
        for d, rs in sorted(by.items()):
            lights = [_light(r) for r in rs]
            raw.append(dict(dir=d, corpus=corpus, n=len(rs),
                            env=_med([r.get('env_share') for r in rs]),
                            sky=_med([r.get('sky_share_frac') for r in rs]),
                            fs=_med([r.get('face_size') for r in rs]),
                            wc=_med([r.get('warm_cool_b') for r in rs]),
                            light=max(set(lights), key=lights.count)))

    def cuts(key):
        v = [r[key] for r in raw if r[key] is not None]
        if len(v) <= 5:
            return 0.0, 1.0
        return float(np.percentile(v, 33)), float(np.percentile(v, 66))

    e_lo, e_hi = cuts('env')
    f_lo, f_hi = cuts('fs')
    out = []
    for r in raw:
        scene = ('studio' if (r['sky'] or 0) >= 0.10
                 else _tag(r['env'], e_lo, e_hi, ['interior', 'street', 'nature']))
        framing = _tag(r['fs'], f_lo, f_hi, ['environment', 'half', 'closeup'])
        # ★ 与 matchcard._COLOR_RULES 对齐（±6），否则批/卡同一批数据会翻面
        cenv = ('warm' if (r['wc'] or 0) > 6 else 'cool' if (r['wc'] or 0) < -6 else 'neutral')
        pal = 'green' if scene == 'nature' else ('warm' if cenv == 'warm' else 'neutral')
        facets = {'background.type': scene, 'background.palette': pal,
                  'color_env.value': cenv, 'composition.framing': framing,
                  'light.direction': r['light']}
        out.append(dict(dir=r['dir'], corpus=r['corpus'], n=r['n'],
                        tags={'light': {k: {'dominant': v, 'confidence': 1.0}
                                        for k, v in facets.items()}},
                        raw=dict(env_share=r['env'], sky_share=r['sky'],
                                 face_size=r['fs'], warm_cool_b=r['wc'])))
    json.dump(out, open(os.path.join(BASE, 'tags_all.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('阈值：env %.3f/%.3f ｜ face_size %.3f/%.3f' % (e_lo, e_hi, f_lo, f_hi))
    for facet in ('background.type', 'background.palette', 'color_env.value',
                  'composition.framing', 'light.direction'):
        print('%-22s %s' % (facet, dict(Counter(e['tags']['light'][facet]['dominant'] for e in out))))
    print('写出 %d 个目录' % len(out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
