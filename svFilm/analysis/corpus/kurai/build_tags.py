# -*- coding: utf-8 -*-
"""重建 tags_all.json（匹配用的分面标签）—— 从 m_*_v4 程序化算，不靠人手标。

四个分面（对齐设计真源 §48/§57 与 matchcard.py 的用法）：
  scene.type            env_share / sky_share  → interior / street / greenery / studio
  composition.framing   face_size 中位         → closeup / half / environment
  color_env.value       warm_cool_b 中位        → warm / neutral / cool
  light                 gap / dark 双区判据     → back / mid / front / unknown

输出：E:\svStudio\svFilm\analysis\corpus\kurai\tags_all.json
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import numpy as np

BASE = r'E:\svStudio\svFilm\analysis\corpus\kurai'
CORPORA = {'鹿井': 'm_鹿井_v4.json', '滨田': 'm_滨田_v4.json', 'xhs': 'm_xhs_v4.json'}


def _med(vals):
    v = [x for x in vals if x is not None]
    return float(np.median(v)) if v else None


def _light(rec):
    bg = rec.get('bg') or {}
    sk = rec.get('skin') or {}
    gap = None
    if bg.get('L_p90') is not None and sk.get('L') is not None:
        gap = float(bg['L_p90']) - float(sk['L'])
    dark = rec.get('face_dark_share40')
    if gap is None:
        return 'unknown'
    if gap >= 35 or (dark is not None and dark >= 0.35 and gap >= 12):
        return 'back'
    if dark is not None and dark <= 0.12 and gap <= 12:
        return 'front'
    return 'mid'


def main() -> int:
    out = []
    q_env, q_fs = [0.2, 0.6], [0.2, 0.5]
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
            env = _med([r.get('env_share') for r in rs])
            sky = _med([r.get('sky_share_frac') for r in rs])
            fs = _med([r.get('face_size') for r in rs])
            wc = _med([r.get('warm_cool_b') for r in rs])
            lights = [_light(r) for r in rs]
            light = max(set(lights), key=lights.count) if lights else 'unknown'

            scene = ('studio' if (sky or 0) >= 0.10 else
                     'greenery' if (env or 0) >= q_env[1] else
                     'street' if (env or 0) >= q_env[0] else 'interior')
            framing = ('closeup' if (fs or 0) >= q_fs[1] else
                       'half' if (fs or 0) >= q_fs[0] else 'environment')
            cenv = ('warm' if (wc or 0) > 3 else 'cool' if (wc or 0) < -2 else 'neutral')
            out.append(dict(dir=d, corpus=corpus, n=len(rs),
                            tags={'background.type': ('nature' if scene == 'greenery' else scene),
                                  'background.palette': ('green' if scene == 'greenery' else cenv),
                                  'color_env.value': cenv,
                                  'composition.framing': framing,
                                  'light.direction': light},
                            raw=dict(env_share=env, sky_share=sky, face_size=fs,
                                     warm_cool_b=wc)))
    dst = os.path.join(BASE, 'tags_all.json')
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    from collections import Counter
    for facet in ('background.type', 'background.palette', 'color_env.value', 'composition.framing', 'light.direction'):
        c = Counter(e['tags'][facet] for e in out)
        print('%-24s %s' % (facet, dict(c)))
    print('写出 %s（%d 个目录）' % (dst, len(out)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
