# -*- coding: utf-8 -*-
r"""**两条预设的逐字段账**（谁新谁旧、差在哪、哪些是死键）。

## 用途
① 比"从零 vs 旧链"这类**同族两份预设**；② 比**同一份预设的两个版本**（换版本/换底座前后）；
③ 顺手分出"只有旧的那份才有的键"= **死键候选**（`_apply()` 不读的）。

## 用法
```python
import _lib.preset_diff as PD
rows, only_old, same = PD.diff(old_json, new_json)
```
CLI：`python -m _lib.preset_diff <A.json> <B.json> [out.md]`
（A = 参照，B = 现在；输出里 `B` 列是"现在"）
"""
from __future__ import annotations

import io
import json
import os
import sys


def leaves(o, pre=''):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from leaves(v, '%s.%s' % (pre, k) if pre else k)
    else:
        yield pre, o


def fmt(v):
    if isinstance(v, list):
        return '[' + ', '.join(fmt(x) for x in v) + ']'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, float):
        return '%.6g' % v
    return str(v)


def _same(a, b):
    r"""**逐位比**（不用 `%.6g` 截断 —— 截断会让 0.9000001 vs 0.9000002 报"相同"）。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return repr(a) == repr(b)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def diff(a_json, b_json):
    r"""@returns {(rows, only_a, only_b, same_n)}
    `rows` = [(键, A 值, B 值)]（值不同）· `only_a` = [(键, 值)]（只有 A 有 ⇒ 死键候选）"""
    a = dict(leaves(json.load(io.open(a_json, encoding='utf-8'))))
    b = dict(leaves(json.load(io.open(b_json, encoding='utf-8'))))
    ka = [k for k in a if not k.split('.')[-1].startswith('_note')]
    kb = [k for k in b if not k.split('.')[-1].startswith('_note')]
    rows, same = [], 0
    for k in kb:
        if k in a:
            if _same(a[k], b[k]):
                same += 1
            else:
                rows.append((k, fmt(a[k]), fmt(b[k])))
    only_a = [(k, fmt(a[k])) for k in ka if k not in b]
    only_b = [(k, fmt(b[k])) for k in kb if k not in a]
    return rows, only_a, only_b, same


def to_md(rows, only_a, only_b, same, a_name, b_name):
    L = ['# 逐字段账：`%s`（参照） vs `%s`（现在）' % (a_name, b_name), '',
         '> ⚠ **JSON 层相同 ≠ 画面相同**：还有一层 `targets.json`（`_scene_engine` / `_stock_engine`）。', '',
         '## 一、值不同的键（%d 个）' % len(rows), '',
         '| 键 | %s（参照） | %s（现在） |' % (a_name, b_name), '|---|---|---|']
    L += ['| `%s` | %s | **%s** |' % (k, x, y) for k, x, y in rows]
    L += ['', '（逐位相同的键 = %d 个，不列）' % same, '',
          '## 二、只有参照有的键（死键候选，%d 个）' % len(only_a), '',
          '| 键 | 值 |', '|---|---|']
    L += ['| `%s` | %s |' % (k, v) for k, v in only_a]
    if only_b:
        L += ['', '## 三、只有"现在"有的键（%d 个）' % len(only_b), '']
        L += ['- `%s` = %s' % (k, v) for k, v in only_b]
    L += ['', '★ 判死键请回读 `svFilm/presets.py::_apply()` 的读取清单（那是唯一权威）。']
    return '\n'.join(L) + '\n'


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(__doc__)
        return 2
    a, b = argv[0], argv[1]
    out = argv[2] if len(argv) > 2 else 'preset_diff.md'
    rows, only_a, only_b, same = diff(a, b)
    io.open(out, 'w', encoding='utf-8', newline='\n').write(
        to_md(rows, only_a, only_b, same,
              os.path.splitext(os.path.basename(a))[0],
              os.path.splitext(os.path.basename(b))[0]))
    print('不同 %d ｜ 相同 %d ｜ 参照独有 %d ｜ 现在独有 %d → %s'
          % (len(rows), same, len(only_a), len(only_b), out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
