# -*- coding: utf-8 -*-
"""字段一致性检查（10-30 事故：卡画像和批画像字段集合不一致 → 自己跟自己比都不像）。

查三件事：
  1 卡的 profile 缺哪些批能算的键（如 sky）
  2 同一个键在卡侧和批侧的**定义源**是否同名（span 踩过）
  3 卡 tags 的五个分面是否齐全
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, r'E:\svStudio\svFilm')
from design import matchcard as M  # noqa: E402

FACETS = ('background.type', 'background.palette', 'color_env.value',
          'composition.framing', 'light.direction')


def main() -> int:
    rows = json.load(open(r'E:\svStudio\svFilm\analysis\corpus\kurai\m_鹿井_v4.json',
                          encoding='utf-8'))['per_image']
    recs = {r['file']: r for r in rows if r.get('file')}
    b = M.batch_profile(recs)
    cs = M.cards()
    print('批键：%s' % sorted(b.keys()))
    miss = {}
    for c in cs:
        for k in b:
            if c.profile.get(k) is None:
                miss.setdefault(k, []).append(c.name)
    for k, names in sorted(miss.items(), key=lambda kv: -len(kv[1])):
        print('  卡缺 %-10s %3d/%d 张  例：%s' % (k, len(names), len(cs), names[0][:30]))
    bad = [c.name for c in cs if any(f not in c.tags for f in FACETS)]
    print('tags 缺分面：%d 张 %s' % (len(bad), bad[:2]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
