# -*- coding: utf-8 -*-
r"""**预设注记索引** —— 把一条预设 JSON 里的 `_note_*` 按**它标注的字段**归位。

## 用途（纪律 §二.3 的工具化）
「改任何值之前，先读它当初为什么是这个值」⇒ 这个模块把"当初为什么"**按字段**摆出来，
并给出每条的**字数**（>400 字的按 §二.6 基本是过程记录，该精简）。

## 用法
```python
import _lib.notes_index as NI
NI.build(r'<preset.json>', r'<out.md>')          # 常规
```
CLI：`python -m _lib.notes_index <preset.json> [out.md]`
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


def build(preset_json, out_md=None, title=None):
    r"""@returns {str} 写出的 md 路径"""
    d = json.load(io.open(preset_json, encoding='utf-8'))
    L = dict(leaves(d))
    notes = [(k, v) for k, v in L.items() if k.split('.')[-1].startswith('_note')]
    by_field = {}
    for k, v in notes:
        parts = k.split('.')
        by_field.setdefault('.'.join(parts[:-1]) or '(顶层)', []).append((parts[-1], v))
    tot = sum(len(v) for _k, v in notes)
    name = title or os.path.basename(preset_json)
    out = ['# %s 注记索引 —— 「它当初为什么是这个值」' % name, '',
           '> 用途 = 纪律 §二.3（改任何值前先读它当初为什么）。',
           '> ★ 按 §二.6，`_note_*` 该写「值 + 一句话依据 + 代价 + 报告目录名」，**不该写历史沿革**。', '',
           '合计 %d 条注记 / %d 字。' % (len(notes), tot), '',
           '| 字段 | 注记 | 字数 | 首行（结论） |', '|---|---|---|---|']
    for field in sorted(by_field):
        for nm, v in by_field[field]:
            out.append('| `%s` | `%s` | %d | %s |'
                       % (field, nm, len(v), v.strip().split('\n')[0][:78]))
    out += ['', '---', '', '## 全文（按字段分组）', '']
    for field in sorted(by_field):
        out.append('### `%s`' % field)
        for nm, v in by_field[field]:
            out += ['', '**`%s`**（%d 字）' % (nm, len(v)), '', '```', v, '```']
        out.append('')
    md = '\n'.join(out) + '\n'
    if out_md:
        io.open(out_md, 'w', encoding='utf-8', newline='\n').write(md)
    return out_md, len(notes), tot, by_field


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    src = argv[0]
    dst = argv[1] if len(argv) > 1 else os.path.splitext(src)[0] + '_notes_index.md'
    _p, n, tot, by = build(src, dst)
    print('注记 %d 条 / %d 字 ｜ %d 个字段 → %s' % (n, tot, len(by), dst))
    return 0


if __name__ == '__main__':
    sys.exit(main())
